import os
import logging
from typing import Annotated
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.dependencies import CurrentContext

from mmss_mcp.db.database import db
from mmss_mcp.services.notification_service import send_hrp_alert, send_visit_request
from mmss_mcp.services.escalation_engine import schedule_next_escalation

logger = logging.getLogger(__name__)


def register_escalation_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def request_asha_visit(
        identifier: Annotated[str, "Registration ID or phone number of the patient"],
        reason: Annotated[str, "Reason for the visit request (optional, e.g. 'general checkup', 'swelling')"] = "",
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Request a routine (non-urgent) ASHA home visit for a registered patient.

        Notifies the assigned ASHA via WhatsApp and logs the request.
        Use this for non-emergency situations where the mother asks for a home visit.
        For emergencies or RED/YELLOW risk, use trigger_escalation instead.
        """
        logger.info(f"[request_asha_visit] Visit requested for: {identifier}")

        try:
            with db() as conn:
                patient = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=? OR phone=?",
                    (identifier, identifier),
                ).fetchone()
                if not patient:
                    return {"success": False, "error": f"Patient not found: {identifier}"}

                asha_phone = patient["asha_phone"] or os.getenv("CALL_CENTRE_PHONE", "")
                conn.execute(
                    "INSERT INTO visit_requests (patient_id, reason, asha_notified) VALUES (?,?,?)",
                    (patient["id"], reason or None, 1 if asha_phone else 0),
                )

            if asha_phone:
                await send_visit_request(
                    to_phone=asha_phone,
                    asha_name=patient["asha_name"] or "ASHA",
                    patient_name=patient["name"],
                    village=patient["village"],
                    reason=reason,
                )

            logger.info(f"[request_asha_visit] Logged for {patient['reg_id']}, ASHA={patient['asha_name']}")
            return {
                "success": True,
                "data": {
                    "reg_id": patient["reg_id"],
                    "patient_name": patient["name"],
                    "asha_name": patient["asha_name"] or "Not assigned",
                    "asha_phone": asha_phone or "Not set",
                    "notified": bool(asha_phone),
                    "message": (
                        f"Visit request logged for {patient['name']}. "
                        + (f"ASHA {patient['asha_name']} को सूचित कर दिया गया है।" if asha_phone else
                           "ASHA phone not on record — request logged for manual follow-up.")
                    ),
                },
            }

        except Exception as e:
            logger.error(f"[request_asha_visit] Failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def verify_asha(
        phone_number: Annotated[str, "WhatsApp phone number of the person claiming to be ASHA/ANM (e.g. +918218467229)"],
    ) -> dict:
        """
        Verify whether a phone number belongs to a registered ASHA or ANM worker.

        Use this at the start of registration when the caller says they are an ASHA
        or ANM registering on behalf of a patient. If not verified, inform the caller
        they can still register via the family member flow.
        """
        logger.info(f"[verify_asha] Checking phone: {phone_number}")

        # Normalise: strip spaces, ensure digits only for comparison
        digits = "".join(c for c in phone_number if c.isdigit())
        # Accept last 10 digits match (handles +91 prefix variations)
        last10 = digits[-10:] if len(digits) >= 10 else digits

        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT asha_name, village, block, district FROM asha_assignments WHERE asha_phone LIKE ?",
                    (f"%{last10}",),
                ).fetchall()
        except Exception as e:
            logger.error(f"[verify_asha] DB error: {e}")
            return {"success": False, "error": str(e)}

        if rows:
            row = rows[0]
            logger.info(f"[verify_asha] Verified: {row['asha_name']}")
            return {
                "success": True,
                "verified": True,
                "data": {
                    "asha_name": row["asha_name"],
                    "area": f"{row['village']}, {row['block']}, {row['district']}",
                    "message": (
                        f"{row['asha_name']} को {row['village']}, {row['block']}, {row['district']} "
                        "के लिए verified ASHA के रूप में पहचाना गया।"
                    ),
                },
            }
        else:
            logger.info(f"[verify_asha] Not found: {last10}")
            return {
                "success": True,
                "verified": False,
                "data": {
                    "message": (
                        "यह नंबर हमारे ASHA/ANM रजिस्टर में नहीं मिला। "
                        "आप 'किसी और के लिए पंजीकरण' वाले flow से परिवार के सदस्य के रूप में register कर सकती हैं।"
                    ),
                },
            }

    @mcp.tool()
    async def trigger_escalation(
        identifier: Annotated[str, "Registration ID or phone number of the patient"],
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Trigger the MMSS SOP escalation chain for a patient's open HRP case.

        Immediately notifies the ASHA worker assigned to this patient, then
        schedules automatic escalation to the next level (Call Centre / PHC MO / CMO)
        if no acknowledgement is received within the SOP timeframe.

        Escalation delays per MMSS SOP (DEMO_MODE compresses to 2 min each):
          RED:    ASHA → 24hr → Call Centre → 24hr → PHC MO → 72hr → CMO → 72hr → NHM+STC
          YELLOW: ASHA → 48hr → PHC MO
          GREEN:  ASHA → 7 days → PHC MO

        Safe to call multiple times — does not create duplicate escalations for the same case.
        """
        logger.info(f"[trigger_escalation] Triggering for: {identifier}")

        try:
            with db() as conn:
                patient = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=? OR phone=?",
                    (identifier, identifier),
                ).fetchone()
                if not patient:
                    return {"success": False, "error": f"Patient not found: {identifier}"}

                pid = patient["id"]

                # Get the most recent open HRP case
                case = conn.execute(
                    "SELECT * FROM hrp_cases WHERE patient_id=? AND status='open' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (pid,),
                ).fetchone()
                if not case:
                    return {
                        "success": False,
                        "error": "No open HRP case found. Run classify_hrp_risk first.",
                    }

                # Check if ASHA escalation already fired for this case
                existing = conn.execute(
                    "SELECT id FROM escalations WHERE hrp_case_id=? AND level='ASHA'",
                    (case["id"],),
                ).fetchone()
                if existing:
                    return {
                        "success": False,
                        "error": "Escalation already triggered for this case.",
                    }

                asha_phone = patient["asha_phone"] or os.getenv("CALL_CENTRE_PHONE", "")

                # Record ASHA-level escalation
                conn.execute(
                    """INSERT INTO escalations
                       (hrp_case_id, level, notified_phone, status)
                       VALUES (?,?,?,'pending')""",
                    (case["id"], "ASHA", asha_phone or "UNSET"),
                )

            # Notify ASHA immediately
            if asha_phone:
                await send_hrp_alert(
                    to_phone=asha_phone,
                    recipient_name=patient["asha_name"] or "ASHA",
                    patient_name=patient["name"],
                    village=patient["village"],
                    risk_level=case["priority"],
                )

            # Schedule next escalation step
            schedule_next_escalation(
                hrp_case_id=case["id"],
                priority=case["priority"],
                next_level_index=0,
            )

            import os as _os
            demo = _os.getenv("DEMO_MODE", "true").lower() == "true"
            delay_note = "2 minutes (DEMO MODE)" if demo else "per MMSS SOP"

            logger.info(
                f"[trigger_escalation] ASHA notified. "
                f"Next escalation in {delay_note}. Case ID: {case['id']}"
            )

            return {
                "success": True,
                "data": {
                    "reg_id": patient["reg_id"],
                    "case_id": case["id"],
                    "priority": case["priority"],
                    "asha_name": patient["asha_name"] or "Unknown",
                    "asha_phone": asha_phone or "Not set",
                    "escalation_started": True,
                    "next_escalation_in": delay_note,
                    "message": (
                        f"Escalation started for {patient['name']} ({case['priority']} risk). "
                        f"ASHA {patient['asha_name']} notified. "
                        f"Auto-escalation scheduled in {delay_note}."
                    ),
                },
            }

        except Exception as e:
            logger.error(f"[trigger_escalation] Failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def acknowledge_escalation(
        case_id: Annotated[int, "HRP case ID (from trigger_escalation response)"],
        level: Annotated[str, "Escalation level being acknowledged: ASHA, CALL_CENTRE, PHC_MO, CMO, NHM_STC"],
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Acknowledge an escalation — confirms the notified person has received and acted on the alert.

        Also optionally mark the HRP case as resolved if the situation is under control.
        Once resolved, no further escalations are sent.

        Call this when an ASHA, ANM, or MO confirms they have visited/contacted the patient.
        """
        logger.info(f"[acknowledge_escalation] Case {case_id} — {level} acknowledging")

        try:
            from datetime import datetime
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            with db() as conn:
                # Find the pending escalation
                esc = conn.execute(
                    "SELECT * FROM escalations "
                    "WHERE hrp_case_id=? AND level=? AND status='pending' "
                    "ORDER BY notified_at DESC LIMIT 1",
                    (case_id, level),
                ).fetchone()
                if not esc:
                    return {
                        "success": False,
                        "error": f"No pending {level} escalation found for case {case_id}.",
                    }

                conn.execute(
                    "UPDATE escalations SET status='acknowledged', acknowledged_at=? WHERE id=?",
                    (now, esc["id"]),
                )

                # Get case details for response
                case = conn.execute(
                    "SELECT h.priority, p.name, p.reg_id FROM hrp_cases h "
                    "JOIN patients p ON h.patient_id = p.id WHERE h.id=?",
                    (case_id,),
                ).fetchone()

            logger.info(f"[acknowledge_escalation] {level} acknowledged case {case_id}")

            return {
                "success": True,
                "data": {
                    "case_id": case_id,
                    "level": level,
                    "acknowledged_at": now,
                    "patient_name": case["name"] if case else "Unknown",
                    "reg_id": case["reg_id"] if case else "Unknown",
                    "message": (
                        f"{level} acknowledged for case {case_id} ({case['name'] if case else ''})."
                        " Thank you for the confirmation."
                    ),
                },
            }

        except Exception as e:
            logger.error(f"[acknowledge_escalation] Failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def resolve_hrp_case(
        case_id: Annotated[int, "HRP case ID to mark as resolved"],
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Mark an HRP case as resolved — stops all further escalations for this case.

        Call this when the patient's condition has been addressed (admitted to hospital,
        risk factor resolved, etc.).
        """
        logger.info(f"[resolve_hrp_case] Resolving case {case_id}")

        try:
            from datetime import datetime
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            with db() as conn:
                case = conn.execute(
                    "SELECT h.id, h.status, h.priority, p.name, p.reg_id "
                    "FROM hrp_cases h JOIN patients p ON h.patient_id = p.id "
                    "WHERE h.id=?",
                    (case_id,),
                ).fetchone()
                if not case:
                    return {"success": False, "error": f"Case {case_id} not found"}
                if case["status"] == "resolved":
                    return {"success": False, "error": f"Case {case_id} is already resolved"}

                conn.execute(
                    "UPDATE hrp_cases SET status='resolved', resolved_at=? WHERE id=?",
                    (now, case_id),
                )

            logger.info(f"[resolve_hrp_case] Case {case_id} resolved")
            return {
                "success": True,
                "data": {
                    "case_id": case_id,
                    "resolved_at": now,
                    "patient_name": case["name"],
                    "reg_id": case["reg_id"],
                    "message": f"HRP case {case_id} for {case['name']} marked as resolved. No further escalations.",
                },
            }

        except Exception as e:
            logger.error(f"[resolve_hrp_case] Failed: {e}")
            return {"success": False, "error": str(e)}
