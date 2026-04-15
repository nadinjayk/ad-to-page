# Design Transfer Pipeline — Technical Specification

## System Overview

An agentic system that:
1. Scrapes a reference website's above-the-fold visual and DOM
2. Reconstructs it as clean HTML/CSS via LLM
3. Extracts brand identity from an ad creative image via LLM
4. Re-skins the reconstructed page with the new brand identity via LLM

Architecture: React frontend + Python (FastAPI) backend + Anthropic API.

---

## Stack

| Layer | Tool | Why |
|---|---|---|
| Frontend | React + TypeScript | File uploads, preview iframes, step-by-step UI |
| Backend | FastAPI (Python 3.11+) | Async, clean routing, native Pydantic validation |
| Scraping | Playwright (Python) | Headless Chromium — screenshot + DOM extraction in one pass |
| LLM | Anthropic API (claude-sonnet-4-20250514) | Vision + code generation. Same model as Claude Code chat |
| Rendering/QA | Playwright (Python) | Headless render of generated HTML for feedback loop |
| File storage | Local filesystem or S3 | Store generated HTML artifacts per session |
| Task queue (optional) | Celery + Redis | If you want async job processing; not required for MVP |

### Python Dependencies

```
fastapi>=0.115
uvicorn>=0.30
anthropic>=0.40
playwright>=1.48
pydantic>=2.0
python-multipart>=0.0.9
beautifulsoup4>=4.12
cssutils>=2.9
Pillow>=10.0
```

### Node/React Dependencies

```
react, react-dom, typescript
axios (API calls)
@monaco-editor/react (optional — for showing/editing generated code)
```

---

## Phase 1: Scraping Engine

### 1.1 Input
- URL string from user

### 1.2 Process

Use Playwright to:

```python
async def scrape_above_fold(url: str, viewport: dict = {"width": 1440, "height": 900}):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport_size=viewport)
        await page.goto(url, wait_until="networkidle")

        # Wait for fonts/images to settle
        await page.wait_for_timeout(2000)

        # 1. Screenshot (above-fold only — viewport rect)
        screenshot_bytes = await page.screenshot(type="png", full_page=False)

        # 2. Extract visible DOM
        dom_data = await page.evaluate("""() => {
            // Get all elements whose bounding rect intersects the viewport
            const viewportHeight = window.innerHeight;
            const viewportWidth = window.innerWidth;

            function isVisible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return (
                    rect.bottom > 0 &&
                    rect.top < viewportHeight &&
                    rect.right > 0 &&
                    rect.left < viewportWidth &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0'
                );
            }

            // Get computed styles for visible elements
            const elements = document.querySelectorAll('*');
            const visibleStyles = [];
            for (const el of elements) {
                if (isVisible(el)) {
                    const computed = window.getComputedStyle(el);
                    visibleStyles.push({
                        tag: el.tagName.toLowerCase(),
                        classes: el.className,
                        text: el.innerText?.slice(0, 200),
                        styles: {
                            fontFamily: computed.fontFamily,
                            fontSize: computed.fontSize,
                            fontWeight: computed.fontWeight,
                            color: computed.color,
                            backgroundColor: computed.backgroundColor,
                            padding: computed.padding,
                            margin: computed.margin,
                            display: computed.display,
                            flexDirection: computed.flexDirection,
                            justifyContent: computed.justifyContent,
                            alignItems: computed.alignItems,
                            gap: computed.gap,
                            borderRadius: computed.borderRadius,
                        },
                        rect: el.getBoundingClientRect().toJSON()
                    });
                }
            }

            return {
                title: document.title,
                html: document.documentElement.outerHTML,
                viewportWidth: viewportWidth,
                viewportHeight: viewportHeight,
                visibleElements: visibleStyles,
                fonts: Array.from(document.fonts).map(f => ({
                    family: f.family,
                    weight: f.weight,
                    style: f.style
                }))
            };
        }""")

        # 3. Extract external stylesheet URLs and inline styles
        stylesheets = await page.evaluate("""() => {
            const sheets = [];
            for (const sheet of document.styleSheets) {
                try {
                    const rules = Array.from(sheet.cssRules || []).map(r => r.cssText);
                    sheets.push({
                        href: sheet.href,
                        rules: rules.slice(0, 500)  // cap to avoid token explosion
                    });
                } catch(e) {
                    sheets.push({ href: sheet.href, rules: [] });
                }
            }
            return sheets;
        }""")

        await browser.close()

        return {
            "screenshot": screenshot_bytes,       # PNG bytes
            "dom": dom_data,                       # Structured DOM info
            "stylesheets": stylesheets,            # CSS rules
            "viewport": viewport
        }
```

