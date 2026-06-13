import logging
from typing import Annotated
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.dependencies import CurrentContext

from mmss_mcp.services.photo_extractor import extract_from_image_url

logger = logging.getLogger(__name__)


def register_hrp_photo_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def extract_from_photo(
        media_url: Annotated[str, "URL of the lab report image (from WhatsApp/Interakt)"],
        identifier: Annotated[str, "Patient registration ID or phone number (optional, for context)"] = "",
        ctx: Context = CurrentContext(),
    ) -> dict:
        """
        Read a lab report photo and extract clinical values using Claude Vision.

        Extracts haemoglobin (Hb), blood pressure (BP), weight, height, and any
        other visible test results. Returns structured values ready to pass directly
        into classify_hrp_risk.

        Use this whenever the patient or ASHA sends a photo of a lab report or
        prescription slip.
        """
        logger.info(f"[extract_from_photo] Processing image for patient: {identifier or 'unknown'}")

        try:
            extracted = await extract_from_image_url(media_url)

            summary_parts = []
            if "hb" in extracted:
                summary_parts.append(f"Hb: {extracted['hb']} g/dL")
            if "bp_systolic" in extracted and "bp_diastolic" in extracted:
                summary_parts.append(f"BP: {extracted['bp_systolic']}/{extracted['bp_diastolic']} mmHg")
            if "weight_kg" in extracted:
                summary_parts.append(f"Weight: {extracted['weight_kg']} kg")
            if "height_cm" in extracted:
                summary_parts.append(f"Height: {extracted['height_cm']} cm")

            raw_count = len(extracted.get("raw_tests", []))
            summary = ", ".join(summary_parts) if summary_parts else "No standard values found"

            logger.info(f"[extract_from_photo] Extracted: {summary} ({raw_count} raw tests)")

            return {
                "success": True,
                "data": {
                    "extracted": extracted,
                    "summary": summary,
                    "hint": (
                        "Pass the extracted fields directly to classify_hrp_risk "
                        f"for patient {identifier}." if identifier else
                        "Pass the extracted fields to classify_hrp_risk."
                    ),
                },
            }

        except Exception as e:
            logger.error(f"[extract_from_photo] Failed: {e}")
            return {"success": False, "error": str(e)}
