from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


VISIBLE_DOM_SCRIPT = """
() => {
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;

    function isVisible(el) {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return (
            rect.width > 0 &&
            rect.height > 0 &&
            rect.bottom > 0 &&
            rect.top < viewportHeight &&
            rect.right > 0 &&
            rect.left < viewportWidth &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0'
        );
    }

    const elements = document.querySelectorAll('*');
    const visibleStyles = [];

    for (const el of elements) {
        if (!isVisible(el)) {
            continue;
        }

        const computed = window.getComputedStyle(el);
        visibleStyles.push({
            tag: el.tagName.toLowerCase(),
            classes: typeof el.className === 'string' ? el.className : '',
            text: (el.innerText || '').trim().slice(0, 200),
            styles: {
                fontFamily: computed.fontFamily,
                fontSize: computed.fontSize,
                fontWeight: computed.fontWeight,
                color: computed.color,
                backgroundColor: computed.backgroundColor,
                padding: computed.padding,
                margin: computed.margin,
                display: computed.display,
                position: computed.position,
                flexDirection: computed.flexDirection,
                justifyContent: computed.justifyContent,
                alignItems: computed.alignItems,
                gap: computed.gap,
                borderRadius: computed.borderRadius,
                border: computed.border,
                boxShadow: computed.boxShadow,
                textAlign: computed.textAlign,
                lineHeight: computed.lineHeight
            },
            rect: el.getBoundingClientRect().toJSON()
        });
    }

    return {
        title: document.title,
        html: document.documentElement.outerHTML,
        viewportWidth,
        viewportHeight,
        visibleElements: visibleStyles,
        fonts: Array.from(document.fonts || []).map(font => ({
            family: font.family,
            weight: font.weight,
            style: font.style,
            status: font.status
        }))
    };
}
"""


STYLESHEET_SCRIPT = """
() => {
    const sheets = [];

    for (const sheet of document.styleSheets) {
        try {
            const rules = Array.from(sheet.cssRules || []).map(rule => rule.cssText);
            sheets.push({
                href: sheet.href,
                rules: rules.slice(0, 500)
            });
        } catch (error) {
            sheets.push({
                href: sheet.href,
                rules: []
            });
        }
    }

    return sheets;
}
"""


async def scrape_above_the_fold(url: str, viewport: dict[str, int]) -> dict[str, Any]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport, device_scale_factor=1)

        try:
            await page.goto(url, wait_until="networkidle", timeout=45_000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)

        await page.wait_for_timeout(2_000)

        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        dom_data = await page.evaluate(VISIBLE_DOM_SCRIPT)
        stylesheets = await page.evaluate(STYLESHEET_SCRIPT)

        await browser.close()

    return {
        "screenshot": screenshot_bytes,
        "dom": dom_data,
        "stylesheets": stylesheets,
        "viewport": viewport,
    }


def persist_job_artifacts(
    job_dir: Path,
    screenshot_bytes: bytes,
    dom_data: dict[str, Any],
    stylesheets: list[dict[str, Any]],
) -> dict[str, str]:
    job_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = job_dir / "screenshot.png"
    dom_path = job_dir / "dom.json"
    styles_path = job_dir / "styles.json"

    screenshot_path.write_bytes(screenshot_bytes)
    dom_path.write_text(json.dumps(dom_data, indent=2), encoding="utf-8")
    styles_path.write_text(json.dumps(stylesheets, indent=2), encoding="utf-8")

    return {
        "screenshot_path": str(screenshot_path),
        "dom_path": str(dom_path),
        "styles_path": str(styles_path),
    }
