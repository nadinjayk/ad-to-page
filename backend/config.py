from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
JOBS_DIR = BACKEND_DIR / "jobs"
ANTHROPIC_KEY_FILE = ROOT_DIR / "ANT API KEY.txt"
OPENAI_KEY_FILE = ROOT_DIR / "OAI API KEY.txt"
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
ALLOWED_VIEWPORT_PRESETS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 1024, "height": 768},
    "mobile": {"width": 390, "height": 844},
}
ALLOWED_ASSET_MODES = {"off", "ad_only", "official_web", "ad_then_web"}
DEFAULT_ASSET_MODE = os.getenv("ASSET_MODE", "official_web").strip().lower() or "official_web"
ASSET_WEB_SEARCH_MODEL = os.getenv("ASSET_WEB_SEARCH_MODEL", "gpt-5").strip() or "gpt-5"
ASSET_DOWNLOAD_MAX_BYTES = int(os.getenv("ASSET_DOWNLOAD_MAX_BYTES", "1800000"))
ASSET_MIN_WIDTH = int(os.getenv("ASSET_MIN_WIDTH", "420"))
ASSET_MIN_HEIGHT = int(os.getenv("ASSET_MIN_HEIGHT", "260"))
ASSET_MAX_CANDIDATE_PAGES = int(os.getenv("ASSET_MAX_CANDIDATE_PAGES", "6"))
ASSET_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ASSET_REQUEST_TIMEOUT_SECONDS", "45"))
ANTHROPIC_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", "180"))
ANTHROPIC_MAX_RETRIES = int(os.getenv("ANTHROPIC_MAX_RETRIES", "4"))
ANTHROPIC_INITIAL_RETRY_DELAY_SECONDS = float(
    os.getenv("ANTHROPIC_INITIAL_RETRY_DELAY_SECONDS", "2.0")
)
ANTHROPIC_MAX_RETRY_DELAY_SECONDS = float(
    os.getenv("ANTHROPIC_MAX_RETRY_DELAY_SECONDS", "12.0")
)
RECONSTRUCTION_MODEL = "claude-opus-4-6"
RECONSTRUCTION_MAX_TOKENS = 20000
BRAND_EXTRACTION_MODEL = "claude-sonnet-4-6"
BRAND_EXTRACTION_MAX_TOKENS = 4000
RESKIN_MODEL = "claude-opus-4-6"
RESKIN_MAX_TOKENS = 20000


def get_anthropic_api_key() -> str:
    env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key

    if ANTHROPIC_KEY_FILE.exists():
        file_key = ANTHROPIC_KEY_FILE.read_text(encoding="utf-8").strip()
        if file_key:
            return file_key

    raise RuntimeError(
        "Anthropic API key not found. Set ANTHROPIC_API_KEY or place the key in 'ANT API KEY.txt'."
    )


def get_openai_api_key() -> str:
    env_key = os.getenv("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    if OPENAI_KEY_FILE.exists():
        file_key = OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
        if file_key:
            return file_key

    raise RuntimeError(
        "OpenAI API key not found. Set OPENAI_API_KEY or place the key in 'OAI API KEY.txt'."
    )
