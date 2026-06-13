"""
APScheduler-based escalation engine.
Fires escalation chain per MMSS SOP.

DEMO_MODE=true compresses all delays to 2 minutes.
Production delays:
  RED:    ASHA→24hr→CALL_CENTRE→24hr→PHC_MO→72hr→CMO→72hr→NHM_STC
  YELLOW: ASHA→48hr→PHC_MO
  GREEN:  ASHA→7d→PHC_MO
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
DEMO_DELAY_SECONDS = 120  # 2 minutes per step in demo

# Production delays in hours
PROD_DELAYS: dict[str, list[tuple[str, int]]] = {
    "RED": [
        ("CALL_CENTRE", 24),
        ("PHC_MO", 24),
        ("CMO", 72),
        ("NHM_STC", 72),
    ],
    "YELLOW": [
        ("PHC_MO", 48),
    ],
    "GREEN": [
        ("PHC_MO", 168),  # 7 days
    ],
}

# Level → phone env var
LEVEL_PHONE_ENV: dict[str, str] = {
    "CALL_CENTRE": "CALL_CENTRE_PHONE",
    "PHC_MO": "PHC_MO_PHONE",
    "CMO": "CMO_PHONE",
    "NHM_STC": "NHM_PHONE",
}

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
        _scheduler.start()
        logger.info("[escalation_engine] APScheduler started")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _delay_seconds(hours: int) -> int:
    if DEMO_MODE:
        return DEMO_DELAY_SECONDS
    return hours * 3600


def schedule_next_escalation(hrp_case_id: int, priority: str, next_level_index: int):
    """
    Schedule the next escalation step for a case.
    next_level_index: index into PROD_DELAYS[priority] list.
    """
    chain = PROD_DELAYS.get(priority, [])
    if next_level_index >= len(chain):
        logger.info(f"[escalation_engine] Case {hrp_case_id}: escalation chain exhausted")
        return

    next_level, delay_hours = chain[next_level_index]
    delay_secs = _delay_seconds(delay_hours)
    fire_at = datetime.now() + timedelta(seconds=delay_secs)

    job_id = f"escalate_{hrp_case_id}_{next_level}"

    scheduler = get_scheduler()
    scheduler.add_job(
        func=_fire_escalation,
        trigger=DateTrigger(run_date=fire_at),
        id=job_id,
        args=[hrp_case_id, priority, next_level, next_level_index],
        replace_existing=True,
        misfire_grace_time=300,
    )

    mode = "DEMO" if DEMO_MODE else "PROD"
    logger.info(
        f"[escalation_engine] [{mode}] Case {hrp_case_id}: "
        f"next escalation → {next_level} in {delay_secs}s (at {fire_at.strftime('%H:%M:%S')})"
    )


def _fire_escalation(hrp_case_id: int, priority: str, level: str, current_index: int):
    """
    Called by APScheduler (sync context). Sends alert and records escalation in DB.
    """
    logger.info(f"[escalation_engine] Firing escalation: case={hrp_case_id} level={level}")

    try:
        # Import here to avoid circular imports at module load
        from mmss_mcp.db.database import db
        from mmss_mcp.services.notification_service import send_hrp_alert

        with db() as conn:
            # Check if case is still open
            case = conn.execute(
                "SELECT h.id, h.status, h.priority, p.name, p.village, p.asha_name "
                "FROM hrp_cases h JOIN patients p ON h.patient_id = p.id "
                "WHERE h.id=?",
                (hrp_case_id,),
            ).fetchone()

            if not case or case["status"] != "open":
                logger.info(f"[escalation_engine] Case {hrp_case_id} already resolved, skipping {level}")
                return

            phone_env = LEVEL_PHONE_ENV.get(level, "")
            to_phone = os.getenv(phone_env, "")

            if not to_phone:
                logger.warning(f"[escalation_engine] No phone configured for {level} ({phone_env} not set)")

            # Record escalation in DB
            conn.execute(
                """INSERT INTO escalations
                   (hrp_case_id, level, notified_phone, status)
                   VALUES (?,?,?,'pending')""",
                (hrp_case_id, level, to_phone or "UNSET"),
            )

        # Send notification (fire-and-forget in sync context)
        if to_phone:
            asyncio.run(
                send_hrp_alert(
                    to_phone=to_phone,
                    recipient_name=level.replace("_", " ").title(),
                    patient_name=case["name"],
                    village=case["village"],
                    risk_level=priority,
                )
            )

        # Schedule next step
        schedule_next_escalation(hrp_case_id, priority, current_index + 1)

    except Exception as e:
        logger.error(f"[escalation_engine] Error firing escalation for case {hrp_case_id}: {e}")
