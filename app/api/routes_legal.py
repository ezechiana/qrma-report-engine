from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["legal"])

@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse("legal/terms.html", {"request": request})

@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse("legal/privacy.html", {"request": request})

@router.get("/cookies", response_class=HTMLResponse)
def cookies_page(request: Request):
    return templates.TemplateResponse("legal/cookies.html", {"request": request})
