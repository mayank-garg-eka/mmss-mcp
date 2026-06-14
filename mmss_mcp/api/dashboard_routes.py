import csv
import io
import json
import logging
import os
from datetime import datetime, date

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mmss_mcp.db.database import db
from mmss_mcp.services.notification_service import send_text

logger = logging.getLogger(__name__)


def _today() -> str:
    return date.today().isoformat()


def _pregnancy_month(lmp_date: str) -> int | str:
    try:
        lmp = date.fromisoformat(lmp_date)
        weeks = (date.today() - lmp).days // 7
        return min(9, max(1, round(weeks / 4.33)))
    except Exception:
        return 0


def _next_anc(conn, patient_id: int) -> str:
    row = conn.execute(
        "SELECT scheduled_date FROM anc_visits "
        "WHERE patient_id=? AND completed_at IS NULL ORDER BY scheduled_date LIMIT 1",
        (patient_id,),
    ).fetchone()
    return row["scheduled_date"] if row else ""


def _anc_progress(conn, patient_id: int) -> list[bool]:
    rows = conn.execute(
        "SELECT visit_number, completed_at FROM anc_visits WHERE patient_id=? ORDER BY visit_number",
        (patient_id,),
    ).fetchall()
    # Standard 5 ANC visits
    progress = [False] * 5
    for r in rows:
        idx = r["visit_number"] - 1
        if 0 <= idx < 5:
            progress[idx] = r["completed_at"] is not None
    return progress


