"""
Send WhatsApp alerts via Interakt template API.
NOTIFY_MODE=whatsapp → real Interakt send (requires approved templates)
NOTIFY_MODE=log      → log only, no actual send (default for demo without templates)
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

NOTIFY_MODE = os.getenv("NOTIFY_MODE", "log")
INTERAKT_API_KEY = os.getenv("INTERAKT_API_KEY", "")
INTERAKT_API_URL = os.getenv("INTERAKT_API_URL", "https://api.interakt.ai/v1/public/message/")


async def send_hrp_alert(
    to_phone: str,
    recipient_name: str,
    patient_name: str,
    village: str,
    risk_level: str,
) -> bool:
    """
    Alert ANM/ASHA/MO about a high-risk patient.
    Template: hrp_alert
    Variables: [recipient_name, patient_name, village, risk_level]
    """
    msg = (
        f"[HRP ALERT] To: {to_phone} | "
        f"Namaste {recipient_name}, aapki patient {patient_name} "
        f"(village: {village}) ko {risk_level} risk flag mila hai. "
        f"Kripya turant contact karein. MMSS Yojna."
    )

    if NOTIFY_MODE == "whatsapp":
        return await _send_template(
            to_phone=to_phone,
            template_name="hrp_alert",
            body_values=[recipient_name, patient_name, village, risk_level],
        )
    else:
        logger.info(f"[notification_service] {msg}")
        return True


async def send_anc_reminder(
    to_phone: str,
    patient_name: str,
    visit_number: int,
    scheduled_date: str,
) -> bool:
    """
    Remind patient about upcoming ANC visit.
    Template: anc_reminder
    Variables: [patient_name, visit_number, scheduled_date]
    """
    msg = (
        f"[ANC REMINDER] To: {to_phone} | "
        f"Namaste {patient_name}, aapka ANC visit number {visit_number}, "
        f"{scheduled_date} ko scheduled hai. Kripya samay par PHC aaein. MMSS Yojna."
    )

    if NOTIFY_MODE == "whatsapp":
        return await _send_template(
            to_phone=to_phone,
            template_name="anc_reminder",
            body_values=[patient_name, str(visit_number), scheduled_date],
        )
    else:
        logger.info(f"[notification_service] {msg}")
        return True


async def send_visit_request(
    to_phone: str,
    asha_name: str,
    patient_name: str,
    village: str,
    reason: str = "",
) -> bool:
    """
    Notify ASHA about a routine visit request.
    Reuses hrp_alert template with 'routine visit' as the 4th param.
    """
    label = f"routine visit ({reason})" if reason else "routine visit"
    msg = (
        f"[VISIT REQUEST] To: {to_phone} | "
        f"Namaste {asha_name}, aapki patient {patient_name} "
        f"(village: {village}) ne {label} request kiya hai. "
        f"Kripya jald se milen. MMSS Yojna."
    )
    if NOTIFY_MODE == "whatsapp":
        return await _send_template(
            to_phone=to_phone,
            template_name="hrp_alert",
            body_values=[asha_name, patient_name, village, label],
        )
    else:
        logger.info(f"[notification_service] {msg}")
        return True


async def _send_template(to_phone: str, template_name: str, body_values: list[str]) -> bool:
    """Send an Interakt template message."""
    if not INTERAKT_API_KEY:
        logger.warning("[notification_service] INTERAKT_API_KEY not set, skipping send")
        return False

    # Strip leading + for Interakt (uses countryCode + phoneNumber format)
    phone = to_phone.lstrip("+")
    country_code = phone[:2]   # e.g. "91"
    number = phone[2:]          # rest

    payload = {
        "countryCode": f"+{country_code}",
        "phoneNumber": number,
        "callbackData": "mmss_alert",
        "type": "Template",
        "template": {
            "name": template_name,
            "languageCode": "hi",
            "bodyValues": body_values,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                INTERAKT_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Basic {INTERAKT_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                logger.info(f"[notification_service] Sent {template_name} to {to_phone}")
                return True
            else:
                logger.error(f"[notification_service] Interakt error {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"[notification_service] Failed to send {template_name}: {e}")
        return False
