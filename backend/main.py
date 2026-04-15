from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from pathlib import Path
import sys
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend.asset_pipeline import (
    build_asset_manifest,
    load_prompt_assets,
    materialize_asset_placeholders,
    resolve_asset_mode,
)
from backend.brand_extractor import extract_brand_identity
from backend.brand_schema import normalize_brand_identity
from backend.config import JOBS_DIR
from backend.html_guardrails import (
    GuardrailReport,
    build_guardrail_failure_summary,
    smoke_test_html_document,
    validate_footer_presence,
    validate_required_asset_usage,
    validate_html_document,
)
from backend.models import (
    BrandExtractResponse,
    JobRecord,
    ReconstructRequest,
    ReconstructResponse,
    ReskinRequest,
    ReskinResponse,
    ScrapeRequest,
    ScrapeResponse,
)
from backend.reconstructor import (
    reconstruct_html_document,
    repair_reconstructed_html_document,
)
from backend.reskinner import (
    repair_reskinned_html_document,
    reskin_html_document,
)
from backend.scraper import persist_job_artifacts, scrape_above_the_fold


if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


app = FastAPI(title="Design Transfer API", version="0.1.0")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=JOBS_DIR), name="files")


def write_job_record(job_dir: Path, record: JobRecord) -> None:
    (job_dir / "job.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")


def read_job_record(job_id: str) -> tuple[JobRecord, Path]:
    job_dir = JOBS_DIR / job_id
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail="Job not found.")

    data = json.loads(job_file.read_text(encoding="utf-8"))
    return JobRecord.model_validate(data), job_dir


