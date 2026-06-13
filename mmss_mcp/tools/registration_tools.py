import json
import uuid
import logging
from datetime import datetime, timedelta
from typing import Annotated, Optional
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.dependencies import CurrentContext

from mmss_mcp.db.database import db
from mmss_mcp.services.hrp_classifier import classify_from_dict

logger = logging.getLogger(__name__)


def _compute_edd(lmp_date: str) -> str:
    """Naegele's rule: EDD = LMP + 280 days."""
    lmp = datetime.strptime(lmp_date, "%Y-%m-%d")
    edd = lmp + timedelta(days=280)
    return edd.strftime("%Y-%m-%d")


def _lookup_asha(village: str, block: str, district: str, conn) -> tuple[str, str]:
    """Return (asha_name, asha_phone) for the village, or ('', '') if not found."""
    row = conn.execute(
        "SELECT asha_name, asha_phone FROM asha_assignments "
        "WHERE lower(village)=lower(?) AND lower(block)=lower(?) AND lower(district)=lower(?)",
        (village.strip(), block.strip(), district.strip()),
    ).fetchone()
    if row:
        return row["asha_name"], row["asha_phone"]
    row = conn.execute(
        "SELECT asha_name, asha_phone FROM asha_assignments WHERE lower(district)=lower(?) LIMIT 1",
        (district.strip(),),
    ).fetchone()
    if row:
        return row["asha_name"], row["asha_phone"]
    return "", ""


