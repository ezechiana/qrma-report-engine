from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.kb_service import (
    get_az_index,
    get_marker_page,
    get_pattern_index,
    get_portal_data,
    get_software_section_markers,
    get_software_sections,
    get_system_index,
    search_knowledge,
)

router = APIRouter(tags=["knowledge-base"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/kb", response_class=HTMLResponse)
@router.get("/kb/", response_class=HTMLResponse)
def knowledge_home(request: Request, db: Session = Depends(get_db)):
    data = get_portal_data(db)
    return templates.TemplateResponse("kb/home.html", {"request": request, **data})


@router.get("/kb/search", response_class=HTMLResponse)
def knowledge_search(
    request: Request,
    q: str = Query(default="", max_length=120),
    db: Session = Depends(get_db),
):
    results = search_knowledge(db, q)
    return templates.TemplateResponse(
        "kb/search.html",
        {"request": request, "query": q.strip(), "results": results},
    )


@router.get("/kb/a-z", response_class=HTMLResponse)
def knowledge_az(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "kb/index.html",
        {
            "request": request,
            "title": "Browse A–Z",
            "description": "All canonical Go360 markers, alphabetically.",
            "markers": get_az_index(db),
            "index_kind": "az",
        },
    )


@router.get("/kb/system/{slug}", response_class=HTMLResponse)
def knowledge_system_index(request: Request, slug: str, db: Session = Depends(get_db)):
    markers = get_system_index(db, slug)
    if not markers:
        raise HTTPException(status_code=404, detail="Go360 system not found")
    return templates.TemplateResponse(
        "kb/index.html",
        {
            "request": request,
            "title": markers[0]["system_name"],
            "description": f"{len(markers)} markers in this Go360 clinical system.",
            "markers": markers,
            "index_kind": "system",
        },
    )


@router.get("/kb/pattern/{slug}", response_class=HTMLResponse)
def knowledge_pattern_index(request: Request, slug: str, db: Session = Depends(get_db)):
    markers = get_pattern_index(db, slug)
    if not markers:
        raise HTTPException(status_code=404, detail="Go360 pattern cluster not found")
    return templates.TemplateResponse(
        "kb/index.html",
        {
            "request": request,
            "title": f"Pattern: {markers[0]['pattern_cluster_name']}",
            "description": f"{len(markers)} markers with a functional relationship to this pattern across Go360 systems.",
            "markers": markers,
            "index_kind": "pattern",
        },
    )


@router.get("/kb/software/{report_type_code}", response_class=HTMLResponse)
def software_report_index(request: Request, report_type_code: str, db: Session = Depends(get_db)):
    sections = get_software_sections(db, report_type_code)
    if not sections:
        raise HTTPException(status_code=404, detail="QRMA software report type not found")
    return templates.TemplateResponse(
        "kb/software.html",
        {"request": request, "report_type": sections[0]["report_type"], "report_type_code": report_type_code, "sections": sections},
    )


@router.get("/kb/software/{report_type_code}/section/{section_id}", response_class=HTMLResponse)
def software_section_index(request: Request, report_type_code: str, section_id: str, db: Session = Depends(get_db)):
    markers = get_software_section_markers(db, report_type_code, section_id)
    if not markers:
        raise HTTPException(status_code=404, detail="QRMA software section not found")
    return templates.TemplateResponse(
        "kb/software_section.html",
        {
            "request": request,
            "report_type": markers[0]["report_type"],
            "report_type_code": report_type_code,
            "section_name": markers[0]["section_name"],
            "markers": markers,
        },
    )


@router.get("/kb/{go360_id}", response_class=HTMLResponse)
def marker_knowledge_page(request: Request, go360_id: str, db: Session = Depends(get_db)):
    marker = get_marker_page(db, go360_id, language_code="en")
    if not marker:
        raise HTTPException(status_code=404, detail="Go360 marker not found")
    return templates.TemplateResponse("kb/marker.html", {"request": request, "marker": marker})
