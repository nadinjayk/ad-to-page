from __future__ import annotations

import base64
import json
from typing import Any

from backend.anthropic_utils import call_anthropic_with_retries
from backend.brand_schema import normalize_brand_identity
from backend.config import (
    BRAND_EXTRACTION_MAX_TOKENS,
    BRAND_EXTRACTION_MODEL,
)


BRAND_SYSTEM_PROMPT = """You extract brand identity from ad creatives.

Return only valid JSON.
Do not include markdown fences.
Do not explain your work.
"""


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def extract_brand_identity(*, image_bytes: bytes, media_type: str) -> dict[str, Any]:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = call_anthropic_with_retries(
        operation_name="brand extraction",
        request_callable=lambda client: client.messages.create(
            model=BRAND_EXTRACTION_MODEL,
            max_tokens=BRAND_EXTRACTION_MAX_TOKENS,
            system=BRAND_SYSTEM_PROMPT,
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
                        {
                            "type": "text",
                            "text": (
                                "Analyze this ad creative and return only valid JSON with this shape:\n"
                                "{\n"
                                '  "brand_name": "string",\n'
                                '  "tagline": "string or null",\n'
                                '  "primary_color": "#hex",\n'
                                '  "secondary_color": "#hex",\n'
                                '  "accent_color": "#hex or null",\n'
                                '  "background_color": "#hex",\n'
                                '  "text_color": "#hex",\n'
                                '  "font_style": "description",\n'
                                '  "product_category": "string",\n'
                                '  "product_name": "string or null",\n'
                                '  "copy_text": {\n'
                                '    "headline": "string",\n'
                                '    "subheadline": "string or null",\n'
                                '    "cta": "string or null",\n'
                                '    "body": "string or null"\n'
                                "  },\n"
                                '  "visual_style": "description",\n'
                                '  "logo_description": "description"\n'
                                "}\n\n"
                                "Guardrails:\n"
                                "- Only use details that are visually supported by the creative.\n"
                                "- If tagline, product_name, or secondary copy is unclear, return null instead of guessing.\n"
                                "- Keep visual_style and logo_description descriptive, not speculative.\n"
                                "- Infer the closest likely hex values from the image.\n"
                                "- Do not invent claims or product details that are not visible."
                            ),
                        },
                    ],
                }
            ],
        ),
    )

    raw = _strip_fences(_response_text(response))
    if not raw:
        raise RuntimeError("Claude returned an empty brand identity response.")

    usage = getattr(response, "usage", None)
    normalized_brand_identity = normalize_brand_identity(json.loads(raw))

    return {
        "brand_identity": normalized_brand_identity,
        "model": getattr(response, "model", BRAND_EXTRACTION_MODEL),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }
