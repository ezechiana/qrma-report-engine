from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/app/share-links", response_class=HTMLResponse)
def share_links_page(request: Request):
    return templates.TemplateResponse(
        "share_links.html",
        {"request": request}
    )