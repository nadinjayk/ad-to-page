from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator

from backend.config import ALLOWED_ASSET_MODES, DEFAULT_ASSET_MODE, DEFAULT_VIEWPORT


class ScrapeRequest(BaseModel):
    url: HttpUrl
    viewport_width: int = Field(default=DEFAULT_VIEWPORT["width"], ge=320, le=2560)
    viewport_height: int = Field(default=DEFAULT_VIEWPORT["height"], ge=320, le=2560)

    @field_validator("url")
    @classmethod
    def allow_http_https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme not in {"http", "https"}:
            raise ValueError("Only http and https URLs are supported.")
        return value


class VisibleElement(BaseModel):
    tag: str
    classes: str
    text: str
    styles: dict[str, Any]
    rect: dict[str, Any]


class ScrapeArtifacts(BaseModel):
    title: str
    html: str
    viewportWidth: int
    viewportHeight: int
    visibleElements: list[VisibleElement]
    fonts: list[dict[str, Any]]


class StylesheetDump(BaseModel):
    href: str | None = None
    rules: list[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    url: str
    viewport: dict[str, int]
    status: str = "created"
    screenshot_path: str | None = None
    dom_data: dict[str, Any] | None = None
    styles_data: list[dict[str, Any]] | None = None
    reconstruction_html_path: str | None = None
    reconstruction_model: str | None = None
    reconstruction_input_tokens: int | None = None
    reconstruction_output_tokens: int | None = None
    ad_image_path: str | None = None
    brand_identity: dict[str, Any] | None = None
    brand_extraction_model: str | None = None
    brand_extraction_input_tokens: int | None = None
    brand_extraction_output_tokens: int | None = None
    asset_mode: str = DEFAULT_ASSET_MODE
    asset_manifest: dict[str, Any] | None = None
    reskinned_html_path: str | None = None
    reskin_model: str | None = None
    reskin_input_tokens: int | None = None
    reskin_output_tokens: int | None = None
    error_message: str | None = None


class ScrapeResponse(BaseModel):
    job_id: str
    status: str
    screenshot_url: str
    dom_url: str
    styles_url: str
    title: str
    visible_element_count: int
    viewport: dict[str, int]


class ReconstructRequest(BaseModel):
    job_id: str = Field(min_length=1)


class ReconstructResponse(BaseModel):
    job_id: str
    status: str
    html_url: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class BrandExtractResponse(BaseModel):
    job_id: str
    status: str
    ad_image_url: str
    brand_identity: dict[str, Any]
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ReskinRequest(BaseModel):
    job_id: str = Field(min_length=1)
    brand_identity: dict[str, Any]
    asset_mode: str | None = None
    color_strategy: str = "use_ad"

    @field_validator("asset_mode")
    @classmethod
    def validate_asset_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in ALLOWED_ASSET_MODES:
            raise ValueError(
                f"asset_mode must be one of: {', '.join(sorted(ALLOWED_ASSET_MODES))}."
            )
        return normalized

    @field_validator("color_strategy")
    @classmethod
    def validate_color_strategy(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"preserve_site", "use_ad"}:
            raise ValueError("color_strategy must be one of: preserve_site, use_ad.")
        return normalized


class ReskinResponse(BaseModel):
    job_id: str
    status: str
    html_url: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