def _open_escalation(conn, patient_id: int):
    row = conn.execute(
        "SELECT e.level FROM escalations e "
        "JOIN hrp_cases h ON e.hrp_case_id = h.id "
        "WHERE h.patient_id=? AND e.status='pending' ORDER BY e.notified_at DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    return row["level"] if row else None


def _build_patient_row(conn, p) -> dict:
    esc_level = _open_escalation(conn, p["id"])
    risk_factors = json.loads(p["risk_reasons"] or "[]")
    return {
        "reg_id": p["reg_id"],
        "name": p["name"],
        "mobile": p["phone"] or "",
        "village": p["village"],
        "block": p["block"],
        "district": p["district"],
        "lmp_date": p["lmp_date"],
        "edd": p["edd"],
        "pregnancy_month": _pregnancy_month(p["lmp_date"]),
        "risk_level": p["risk_level"] or "GREEN",
        "risk_factors": risk_factors,
        "next_anc_date": _next_anc(conn, p["id"]),
        "anc_progress": _anc_progress(conn, p["id"]),
        "has_open_escalation": esc_level is not None,
        "escalation_level": esc_level or "",
        "registered_by": p["registered_by"] or "self",
        "verified": "none",
        "asha_name": p["asha_name"] or "",
    }


def register_dashboard_routes(mcp: FastMCP) -> None:

    @mcp.custom_route("/api/stats", methods=["GET"])
    async def api_stats(request: Request) -> JSONResponse:
        try:
            with db() as conn:
                total = conn.execute("SELECT COUNT(*) AS n FROM patients WHERE status='active'").fetchone()["n"]
                red = conn.execute(
                    "SELECT COUNT(*) AS n FROM patients WHERE risk_level='RED' AND status='active'"
                ).fetchone()["n"]
                yellow = conn.execute(
                    "SELECT COUNT(*) AS n FROM patients WHERE risk_level='YELLOW' AND status='active'"
                ).fetchone()["n"]
                pending_esc = conn.execute(
                    "SELECT COUNT(*) AS n FROM escalations WHERE status='pending'"
                ).fetchone()["n"]
                anc_due = conn.execute(
                    "SELECT COUNT(DISTINCT patient_id) AS n FROM anc_visits "
                    "WHERE completed_at IS NULL AND scheduled_date <= date('now','+7 days')"
                ).fetchone()["n"]
            return JSONResponse({
                "total_patients": total,
                "red_count": red,
                "yellow_count": yellow,
                "pending_escalations": pending_esc,
                "anc_due_week": anc_due,
            })
        except Exception as e:
            logger.error("[api_stats] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients", methods=["GET"])
    async def api_patients(request: Request) -> JSONResponse:
        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT * FROM patients WHERE status='active' ORDER BY created_at DESC"
                ).fetchall()
                result = [_build_patient_row(conn, p) for p in rows]
            return JSONResponse(result)
        except Exception as e:
            logger.error("[api_patients] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients/export.csv", methods=["GET"])
    async def api_export_csv(request: Request) -> Response:
        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT p.reg_id, p.name, p.phone, p.age, p.village, p.block, p.district, "
                    "p.lmp_date, p.edd, p.risk_level, p.risk_reasons, p.registered_by, "
                    "p.asha_name, p.asha_phone, p.created_at, "
                    "h.priority AS hrp_priority, h.status AS hrp_status, h.reasons AS hrp_reasons "
                    "FROM patients p "
                    "LEFT JOIN hrp_cases h ON h.patient_id=p.id AND h.status='open' "
                    "WHERE p.status='active' ORDER BY p.created_at DESC"
                ).fetchall()

            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "Reg ID", "Name", "Phone", "Age", "Village", "Block", "District",
                "LMP Date", "EDD", "Risk Level", "Risk Reasons", "Registered By",
                "ASHA Name", "ASHA Phone", "Registered At",
                "HRP Priority", "HRP Status", "HRP Reasons",
            ])
            for r in rows:
                writer.writerow([
                    r["reg_id"], r["name"], r["phone"], r["age"],
                    r["village"], r["block"], r["district"],
                    r["lmp_date"], r["edd"], r["risk_level"], r["risk_reasons"],
                    r["registered_by"], r["asha_name"], r["asha_phone"], r["created_at"],
                    r["hrp_priority"] or "", r["hrp_status"] or "", r["hrp_reasons"] or "",
                ])

            fname = f"maatri_patients_{_today()}.csv"
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{fname}"'},
            )
        except Exception as e:
            logger.error("[api_export_csv] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients/{reg_id}", methods=["GET"])
    async def api_patient_detail(request: Request) -> JSONResponse:
        reg_id = request.path_params.get("reg_id", "")
        try:
            with db() as conn:
                p = conn.execute(
                    "SELECT * FROM patients WHERE reg_id=?", (reg_id,)
                ).fetchone()
                if not p:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                result = _build_patient_row(conn, p)

                # Clinical data
                clinical = conn.execute(
                    "SELECT * FROM clinical_data WHERE patient_id=? ORDER BY recorded_at DESC LIMIT 1",
                    (p["id"],),
                ).fetchone()
                result["clinical"] = dict(clinical) if clinical else {}

                # Open HRP case
                hrp = conn.execute(
                    "SELECT * FROM hrp_cases WHERE patient_id=? AND status='open' ORDER BY created_at DESC LIMIT 1",
                    (p["id"],),
                ).fetchone()
                result["hrp_case"] = dict(hrp) if hrp else None
                if result["hrp_case"]:
                    result["hrp_case"]["case_id"] = hrp["id"]

            return JSONResponse(result)
        except Exception as e:
            logger.error("[api_patient_detail] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients/{reg_id}/risk", methods=["POST"])
    async def api_risk_override(request: Request) -> JSONResponse:
        reg_id = request.path_params.get("reg_id", "")
        try:
            body = await request.json()
            level = body.get("level", "").upper()
            reason = body.get("reason", "")
            if level not in ("RED", "YELLOW", "GREEN"):
                return JSONResponse({"error": "level must be RED, YELLOW, or GREEN"}, status_code=400)
            with db() as conn:
                p = conn.execute("SELECT id FROM patients WHERE reg_id=?", (reg_id,)).fetchone()
                if not p:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                override_reason = reason or "asha_override"
                conn.execute(
                    "UPDATE patients SET risk_level=? WHERE reg_id=?", (level, reg_id)
                )
                if level in ("RED", "YELLOW"):
                    existing = conn.execute(
                        "SELECT id FROM hrp_cases WHERE patient_id=? AND status='open'", (p["id"],)
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO hrp_cases (patient_id, priority, reasons) VALUES (?,?,?)",
                            (p["id"], level, json.dumps([override_reason])),
                        )
            return JSONResponse({"success": True, "reg_id": reg_id, "risk_level": level})
        except Exception as e:
            logger.error("[api_risk_override] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients/{reg_id}/verify", methods=["POST"])
    async def api_verify_patient(request: Request) -> JSONResponse:
        reg_id = request.path_params.get("reg_id", "")
        try:
            body = await request.json()
            verify_type = body.get("type", "")
            value = body.get("value", "")
            # In production this would call ABHA API; here we just record it
            with db() as conn:
                p = conn.execute("SELECT id FROM patients WHERE reg_id=?", (reg_id,)).fetchone()
                if not p:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                # Mock: mark as verified — real impl would call NHA ABHA API
                logger.info("[api_verify_patient] %s type=%s value=%s (mock)", reg_id, verify_type, value[:4] + "***")
            abha_address = f"{reg_id.lower().replace('-', '')}@abdm" if verify_type != "abha_address" else value
            return JSONResponse({"success": True, "abha_address": abha_address})
        except Exception as e:
            logger.error("[api_verify_patient] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients/{reg_id}/risk-factors", methods=["POST"])
    async def api_add_risk_factor(request: Request) -> JSONResponse:
        reg_id = request.path_params.get("reg_id", "")
        try:
            body = await request.json()
            code = body.get("add", "").strip()
            if not code:
                return JSONResponse({"error": "add field required"}, status_code=400)
            with db() as conn:
                p = conn.execute("SELECT id, risk_reasons, risk_level FROM patients WHERE reg_id=?", (reg_id,)).fetchone()
                if not p:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                factors = json.loads(p["risk_reasons"] or "[]")
                # Strip override sentinel so we re-derive cleanly
                factors = [f for f in factors if f != "__asha_override__"]
                if code not in factors:
                    factors.append(code)
                conn.execute("UPDATE patients SET risk_reasons=? WHERE reg_id=?", (json.dumps(factors), reg_id))
            return JSONResponse({"success": True, "reg_id": reg_id, "risk_factors": factors})
        except Exception as e:
            logger.error("[api_add_risk_factor] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/patients/{reg_id}/risk-factors/{code}", methods=["DELETE"])
    async def api_remove_risk_factor(request: Request) -> JSONResponse:
        reg_id = request.path_params.get("reg_id", "")
        code = request.path_params.get("code", "")
        try:
            with db() as conn:
                p = conn.execute("SELECT id, risk_reasons FROM patients WHERE reg_id=?", (reg_id,)).fetchone()
                if not p:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                factors = json.loads(p["risk_reasons"] or "[]")
                factors = [f for f in factors if f not in (code, "__asha_override__")]
                conn.execute("UPDATE patients SET risk_reasons=? WHERE reg_id=?", (json.dumps(factors), reg_id))
            return JSONResponse({"success": True, "reg_id": reg_id, "risk_factors": factors})
        except Exception as e:
            logger.error("[api_remove_risk_factor] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/escalations", methods=["GET"])
    async def api_escalations(request: Request) -> JSONResponse:
        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT e.id, e.level, e.notified_at, e.status, "
                    "h.priority, h.id AS case_id, "
                    "p.reg_id, p.name, p.village, p.block, p.district "
                    "FROM escalations e "
                    "JOIN hrp_cases h ON e.hrp_case_id = h.id "
                    "JOIN patients p ON h.patient_id = p.id "
                    "WHERE e.status='pending' ORDER BY e.notified_at DESC"
                ).fetchall()
            return JSONResponse([dict(r) for r in rows])
        except Exception as e:
            logger.error("[api_escalations] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/escalations/{esc_id}/ack", methods=["POST"])
    async def api_ack_escalation(request: Request) -> JSONResponse:
        esc_id = request.path_params.get("esc_id", "")
        try:
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            with db() as conn:
                esc = conn.execute(
                    "SELECT id FROM escalations WHERE id=? AND status='pending'", (esc_id,)
                ).fetchone()
                if not esc:
                    return JSONResponse({"error": "Escalation not found or already acknowledged"}, status_code=404)
                conn.execute(
                    "UPDATE escalations SET status='acknowledged', acknowledged_at=? WHERE id=?",
                    (now, esc_id),
                )
            return JSONResponse({"success": True, "esc_id": esc_id, "acknowledged_at": now})
        except Exception as e:
            logger.error("[api_ack_escalation] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/visit-requests", methods=["GET"])
    async def api_visit_requests(request: Request) -> JSONResponse:
        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT v.id, v.reason, v.requested_at, v.status, v.asha_notified, "
                    "p.reg_id, p.name, p.village, p.block, p.district, p.phone "
                    "FROM visit_requests v JOIN patients p ON v.patient_id = p.id "
                    "WHERE v.status='pending' ORDER BY v.requested_at DESC"
                ).fetchall()
            return JSONResponse([dict(r) for r in rows])
        except Exception as e:
            logger.error("[api_visit_requests] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/reminders", methods=["POST"])
    async def api_reminders(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            text = body.get("text", "").strip()
            recipients = body.get("recipients", [])
            if not text or not recipients:
                return JSONResponse({"error": "text and recipients required"}, status_code=400)

            sent = 0
            for r in recipients:
                phone = r.get("mobile", "")
                name = r.get("name", "माता")
                if not phone:
                    continue
                try:
                    await send_text(to_phone=phone, message=text)
                    sent += 1
                except Exception as sms_err:
                    logger.warning("[api_reminders] failed for %s: %s", phone, sms_err)

            return JSONResponse({"success": True, "sent": sent, "total": len(recipients)})
        except Exception as e:
            logger.error("[api_reminders] %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)
