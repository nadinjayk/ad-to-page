from __future__ import annotations

import base64
import json
from typing import Any

from backend.anthropic_utils import call_anthropic_with_retries
from backend.config import (
    RECONSTRUCTION_MAX_TOKENS,
    RECONSTRUCTION_MODEL,
)


SYSTEM_PROMPT = """You are an elite front-end engineer recreating webpages from screenshots.

Return only a complete self-contained HTML document.
Do not include markdown fences.
Do not explain your work.
Do not output JSON.
Do not include JavaScript.
Do not reference external assets, local files, or missing images.
If a visual detail is unclear, use a simple placeholder that preserves composition instead of inventing specifics.
"""


FOOTER_REQUIREMENTS = (
    "- The final HTML must include a matching <footer> as the last major section so the page feels complete.\n"
    "- Even if the screenshot only shows above-the-fold content, extend the page with a restrained footer that matches the page's typography, colors, spacing, and brand tone.\n"
    "- The footer can be simple, but it should feel intentional and production-ready rather than empty filler.\n"
)


def _clip(value: str, limit: int) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 1]}..."


def _summarize_visible_elements(visible_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []

    for element in visible_elements[:140]:
        rect = element.get("rect") or {}
        styles = element.get("styles") or {}
        width = rect.get("width") or 0
        height = rect.get("height") or 0
        area = width * height
        text = (element.get("text") or "").strip()
        tag = (element.get("tag") or "").lower()

        if not text and area < 2500 and tag not in {"img", "button", "section", "header", "nav"}:
            continue

        summary.append(
            {
                "tag": tag,
                "classes": _clip(str(element.get("classes") or ""), 80),
                "text": _clip(text, 140),
                "rect": {
                    "x": round(rect.get("x", 0)),
                    "y": round(rect.get("y", 0)),
                    "width": round(width),
                    "height": round(height),
                },
                "styles": {
                    "display": styles.get("display"),
                    "fontFamily": styles.get("fontFamily"),
                    "fontSize": styles.get("fontSize"),
                    "fontWeight": styles.get("fontWeight"),
                    "color": styles.get("color"),
                    "backgroundColor": styles.get("backgroundColor"),
                    "justifyContent": styles.get("justifyContent"),
                    "alignItems": styles.get("alignItems"),
                    "borderRadius": styles.get("borderRadius"),
                    "boxShadow": styles.get("boxShadow"),
                    "textAlign": styles.get("textAlign"),
                },
            }
        )

        if len(summary) >= 90:
            break

    return summary


def _summarize_stylesheets(stylesheets: list[dict[str, Any]]) -> list[str]:
    collected_rules: list[str] = []
    char_budget = 12000
    current_size = 0

    for sheet in stylesheets[:12]:
        href = sheet.get("href")
        if href:
            label = f"/* stylesheet: {href} */"
            if current_size + len(label) > char_budget:
                break
            collected_rules.append(label)
            current_size += len(label)

        for rule in sheet.get("rules", [])[:60]:
            compact_rule = _clip(rule, 240)
            if current_size + len(compact_rule) > char_budget:
                return collected_rules
            collected_rules.append(compact_rule)
            current_size += len(compact_rule)

    return collected_rules


def _extract_text_from_response(response: Any) -> str:
    parts: list[str] = []

    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)

    return "\n".join(parts).strip()


def _sanitize_html_output(raw_html: str) -> str:
    html = raw_html.strip()

    if html.startswith("```html"):
        html = html[len("```html") :].strip()
    elif html.startswith("```"):
        html = html[3:].strip()

    if html.endswith("```"):
        html = html[:-3].strip()

    return html


def _build_context(
    *,
    dom_data: dict[str, Any],
    stylesheets: list[dict[str, Any]],
    viewport: dict[str, int],
    source_url: str,
) -> dict[str, Any]:
    return {
        "source_url": source_url,
        "page_title": dom_data.get("title"),
        "viewport": viewport,
        "fonts": dom_data.get("fonts", [])[:20],
        "visible_elements": _summarize_visible_elements(dom_data.get("visibleElements", [])),
        "stylesheet_hints": _summarize_stylesheets(stylesheets),
    }


