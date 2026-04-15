# Models and Prompting Strategy

## Model Allocation

The current model choices are defined in [backend/config.py](</C:/Users/91956/Desktop/assignment final/backend/config.py>).

### Reconstruction

- Model: `claude-opus-4-6`
- File: [backend/reconstructor.py](</C:/Users/91956/Desktop/assignment final/backend/reconstructor.py>)

Why:

- reconstruction is the most layout-sensitive code generation step
- it needs high-quality multimodal reasoning from screenshot plus DOM and stylesheet hints
- the goal is chat-quality HTML output rather than structured extraction for a separate patching engine

### Brand Extraction

- Model: `claude-sonnet-4-6`
- File: [backend/brand_extractor.py](</C:/Users/91956/Desktop/assignment final/backend/brand_extractor.py>)

Why:

- this step is classification and normalization rather than long-form code generation
- Sonnet is cheaper and still strong for visual JSON extraction

### Reskin Generation

- Model: `claude-opus-4-6`
- File: [backend/reskinner.py](</C:/Users/91956/Desktop/assignment final/backend/reskinner.py>)

Why:

- this is the highest-value output stage
- it edits and reinterprets the reconstructed page into a polished branded result
- output quality matters more than squeezing cost at this stage

### Asset Discovery

- Model: `gpt-5` via OpenAI Responses API web search
- File: [backend/asset_pipeline.py](</C:/Users/91956/Desktop/assignment final/backend/asset_pipeline.py>)

Why:

- this stage is not generating final HTML
- it is used to find candidate source pages for approved imagery
- the OpenAI web search tool is useful for retrieving pages rather than writing the page itself

## Prompting Philosophy

The product deliberately avoids a brittle JSON-edit pipeline for code generation.

Instead, it uses:

- strong multimodal models
- direct full-document HTML generation
- explicit constraints
- post-generation validation
- one repair pass when needed

This matches the practical insight that chat-quality code generation worked better than extraction-plus-manual-edit orchestration.

## Reconstruction Prompt Strategy

The reconstruction prompt combines:

- screenshot input
- summarized visible DOM context
- summarized stylesheet hints
- viewport information
- source URL

It tells the model to:

- return one complete self-contained HTML document
- use inline CSS only
- avoid JavaScript
- avoid external assets
- preserve visible composition
- use placeholders when imagery is unclear
- include a matching footer so the page feels complete

## Brand Extraction Prompt Strategy

The brand-extraction prompt tells the model to return only valid JSON in a fixed schema.

It explicitly instructs the model to:

- use only visually supported details
- return `null` when unclear
- avoid invented claims
- infer colors conservatively
- keep descriptive fields non-speculative

That JSON is then normalized through [backend/brand_schema.py](</C:/Users/91956/Desktop/assignment final/backend/brand_schema.py>) so the app does not continue with malformed brand data.

## Reskin Prompt Strategy

The reskin prompt combines:

- source screenshot
- reconstructed base HTML
- normalized brand identity JSON
- viewport
- source URL
- optional approved assets

It tells the model to:

- preserve structure and hierarchy
- rewrite copy and visual identity
- use either site colors or ad colors depending on operator choice
- use only approved assets when assets are supplied
- avoid inventing external image URLs
- stay self-contained
- include a visually matching footer

## Color Strategy Options

The UI exposes two modes:

- `preserve_site`
- `use_ad`

Behavior:

- `preserve_site` keeps the source page's color logic and injects the new brand mostly through copy, typography feel, and product framing
- `use_ad` applies the palette extracted from the ad analysis

## Asset Strategy Options

The UI maps asset behavior to two operator choices:

- placeholder assets
- searched assets

Current backend mapping:

- placeholder assets -> `asset_mode = off`
- searched assets -> `asset_mode = official_web`

The backend still supports more modes internally:

- `off`
- `ad_only`
- `official_web`
- `ad_then_web`

## Prompting Decisions That Came From Experience

Several prompt constraints were added because they solved real issues seen during implementation:

- "Do not reference external assets" because generated pages broke when remote URLs failed
- "Use placeholders when uncertain" because guessed visuals looked worse than restrained placeholders
- "Do not invent missing details" because speculative brand claims reduce trust
- "Include a matching footer" because otherwise outputs could look incomplete
- "Use exact asset placeholders" because model-generated asset IDs and URLs are unreliable without enforcement
