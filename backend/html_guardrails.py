from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import cssutils
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


cssutils.log.setLevel(logging.CRITICAL)


@dataclass
class GuardrailReport:
    name: str
    passed: bool
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_srcset_urls(value: str) -> list[str]:
    urls: list[str] = []

    for candidate in value.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        urls.append(candidate.split()[0])

    return urls


def _extract_css_urls(stylesheet_text: str) -> list[str]:
    if not stylesheet_text.strip():
        return []

    try:
        stylesheet = cssutils.parseString(stylesheet_text)
    except Exception:
        return []

    return [str(url).strip() for url in cssutils.getUrls(stylesheet)]


def _extract_inline_style_urls(style_value: str) -> list[str]:
    if not style_value.strip():
        return []

    try:
        stylesheet = cssutils.parseString(f"* {{{style_value}}}")
    except Exception:
        return []

    return [str(url).strip() for url in cssutils.getUrls(stylesheet)]


def _validate_asset_reference(url: str, *, context: str) -> str | None:
    candidate = url.strip().strip("\"'").strip()
    if not candidate or candidate.startswith("#"):
        return None

    lowered = candidate.lower()
    if lowered.startswith("data:") or lowered.startswith("about:"):
        return None

    if candidate.startswith("//"):
        return f"{context} uses a protocol-relative external asset: {candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme:
        if parsed.scheme in {"http", "https"}:
            return f"{context} uses an external asset URL: {candidate}"
        if parsed.scheme == "javascript":
            return f"{context} uses a javascript URL, which is not allowed."
        return f"{context} uses an unsupported asset URL scheme: {candidate}"

    return (
        f"{context} references a non-embedded asset ({candidate}). "
        "Generated HTML must be self-contained and use placeholders or data URLs instead."
    )


def validate_html_document(html: str) -> GuardrailReport:
    errors: list[str] = []
    stripped_html = html.strip()

    if not stripped_html:
        errors.append("HTML output is empty.")
        return GuardrailReport(name="html_validation", passed=False, errors=errors)

    lowered_html = stripped_html.lower()
    if "```" in stripped_html:
        errors.append("HTML output still contains markdown fences.")

    if not lowered_html.startswith("<!doctype html>"):
        errors.append("HTML output must start with <!DOCTYPE html>.")

    soup = BeautifulSoup(stripped_html, "html.parser")
    html_tag = soup.find("html")
    head_tag = soup.find("head")
    body_tag = soup.find("body")

    if html_tag is None:
        errors.append("HTML output is missing the <html> element.")
    if head_tag is None:
        errors.append("HTML output is missing the <head> element.")
    if body_tag is None:
        errors.append("HTML output is missing the <body> element.")

    if soup.find("script") is not None:
        errors.append("HTML output includes <script> tags, which are not allowed.")

    if soup.find("iframe") is not None:
        errors.append("HTML output includes nested <iframe> tags, which are not allowed.")

    if soup.find("link") is not None:
        errors.append("HTML output includes <link> tags. Inline all CSS and assets instead.")

    if soup.find("style") is None:
        errors.append("HTML output is missing an inline <style> tag.")

    for element in soup.find_all(True):
        for attribute_name in element.attrs:
            if attribute_name.lower().startswith("on"):
                errors.append(
                    f"HTML output includes inline event handlers ({attribute_name}) on <{element.name}>."
                )

    asset_attributes = {
        "img": ("src", "srcset"),
        "source": ("src", "srcset"),
        "video": ("src", "poster"),
        "audio": ("src",),
        "embed": ("src",),
        "object": ("data",),
        "link": ("href",),
        "iframe": ("src",),
    }

    for tag_name, attributes in asset_attributes.items():
        for tag in soup.find_all(tag_name):
            for attribute_name in attributes:
                raw_value = tag.get(attribute_name)
                if not raw_value:
                    continue

                candidates = (
                    _extract_srcset_urls(raw_value)
                    if attribute_name == "srcset"
                    else [str(raw_value)]
                )

                for candidate in candidates:
                    validation_error = _validate_asset_reference(
                        candidate,
                        context=f"<{tag_name}>[{attribute_name}]",
                    )
                    if validation_error:
                        errors.append(validation_error)

    for style_tag in soup.find_all("style"):
        for url in _extract_css_urls(style_tag.get_text()):
            validation_error = _validate_asset_reference(url, context="<style> url()")
            if validation_error:
                errors.append(validation_error)

    for styled_tag in soup.find_all(style=True):
        for url in _extract_inline_style_urls(str(styled_tag.get("style", ""))):
            validation_error = _validate_asset_reference(
                url,
                context=f"<{styled_tag.name}>[style] url()",
            )
            if validation_error:
                errors.append(validation_error)

    text_length = 0
    if body_tag is not None:
        text_length = len(body_tag.get_text(" ", strip=True))
        if body_tag.find(True) is None:
            errors.append("HTML body does not contain any elements.")
        if text_length == 0 and body_tag.find(["img", "svg", "canvas", "picture", "video"]) is None:
            errors.append("HTML body appears empty.")

    details = {
        "body_text_length": text_length,
        "style_tag_count": len(soup.find_all("style")),
    }

    return GuardrailReport(
        name="html_validation",
        passed=not errors,
        errors=errors,
        details=details,
    )