def _run_reconstruction_request(*, screenshot_b64: str, prompt_text: str) -> dict[str, Any]:
    response = call_anthropic_with_retries(
        operation_name="reconstruction",
        request_callable=lambda client: client.messages.create(
            model=RECONSTRUCTION_MODEL,
            max_tokens=RECONSTRUCTION_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt_text,
                        },
                    ],
                }
            ],
        ),
    )

    html = _sanitize_html_output(_extract_text_from_response(response))
    if not html:
        raise RuntimeError("Claude returned an empty reconstruction.")

    usage = getattr(response, "usage", None)
    return {
        "html": html,
        "model": getattr(response, "model", RECONSTRUCTION_MODEL),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }


def reconstruct_html_document(
    *,
    screenshot_bytes: bytes,
    dom_data: dict[str, Any],
    stylesheets: list[dict[str, Any]],
    viewport: dict[str, int],
    source_url: str,
) -> dict[str, Any]:
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    context = _build_context(
        dom_data=dom_data,
        stylesheets=stylesheets,
        viewport=viewport,
        source_url=source_url,
    )

    prompt_text = (
        "Recreate the screenshot as a single self-contained HTML file with inline CSS.\n\n"
        "Priority order:\n"
        "1. Match the screenshot as closely as possible.\n"
        "2. Use the DOM/style context as supporting evidence.\n"
        "3. Preserve the visible above-the-fold layout first, then close the page cleanly with a matching footer.\n\n"
        "Guardrails:\n"
        "- Return one complete HTML document.\n"
        "- Put all CSS inside a <style> tag.\n"
        "- Do not include JavaScript, scripts, or event handlers.\n"
        "- Match spacing, typography, colors, gradients, borders, and alignment as closely as possible.\n"
        "- Do not rely on external image URLs, local file paths, or missing assets.\n"
        "- Use gradients, solid blocks, inline SVG, or abstract placeholders for imagery while preserving composition.\n"
        "- Only recreate sections clearly supported by the screenshot and context.\n"
        "- If content is unclear, keep it simple instead of hallucinating missing features.\n"
        "- Make the result look correct at the provided viewport.\n"
        "- Use semantic, readable HTML and CSS.\n\n"
        f"{FOOTER_REQUIREMENTS}"
        "\n"
        f"Context JSON:\n{json.dumps(context, indent=2)}"
    )

    return _run_reconstruction_request(screenshot_b64=screenshot_b64, prompt_text=prompt_text)


def repair_reconstructed_html_document(
    *,
    screenshot_bytes: bytes,
    dom_data: dict[str, Any],
    stylesheets: list[dict[str, Any]],
    viewport: dict[str, int],
    source_url: str,
    current_html: str,
    failure_report: str,
) -> dict[str, Any]:
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    context = _build_context(
        dom_data=dom_data,
        stylesheets=stylesheets,
        viewport=viewport,
        source_url=source_url,
    )

    prompt_text = (
        "The previous reconstruction failed automated validation.\n"
        "Fix the existing HTML instead of starting from scratch.\n\n"
        "Issues to fix:\n"
        f"{failure_report}\n\n"
        "Requirements:\n"
        "- Return one complete self-contained HTML document.\n"
        "- Keep the successful parts of the current layout.\n"
        "- Remove anything that causes broken structure, blank rendering, horizontal overflow, scripts, or unsafe assets.\n"
        "- Do not reference external URLs, local files, or missing images.\n"
        "- If an image or detail is uncertain, replace it with a clean placeholder or inline SVG.\n"
        "- Preserve the visible above-the-fold composition from the screenshot.\n\n"
        f"{FOOTER_REQUIREMENTS}"
        "\n"
        f"Context JSON:\n{json.dumps(context, indent=2)}\n\n"
        f"Current invalid HTML:\n{current_html}"
    )

    return _run_reconstruction_request(screenshot_b64=screenshot_b64, prompt_text=prompt_text)
