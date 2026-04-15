# Design Transfer

Design Transfer converts a reference webpage into a new branded output using a simple operator flow:

1. Process a URL
2. Upload and process an ad creative
3. Convert into a final branded HTML output
4. Download only the final output HTML

The app is intentionally optimized for output quality over pipeline cleverness. It keeps the flow simple, uses strong multimodal models for the heavy lifting, and adds guardrails around generated HTML so the result is usable instead of merely interesting.

## Quick Start

Run:

```bat
launch_app.bat
```

The launcher will:

- create or reuse the Python virtual environment
- install backend dependencies
- install Playwright Chromium
- install frontend dependencies
- reclaim local dev ports `8765` and `5173` if another process is occupying them
- start the backend at `http://127.0.0.1:8765`
- start the frontend at `http://127.0.0.1:5173`
- open the browser once the frontend responds

API keys can be supplied either through environment variables or the local text files already supported by the project:

- `ANT API KEY.txt`
- `OAI API KEY.txt`

## Stack

- Frontend: React 18 + TypeScript + Vite
- Backend: FastAPI + Pydantic
- Scraping and validation: Playwright
- Primary generation models: Anthropic Claude
- Web asset discovery: OpenAI Responses API web search

## Documentation

Detailed project documentation lives in [docs/README.md](</C:/Users/91956/Desktop/assignment final/docs/README.md>).

The docs cover:

- how the product works
- architecture and API flow
- models used and why
- UI and UX behavior
- guardrails and hallucination handling
- local launcher behavior
- lessons learned and troubleshooting

## Current Status

The current app supports:

- above-the-fold webpage scraping
- single-request reconstruction with one automated repair pass
- ad brand extraction
- optional searched assets or placeholder-only mode
- final reskin generation
- output preview and output HTML download

Reconstruction HTML is still produced internally as part of the pipeline, but the UI now exposes only the final output file.