def write_json_report(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sum_token_counts(*values: int | None) -> int | None:
    present_values = [value for value in values if value is not None]
    if not present_values:
        return None
    return sum(present_values)


async def run_html_guardrails(*, html: str, viewport: dict[str, int]) -> list[GuardrailReport]:
    reports: list[GuardrailReport] = [
        validate_html_document(html),
        validate_footer_presence(html),
    ]
    if all(report.passed for report in reports):
        reports.append(await smoke_test_html_document(html, viewport))
    return reports


def guardrail_reports_to_dict(reports: list[GuardrailReport]) -> dict[str, Any]:
    return {report.name: report.to_dict() for report in reports}


def build_asset_usage_reports(
    html: str,
    *,
    required_asset_placeholders: list[str],
) -> list[GuardrailReport]:
    return [
        validate_required_asset_usage(
            html,
            required_asset_placeholders=required_asset_placeholders,
        )
    ]


async def validate_with_single_repair(
    *,
    initial_result: dict[str, Any],
    viewport: dict[str, int],
    repair_callable: Any,
    report_path: Path,
    finalize_html: Callable[[str], str] | None = None,
    build_raw_reports: Callable[[str], list[GuardrailReport]] | None = None,
) -> dict[str, Any]:
    def finalize_or_fail(raw_html: str) -> tuple[str | None, list[GuardrailReport]]:
        if finalize_html is None:
            return raw_html, []

        try:
            return finalize_html(raw_html), []
        except Exception as exc:
            return None, [
                GuardrailReport(
                    name="asset_materialization",
                    passed=False,
                    errors=[str(exc)],
                )
            ]

    initial_reports = []
    if build_raw_reports is not None:
        initial_reports.extend(build_raw_reports(initial_result["html"]))

    initial_html, initial_finalize_reports = finalize_or_fail(initial_result["html"])
    initial_reports.extend(initial_finalize_reports)
    if initial_html is not None:
        initial_reports.extend(await run_html_guardrails(html=initial_html, viewport=viewport))
    report_payload: dict[str, Any] = {
        "initial": guardrail_reports_to_dict(initial_reports),
        "used_repair": False,
    }

    if all(report.passed for report in initial_reports):
        write_json_report(report_path, report_payload)
        return {
            **initial_result,
            "html": initial_html,
        }

    failure_summary = build_guardrail_failure_summary(*initial_reports)
    repaired_result = await asyncio.to_thread(
        repair_callable,
        current_html=initial_result["html"],
        failure_report=failure_summary,
    )

    repaired_reports = []
    if build_raw_reports is not None:
        repaired_reports.extend(build_raw_reports(repaired_result["html"]))

    repaired_html, repaired_finalize_reports = finalize_or_fail(repaired_result["html"])
    repaired_reports.extend(repaired_finalize_reports)
    if repaired_html is not None:
        repaired_reports.extend(await run_html_guardrails(html=repaired_html, viewport=viewport))
    report_payload["used_repair"] = True
    report_payload["repair"] = guardrail_reports_to_dict(repaired_reports)
    write_json_report(report_path, report_payload)

    if not all(report.passed for report in repaired_reports):
        repaired_failure_summary = build_guardrail_failure_summary(*repaired_reports)
        raise RuntimeError(
            "Automated guardrails failed after one repair pass.\n"
            f"{repaired_failure_summary}"
        )

    return {
        "html": repaired_html,
        "model": repaired_result["model"],
        "input_tokens": sum_token_counts(
            initial_result.get("input_tokens"),
            repaired_result.get("input_tokens"),
        ),
        "output_tokens": sum_token_counts(
            initial_result.get("output_tokens"),
            repaired_result.get("output_tokens"),
        ),
    }


@app.get("/api/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/scrape", response_model=ScrapeResponse)
async def scrape_page(payload: ScrapeRequest) -> ScrapeResponse:
    job = JobRecord(
        url=str(payload.url),
        viewport={"width": payload.viewport_width, "height": payload.viewport_height},
        status="scraping",
    )
    job_dir = JOBS_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    write_job_record(job_dir, job)

    try:
        scrape_result = await scrape_above_the_fold(job.url, job.viewport)
        persisted = persist_job_artifacts(
            job_dir=job_dir,
            screenshot_bytes=scrape_result["screenshot"],
            dom_data=scrape_result["dom"],
            stylesheets=scrape_result["stylesheets"],
        )
    except Exception as exc:
        logger.exception("Scrape failed for %s", job.url)
        job.status = "error"
        message = str(exc).strip()
        job.error_message = (
            f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__
        )
        write_job_record(job_dir, job)
        raise HTTPException(
            status_code=500,
            detail=f"Scrape failed: {job.error_message}",
        ) from exc

    job.status = "complete"
    job.screenshot_path = persisted["screenshot_path"]
    job.dom_data = scrape_result["dom"]
    job.styles_data = scrape_result["stylesheets"]
    write_job_record(job_dir, job)

    return ScrapeResponse(
        job_id=job.id,
        status=job.status,
        screenshot_url=f"/files/{job.id}/screenshot.png",
        dom_url=f"/files/{job.id}/dom.json",
        styles_url=f"/files/{job.id}/styles.json",
        title=scrape_result["dom"].get("title") or "Untitled",
        visible_element_count=len(scrape_result["dom"].get("visibleElements", [])),
        viewport=job.viewport,
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    job, _ = read_job_record(job_id)
    data = job.model_dump(mode="json")
    return JSONResponse(content=data)


@app.post("/api/reconstruct", response_model=ReconstructResponse)
async def reconstruct_page(payload: ReconstructRequest) -> ReconstructResponse:
    job, job_dir = read_job_record(payload.job_id)

    screenshot_path = job_dir / "screenshot.png"
    dom_path = job_dir / "dom.json"
    styles_path = job_dir / "styles.json"

    if not screenshot_path.exists() or not dom_path.exists() or not styles_path.exists():
        raise HTTPException(status_code=400, detail="This job does not have scrape artifacts yet.")

    job.status = "reconstructing"
    job.error_message = None
    write_job_record(job_dir, job)

    try:
        screenshot_bytes = screenshot_path.read_bytes()
        dom_data = json.loads(dom_path.read_text(encoding="utf-8"))
        styles_data = json.loads(styles_path.read_text(encoding="utf-8"))

        reconstruction = await asyncio.to_thread(
            reconstruct_html_document,
            screenshot_bytes=screenshot_bytes,
            dom_data=dom_data,
            stylesheets=styles_data,
            viewport=job.viewport,
            source_url=job.url,
        )
        reconstruction = await validate_with_single_repair(
            initial_result=reconstruction,
            viewport=job.viewport,
            repair_callable=partial(
                repair_reconstructed_html_document,
                screenshot_bytes=screenshot_bytes,
                dom_data=dom_data,
                stylesheets=styles_data,
                viewport=job.viewport,
                source_url=job.url,
            ),
            report_path=job_dir / "reconstruction-guardrails.json",
        )

        html_path = job_dir / "reconstruction.html"
        html_path.write_text(reconstruction["html"], encoding="utf-8")
    except Exception as exc:
        logger.exception("Reconstruction failed for %s", payload.job_id)
        job.status = "error"
        message = str(exc).strip()
        job.error_message = (
            f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__
        )
        write_job_record(job_dir, job)
        raise HTTPException(
            status_code=500,
            detail=f"Reconstruction failed: {job.error_message}",
        ) from exc

    job.status = "reconstruction_complete"
    job.reconstruction_html_path = str(html_path)
    job.reconstruction_model = reconstruction["model"]
    job.reconstruction_input_tokens = reconstruction["input_tokens"]
    job.reconstruction_output_tokens = reconstruction["output_tokens"]
    write_job_record(job_dir, job)

    return ReconstructResponse(
        job_id=job.id,
        status=job.status,
        html_url=f"/files/{job.id}/reconstruction.html",
        model=reconstruction["model"],
        input_tokens=reconstruction["input_tokens"],
        output_tokens=reconstruction["output_tokens"],
    )


@app.post("/api/extract-brand", response_model=BrandExtractResponse)
async def extract_brand(
    job_id: str = Form(...),
    ad_image: UploadFile = File(...),
) -> BrandExtractResponse:
    job, job_dir = read_job_record(job_id)

    media_type = (ad_image.content_type or "").strip() or "image/png"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image.")

    suffix = Path(ad_image.filename or "ad-image.png").suffix or ".png"
    safe_suffix = suffix[:10]
    ad_image_path = job_dir / f"ad-image{safe_suffix}"

    job.status = "extracting_brand"
    job.error_message = None
    write_job_record(job_dir, job)

    try:
        image_bytes = await ad_image.read()
        ad_image_path.write_bytes(image_bytes)

        extraction = await asyncio.to_thread(
            extract_brand_identity,
            image_bytes=image_bytes,
            media_type=media_type,
        )
        normalized_brand_identity = normalize_brand_identity(extraction["brand_identity"])

        brand_json_path = job_dir / "brand-identity.json"
        brand_json_path.write_text(
            json.dumps(normalized_brand_identity, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.exception("Brand extraction failed for %s", job_id)
        job.status = "error"
        message = str(exc).strip()
        job.error_message = (
            f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__
        )
        write_job_record(job_dir, job)
        raise HTTPException(
            status_code=500,
            detail=f"Brand extraction failed: {job.error_message}",
        ) from exc

    job.status = "brand_extracted"
    job.ad_image_path = str(ad_image_path)
    job.brand_identity = normalized_brand_identity
    job.brand_extraction_model = extraction["model"]
    job.brand_extraction_input_tokens = extraction["input_tokens"]
    job.brand_extraction_output_tokens = extraction["output_tokens"]
    write_job_record(job_dir, job)

    return BrandExtractResponse(
        job_id=job.id,
        status=job.status,
        ad_image_url=f"/files/{job.id}/{ad_image_path.name}",
        brand_identity=normalized_brand_identity,
        model=extraction["model"],
        input_tokens=extraction["input_tokens"],
        output_tokens=extraction["output_tokens"],
    )


@app.post("/api/reskin", response_model=ReskinResponse)
async def reskin_page(payload: ReskinRequest) -> ReskinResponse:
    job, job_dir = read_job_record(payload.job_id)

    screenshot_path = job_dir / "screenshot.png"
    reconstruction_path = job_dir / "reconstruction.html"
    if not screenshot_path.exists() or not reconstruction_path.exists():
        raise HTTPException(
            status_code=400,
            detail="This job needs both a scrape and a reconstruction before reskinning.",
        )

    job.status = "reskinning"
    job.error_message = None
    write_job_record(job_dir, job)

    try:
        try:
            normalized_brand_identity = normalize_brand_identity(payload.brand_identity)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid brand JSON: {exc}") from exc

        screenshot_bytes = screenshot_path.read_bytes()
        base_html = reconstruction_path.read_text(encoding="utf-8")
        asset_mode = resolve_asset_mode(payload.asset_mode)
        asset_manifest = await asyncio.to_thread(
            build_asset_manifest,
            job_dir=job_dir,
            mode=asset_mode,
            source_url=job.url,
            brand_identity=normalized_brand_identity,
            ad_image_path=job.ad_image_path,
        )
        prompt_assets = await asyncio.to_thread(load_prompt_assets, asset_manifest)
        required_asset_placeholders = [
            asset["placeholder"] for asset in prompt_assets if asset.get("placeholder")
        ]

        reskin = await asyncio.to_thread(
            reskin_html_document,
            base_html=base_html,
            brand_identity=normalized_brand_identity,
            screenshot_bytes=screenshot_bytes,
            viewport=job.viewport,
            source_url=job.url,
            color_strategy=payload.color_strategy,
            approved_assets=prompt_assets,
        )
        reskin = await validate_with_single_repair(
            initial_result=reskin,
            viewport=job.viewport,
            repair_callable=partial(
                repair_reskinned_html_document,
                base_html=base_html,
                brand_identity=normalized_brand_identity,
                screenshot_bytes=screenshot_bytes,
                viewport=job.viewport,
                source_url=job.url,
                color_strategy=payload.color_strategy,
                approved_assets=prompt_assets,
            ),
            report_path=job_dir / "reskin-guardrails.json",
            finalize_html=partial(materialize_asset_placeholders, prompt_assets=prompt_assets),
            build_raw_reports=partial(
                build_asset_usage_reports,
                required_asset_placeholders=required_asset_placeholders,
            )
            if required_asset_placeholders
            else None,
        )

        reskinned_path = job_dir / "reskinned.html"
        reskinned_path.write_text(reskin["html"], encoding="utf-8")
        (job_dir / "brand-identity.json").write_text(
            json.dumps(normalized_brand_identity, indent=2),
            encoding="utf-8",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Reskin failed for %s", payload.job_id)
        job.status = "error"
        message = str(exc).strip()
        job.error_message = (
            f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__
        )
        write_job_record(job_dir, job)
        raise HTTPException(
            status_code=500,
            detail=f"Reskin failed: {job.error_message}",
        ) from exc

    job.status = "reskin_complete"
    job.asset_mode = asset_mode
    job.asset_manifest = asset_manifest
    job.brand_identity = normalized_brand_identity
    job.reskinned_html_path = str(reskinned_path)
    job.reskin_model = reskin["model"]
    job.reskin_input_tokens = reskin["input_tokens"]
    job.reskin_output_tokens = reskin["output_tokens"]
    write_job_record(job_dir, job)

    return ReskinResponse(
        job_id=job.id,
        status=job.status,
        html_url=f"/files/{job.id}/reskinned.html",
        model=reskin["model"],
        input_tokens=reskin["input_tokens"],
        output_tokens=reskin["output_tokens"],
    )