### 1.3 Output
- `screenshot.png` — above-fold viewport capture
- `dom.json` — visible element tree with computed styles, font info, layout metrics
- `styles.json` — extracted CSS rules

### 1.4 Key Decisions
- Viewport fixed at 1440x900 (standard desktop). User can override.
- `wait_until="networkidle"` + 2s delay ensures fonts/hero images load.
- DOM extraction captures computed styles (not source CSS) so the LLM sees actual rendered values.
- Cap CSS rules at ~500 per sheet to stay within token budget.

---

## Phase 2: Visual Reconstruction via LLM

### 2.1 The Core Problem

Single-shot HTML generation from a screenshot produces ~70% fidelity. Chat-based Claude Code gets ~95% because it iterates: write code, render, compare, fix. We need to replicate that loop.

### 2.2 Reconstruction Loop

```
[screenshot + DOM data]
        |
        v
   LLM: Generate HTML/CSS  (attempt 1)
        |
        v
   Playwright: Render generated HTML, screenshot it
        |
        v
   LLM: Compare original vs reconstruction screenshot
        |          "What's different? Fix it."
        v
   LLM: Generate corrected HTML/CSS  (attempt 2)
        |
        v
   Playwright: Render again, screenshot
        |
        v
   (repeat until acceptable or max 3 iterations)
        |
        v
   Final HTML/CSS artifact
```

### 2.3 API Call — Initial Generation

```python
import anthropic
import base64

client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var

def reconstruct_html(screenshot_b64: str, dom_summary: dict, styles_summary: list) -> str:
    # Prepare a condensed style/structure context
    # Keep this under ~4000 tokens to leave room for the image + response
    context = build_context_string(dom_summary, styles_summary)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""Recreate this webpage's above-the-fold view as a single self-contained HTML file with inline CSS.

Requirements:
- Match the visual layout, spacing, typography, and color scheme as closely as possible.
- Use the exact fonts, sizes, and colors from the extracted styles below.
- All CSS must be inline in a <style> tag. No external dependencies except Google Fonts if needed.
- Use placeholder images (solid color divs or simple SVG shapes) where photos appear.
- The page should look correct at 1440x900 viewport.
- Do NOT include any JavaScript.
- Output ONLY the HTML file contents. No explanation, no markdown fences.

Extracted page metadata:
- Title: {dom_summary.get('title', 'Unknown')}
- Fonts detected: {dom_summary.get('fonts', [])}

Key element styles (computed):
{context}"""
                    }
                ]
            }
        ]
    )

    return response.content[0].text
```

### 2.4 API Call — Refinement Loop

```python
async def refine_reconstruction(
    original_screenshot_b64: str,
    current_html: str,
    max_iterations: int = 3
) -> str:
    for i in range(max_iterations):
        # Render current HTML
        reconstruction_screenshot = await render_html_to_screenshot(current_html)
        recon_b64 = base64.b64encode(reconstruction_screenshot).decode()

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=16000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Original website screenshot:"
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": original_screenshot_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": "Current reconstruction screenshot:"
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": recon_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": f"""Compare these two screenshots. The first is the original website, the second is our HTML reconstruction.

Identify visual differences and output a corrected version of the HTML that more closely matches the original.

Current HTML:
```html
{current_html}
```

Output ONLY the corrected HTML. No explanation."""
                        }
                    ]
                }
            ]
        )

        current_html = response.content[0].text
        current_html = current_html.strip().removeprefix("```html").removesuffix("```").strip()

    return current_html
```

### 2.5 Headless Render Helper

```python
async def render_html_to_screenshot(html: str, viewport={"width": 1440, "height": 900}) -> bytes:
    """Render HTML string in headless browser and return PNG screenshot bytes."""
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(html)
        tmp_path = f.name

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport_size=viewport)
            await page.goto(f"file://{tmp_path}", wait_until="load")
            await page.wait_for_timeout(1000)
            screenshot = await page.screenshot(type="png", full_page=False)
            await browser.close()
        return screenshot
    finally:
        os.unlink(tmp_path)
```

### 2.6 Token Budget Considerations

Each iteration costs roughly:
- 2 images at 1440x900 ~1600 tokens each (low detail) or ~4000 each (high detail)
- DOM context: ~2000-4000 tokens
- HTML output: ~3000-8000 tokens
- Total per iteration: ~12,000-20,000 tokens

With 3 refinement iterations: **~60,000-80,000 tokens total** per reconstruction.

Use `"detail": "high"` on the image source for better fidelity (costs more tokens but significant quality improvement for layout matching).

