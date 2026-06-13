import logging
from typing import Annotated, Optional
import httpx
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.dependencies import CurrentContext

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_HEADERS = {"User-Agent": "mmss-mcp/1.0 (maternal care platform, UP Government)"}


def register_location_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def resolve_location(
        latitude: Annotated[float, "Latitude from WhatsApp location pin"],
        longitude: Annotated[float, "Longitude from WhatsApp location pin"],
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Convert a WhatsApp location pin (latitude/longitude) into village, block, and district.

        Use this whenever the user shares a WhatsApp location pin during registration
        or an emergency — it extracts the address fields needed for registration
        or to dispatch help.
        """
        logger.info(f"[resolve_location] Reversing lat={latitude}, lng={longitude}")

        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    _NOMINATIM_URL,
                    params={"lat": latitude, "lon": longitude, "format": "json", "addressdetails": 1},
                )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[resolve_location] Nominatim failed: {e}")
            return {"success": False, "error": f"Could not resolve location: {e}"}

        addr = data.get("address", {})

        # Nominatim field names vary by region; try several keys for each level
        village = (
            addr.get("village") or addr.get("hamlet") or addr.get("suburb") or
            addr.get("neighbourhood") or addr.get("town") or ""
        )
        block = (
            addr.get("county") or addr.get("subdistrict") or
            addr.get("city_block") or addr.get("city") or ""
        )
        district = (
            addr.get("state_district") or addr.get("district") or
            addr.get("city") or ""
        )
        state = addr.get("state", "")
        display = data.get("display_name", "")

        logger.info(f"[resolve_location] Resolved: village={village}, block={block}, district={district}")

        return {
            "success": True,
            "data": {
                "village": village,
                "block": block,
                "district": district,
                "state": state,
                "display_name": display,
                "raw_address": addr,
                "message": (
                    f"Location resolved: {village or '(unknown village)'}, "
                    f"{block or '(unknown block)'}, {district}, {state}. "
                    "Use these values for registration if they look correct, "
                    "or ask the user to confirm/correct the village and block name."
                ),
            },
        }
