import json
import logging
from datetime import datetime, timedelta
from typing import Annotated, Optional
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.dependencies import CurrentContext

from mmss_mcp.db.database import db
from mmss_mcp.services.hrp_classifier import classify_from_dict

logger = logging.getLogger(__name__)

# MMSS Yojna ANC schedule — gestational weeks from LMP
ANC_SCHEDULE_WEEKS = [8, 14, 18, 22, 26, 30, 34, 36, 40]


def _lmp_to_visit_date(lmp_date: str, weeks: int) -> str:
    lmp = datetime.strptime(lmp_date, "%Y-%m-%d")
    return (lmp + timedelta(weeks=weeks)).strftime("%Y-%m-%d")


def register_anc_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def schedule_anc_visits(
        identifier: Annotated[str, "Registration ID or phone number of the patient"],
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Create the full 9-visit ANC schedule for a registered patient per MMSS Yojna protocol.

        Visits are scheduled at gestational weeks 8, 14, 18, 22, 26, 30, 34, 36, and 40
        calculated from the patient's LMP date.

        Safe to call multiple times — skips visits that are already scheduled.
        Returns the full visit schedule.
        """
        logger.info(f"[schedule_anc_visits] Scheduling for: {identifier}")

        try:
            with db() as conn:
                patient = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=? OR phone=?",
                    (identifier, identifier),
                ).fetchone()
                if not patient:
                    return {"success": False, "error": f"Patient not found: {identifier}"}

                pid = patient["id"]
                lmp_date = patient["lmp_date"]
                created = 0

                for i, weeks in enumerate(ANC_SCHEDULE_WEEKS, start=1):
                    scheduled_date = _lmp_to_visit_date(lmp_date, weeks)
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO anc_visits
                               (patient_id, visit_number, scheduled_date)
                               VALUES (?,?,?)""",
                            (pid, i, scheduled_date),
                        )
                        created += 1
                    except Exception:
                        pass  # UNIQUE constraint — already exists

                visits = conn.execute(
                    "SELECT visit_number, scheduled_date, completed_at "
                    "FROM anc_visits WHERE patient_id=? ORDER BY visit_number",
                    (pid,),
                ).fetchall()

            schedule = [
                {
                    "visit": v["visit_number"],
                    "date": v["scheduled_date"],
                    "week": ANC_SCHEDULE_WEEKS[v["visit_number"] - 1],
                    "completed": bool(v["completed_at"]),
                }
                for v in visits
            ]

            logger.info(f"[schedule_anc_visits] {len(schedule)} visits scheduled for {patient['reg_id']}")
            return {
                "success": True,
                "data": {
                    "reg_id": patient["reg_id"],
                    "name": patient["name"],
                    "lmp_date": lmp_date,
                    "visits": schedule,
                    "message": f"9 ANC visits scheduled from LMP {lmp_date}.",
                },
            }

        except Exception as e:
            logger.error(f"[schedule_anc_visits] Failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def log_anc_visit(
        identifier: Annotated[str, "Registration ID or phone number of the patient"],
        visit_number: Annotated[int, "ANC visit number (1–9)"],
        bp_systolic: Annotated[Optional[int], "Systolic blood pressure measured at visit (mmHg)"] = None,
        bp_diastolic: Annotated[Optional[int], "Diastolic blood pressure measured at visit (mmHg)"] = None,
        hb: Annotated[Optional[float], "Haemoglobin measured at visit (g/dL)"] = None,
        weight_kg: Annotated[Optional[float], "Weight at visit (kg)"] = None,
        notes: Annotated[Optional[str], "Clinical notes from the visit"] = None,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Mark an ANC visit as completed and record clinical measurements.

        Automatically re-runs HRP risk classification with the new clinical values.
        Creates or updates an HRP case if risk level changes.

        Returns updated risk level and whether an escalation is needed.
        """
        logger.info(f"[log_anc_visit] Logging visit {visit_number} for: {identifier}")

        try:
            with db() as conn:
                patient = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=? OR phone=?",
                    (identifier, identifier),
                ).fetchone()
                if not patient:
                    return {"success": False, "error": f"Patient not found: {identifier}"}

                pid = patient["id"]

                visit = conn.execute(
                    "SELECT * FROM anc_visits WHERE patient_id=? AND visit_number=?",
                    (pid, visit_number),
                ).fetchone()
                if not visit:
                    return {"success": False, "error": f"Visit {visit_number} not scheduled for this patient"}

                now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                conn.execute(
                    "UPDATE anc_visits SET completed_at=?, notes=? WHERE id=?",
                    (now, notes, visit["id"]),
                )

                # Store clinical data if provided
                if any(v is not None for v in [bp_systolic, bp_diastolic, hb, weight_kg]):
                    conn.execute(
                        """INSERT INTO clinical_data
                           (patient_id, bp_systolic, bp_diastolic, hb, weight_kg, source)
                           VALUES (?,?,?,?,?,'anc_visit')""",
                        (pid, bp_systolic, bp_diastolic, hb, weight_kg),
                    )

                # Re-classify risk with new values
                clinical = {
                    "age": patient["age"],
                    "parity": patient["parity"],
                    "bp_systolic": bp_systolic,
                    "bp_diastolic": bp_diastolic,
                    "hb": hb,
                    "weight_kg": weight_kg,
                }
                clinical_clean = {k: v for k, v in clinical.items() if v is not None}
                result = classify_from_dict(clinical_clean)

                prev_risk = patient["risk_level"]
                new_risk = result["level"]

                reasons_json = json.dumps(result["reasons"])
                conn.execute(
                    "UPDATE patients SET risk_level=?, risk_reasons=? WHERE id=?",
                    (new_risk, reasons_json, pid),
                )

                # Open a new HRP case if risk escalated
                risk_escalated = (
                    (prev_risk == "GREEN" and new_risk in ("YELLOW", "RED")) or
                    (prev_risk == "YELLOW" and new_risk == "RED")
                )
                if risk_escalated:
                    conn.execute(
                        "INSERT INTO hrp_cases (patient_id, priority, reasons) VALUES (?,?,?)",
                        (pid, new_risk, reasons_json),
                    )

            logger.info(
                f"[log_anc_visit] Visit {visit_number} logged. "
                f"Risk: {prev_risk} → {new_risk}"
            )

            return {
                "success": True,
                "data": {
                    "reg_id": patient["reg_id"],
                    "visit_number": visit_number,
                    "completed_at": now,
                    "risk_level": new_risk,
                    "risk_reasons": result["reasons"],
                    "risk_changed": prev_risk != new_risk,
                    "escalation_needed": risk_escalated,
                    "message": (
                        f"Visit {visit_number} completed. Risk: {new_risk}."
                        + (" Escalation recommended!" if risk_escalated else "")
                    ),
                },
            }

        except Exception as e:
            logger.error(f"[log_anc_visit] Failed: {e}")
            return {"success": False, "error": str(e)}
