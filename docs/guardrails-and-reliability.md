# Guardrails and Reliability

## Philosophy

The app uses strong models, but it does not trust raw model output blindly.

The reliability strategy is:

1. constrain the prompts
2. validate the output
3. repair once if validation fails
4. fail clearly if the repaired output still does not pass

This approach is implemented mainly in:

- [backend/html_guardrails.py](</C:/Users/91956/Desktop/assignment final/backend/html_guardrails.py>)
- [backend/main.py](</C:/Users/91956/Desktop/assignment final/backend/main.py>)
- [backend/anthropic_utils.py](</C:/Users/91956/Desktop/assignment final/backend/anthropic_utils.py>)
- [backend/asset_pipeline.py](</C:/Users/91956/Desktop/assignment final/backend/asset_pipeline.py>)

## Hallucination Control Methods

### Prompt-Level Constraints

The prompts explicitly tell the model to:

- avoid external assets
- avoid JavaScript
- avoid unsupported claims
- use placeholders when details are unclear
- use generic copy when brand details are missing
- return only HTML or only JSON, depending on the step
- include a matching footer

### Brand JSON Normalization

Brand extraction does not proceed with raw model output alone.

It is normalized against a strict schema that enforces:

- non-empty required text fields
- nullable optional fields
- valid hex colors
- ignored extra fields instead of silently depending on them

This reduces downstream confusion from malformed or over-invented brand data.

## Claude Reliability Wrapper

[backend/anthropic_utils.py](</C:/Users/91956/Desktop/assignment final/backend/anthropic_utils.py>) wraps Anthropic calls with retries.

Retryable cases include:

- connection errors
- timeouts
- rate limits
- internal server errors
- specific API status codes such as `429`, `500`, `503`, and `529`

Behavior:

- exponential backoff
- jitter
- clear error message if retries are exhausted

This was added because transient overload errors and temporary service failures occurred in real usage.

## HTML Validation

The app validates generated HTML structurally before trusting it.

Checks include:

- HTML is not empty
- starts with `<!DOCTYPE html>`
- has `<html>`, `<head>`, and `<body>`
- has at least one inline `<style>` tag
- contains no `<script>` tags
- contains no `<iframe>` tags
- contains no `<link>` tags
- contains no inline event handlers such as `onclick`
- contains no external asset URLs in `src`, `srcset`, `poster`, `url()` and similar references

Why this matters:

- the generated output must stay self-contained
- broken external references create fragile demos and failed renders

## Footer Guardrail

A dedicated footer check now requires:

- a real `<footer>` element
- enough meaningful content that it feels intentional

This was added because some pages looked incomplete even when the hero section was strong.

## Browser Smoke Test

After HTML validation, Playwright performs a browser smoke test.

This test:

- renders the generated HTML locally
- blocks all external HTTP and HTTPS requests
- captures browser and console errors
- measures visibility and page geometry

It checks for problems such as:

- effectively blank pages
- collapsed body height
- horizontal overflow
- broken rendering despite syntactically valid HTML

## Single Repair Pass

If validation fails, the app performs one repair pass instead of looping indefinitely.

Flow:

1. collect failure reports
2. summarize failures
3. send current HTML plus failures back to the model
4. validate again
5. accept only if the repaired result passes

This keeps the pipeline simple while still correcting common generation mistakes.

## Approved Asset Guardrails

When approved assets exist, the system enforces usage rules.

Mechanisms:

- prompt assets are provided with explicit placeholders such as `asset://primary_visual`
- the model is told to use exact placeholders only
- `validate_required_asset_usage` checks that approved placeholders were actually used
- `materialize_asset_placeholders` replaces placeholders with embedded data URLs
- unresolved placeholders trigger failure

This prevents the model from:

- inventing random asset IDs
- substituting remote image URLs
- silently ignoring approved assets

## Asset Search Safety

The asset pipeline avoids naive free-for-all image scraping.

Guardrails include:

- preferred brand-domain discovery
- blocked-domain filtering
- host and URL relevance scoring
- image candidate scoring using page URL, image URL, and alt text
- penalties for likely meta-share images, logos, and favicons
- image byte-size limits
- minimum dimensions
- aspect-ratio bounds

## Generic Product Fallback

If brand-specific asset search fails, the system can fall back to generic product imagery.

Example:

- if a branded washing machine image is not found, it can try a neutral washing machine image instead

This fallback still uses the same validation pipeline, so it remains constrained rather than arbitrary.

## Why Placeholder Mode Still Exists

Placeholder mode is intentionally preserved because web asset sourcing is inherently brittle.

Reasons:

- official sites can return `403` or `404`
- product pages can change
- campaign pages may contain misleading imagery
- image search can still miss the exact intended brand visual

Placeholder mode provides a stable fallback when output quality matters more than asset realism.
