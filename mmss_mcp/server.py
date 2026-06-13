import os
import logging
import pathlib
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse, FileResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from mmss_mcp.db.database import init_db
from mmss_mcp.tools.registration_tools import register_registration_tools
from mmss_mcp.tools.hrp_tools import register_hrp_photo_tools
from mmss_mcp.tools.anc_tools import register_anc_tools
from mmss_mcp.tools.escalation_tools import register_escalation_tools
from mmss_mcp.tools.location_tools import register_location_tools
from mmss_mcp.services.escalation_engine import get_scheduler, stop_scheduler
from mmss_mcp.api.dashboard_routes import register_dashboard_routes

STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


def create_server() -> FastMCP:
    init_db()

    mcp = FastMCP(
        name="MMSS Maternal Care MCP Server",
        stateless_http=True,
        instructions="""
            MMSS Yojna — Maternal Care Platform for Uttar Pradesh.
            Tools for pregnancy registration, HRP risk classification,
            ANC scheduling, escalation management, and neonatal tracking.
        """,
    )

    # ── Static files + dashboard ───────────────────────────────────────────────
    mcp._additional_http_routes.append(
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)))
    )

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(request: Request) -> FileResponse:
        return FileResponse(str(STATIC_DIR / "dashboard.html"))

    # ── Tool modules ──────────────────────────────────────────────────────────
    register_registration_tools(mcp)
    register_hrp_photo_tools(mcp)
    register_anc_tools(mcp)
    register_escalation_tools(mcp)
    register_location_tools(mcp)
    register_dashboard_routes(mcp)
    # register_neonatal_tools(mcp)     ← Day 4

    # ── Escalation engine ─────────────────────────────────────────────────────
    get_scheduler()  # start APScheduler background thread

    # ── Health check ──────────────────────────────────────────────────────────
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> PlainTextResponse:
        return PlainTextResponse("OK")

    @mcp.custom_route("/status", methods=["GET"])
    async def status(request: Request) -> JSONResponse:
        from mmss_mcp.db.database import db
        with db() as conn:
            patients = conn.execute("SELECT COUNT(*) as n FROM patients").fetchone()["n"]
            hrp_open = conn.execute(
                "SELECT COUNT(*) as n FROM hrp_cases WHERE status='open'"
            ).fetchone()["n"]
            escalations = conn.execute(
                "SELECT COUNT(*) as n FROM escalations WHERE status='pending'"
            ).fetchone()["n"]
        return JSONResponse({
            "status": "running",
            "patients_registered": patients,
            "hrp_cases_open": hrp_open,
            "escalations_pending": escalations,
        })

    return mcp


def main():
    import atexit
    atexit.register(stop_scheduler)

    port = int(os.getenv("MCP_PORT", 8889))
    demo = os.getenv("DEMO_MODE", "true").lower() == "true"
    logger.info(f"Starting MMSS MCP server on port {port} (DEMO_MODE={demo})")
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
