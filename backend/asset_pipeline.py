from __future__ import annotations

import base64
import json
import mimetypes
import re
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from PIL import Image

from backend.config import (
    ALLOWED_ASSET_MODES,
    ASSET_DOWNLOAD_MAX_BYTES,
    ASSET_MAX_CANDIDATE_PAGES,
    ASSET_MIN_HEIGHT,
    ASSET_MIN_WIDTH,
    ASSET_REQUEST_TIMEOUT_SECONDS,
    ASSET_WEB_SEARCH_MODEL,
    DEFAULT_ASSET_MODE,
    get_openai_api_key,
)


ASSET_PLACEHOLDER_PATTERN = re.compile(r"asset://([a-z0-9._-]+)")
USER_AGENT = "DesignTransferAssetBot/1.0"
SUPPORTED_PROMPT_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
GENERIC_ASSET_MARKERS = (
    "meta-image",
    "open_graph",
    "open-graph",
    "structured-data",
    "og-image",
    "share-image",
    "social-image",
    "logo",
    "favicon",
    "icon",
)


def resolve_asset_mode(request_mode: str | None) -> str:
    mode = (request_mode or DEFAULT_ASSET_MODE).strip().lower()
    if mode not in ALLOWED_ASSET_MODES:
        raise ValueError(f"Unsupported asset mode: {mode}")
    return mode


def build_asset_manifest(
    *,
    job_dir: Path,
    mode: str,
    source_url: str,
    brand_identity: dict[str, Any],
    ad_image_path: str | None,
) -> dict[str, Any]:
    assets_dir = job_dir / "approved-assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "mode": mode,
        "allowed_domains": [],
        "approved_assets": [],
        "search_query": None,
        "search_queries": [],
        "search_sources": [],
        "fallback_search_query": None,
        "fallback_search_queries": [],
        "fallback_search_sources": [],
        "notes": [],
        "rejections": [],
    }
    discovered_domains = _discover_brand_domains(brand_identity=brand_identity, manifest=manifest)
    allowed_domains = discovered_domains
    manifest["brand_domains"] = discovered_domains
    manifest["allowed_domains"] = allowed_domains

    if mode == "off":
        manifest["notes"].append("Asset sourcing disabled.")
        _write_asset_manifest(job_dir, manifest)
        return manifest

    if mode in {"ad_only", "ad_then_web"} and ad_image_path:
        approved_asset = _build_ad_asset(
            assets_dir=assets_dir,
            ad_image_path=Path(ad_image_path),
            manifest=manifest,
        )
        if approved_asset is not None:
            manifest["approved_assets"].append(approved_asset)
            _write_asset_manifest(job_dir, manifest)
            return manifest

    if mode in {"official_web", "ad_then_web"}:
        approved_asset: dict[str, Any] | None = None
        if not allowed_domains:
            manifest["notes"].append(
                "No trusted brand-owned domain was identified, so brand-specific asset lookup was skipped."
            )
        else:
            try:
                approved_asset = _build_web_asset(
                    assets_dir=assets_dir,
                    brand_identity=brand_identity,
                    allowed_domains=allowed_domains,
                    manifest=manifest,
                )
                if approved_asset is not None:
                    manifest["approved_assets"].append(approved_asset)
            except Exception as exc:
                manifest["rejections"].append(
                    {
                        "source": "official_web",
                        "reason": f"Asset sourcing failed unexpectedly: {exc}",
                    }
                )

        if approved_asset is None:
            manifest["notes"].append(
                "Brand-specific asset lookup did not produce an approved image. Falling back to a generic product search."
            )
            try:
                generic_asset = _build_generic_product_asset(
                    assets_dir=assets_dir,
                    brand_identity=brand_identity,
                    manifest=manifest,
                )
                if generic_asset is not None:
                    manifest["approved_assets"].append(generic_asset)
            except Exception as exc:
                manifest["rejections"].append(
                    {
                        "source": "generic_product_web",
                        "reason": f"Generic product asset sourcing failed unexpectedly: {exc}",
                    }
                )

    if not manifest["approved_assets"]:
        manifest["notes"].append("No approved assets were found. Placeholder mode will be used.")

    _write_asset_manifest(job_dir, manifest)
    return manifest


