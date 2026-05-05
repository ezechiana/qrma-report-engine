# app/api/routes.py

from fastapi import Request, APIRouter, UploadFile, File, Body
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models.schema import ReportBuildRequest, NarrativeBlock
from app.parser.html_parser import parse_html_report
from app.services.catalog import apply_catalog
from app.services.config_service import load_practitioner_config, save_practitioner_config
from app.services.interpreter import enrich_report
from app.services.rules_engine import apply_insight_engine
from app.services.final_enrichment_pipeline import apply_final_report_enrichment
from app.services.overrides_service import apply_report_overrides
from app.services.report_builder import render_report_html
from app.services.pdf_service import save_html, save_pdf
from app.services.storage_service import generate_presigned_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
def landing_page(request: Request):
    return templates.TemplateResponse(
        "landing.html",
        {"request": request}
    )

def decode_uploaded_file(content: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


def process_uploaded_html(html: str):
    parsed = parse_html_report(html)
    catalogued = apply_catalog(parsed)
    interpreted = enrich_report(catalogued)
    enriched = apply_insight_engine(interpreted)

    # This now runs:
    # 1) recommendation engine
    # 2) practitioner intelligence layer
    enriched = apply_final_report_enrichment(enriched)

    # Defensive coercion if narrative comes back as dict
    if isinstance(getattr(enriched, "narrative", None), dict):
        enriched.narrative = NarrativeBlock(**enriched.narrative)

    return enriched


@router.post("/upload")
async def upload_report(file: UploadFile = File(...)):
    content = await file.read()
    html = decode_uploaded_file(content)
    enriched = process_uploaded_html(html)
    return enriched


@router.post("/preview-report", response_class=HTMLResponse)
async def preview_report(file: UploadFile = File(...)):
    content = await file.read()
    html = decode_uploaded_file(content)
    enriched = process_uploaded_html(html)
    report_html = render_report_html(enriched)
    save_html(report_html, "report_preview.html")
    return report_html


@router.post("/build-report-pdf")
async def build_report_pdf(file: UploadFile = File(...)):
    content = await file.read()
    html = decode_uploaded_file(content)
    enriched = process_uploaded_html(html)
    report_html = render_report_html(enriched)
    pdf_key = await save_pdf(report_html, "debug/wellness_report.pdf")
    return RedirectResponse(url=generate_presigned_url(pdf_key), status_code=302)

@router.get("/config")
async def get_config():
    return load_practitioner_config()


@router.post("/config")
async def update_config(payload: dict = Body(...)):
    return save_practitioner_config(payload)


@router.post("/build-custom-report-pdf")
async def build_custom_report_pdf(payload: ReportBuildRequest):
    overridden_report = apply_report_overrides(payload.report_data, payload.overrides)

    if isinstance(getattr(overridden_report, "narrative", None), dict):
        overridden_report.narrative = NarrativeBlock(**overridden_report.narrative)

    report_html = render_report_html(overridden_report, overrides=payload.overrides)
    pdf_key = await save_pdf(report_html, "debug/custom_wellness_report.pdf")
    return RedirectResponse(url=generate_presigned_url(pdf_key), status_code=302)



@router.post("/build-custom-report-pdf")
async def build_custom_report_pdf(payload: ReportBuildRequest):
    overridden_report = apply_report_overrides(payload.report_data, payload.overrides)

    if isinstance(getattr(overridden_report, "narrative", None), dict):
        overridden_report.narrative = NarrativeBlock(**overridden_report.narrative)

    report_html = render_report_html(overridden_report, overrides=payload.overrides)
    pdf_path = await save_pdf(report_html, "custom_wellness_report.pdf")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename="custom_wellness_report.pdf",
    )