def register_registration_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def register_pregnancy(
        name: Annotated[str, "Full name of the pregnant woman"],
        age: Annotated[int, "Age in years"],
        lmp_date: Annotated[str, "Last menstrual period date in YYYY-MM-DD format"],
        village: Annotated[str, "Village name"],
        block: Annotated[str, "Block / tehsil name"],
        district: Annotated[str, "District name"],
        phone: Annotated[Optional[str], "Woman's WhatsApp phone number (with country code)"] = None,
        parity: Annotated[int, "Number of previous deliveries (0 for first pregnancy)"] = 0,
        registered_by: Annotated[str, "Who is registering: 'self' or 'asha'"] = "self",
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Register a new pregnancy in the MMSS system.

        Automatically:
        - Generates a unique registration ID (format: MMSS-DIST-XXXX)
        - Computes Expected Delivery Date (EDD) from LMP using Naegele's rule
        - Looks up the assigned ASHA worker for the woman's village

        Returns registration ID, EDD, and ASHA details.
        """
        logger.info(f"[register_pregnancy] Registering: {name}, village={village}, district={district}")

        try:
            edd = _compute_edd(lmp_date)
            reg_id = f"MMSS-{district[:3].upper()}-{uuid.uuid4().hex[:6].upper()}"

            with db() as conn:
                asha_name, asha_phone = _lookup_asha(village, block, district, conn)

                conn.execute(
                    """INSERT INTO patients
                       (reg_id, name, age, phone, lmp_date, edd, village, block, district,
                        asha_name, asha_phone, parity, risk_level, risk_reasons, registered_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'GREEN','[]',?)""",
                    (reg_id, name, age, phone, lmp_date, edd,
                     village, block, district, asha_name, asha_phone, parity, registered_by),
                )

            logger.info(f"[register_pregnancy] Registered {reg_id}, EDD={edd}, ASHA={asha_name}")
            return {
                "success": True,
                "data": {
                    "reg_id": reg_id,
                    "name": name,
                    "edd": edd,
                    "asha_name": asha_name or "Not assigned",
                    "asha_phone": asha_phone or "",
                    "message": f"Registration successful. ID: {reg_id}. EDD: {edd}.",
                },
            }
        except Exception as e:
            logger.error(f"[register_pregnancy] Failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def get_patient_status(
        identifier: Annotated[str, "Registration ID (MMSS-XXX-XXXXXX) or phone number"],
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Get the full current status of a registered patient.

        Returns registration details, risk level, ANC visit schedule,
        open HRP cases, and any active escalations.
        """
        logger.info(f"[get_patient_status] Looking up: {identifier}")

        try:
            with db() as conn:
                patient = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=? OR phone=?",
                    (identifier, identifier),
                ).fetchone()

                if not patient:
                    return {"success": False, "error": f"Patient not found: {identifier}"}

                pid = patient["id"]

                anc_visits = conn.execute(
                    "SELECT visit_number, scheduled_date, completed_at "
                    "FROM anc_visits WHERE patient_id=? ORDER BY visit_number",
                    (pid,),
                ).fetchall()

                open_cases = conn.execute(
                    "SELECT id, priority, reasons, created_at FROM hrp_cases "
                    "WHERE patient_id=? AND status='open'",
                    (pid,),
                ).fetchall()

                active_escalations = conn.execute(
                    """SELECT e.level, e.notified_at, e.status
                       FROM escalations e
                       JOIN hrp_cases h ON e.hrp_case_id = h.id
                       WHERE h.patient_id=? AND e.status='pending'
                       ORDER BY e.notified_at DESC""",
                    (pid,),
                ).fetchall()

            data = {
                "reg_id": patient["reg_id"],
                "name": patient["name"],
                "age": patient["age"],
                "edd": patient["edd"],
                "village": patient["village"],
                "district": patient["district"],
                "asha_name": patient["asha_name"],
                "risk_level": patient["risk_level"],
                "risk_reasons": json.loads(patient["risk_reasons"] or "[]"),
                "anc_visits": [
                    {
                        "visit_number": v["visit_number"],
                        "scheduled_date": v["scheduled_date"],
                        "completed": bool(v["completed_at"]),
                    }
                    for v in anc_visits
                ],
                "open_hrp_cases": [
                    {
                        "case_id": c["id"],
                        "priority": c["priority"],
                        "reasons": json.loads(c["reasons"]),
                        "since": c["created_at"],
                    }
                    for c in open_cases
                ],
                "active_escalations": [
                    {"level": e["level"], "notified_at": e["notified_at"]}
                    for e in active_escalations
                ],
            }
            logger.info(f"[get_patient_status] Found {patient['reg_id']}, risk={patient['risk_level']}")
            return {"success": True, "data": data}

        except Exception as e:
            logger.error(f"[get_patient_status] Failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def classify_hrp_risk(
        identifier: Annotated[str, "Registration ID or phone number of the patient"],
        bp_systolic: Annotated[Optional[int], "Systolic blood pressure (mmHg)"] = None,
        bp_diastolic: Annotated[Optional[int], "Diastolic blood pressure (mmHg)"] = None,
        hb: Annotated[Optional[float], "Haemoglobin level (g/dL)"] = None,
        weight_kg: Annotated[Optional[float], "Weight in kg"] = None,
        height_cm: Annotated[Optional[float], "Height in cm"] = None,
        gdm: Annotated[Optional[bool], "Gestational diabetes present"] = None,
        hiv: Annotated[Optional[bool], "HIV positive"] = None,
        tb_active: Annotated[Optional[bool], "Active TB"] = None,
        hypothyroid: Annotated[Optional[bool], "Hypothyroidism"] = None,
        heart_disease: Annotated[Optional[bool], "Heart disease"] = None,
        epilepsy: Annotated[Optional[bool], "Epilepsy"] = None,
        bad_obs_history: Annotated[Optional[bool], "Bad obstetric history"] = None,
        prev_lscs: Annotated[Optional[bool], "Previous LSCS or assisted delivery"] = None,
        multiple_pregnancy: Annotated[Optional[bool], "Multiple pregnancy (twins etc)"] = None,
        parity: Annotated[Optional[int], "Number of previous deliveries"] = None,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Classify HRP risk for a registered patient using MMSS Yojna criteria.

        Updates the patient's risk_level in DB and creates an HRP case if RED or YELLOW.
        Returns level (RED/YELLOW/GREEN) and specific reasons.
        """
        logger.info(f"[classify_hrp_risk] Classifying risk for: {identifier}")

        try:
            with db() as conn:
                patient = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=? OR phone=?",
                    (identifier, identifier),
                ).fetchone()
                if not patient:
                    return {"success": False, "error": f"Patient not found: {identifier}"}

                pid = patient["id"]
                effective_parity = parity if parity is not None else patient["parity"]

                clinical = {
                    "age": patient["age"],
                    "parity": effective_parity,
                    "bp_systolic": bp_systolic,
                    "bp_diastolic": bp_diastolic,
                    "hb": hb,
                    "weight_kg": weight_kg,
                    "height_cm": height_cm,
                    "gdm": gdm,
                    "hiv": hiv,
                    "tb_active": tb_active,
                    "hypothyroid": hypothyroid,
                    "heart_disease": heart_disease,
                    "epilepsy": epilepsy,
                    "bad_obs_history": bad_obs_history,
                    "prev_lscs": prev_lscs,
                    "multiple_pregnancy": multiple_pregnancy,
                }
                result = classify_from_dict({k: v for k, v in clinical.items() if v is not None})

                conn.execute(
                    """INSERT INTO clinical_data
                       (patient_id, bp_systolic, bp_diastolic, hb, weight_kg, height_cm,
                        gdm, hiv, tb_active, hypothyroid, heart_disease, epilepsy,
                        bad_obs_history, prev_lscs, source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'conversation')""",
                    (pid, bp_systolic, bp_diastolic, hb, weight_kg, height_cm,
                     1 if gdm else None, 1 if hiv else None, 1 if tb_active else None,
                     1 if hypothyroid else None, 1 if heart_disease else None,
                     1 if epilepsy else None, 1 if bad_obs_history else None,
                     1 if prev_lscs else None),
                )

                reasons_json = json.dumps(result["reasons"])
                conn.execute(
                    "UPDATE patients SET risk_level=?, risk_reasons=? WHERE id=?",
                    (result["level"], reasons_json, pid),
                )

                if result["level"] in ("RED", "YELLOW"):
                    conn.execute(
                        "INSERT INTO hrp_cases (patient_id, priority, reasons) VALUES (?,?,?)",
                        (pid, result["level"], reasons_json),
                    )

            logger.info(f"[classify_hrp_risk] Result: {result['level']} — {result['reasons']}")
            return {"success": True, "data": result}

        except Exception as e:
            logger.error(f"[classify_hrp_risk] Failed: {e}")
            return {"success": False, "error": str(e)}
