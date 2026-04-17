from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "title": "Login",
        },
    )


@router.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "request": request,
            "title": "Register",
        },
    )


@router.get("/logout")
def logout():
    return RedirectResponse("/login", status_code=302)


@router.get("/app")
def dashboard_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "title": "Dashboard",
        },
    )

@router.get("/app/reports")
def reports_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "request": request,
            "title": "Reports",
        },
    )


@router.get("/app/cases/new")
def new_case_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="cases_new.html",
        context={
            "request": request,
            "title": "New Case",
        },
    )


@router.get("/app/cases/{case_id}")
def case_detail_page(case_id: str, request: Request):
    return templates.TemplateResponse(
        request=request,
        name="case_detail.html",
        context={
            "request": request,
            "title": "Case Detail",
            "case_id": case_id,
        },
    )


@router.get("/app/reports/{report_id}")
def report_detail_page(report_id: str, request: Request):
    return templates.TemplateResponse(
        request=request,
        name="report_detail.html",
        context={
            "request": request,
            "title": "Report Detail",
            "report_id": report_id,
        },
    )

@router.get("/app/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"request": request, "title": "Settings"},
    )


@router.get("/app/billing")
def billing_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="billing.html",
        context={
            "request": request,
            "title": "Billing",
        },
    )