---

## Phase 3: Ad Creative Analysis

### 3.1 Input
- Image file of an ad creative (PNG/JPG) uploaded by user

### 3.2 API Call — Brand Extraction

```python
def extract_brand_identity(ad_image_b64: str, media_type: str = "image/png") -> dict:
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": ad_image_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Analyze this ad creative and extract the brand identity as a JSON object.

Return ONLY valid JSON with this structure:
{
    "brand_name": "string",
    "tagline": "string or null",
    "primary_color": "#hex",
    "secondary_color": "#hex",
    "accent_color": "#hex or null",
    "background_color": "#hex",
    "text_color": "#hex",
    "font_style": "description of typography (e.g., 'clean sans-serif, bold headings')",
    "product_category": "string",
    "product_name": "string or null",
    "copy_text": {
        "headline": "string",
        "subheadline": "string or null",
        "cta": "string or null",
        "body": "string or null"
    },
    "visual_style": "description of overall aesthetic (e.g., 'minimalist, lots of whitespace, product-centric')",
    "logo_description": "what the logo looks like"
}

No markdown fences. No explanation. Only JSON."""
                    }
                ]
            }
        ]
    )

    import json
    raw = response.content[0].text.strip()
    raw = raw.removeprefix("```json").removesuffix("```").strip()
    return json.loads(raw)
```

### 3.3 Output
Structured JSON with brand identity: colors, typography, copy, visual style.

---

## Phase 4: Design Transfer (Re-skinning)

### 4.1 Input
- Reconstructed HTML from Phase 2
- Brand identity JSON from Phase 3

### 4.2 API Call — Re-skin

```python
def reskin_page(
    base_html: str,
    brand_identity: dict,
    original_screenshot_b64: str
) -> str:
    import json
    brand_json = json.dumps(brand_identity, indent=2)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": original_screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""You are re-skinning an existing webpage design for a different brand.

Here is the current HTML (which replicates the layout of the reference site shown in the image):

```html
{base_html}
```

Here is the new brand identity to apply:

```json
{brand_json}
```

Transform the HTML to represent the new brand while preserving the original layout structure. Specifically:

1. PRESERVE: Overall grid/flex layout, section arrangement, spacing proportions, element hierarchy.
2. CHANGE:
   - All colors to match the new brand palette.
   - All text content (headings, body, CTAs) to match the new brand's copy.
   - Replace any original brand name/logo references with the new brand name.
   - Adjust typography to match the new brand's font style (use Google Fonts if needed).
   - Remove any references to the original brand (logos, trademarks, product names).
3. Product imagery placeholders should reflect the new product category.
4. Navigation links should be generic or reflect the new brand.

Output ONLY the complete HTML file. No explanation."""
                    }
                ]
            }
        ]
    )

    return response.content[0].text.strip().removeprefix("```html").removesuffix("```").strip()
```

### 4.3 Optional: Refinement Loop (Same Pattern as Phase 2)

If quality isn't sufficient in one pass, run the same render-compare-fix loop:
render the re-skinned page, screenshot it, send both to the LLM asking "does this look like a coherent branded page?", iterate.

---

## Phase 5: API & Frontend

### 5.1 FastAPI Endpoints

```
POST /api/scrape
  Body: { "url": "https://..." , "viewport_width": 1440, "viewport_height": 900 }
  Response: { "job_id": "uuid", "screenshot_url": "/files/{id}/screenshot.png", "status": "complete" }

POST /api/reconstruct
  Body: { "job_id": "uuid" }
  Response: { "html_url": "/files/{id}/reconstruction.html", "iterations": 3, "status": "complete" }

POST /api/extract-brand
  Body: multipart/form-data with ad image
  Response: { "brand_identity": { ... } }

POST /api/reskin
  Body: { "job_id": "uuid", "brand_identity": { ... } }
  Response: { "html_url": "/files/{id}/reskinned.html", "status": "complete" }

GET /files/{job_id}/{filename}
  Static file serving for screenshots and HTML artifacts
```

### 5.2 React Frontend Flow

```
Step 1: URL Input
  [text field] [viewport selector: Desktop/Tablet/Mobile]
  [Scrape] button
  -> Shows loading spinner, then above-fold screenshot preview

Step 2: Reconstruction
  [Reconstruct] button
  -> Shows side-by-side: original screenshot | iframe with reconstructed HTML
  -> Shows iteration progress (attempt 1/3, 2/3, 3/3)

Step 3: Ad Upload
  [file upload zone] for ad creative image
  [Extract Brand] button
  -> Shows extracted brand identity as editable JSON/form
  -> User can tweak colors, copy, etc. before proceeding

Step 4: Re-skin
  [Apply Brand] button
  -> Shows final result in iframe
  -> [Download HTML] button
```

