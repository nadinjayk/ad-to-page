from __future__ import annotations

import base64
import json
from typing import Any

from backend.anthropic_utils import call_anthropic_with_retries
from backend.config import RESKIN_MAX_TOKENS, RESKIN_MODEL


RESKIN_SYSTEM_PROMPT = """You are an elite front-end engineer rebranding existing webpages.

Return only a complete self-contained HTML document.
Do not include markdown fences.
Do not explain your work.
Do not include JavaScript.
Do not reference external assets, local files, or missing images.
When approved assets are provided, treat them as production-ready inputs rather than decorative suggestions.
Preserve the layout and use placeholders whenever the new brand details are uncertain.
"""


FOOTER_REQUIREMENTS = (
    "- The final HTML must include a visually matching <footer> as the last major section.\n"
    "- If the source layout feels incomplete, add or strengthen a restrained footer so the page closes cleanly.\n"
    "- The footer should match the page's palette, typography, spacing rhythm, and brand tone.\n"
)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```html"):
        cleaned = cleaned[len("```html") :].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def _build_multimodal_content(
    *,
    screenshot_b64: str,
    prompt_text: str,
    approved_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Reference screenshot of the source webpage:",
        },
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": screenshot_b64,
            },
        },
    ]

    for asset in approved_assets:
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"Approved asset `{asset['asset_id']}` follows.\n"
                        f"Label: {asset['label']}\n"
                        f"Usage hint: {asset['usage_hint']}\n"
                        f"Dimensions: {asset['width']}x{asset['height']}\n"
                        f"Source type: {asset.get('source_type')}\n"
                        f"If you use this asset in the HTML, reference it exactly as "
                        f'`src="asset://{asset["asset_id"]}"`.\n'
                        "Do not invent any other asset IDs or external image URLs."
                    ),
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": asset["media_type"],
                        "data": base64.b64encode(asset["bytes"]).decode("utf-8"),
                    },
                },
            ]
        )

    content.append({"type": "text", "text": prompt_text})
    return content


def _run_reskin_request(
    *,
    screenshot_b64: str,
    prompt_text: str,
    approved_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    response = call_anthropic_with_retries(
        operation_name="reskin",
        request_callable=lambda client: client.messages.create(
            model=RESKIN_MODEL,
            max_tokens=RESKIN_MAX_TOKENS,
            system=RESKIN_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _build_multimodal_content(
                        screenshot_b64=screenshot_b64,
                        prompt_text=prompt_text,
                        approved_assets=approved_assets,
                    ),
                }
            ],
        ),
    )

    html = _strip_fences(_response_text(response))
    if not html:
        raise RuntimeError("Claude returned an empty reskin response.")

    usage = getattr(response, "usage", None)
    return {
        "html": html,
        "model": getattr(response, "model", RESKIN_MODEL),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }


def reskin_html_document(
    *,
    base_html: str,
    brand_identity: dict[str, Any],
    screenshot_bytes: bytes,
    viewport: dict[str, int],
    source_url: str,
    color_strategy: str = "use_ad",
    approved_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    approved_assets = approved_assets or []
    color_instruction = (
        "- Preserve the original source site's color system as closely as possible.\n"
        "- Keep the source page's dominant surface, CTA, and navigation colors instead of recoloring to the ad palette.\n"
        "- You may still change copy, logos, typography feel, and product framing to match the ad.\n"
    ) if color_strategy == "preserve_site" else (
        "- Update colors to match the ad-derived brand palette from the brand identity JSON.\n"
        "- Use the ad's primary, secondary, accent, background, and text colors where they fit naturally.\n"
    )
    asset_instruction = (
        "- At least one approved asset is available. Use one approved asset as the dominant hero "
        "or product visual with its exact asset:// placeholder.\n"
        "- Only fall back to abstract placeholders if no approved assets were supplied.\n"
    ) if approved_assets else (
        "- Use gradients, solid blocks, inline SVG, or abstract placeholders for imagery.\n"
    )

    prompt_text = (
        "You are re-skinning an existing webpage for a new brand.\n\n"
        "Preserve the layout structure as much as possible.\n"
        "Change the visual identity, copy, palette, and product framing to match the new brand.\n\n"
        "Requirements:\n"
        "- Keep the overall page structure, spacing rhythm, section order, and hierarchy.\n"
        "- Replace brand names, copy, CTA labels, and product references with the new brand identity.\n"
        f"{color_instruction}"
        "- Update typography feel and decorative styling to reflect the new brand direction.\n"
        "- Do not add new sections unless the existing layout clearly needs a placeholder to stay balanced.\n"
        "- Do not rely on external image URLs, local file paths, or missing assets.\n"
        f"{asset_instruction}"
        "- If approved assets are provided, only use them through their exact asset:// placeholders.\n"
        "- Do not invent or omit approved asset placeholders when an approved asset is available.\n"
        "- Make imagery placeholders reflect the product category from the brand JSON.\n"
        "- If the brand JSON is missing a detail, use restrained generic copy instead of hallucinating specifics.\n"
        "- Keep everything inside one HTML document with inline CSS.\n"
        "- Do not include JavaScript.\n"
        f"{FOOTER_REQUIREMENTS}"
        "\n"
        f"Source URL: {source_url}\n"
        f"Viewport: {json.dumps(viewport)}\n\n"
        f"Brand identity JSON:\n{json.dumps(brand_identity, indent=2)}\n\n"
        f"Current HTML to reskin:\n{base_html}"
    )

    return _run_reskin_request(
        screenshot_b64=screenshot_b64,
        prompt_text=prompt_text,
        approved_assets=approved_assets,
    )


def repair_reskinned_html_document(
    *,
    base_html: str,
    brand_identity: dict[str, Any],
    screenshot_bytes: bytes,
    viewport: dict[str, int],
    source_url: str,
    current_html: str,
    failure_report: str,
    color_strategy: str = "use_ad",
    approved_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    approved_assets = approved_assets or []
    color_instruction = (
        "- Preserve the source site's original color palette while fixing the current HTML.\n"
        "- Do not recolor the interface to the ad palette in this mode.\n"
    ) if color_strategy == "preserve_site" else (
        "- Keep or restore the ad-derived brand palette from the brand identity JSON while fixing the HTML.\n"
    )
    asset_instruction = (
        "- Approved assets were supplied. Keep at least one approved asset in the final HTML "
        "using its exact asset:// placeholder.\n"
        "- If the current HTML omitted the approved asset, insert it into the main hero or "
        "primary product visual area.\n"
    ) if approved_assets else (
        "- Use placeholders or inline SVG for any imagery.\n"
    )

    prompt_text = (
        "The previous reskinned HTML failed automated validation.\n"
        "Fix the current HTML instead of rewriting the whole concept.\n\n"
        "Issues to fix:\n"
        f"{failure_report}\n\n"
        "Requirements:\n"
        "- Keep the current layout and brand direction where they already work.\n"
        "- Remove anything that causes broken structure, blank rendering, horizontal overflow, scripts, or unsafe assets.\n"
        "- Keep the result self-contained with inline CSS only.\n"
        f"{color_instruction}"
        "- Do not reference external URLs, local file paths, or missing images.\n"
        f"{asset_instruction}"
        "- If approved assets are provided, only use the exact asset:// placeholders for them.\n"
        "- Stay faithful to the supplied brand JSON and do not invent unsupported brand claims.\n"
        f"{FOOTER_REQUIREMENTS}"
        "\n"
        f"Source URL: {source_url}\n"
        f"Viewport: {json.dumps(viewport)}\n\n"
        f"Brand identity JSON:\n{json.dumps(brand_identity, indent=2)}\n\n"
        f"Original reconstruction HTML:\n{base_html}\n\n"
        f"Current invalid reskinned HTML:\n{current_html}"
    )

    return _run_reskin_request(
        screenshot_b64=screenshot_b64,
        prompt_text=prompt_text,
        approved_assets=approved_assets,
    )