def load_prompt_assets(asset_manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not asset_manifest:
        return []

    prompt_assets: list[dict[str, Any]] = []
    for asset in asset_manifest.get("approved_assets", []):
        local_path = Path(str(asset.get("local_path") or ""))
        if not local_path.exists():
            continue
        normalized_bytes, normalized_media_type, normalized_width, normalized_height = (
            _normalize_prompt_image_bytes(
                local_path.read_bytes(),
                asset.get("media_type") or "image/png",
            )
        )
        prompt_assets.append(
            {
                "asset_id": asset["asset_id"],
                "label": asset.get("label") or asset["asset_id"],
                "usage_hint": asset.get("usage_hint") or "Use as the primary visual if it fits the layout.",
                "media_type": normalized_media_type,
                "width": normalized_width or asset.get("width"),
                "height": normalized_height or asset.get("height"),
                "bytes": normalized_bytes,
                "source_type": asset.get("source_type"),
                "source_url": asset.get("source_url"),
                "placeholder": asset.get("placeholder") or f"asset://{asset['asset_id']}",
            }
        )
    return prompt_assets


def materialize_asset_placeholders(html: str, prompt_assets: list[dict[str, Any]]) -> str:
    rendered_html = html
    replacements = {
        asset["placeholder"]: _build_data_url(asset["media_type"], asset["bytes"])
        for asset in prompt_assets
    }

    for placeholder, data_url in replacements.items():
        rendered_html = rendered_html.replace(placeholder, data_url)

    unresolved_placeholders = sorted(set(ASSET_PLACEHOLDER_PATTERN.findall(rendered_html)))
    if unresolved_placeholders:
        unresolved_tokens = ", ".join(f"asset://{token}" for token in unresolved_placeholders)
        raise RuntimeError(f"Generated HTML referenced unknown asset placeholders: {unresolved_tokens}")

    return rendered_html


def _write_asset_manifest(job_dir: Path, manifest: dict[str, Any]) -> None:
    (job_dir / "asset-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _build_data_url(media_type: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _normalize_prompt_image_bytes(
    image_bytes: bytes,
    media_type: str,
) -> tuple[bytes, str, int | None, int | None]:
    normalized_media_type = str(media_type or "").lower().strip() or "image/png"
    if normalized_media_type in SUPPORTED_PROMPT_MEDIA_TYPES:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size
        except Exception:
            width = None
            height = None
        return image_bytes, normalized_media_type, width, height

    with Image.open(BytesIO(image_bytes)) as image:
        width, height = image.size
        converted = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
        buffer = BytesIO()
        if converted.mode == "RGBA":
            converted.save(buffer, format="PNG")
            return buffer.getvalue(), "image/png", width, height

        converted.save(buffer, format="PNG")
        return buffer.getvalue(), "image/png", width, height


def _allowed_domain_filters(source_url: str) -> list[str]:
    host = (urlparse(source_url).hostname or "").strip(".").lower()
    if not host:
        return []

    filters = [host]
    if host.startswith("www."):
        filters.append(host[4:])

    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and len(parts[-2]) <= 3:
        filters.append(".".join(parts[-3:]))
    elif len(parts) >= 2:
        filters.append(".".join(parts[-2:]))

    unique_filters: list[str] = []
    for item in filters:
        if item and item not in unique_filters:
            unique_filters.append(item)
    return unique_filters


def _build_search_queries(brand_identity: dict[str, Any], allowed_domains: list[str]) -> list[str]:
    brand_name = str(brand_identity.get("brand_name") or "").strip()
    product_name = str(brand_identity.get("product_name") or "").strip()
    brand_tokens = _tokenize_search_terms(brand_name)
    product_tokens = _build_product_tokens(brand_identity)
    category_phrases = _build_category_focus_terms(brand_identity)

    queries: list[str] = []
    query_candidates: list[str] = []
    for category_phrase in category_phrases[:3]:
        query_candidates.append(" ".join(brand_tokens + [category_phrase]).strip())

    if product_tokens:
        if category_phrases:
            query_candidates.append(
                " ".join(brand_tokens + product_tokens[:2] + [category_phrases[0]]).strip()
            )
        query_candidates.append(" ".join(brand_tokens + product_tokens[:2]).strip())

    if not query_candidates and brand_tokens:
        query_candidates.append(" ".join(brand_tokens).strip())

    for query in query_candidates:
        query = " ".join(query.split()).strip()
        if not query:
            continue
        if query not in queries:
            queries.append(query)

    return queries[:4]


def _build_generic_product_queries(brand_identity: dict[str, Any]) -> list[str]:
    category_phrases = _build_category_focus_terms(brand_identity)
    product_tokens = _build_product_tokens(brand_identity)

    queries: list[str] = []
    query_candidates: list[str] = []

    for category_phrase in category_phrases[:3]:
        query_candidates.append(f"{category_phrase} product image")
        query_candidates.append(f"{category_phrase} product photo")

    if not query_candidates and product_tokens:
        product_phrase = " ".join(product_tokens[:2]).strip()
        if product_phrase:
            query_candidates.append(f"{product_phrase} product image")
            query_candidates.append(f"{product_phrase} product photo")

    for query in query_candidates:
        normalized_query = " ".join(query.split()).strip()
        if normalized_query and normalized_query not in queries:
            queries.append(normalized_query)

    return queries[:4]


def _tokenize_search_terms(value: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-zA-Z0-9]{2,}", str(value or "").strip()):
        lowered = token.lower()
        if lowered not in tokens:
            tokens.append(lowered)
    return tokens


def _openai_web_search(*, query: str, allowed_domains: list[str]) -> list[str]:
    api_key = get_openai_api_key()
    tool_definition: dict[str, Any] = {
        "type": "web_search",
    }
    if allowed_domains:
        tool_definition["filters"] = {"allowed_domains": allowed_domains}

    request_body = {
        "model": ASSET_WEB_SEARCH_MODEL,
        "reasoning": {"effort": "low"},
        "tools": [tool_definition],
        "tool_choice": "auto",
        "include": ["web_search_call.action.sources"],
        "input": query,
    }

    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=ASSET_REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    source_urls: list[str] = []
    for output_item in payload.get("output", []):
        if output_item.get("type") == "web_search_call":
            action = output_item.get("action") or {}
            for source in action.get("sources") or []:
                url = str(source.get("url") or "").strip()
                if url:
                    source_urls.append(url)
        if output_item.get("type") == "message":
            for content_item in output_item.get("content", []):
                for annotation in content_item.get("annotations") or []:
                    citation = annotation.get("url_citation") or {}
                    url = str(citation.get("url") or "").strip()
                    if url:
                        source_urls.append(url)

    unique_urls: list[str] = []
    for url in source_urls:
        if url not in unique_urls:
            unique_urls.append(url)

    return unique_urls[:ASSET_MAX_CANDIDATE_PAGES]


def _fetch_text(url: str) -> tuple[str | None, str | None]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    try:
        with urlopen(request, timeout=ASSET_REQUEST_TIMEOUT_SECONDS) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type:
                return None, f"Page did not return HTML: {url}"
            body = response.read(1_200_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        return None, f"Page request returned HTTP {exc.code}: {url}"
    except URLError as exc:
        return None, f"Page request failed for {url}: {exc.reason}"
    except Exception as exc:
        return None, f"Page request failed for {url}: {exc}"

    return body.decode(charset, errors="ignore"), None


def _extract_image_candidates(
    *,
    page_url: str,
    html: str,
    relevance_tokens: list[str],
    focus_tokens: list[str],
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def add_candidate(url_value: str | None, *, source_kind: str, base_score: int, alt_text: str = "") -> None:
        if not url_value:
            return
        absolute_url = urljoin(page_url, url_value.strip())
        if not absolute_url or absolute_url in seen_urls:
            return
        seen_urls.add(absolute_url)

        score = base_score
        lowered = f"{page_url} {absolute_url} {alt_text}".lower()
        for token in relevance_tokens:
            if token in lowered:
                score += 6
        focus_matches = 0
        for token in focus_tokens:
            if token in lowered:
                score += 12
                focus_matches += 1

        if source_kind.startswith("meta[") and focus_matches == 0:
            score -= 24

        if any(marker in absolute_url.lower() for marker in GENERIC_ASSET_MARKERS) and focus_matches == 0:
            score -= 40

        candidates.append(
            {
                "url": absolute_url,
                "source_kind": source_kind,
                "score": score,
                "alt_text": alt_text,
                "focus_matches": focus_matches,
            }
        )

    meta_candidates = [
        ("meta[property='og:image']", 120),
        ("meta[property='og:image:secure_url']", 118),
        ("meta[name='twitter:image']", 110),
        ("meta[itemprop='image']", 104),
    ]
    for selector, score in meta_candidates:
        tag = soup.select_one(selector)
        add_candidate(tag.get("content") if tag else None, source_kind=selector, base_score=score)

    for image_tag in soup.find_all("img", src=True)[:40]:
        alt_text = " ".join(str(image_tag.get("alt") or "").split()).strip()
        add_candidate(
            image_tag.get("src"),
            source_kind="img",
            base_score=72,
            alt_text=alt_text,
        )
        srcset = str(image_tag.get("srcset") or "")
        for srcset_item in srcset.split(","):
            add_candidate(
                srcset_item.strip().split()[0] if srcset_item.strip() else None,
                source_kind="img[srcset]",
                base_score=70,
                alt_text=alt_text,
            )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def _fetch_image_candidate(url: str) -> tuple[dict[str, Any] | None, str | None]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )

    try:
        with urlopen(request, timeout=ASSET_REQUEST_TIMEOUT_SECONDS) as response:
            content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None, f"Candidate did not return an image: {url}"

            image_bytes = response.read(ASSET_DOWNLOAD_MAX_BYTES + 1)
            if len(image_bytes) > ASSET_DOWNLOAD_MAX_BYTES:
                return None, f"Candidate image exceeded {ASSET_DOWNLOAD_MAX_BYTES} bytes: {url}"
    except HTTPError as exc:
        return None, f"Candidate image request returned HTTP {exc.code}: {url}"
    except URLError as exc:
        return None, f"Candidate image request failed for {url}: {exc.reason}"
    except Exception as exc:
        return None, f"Candidate image request failed for {url}: {exc}"

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            image_format = str(image.format or "").upper()
    except Exception as exc:
        return None, f"Candidate image could not be decoded ({url}): {exc}"

    if width < ASSET_MIN_WIDTH or height < ASSET_MIN_HEIGHT:
        return None, f"Candidate image was too small ({width}x{height}): {url}"

    aspect_ratio = width / max(height, 1)
    if aspect_ratio < 0.35 or aspect_ratio > 3.5:
        return None, f"Candidate image had an unusable aspect ratio ({aspect_ratio:.2f}): {url}"

    inferred_media_type = Image.MIME.get(image_format)
    media_type = inferred_media_type or content_type or "image/png"
    image_bytes, media_type, width, height = _normalize_prompt_image_bytes(image_bytes, media_type)

    return {
        "bytes": image_bytes,
        "media_type": media_type,
        "width": width,
        "height": height,
        "image_format": image_format,
        "url": url,
    }, None


def _save_asset(
    *,
    assets_dir: Path,
    asset_id: str,
    label: str,
    usage_hint: str,
    source_type: str,
    source_url: str | None,
    image_payload: dict[str, Any],
) -> dict[str, Any]:
    extension = mimetypes.guess_extension(image_payload["media_type"]) or ".bin"
    local_path = assets_dir / f"{asset_id}{extension}"
    local_path.write_bytes(image_payload["bytes"])

    return {
        "asset_id": asset_id,
        "placeholder": f"asset://{asset_id}",
        "label": label,
        "usage_hint": usage_hint,
        "source_type": source_type,
        "source_url": source_url,
        "local_path": str(local_path),
        "media_type": image_payload["media_type"],
        "width": image_payload["width"],
        "height": image_payload["height"],
        "size_bytes": len(image_payload["bytes"]),
    }


def _build_ad_asset(
    *,
    assets_dir: Path,
    ad_image_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    if not ad_image_path.exists():
        manifest["rejections"].append({"source": "ad_upload", "reason": "Uploaded ad file was missing."})
        return None

    try:
        image_bytes = ad_image_path.read_bytes()
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            image_format = str(image.format or "").upper()
        media_type = Image.MIME.get(image_format) or mimetypes.guess_type(ad_image_path.name)[0] or "image/png"
        image_bytes, media_type, width, height = _normalize_prompt_image_bytes(image_bytes, media_type)
        image_payload = {
            "bytes": image_bytes,
            "media_type": media_type,
            "width": width,
            "height": height,
            "image_format": image_format,
            "url": str(ad_image_path),
        }
        error = None
        if len(image_bytes) > ASSET_DOWNLOAD_MAX_BYTES:
            error = f"Uploaded ad image exceeded {ASSET_DOWNLOAD_MAX_BYTES} bytes."
        elif width < ASSET_MIN_WIDTH or height < ASSET_MIN_HEIGHT:
            error = f"Uploaded ad image was too small ({width}x{height})."
        else:
            aspect_ratio = width / max(height, 1)
            if aspect_ratio < 0.35 or aspect_ratio > 3.5:
                error = f"Uploaded ad image had an unusable aspect ratio ({aspect_ratio:.2f})."
    except Exception as exc:
        image_payload = None
        error = f"Uploaded ad image could not be decoded: {exc}"

    if error or image_payload is None:
        manifest["rejections"].append({"source": "ad_upload", "reason": error or "Ad asset rejected."})
        return None

    manifest["notes"].append("Using the uploaded ad image as the approved asset.")
    return _save_asset(
        assets_dir=assets_dir,
        asset_id="primary_visual",
        label="Primary visual",
        usage_hint="Use this only if the full creative fits naturally as a branded visual block.",
        source_type="ad_upload",
        source_url=str(ad_image_path),
        image_payload=image_payload,
    )


def _build_web_asset(
    *,
    assets_dir: Path,
    brand_identity: dict[str, Any],
    allowed_domains: list[str],
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    relevance_tokens = _build_relevance_tokens(brand_identity)
    focus_tokens = _build_focus_tokens(brand_identity)
    search_queries = _build_search_queries(brand_identity, allowed_domains)
    manifest["search_queries"] = search_queries
    manifest["search_query"] = search_queries[0] if search_queries else None
    source_urls = _seed_source_pages(allowed_domains)

    aggregated_search_sources: list[str] = []
    for query in search_queries:
        try:
            searched_urls = _openai_web_search(query=query, allowed_domains=allowed_domains)
            if searched_urls:
                manifest["search_query"] = query
            for url in searched_urls:
                if url not in aggregated_search_sources:
                    aggregated_search_sources.append(url)
                if url not in source_urls:
                    source_urls.append(url)
        except Exception as exc:
            manifest["rejections"].append(
                {"source": f"official_web:{query}", "reason": f"OpenAI web search failed: {exc}"}
            )

    manifest["search_sources"] = aggregated_search_sources
    if search_queries:
        manifest["notes"].append(f"OpenAI web search model: {ASSET_WEB_SEARCH_MODEL}")

    return _approve_asset_from_source_pages(
        assets_dir=assets_dir,
        brand_identity=brand_identity,
        source_urls=source_urls,
        relevance_tokens=relevance_tokens,
        focus_tokens=focus_tokens,
        manifest=manifest,
        source_key="official_web",
        approval_note="Approved official web asset",
        rejection_reason="No official web image passed the asset checks.",
        usage_hint="Use this as the main product or hero image if it strengthens the composition.",
        source_type="official_web",
        scorer=_score_url_relevance,
    )


def _build_generic_product_asset(
    *,
    assets_dir: Path,
    brand_identity: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    relevance_tokens = _build_generic_relevance_tokens(brand_identity)
    focus_tokens = _build_focus_tokens(brand_identity)
    search_queries = _build_generic_product_queries(brand_identity)
    manifest["fallback_search_queries"] = search_queries
    manifest["fallback_search_query"] = search_queries[0] if search_queries else None

    source_urls: list[str] = []
    aggregated_search_sources: list[str] = []
    for query in search_queries:
        try:
            searched_urls = _openai_web_search(query=query, allowed_domains=[])
            if searched_urls:
                manifest["fallback_search_query"] = query
            for url in searched_urls:
                if url not in aggregated_search_sources:
                    aggregated_search_sources.append(url)
                host = (urlparse(url).hostname or "").lower().strip(".")
                if not host or _is_blocked_discovery_domain(host):
                    manifest["rejections"].append(
                        {
                            "source": url,
                            "reason": "Generic product search result came from a blocked or unsupported domain.",
                        }
                    )
                    continue
                if url not in source_urls:
                    source_urls.append(url)
        except Exception as exc:
            manifest["rejections"].append(
                {
                    "source": f"generic_product_web:{query}",
                    "reason": f"OpenAI web search failed: {exc}",
                }
            )

    manifest["fallback_search_sources"] = aggregated_search_sources
    if search_queries:
        manifest["notes"].append(
            f"Generic product fallback used OpenAI web search model: {ASSET_WEB_SEARCH_MODEL}"
        )

    return _approve_asset_from_source_pages(
        assets_dir=assets_dir,
        brand_identity=brand_identity,
        source_urls=source_urls,
        relevance_tokens=relevance_tokens,
        focus_tokens=focus_tokens,
        manifest=manifest,
        source_key="generic_product_web",
        approval_note="Approved generic product fallback asset",
        rejection_reason="No generic product image passed the asset checks.",
        usage_hint=(
            "Use this as a neutral product visual only when no brand-specific approved asset was available."
        ),
        source_type="generic_product_web",
        scorer=_score_generic_url_relevance,
    )


def _approve_asset_from_source_pages(
    *,
    assets_dir: Path,
    brand_identity: dict[str, Any],
    source_urls: list[str],
    relevance_tokens: list[str],
    focus_tokens: list[str],
    manifest: dict[str, Any],
    source_key: str,
    approval_note: str,
    rejection_reason: str,
    usage_hint: str,
    source_type: str,
    scorer: Any,
) -> dict[str, Any] | None:
    if not source_urls:
        manifest["rejections"].append(
            {"source": source_key, "reason": "No source pages were available for asset extraction."}
        )
        return None

    ranked_source_urls = sorted(
        source_urls,
        key=lambda candidate_url: scorer(candidate_url, brand_identity),
        reverse=True,
    )

    for page_url in ranked_source_urls[:ASSET_MAX_CANDIDATE_PAGES]:
        html, page_error = _fetch_text(page_url)
        if page_error or html is None:
            manifest["rejections"].append({"source": page_url, "reason": page_error or "Page fetch failed."})
            continue

        candidates = _extract_image_candidates(
            page_url=page_url,
            html=html,
            relevance_tokens=relevance_tokens,
            focus_tokens=focus_tokens,
        )
        if not candidates:
            manifest["rejections"].append({"source": page_url, "reason": "No image candidates found on page."})
            continue

        for candidate in candidates[:14]:
            image_payload, image_error = _fetch_image_candidate(candidate["url"])
            if image_error or image_payload is None:
                manifest["rejections"].append(
                    {"source": candidate["url"], "reason": image_error or "Image candidate rejected."}
                )
                continue

            manifest["notes"].append(f"{approval_note} from {page_url}")
            return _save_asset(
                assets_dir=assets_dir,
                asset_id="primary_visual",
                label="Primary visual",
                usage_hint=usage_hint,
                source_type=source_type,
                source_url=candidate["url"],
                image_payload=image_payload,
            )

    manifest["rejections"].append({"source": source_key, "reason": rejection_reason})
    return None


def _seed_source_pages(allowed_domains: list[str]) -> list[str]:
    seeded_urls: list[str] = []
    for domain in allowed_domains[:3]:
        domain_variants = [domain]
        if domain.startswith("www."):
            domain_variants.append(domain[4:])
        else:
            domain_variants.append(f"www.{domain}")

        for variant in domain_variants:
            candidate = f"https://{variant}/"
            if candidate not in seeded_urls:
                seeded_urls.append(candidate)
    return seeded_urls


def _build_relevance_tokens(brand_identity: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for raw_value in [
        brand_identity.get("brand_name"),
        brand_identity.get("product_name"),
        brand_identity.get("product_category"),
    ]:
        for token in re.findall(r"[a-zA-Z0-9]{4,}", str(raw_value or "").lower()):
            if token not in tokens:
                tokens.append(token)
    for token in _build_focus_tokens(brand_identity):
        if len(token) >= 3 and token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _build_generic_relevance_tokens(brand_identity: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for raw_value in [brand_identity.get("product_name"), brand_identity.get("product_category")]:
        for token in re.findall(r"[a-zA-Z0-9]{3,}", str(raw_value or "").lower()):
            if token not in tokens:
                tokens.append(token)
    for token in _build_focus_tokens(brand_identity):
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _discover_brand_domains(*, brand_identity: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    extracted_domains = _extract_domains_from_brand_identity(brand_identity)
    if extracted_domains:
        manifest["notes"].append(
            f"Using brand domains extracted from brand JSON: {', '.join(extracted_domains)}"
        )
        return extracted_domains

    brand_name = str(brand_identity.get("brand_name") or "").strip()
    discovery_queries: list[str] = []
    primary_query_parts = _tokenize_search_terms(brand_name)
    primary_query_parts.extend(
        term
        for term in _build_category_focus_terms(brand_identity)[:2]
        if term not in primary_query_parts
    )
    primary_query_parts.extend(["official", "site"])
    primary_query = " ".join(primary_query_parts).strip()
    if primary_query:
        discovery_queries.append(primary_query)

    brand_only_query_parts = _tokenize_search_terms(brand_name)
    brand_only_query_parts.extend(["official", "site"])
    brand_only_query = " ".join(brand_only_query_parts).strip()
    if brand_only_query and brand_only_query not in discovery_queries:
        discovery_queries.append(brand_only_query)

    source_urls: list[str] = []
    for query in discovery_queries:
        try:
            for url in _openai_web_search(query=query, allowed_domains=[]):
                if url not in source_urls:
                    source_urls.append(url)
        except Exception as exc:
            manifest["rejections"].append(
                {
                    "source": f"brand_domain_discovery:{query}",
                    "reason": f"OpenAI domain discovery failed: {exc}",
                }
            )

    candidate_domains: list[str] = []
    brand_tokens = _build_brand_tokens(brand_identity)
    for url in source_urls:
        host = (urlparse(url).hostname or "").lower().strip(".")
        if not host or _is_blocked_discovery_domain(host):
            continue
        if brand_tokens and not _host_matches_brand_tokens(host, brand_tokens):
            continue
        if host.startswith("www."):
            host = host[4:]
        if host not in candidate_domains:
            candidate_domains.append(host)

    brand_root_guesses = _build_brand_root_domain_guesses(brand_identity)
    for host in brand_root_guesses:
        if host not in candidate_domains:
            candidate_domains.append(host)

    candidate_domains.sort(
        key=lambda host: _score_host_relevance(host, brand_identity),
        reverse=True,
    )
    candidate_domains = _merge_priority_domains(
        candidate_domains,
        priority_domains=brand_root_guesses,
    )

    if candidate_domains:
        manifest["notes"].append(
            f"Using brand domains discovered via OpenAI web search: {', '.join(candidate_domains[:3])}"
        )
    else:
        manifest["rejections"].append(
            {"source": "brand_domain_discovery", "reason": "No official brand domain could be identified."}
        )

    return candidate_domains[:5]


def _extract_domains_from_brand_identity(brand_identity: dict[str, Any]) -> list[str]:
    brand_tokens = _build_brand_tokens(brand_identity)
    searchable_text = "\n".join(
        [
            str(brand_identity.get("brand_name") or ""),
            str(brand_identity.get("product_name") or ""),
            str(brand_identity.get("product_category") or ""),
            str(brand_identity.get("tagline") or ""),
            json.dumps(brand_identity.get("copy_text") or {}),
            str(brand_identity.get("visual_style") or ""),
            str(brand_identity.get("logo_description") or ""),
        ]
    )

    candidate_domains: list[str] = []
    for raw_match in re.findall(r"(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}", searchable_text, re.IGNORECASE):
        host = raw_match.lower().strip().strip(".,;:/")
        host = host.replace("https://", "").replace("http://", "")
        if host.startswith("www."):
            host = host[4:]
        if not host or _is_blocked_discovery_domain(host):
            continue
        if brand_tokens and not _host_matches_brand_tokens(host, brand_tokens):
            continue
        if host not in candidate_domains:
            candidate_domains.append(host)

    candidate_domains.sort(
        key=lambda host: _score_host_relevance(host, brand_identity),
        reverse=True,
    )
    return candidate_domains[:5]


def _build_brand_tokens(brand_identity: dict[str, Any]) -> list[str]:
    brand_name = str(brand_identity.get("brand_name") or "").lower()
    tokens: list[str] = []
    compact_brand_name = re.sub(r"[^a-z0-9]+", " ", brand_name)
    for token in re.findall(r"[a-z0-9]{2,}", compact_brand_name):
        if token not in tokens:
            tokens.append(token)
    return tokens[:5]


def _build_brand_root_domain_guesses(brand_identity: dict[str, Any]) -> list[str]:
    brand_name = str(brand_identity.get("brand_name") or "").lower()
    compact = "".join(re.findall(r"[a-z0-9]+", brand_name))
    guesses: list[str] = []
    if len(compact) >= 2:
        guesses.append(f"{compact}.com")
    return guesses[:2]


def _merge_priority_domains(candidate_domains: list[str], *, priority_domains: list[str]) -> list[str]:
    if not priority_domains:
        return candidate_domains

    merged: list[str] = []
    if candidate_domains:
        merged.append(candidate_domains[0])

    for host in priority_domains:
        if host not in merged:
            merged.append(host)

    for host in candidate_domains[1:]:
        if host not in merged:
            merged.append(host)

    return merged


def _host_matches_brand_tokens(host: str, brand_tokens: list[str]) -> bool:
    normalized_host = host[4:] if host.startswith("www.") else host
    host_labels = re.findall(r"[a-z0-9]+", normalized_host)
    if not host_labels:
        return False

    for token in brand_tokens:
        for label in host_labels:
            if len(token) <= 3:
                if label == token:
                    return True
            else:
                if label == token or label.startswith(token) or token in label:
                    return True
    return False


def _build_product_tokens(brand_identity: dict[str, Any]) -> list[str]:
    brand_tokens = _build_brand_tokens(brand_identity)
    product_name = str(brand_identity.get("product_name") or "").lower()
    product_tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]{3,}", product_name):
        if token not in brand_tokens and token not in product_tokens:
            product_tokens.append(token)
    return product_tokens[:6]


def _build_category_focus_terms(brand_identity: dict[str, Any]) -> list[str]:
    product_category = str(brand_identity.get("product_category") or "").lower()
    category_text = " ".join(product_category.split())
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = " ".join(term.lower().split()).strip()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)

    if "air condition" in category_text or "air-condition" in category_text or "hvac" in category_text:
        add("air conditioner")
        add("ac")
        add("air conditioners")
    if "refrigerator" in category_text or "fridge" in category_text:
        add("refrigerator")
        add("fridge")
    if "washing" in category_text and "machine" in category_text:
        add("washing machine")
    if "television" in category_text or re.search(r"\btv\b", category_text):
        add("tv")
        add("television")

    raw_tokens = [
        token
        for token in re.findall(r"[a-z0-9]{2,}", category_text)
        if token not in {"home", "product", "products", "category", "range"}
    ]
    if raw_tokens:
        add(" ".join(raw_tokens[:3]))
    for token in raw_tokens[:4]:
        add(token)

    return terms[:6]


def _build_focus_tokens(brand_identity: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for term in _build_category_focus_terms(brand_identity):
        for token in re.findall(r"[a-z0-9]{2,}", term.lower()):
            if token not in tokens:
                tokens.append(token)
    for token in _build_product_tokens(brand_identity):
        if token not in tokens:
            tokens.append(token)
    return tokens[:10]


def _score_host_relevance(host: str, brand_identity: dict[str, Any]) -> int:
    normalized_host = host[4:] if host.startswith("www.") else host
    host_labels = re.findall(r"[a-z0-9]+", normalized_host)
    if not host_labels:
        return 0

    brand_tokens = _build_brand_tokens(brand_identity)
    product_tokens = _build_product_tokens(brand_identity)
    focus_tokens = _build_focus_tokens(brand_identity)
    score = 0

    for token in brand_tokens:
        for label in host_labels:
            if len(token) <= 3:
                if label == token:
                    score += 12
            elif label == token or label.startswith(token) or token in label:
                score += 12

    for token in product_tokens:
        for label in host_labels:
            if label == token:
                score += 28
            elif token in label or label in token:
                score += 18

    for token in focus_tokens:
        for label in host_labels:
            if len(token) <= 2:
                if label == token:
                    score += 10
            elif label == token or token in label or label in token:
                score += 10

    return score


def _score_url_relevance(url: str, brand_identity: dict[str, Any]) -> int:
    parsed = urlparse(url)
    host_score = _score_host_relevance(parsed.hostname or "", brand_identity)
    path_text = f"{parsed.path} {parsed.query}".lower()
    product_tokens = _build_product_tokens(brand_identity)
    focus_tokens = _build_focus_tokens(brand_identity)
    score = host_score

    for token in product_tokens:
        if f"/{token}" in path_text or f"-{token}" in path_text:
            score += 20
        elif token in path_text:
            score += 10

    for token in focus_tokens:
        if len(token) <= 2:
            if re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", path_text):
                score += 14
        elif f"/{token}" in path_text or f"-{token}" in path_text:
            score += 16
        elif token in path_text:
            score += 8

    return score


def _score_generic_url_relevance(url: str, brand_identity: dict[str, Any]) -> int:
    parsed = urlparse(url)
    path_text = f"{parsed.path} {parsed.query}".lower()
    host_text = (parsed.hostname or "").lower()
    combined_text = f"{host_text} {path_text}"
    category_phrases = _build_category_focus_terms(brand_identity)
    focus_tokens = _build_focus_tokens(brand_identity)
    score = 0

    for phrase in category_phrases:
        if phrase in combined_text:
            score += 28

    for token in focus_tokens:
        if len(token) <= 2:
            if re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", combined_text):
                score += 8
        elif f"/{token}" in path_text or f"-{token}" in path_text:
            score += 14
        elif token in combined_text:
            score += 8

    if any(marker in combined_text for marker in ("product", "products", "shop", "category", "gallery", "image")):
        score += 4

    return score


def _is_blocked_discovery_domain(host: str) -> bool:
    blocked_markers = (
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "youtube.com",
        "x.com",
        "twitter.com",
        "wikipedia.org",
        "amazon.",
        "flipkart.com",
        "myntra.com",
        "ebay.",
        "play.google.com",
        "apps.apple.com",
        "music.apple.com",
        "podcasts.apple.com",
        "support.",
        "bookmyshow.com",
        "ticketmaster.",
        "eventbrite.",
        "stubhub.",
        "bandsintown.com",
        "songkick.com",
        "spares",
        "spare",
        "parts",
    )
    return any(marker in host for marker in blocked_markers)
