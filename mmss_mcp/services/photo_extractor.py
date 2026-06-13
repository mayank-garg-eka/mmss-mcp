"""
Extract clinical values from lab report photos using Claude Vision.
Returns structured data ready to feed into classify_hrp_risk.
"""
import os
import base64
import logging
import httpx
from anthropic import Anthropic

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


EXTRACTION_PROMPT = """
You are reading a medical lab report image from India. Extract ALL test results you can see.

Return ONLY a JSON object with this structure (include only fields you can clearly read):
{
  "hb": <float, haemoglobin in g/dL>,
  "bp_systolic": <int, systolic BP in mmHg>,
  "bp_diastolic": <int, diastolic BP in mmHg>,
  "weight_kg": <float>,
  "height_cm": <float>,
  "raw_tests": [
    {"name": "<test name>", "value": "<value>", "unit": "<unit>", "flag": "<H/L/normal if shown>"}
  ]
}

Rules:
- Only include fields you can actually read from the image
- For hb/Hb/Haemoglobin: convert to g/dL if needed
- For BP written as "120/80": systolic=120, diastolic=80
- If a value is illegible or absent, omit that field entirely
- Return only the JSON object, no explanation
"""


async def extract_from_image_url(media_url: str) -> dict:
    """
    Download image from URL and extract clinical values via Claude Vision.
    Returns dict with extracted fields + raw_tests list.
    """
    logger.info(f"[photo_extractor] Downloading image from {media_url[:60]}...")

    # Download image bytes (Interakt URLs may need no auth for short-lived signed URLs)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(media_url)
            resp.raise_for_status()
            image_bytes = resp.content
            content_type = resp.headers.get("content-type", "image/jpeg")
            # Normalize content type
            if "png" in content_type:
                media_type = "image/png"
            elif "gif" in content_type:
                media_type = "image/gif"
            elif "webp" in content_type:
                media_type = "image/webp"
            else:
                media_type = "image/jpeg"
    except Exception as e:
        logger.error(f"[photo_extractor] Failed to download image: {e}")
        raise ValueError(f"Could not download image: {e}")

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    logger.info(f"[photo_extractor] Downloaded {len(image_bytes)} bytes ({media_type}), sending to Claude Vision")

    anthropic = _get_client()
    message = anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()
    logger.info(f"[photo_extractor] Claude raw response: {raw[:200]}")

    # Parse JSON — strip markdown fences if present
    import json, re
    json_str = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(json_str)

    logger.info(f"[photo_extractor] Extracted: {result}")
    return result