def validate_footer_presence(html: str) -> GuardrailReport:
    soup = BeautifulSoup(html.strip(), "html.parser")
    body_tag = soup.find("body")
    if body_tag is None:
        return GuardrailReport(
            name="footer_presence",
            passed=False,
            errors=["HTML output is missing the <body> element, so footer presence could not be verified."],
        )

    footer_tag = body_tag.find("footer")
    if footer_tag is None:
        return GuardrailReport(
            name="footer_presence",
            passed=False,
            errors=[
                "HTML output must include a visually matching <footer> so the page feels complete."
            ],
        )

    footer_text = footer_tag.get_text(" ", strip=True)
    footer_link_count = len(footer_tag.find_all("a"))
    footer_child_count = len(footer_tag.find_all(recursive=False))
    errors: list[str] = []

    if len(footer_text) < 18 and footer_link_count == 0 and footer_child_count < 2:
        errors.append(
            "The <footer> exists but is too empty. Include a simple branded footer with meaningful content or links."
        )

    return GuardrailReport(
        name="footer_presence",
        passed=not errors,
        errors=errors,
        details={
            "footer_text_length": len(footer_text),
            "footer_link_count": footer_link_count,
            "footer_child_count": footer_child_count,
        },
    )


def validate_required_asset_usage(
    html: str,
    *,
    required_asset_placeholders: list[str],
) -> GuardrailReport:
    if not required_asset_placeholders:
        return GuardrailReport(
            name="asset_usage",
            passed=True,
            details={"required_asset_placeholders": []},
        )

    matched_placeholders = [
        placeholder for placeholder in required_asset_placeholders if placeholder in html
    ]
    if matched_placeholders:
        return GuardrailReport(
            name="asset_usage",
            passed=True,
            details={
                "required_asset_placeholders": required_asset_placeholders,
                "matched_placeholders": matched_placeholders,
            },
        )

    return GuardrailReport(
        name="asset_usage",
        passed=False,
        errors=[
            "Approved assets were available but the HTML did not use any approved asset placeholder."
        ],
        details={"required_asset_placeholders": required_asset_placeholders},
    )


async def smoke_test_html_document(html: str, viewport: dict[str, int]) -> GuardrailReport:
    browser_errors: list[str] = []
    console_errors: list[str] = []
    blocked_requests: list[str] = []
    metrics: dict[str, Any] = {}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": viewport["width"], "height": viewport["height"]},
            device_scale_factor=1,
        )
        page = await context.new_page()

        async def abort_external_request(route: Any) -> None:
            request = route.request
            blocked_requests.append(f"{request.resource_type}: {request.url}")
            await route.abort()

        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda exc: browser_errors.append(str(exc)))

        await page.route("http://**/*", abort_external_request)
        await page.route("https://**/*", abort_external_request)

        try:
            await page.set_content(html, wait_until="domcontentloaded")
            await page.wait_for_timeout(150)
            metrics = await page.evaluate(
                """
                () => {
                  const root = document.documentElement;
                  const body = document.body;
                  const visibleNodes = Array.from(body.querySelectorAll("*")).filter((element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return (
                      style.display !== "none" &&
                      style.visibility !== "hidden" &&
                      Number(style.opacity || "1") > 0 &&
                      rect.width >= 12 &&
                      rect.height >= 12
                    );
                  });

                  return {
                    bodyChildCount: body.children.length,
                    textLength: (body.innerText || "").replace(/\\s+/g, " ").trim().length,
                    visibleNodeCount: visibleNodes.length,
                    mediaCount: body.querySelectorAll("img,svg,canvas,picture,video").length,
                    scrollWidth: root.scrollWidth,
                    scrollHeight: root.scrollHeight,
                    bodyWidth: body.getBoundingClientRect().width,
                    bodyHeight: body.getBoundingClientRect().height
                  };
                }
                """
            )
        except Exception as exc:
            browser_errors.append(f"Browser render failed: {exc}")
        finally:
            await context.close()
            await browser.close()

    errors: list[str] = []
    errors.extend(browser_errors)
    errors.extend(f"Console error: {message}" for message in console_errors)
    errors.extend(f"Blocked external request: {request}" for request in blocked_requests)

    if metrics:
        if metrics.get("bodyChildCount", 0) == 0:
            errors.append("Rendered page body has no child elements.")

        visible_node_count = metrics.get("visibleNodeCount", 0)
        media_count = metrics.get("mediaCount", 0)
        text_length = metrics.get("textLength", 0)
        if visible_node_count < 2 and media_count == 0 and text_length < 20:
            errors.append("Rendered page looks effectively blank.")

        scroll_width = metrics.get("scrollWidth", 0)
        if scroll_width > viewport["width"] + 48:
            errors.append(
                f"Rendered page overflows horizontally ({scroll_width}px for a {viewport['width']}px viewport)."
            )

        body_height = metrics.get("bodyHeight", 0)
        if body_height < 120:
            errors.append("Rendered page body height collapsed unexpectedly.")

    return GuardrailReport(
        name="browser_smoke_test",
        passed=not errors,
        errors=errors,
        details=metrics,
    )


def build_guardrail_failure_summary(*reports: GuardrailReport) -> str:
    lines: list[str] = []

    for report in reports:
        if report.passed:
            continue
        for error in report.errors:
            lines.append(f"- [{report.name}] {error}")

    return "\n".join(lines)