### 5.3 Session/Job Model

```python
from pydantic import BaseModel
from uuid import uuid4
from pathlib import Path

class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    url: str
    viewport: dict = {"width": 1440, "height": 900}
    status: str = "created"  # created | scraping | reconstructing | complete | error

    # Phase 1 outputs
    screenshot_path: str | None = None
    dom_data: dict | None = None
    styles_data: list | None = None

    # Phase 2 outputs
    reconstruction_html_path: str | None = None
    reconstruction_iterations: int = 0

    # Phase 3 outputs
    brand_identity: dict | None = None

    # Phase 4 outputs
    reskinned_html_path: str | None = None
```

---

## Key Design Decisions & Tradeoffs

### Why Sonnet 4 and not Opus 4?

Sonnet is the right call for this pipeline. The tasks (HTML generation, visual comparison, structured extraction) are all well within Sonnet's capability ceiling, and you're making 4-10 API calls per job. At Opus pricing that adds up fast for what is essentially template-level code generation. Upgrade to Opus only if Sonnet's reconstruction quality consistently fails after 3 refinement iterations.

### Why a refinement loop matters

The single biggest quality gap between "LLM generates HTML from screenshot" and "Claude Code chat" is iteration. In chat, the model sees its own output rendered, identifies problems, and fixes them. Without this loop, you get:
- Wrong spacing/padding (the most common issue)
- Missing hover states or subtle gradients
- Font weight mismatches
- Incorrect flex/grid proportions

The render-screenshot-compare loop closes this gap. 3 iterations is the sweet spot — diminishing returns after that.

### DOM extraction vs. raw HTML

Sending the entire raw HTML of a modern site would blow your token budget (50k-200k tokens for apple.com). The computed-styles approach extracts only what the LLM needs: actual rendered font sizes, colors, layout modes, and element dimensions. This typically compresses to 2-4k tokens.

### Image detail level

Use `"detail": "high"` for the Anthropic API image inputs (costs ~4x tokens vs low detail). For layout reconstruction, the model needs to see fine details like font weights, exact spacing, border radii. Low detail mode will produce noticeably worse reconstructions.

To use high detail:
```python
{
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": screenshot_b64
    }
    # Note: Anthropic API auto-selects detail level based on image size.
    # 1440x900 will use high detail automatically.
}
```

---

## Cost Estimation (Per Job)

| Phase | API Calls | Approx Tokens | Sonnet Cost |
|---|---|---|---|
| Phase 2: Initial reconstruction | 1 | ~20k | ~$0.10 |
| Phase 2: Refinement (x3) | 3 | ~60k | ~$0.30 |
| Phase 3: Brand extraction | 1 | ~5k | ~$0.03 |
| Phase 4: Re-skin | 1 | ~20k | ~$0.10 |
| Phase 4: Re-skin refinement (x2) | 2 | ~40k | ~$0.20 |
| **Total** | **~8** | **~145k** | **~$0.73** |

These are rough estimates. Actual cost depends on HTML complexity and DOM context size.

---

## File Structure

```
design-transfer/
  frontend/
    src/
      App.tsx
      components/
        UrlInput.tsx
        ScreenshotPreview.tsx
        ReconstructionView.tsx      # side-by-side original vs reconstruction
        AdUpload.tsx
        BrandIdentityEditor.tsx     # editable JSON/form for extracted brand
        ReskinPreview.tsx
      api/
        client.ts                   # axios wrapper for backend endpoints
  backend/
    main.py                         # FastAPI app
    scraper.py                      # Playwright scraping logic
    reconstructor.py                # LLM reconstruction + refinement loop
    brand_extractor.py              # Ad creative analysis
    reskinner.py                    # Design transfer
    renderer.py                     # Headless HTML render helper
    models.py                       # Pydantic models (Job, BrandIdentity)
    config.py                       # API keys, defaults
    jobs/                           # Per-job file storage
  requirements.txt
  package.json
```

---

## Implementation Order

1. **Scraper** (scraper.py + renderer.py) — get Playwright working, verify screenshot quality
2. **Reconstruction** (reconstructor.py) — single-shot first, then add refinement loop
3. **Brand extraction** (brand_extractor.py) — straightforward single API call
4. **Re-skin** (reskinner.py) — ties it all together
5. **API endpoints** (main.py) — expose everything over HTTP
6. **Frontend** — step-by-step wizard UI

Each phase is independently testable from the command line before wiring up the API layer.
