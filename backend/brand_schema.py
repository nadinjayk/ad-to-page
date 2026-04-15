from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


HEX_COLOR_PATTERN = re.compile(r"^#?(?P<digits>[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _normalize_required_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return text


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _normalize_hex_color(value: Any, *, field_name: str, allow_none: bool = False) -> str | None:
    if value is None or str(value).strip() == "":
        if allow_none:
            return None
        raise ValueError(f"{field_name} must be a hex color.")

    match = HEX_COLOR_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"{field_name} must be a 3-digit or 6-digit hex color.")

    digits = match.group("digits").upper()
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)

    return f"#{digits}"


class BrandCopyText(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str
    subheadline: str | None = None
    cta: str | None = None
    body: str | None = None

    @field_validator("headline", mode="before")
    @classmethod
    def validate_headline(cls, value: Any) -> str:
        return _normalize_required_text(value, field_name="copy_text.headline")

    @field_validator("subheadline", "cta", "body", mode="before")
    @classmethod
    def validate_optional_text(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class BrandIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    brand_name: str
    tagline: str | None = None
    primary_color: str
    secondary_color: str
    accent_color: str | None = None
    background_color: str
    text_color: str
    font_style: str
    product_category: str
    product_name: str | None = None
    copy_text: BrandCopyText
    visual_style: str
    logo_description: str

    @field_validator("brand_name", "font_style", "product_category", "visual_style", "logo_description", mode="before")
    @classmethod
    def validate_required_text(cls, value: Any, info: Any) -> str:
        return _normalize_required_text(value, field_name=info.field_name)

    @field_validator("tagline", "product_name", mode="before")
    @classmethod
    def validate_optional_text(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("primary_color", "secondary_color", "background_color", "text_color", mode="before")
    @classmethod
    def validate_required_color(cls, value: Any, info: Any) -> str:
        normalized = _normalize_hex_color(value, field_name=info.field_name)
        assert isinstance(normalized, str)
        return normalized

    @field_validator("accent_color", mode="before")
    @classmethod
    def validate_optional_color(cls, value: Any) -> str | None:
        return _normalize_hex_color(value, field_name="accent_color", allow_none=True)


def normalize_brand_identity(data: dict[str, Any]) -> dict[str, Any]:
    return BrandIdentity.model_validate(data).model_dump(mode="json")
