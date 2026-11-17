# app/api/routes_share.py

from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.share_helpers import (
    DEFAULT_SHARE_PRICE_AMOUNT,
    DEFAULT_SHARE_PRICE_CURRENCY,
    _calculate_platform_fee,
    _clear_share_cookie,
    _connect_destination_for_report,
    _enforce_password_gate,
    _enforce_share_payment_or_402,
    _get_report_for_share_or_404,
    _has_valid_share_cookie,
    _latest_ready_report_for_patient,
    _mark_share_paid,
    _patient_display_name,
    _public_report_payload,
    _set_share_cookie,
    _set_share_payment_config,
    _share_payment_state,
    _stripe_api_request,
    _tenant_theme_from_report,
    _track_share_event,
    _trend_owner_from_report,
    _validate_share_link_or_render,
)
from app.db.models import Case, Patient, ReportVersion, ShareLink, User
from app.schemas.share import ShareLinkCreate
from app.services.storage_service import object_exists, generate_presigned_url
from app.services.audit_service import log_action
from app.services.share_link_service import (
    create_share_link,
    validate_share_link_password,
)
from app.services.subscription_service import require_subscription_feature

router = APIRouter(tags=["share"])
templates = Jinja2Templates(directory="app/templates")






@router.get("/share/{token}", response_class=HTMLResponse)
def access_shared_report(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)

    if getattr(report, "report_type", "assessment") == "trend":
        return RedirectResponse(url=f"/share/{token}/trend", status_code=302)


    if link.password_hash and not _has_valid_share_cookie(request, token):
        response = templates.TemplateResponse(
            request=request,
            name="password_required.html",
            context={
                "request": request,
                "token": token,
            },
        )
        _clear_share_cookie(response)
        return response

    log_action(
        db,
        "share_link_opened",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )
    
    if request.query_params.get("preview") != "1":
        _track_share_event(db, token, "client_view", {"share_type": "assessment", "source": "report_view", "actor": "client"})

    payload = _public_report_payload(db, report, token)
    tenant = _tenant_theme_from_report(db, report)

    return templates.TemplateResponse(
        request=request,
        name="report_viewer.html",
        context={
            "request": request,
            "report": payload,
            "tenant": tenant,
            "viewer_mode": "patient",
        },
    )


@router.post("/share/{token}/verify-password", response_class=HTMLResponse)
def verify_share_password(
    token: str,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    if not link.password_hash:
        report = _get_report_for_share_or_404(db, link)
        payload = _public_report_payload(db, report, token)
        tenant = _tenant_theme_from_report(db, report)
        return templates.TemplateResponse(
            request=request,
            name="report_viewer.html",
            context={
                "request": request,
                "report": payload,
                "tenant": tenant,
                "viewer_mode": "patient",
            },
        )

    if not validate_share_link_password(link, password):
        raise HTTPException(status_code=401, detail="Invalid password")

    report = _get_report_for_share_or_404(db, link)

    log_action(
        db,
        "share_link_opened",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token, "password_verified": True},
    )
    _track_share_event(db, token, "client_view", {"share_type": "assessment", "source": "password_verified", "actor": "client"})

    payload = _public_report_payload(db, report, token)
    tenant = _tenant_theme_from_report(db, report)

    response = templates.TemplateResponse(
        request=request,
        name="report_viewer.html",
        context={
            "request": request,
            "report": payload,
            "tenant": tenant,
            "viewer_mode": "patient",
        },
    )
    _set_share_cookie(response, token)
    return response


@router.get("/share/{token}/data")
def get_shared_report_data(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)
    payload = _public_report_payload(db, report, token)
    tenant = _tenant_theme_from_report(db, report)

    return JSONResponse(
        {
            "viewer_mode": "patient",
            "tenant": tenant,
            "report": payload,
        }
    )


@router.get("/share/{token}/pdf")
def get_shared_report_pdf(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)

    if not report.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not found")

    if not object_exists(report.pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found")

    log_action(
        db,
        "share_link_pdf_downloaded",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    signed_url = generate_presigned_url(report.pdf_path)
    return RedirectResponse(url=signed_url, status_code=302)


@router.get("/share/{token}/html")
def get_shared_report_html(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)

    report = _get_report_for_share_or_404(db, link)

    if not report.html_path:
        raise HTTPException(status_code=404, detail="HTML not found")

    if not object_exists(report.html_path):
        raise HTTPException(status_code=404, detail="HTML file not found")

    log_action(
        db,
        "share_link_html_downloaded",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    signed_url = generate_presigned_url(report.html_path)
    return RedirectResponse(url=signed_url, status_code=302)


@router.post("/api/patients/{patient_id}/trend-share-links")
def create_patient_trend_share_link(
    patient_id: UUID,
    payload: ShareLinkCreate,
    request: Request,
    requires_payment: bool = False,
    price_amount: int | None = None,
    price_currency: str = DEFAULT_SHARE_PRICE_CURRENCY,
    access_label: str = "Client progress view",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_subscription_feature(db, current_user, "share_link")
    if requires_payment:
        require_subscription_feature(db, current_user, "sell_shares")

    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    latest_report = _latest_ready_report_for_patient(db, patient.id, current_user.id)
    if not latest_report:
        raise HTTPException(status_code=404, detail="No ready report found for this patient")

    link = create_share_link(
        db,
        report_version=latest_report,
        patient_id=patient.id,
        share_type="trend",
        password=payload.password,
        expires_at=payload.expires_at,
    )

    _set_share_payment_config(
        db,
        link,
        requires_payment=bool(requires_payment),
        price_amount=price_amount,
        price_currency=price_currency,
        access_label=access_label,
    )

    log_action(
        db,
        "trend_share_link_created",
        user_id=current_user.id,
        case_id=latest_report.case_id,
        report_version_id=latest_report.id,
        metadata_json={"token": link.token, "patient_id": str(patient.id)},
    )

    base_url = str(request.base_url).rstrip("/")
    return {
        "share_url": f"{base_url}/share/{link.token}/trend",
        "token": link.token,
        "type": "patient_trend",
        "patient_id": str(patient.id),
        "report_version_id": str(latest_report.id),
        "requires_payment": bool(requires_payment),
        "payment_status": "unpaid" if requires_payment else "not_required",
    }


@router.get("/share/{token}/trend", response_class=HTMLResponse)
def access_shared_patient_trend(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)

    if link.password_hash and not _has_valid_share_cookie(request, token):
        response = templates.TemplateResponse(
            request=request,
            name="patient_trend_password_required.html",
            context={"request": request, "token": token},
        )
        _clear_share_cookie(response)
        return response

    tenant = _tenant_theme_from_report(db, report)
    payment = _share_payment_state(db, link)

    if payment["requires_payment"] and not payment["is_unlocked"]:
        _track_share_event(db, token, "paywall_view", {"share_type": "trend", "payment_status": payment.get("payment_status")})
        return templates.TemplateResponse(
            request=request,
            name="patient_trend_paywall.html",
            context={"request": request, "token": token, "tenant": tenant, "payment": payment},
        )

    
    if request.query_params.get("preview") != "1":
        _track_share_event(db, token, "client_view", {"share_type": "trend", "payment_status": payment.get("payment_status"), "actor": "client"})

    log_action(
        db,
        "trend_share_link_opened",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token, "payment_status": payment.get("payment_status")},
    )

    return templates.TemplateResponse(
        request=request,
        name="patient_trend_share.html",
        context={"request": request, "token": token, "tenant": tenant},
    )


@router.post("/share/{token}/trend/verify-password", response_class=HTMLResponse)
def verify_trend_share_password(
    token: str,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    if link.password_hash and not validate_share_link_password(link, password):
        raise HTTPException(status_code=401, detail="Invalid password")

    report = _get_report_for_share_or_404(db, link)
    log_action(
        db,
        "trend_share_password_verified",
        case_id=report.case_id,
        report_version_id=report.id,
        metadata_json={"token": token},
    )

    response = RedirectResponse(url=f"/share/{token}/trend", status_code=302)
    _set_share_cookie(response, token)
    return response




@router.post("/api/share-links/{token}/payment-settings")
def update_share_link_payment_settings(
    token: str,
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)
    case = getattr(report, "case", None)

    if not case or case.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Share link not found")

    if payload.get("requires_payment"):
        require_subscription_feature(db, current_user, "sell_shares")

    _set_share_payment_config(
        db,
        link,
        requires_payment=bool(payload.get("requires_payment")),
        price_amount=payload.get("price_amount"),
        price_currency=payload.get("price_currency") or DEFAULT_SHARE_PRICE_CURRENCY,
        access_label=payload.get("access_label") or "Client progress view",
    )

    return {"ok": True, "token": token, "payment": _share_payment_state(db, link)}


@router.post("/share/{token}/checkout")
def create_share_checkout_session(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    report = _get_report_for_share_or_404(db, link)
    payment = _share_payment_state(db, link)

    if not payment["requires_payment"] or payment["is_unlocked"]:
        return RedirectResponse(url=f"/share/{token}/trend", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    label = payment.get("access_label") or "Client progress view"
    amount = int(payment.get("price_amount") or DEFAULT_SHARE_PRICE_AMOUNT)
    currency = (payment.get("price_currency") or DEFAULT_SHARE_PRICE_CURRENCY).lower()

    connect = _connect_destination_for_report(db, report)
    connect_mode = connect.get("mode") or "platform"
    destination_account = connect.get("account_id") if connect_mode == "destination_charge" else None
    platform_fee_amount = _calculate_platform_fee(amount) if destination_account else None

    checkout_payload = {
        "mode": "payment",
        "success_url": f"{base_url}/share/{token}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/share/{token}/trend",
        "client_reference_id": token,
        "metadata[token]": token,
        "metadata[share_link_id]": str(link.id),
        "metadata[report_version_id]": str(report.id),
        "metadata[stripe_connect_mode]": connect_mode,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(amount),
        "line_items[0][price_data][product_data][name]": label,
    }

    if destination_account:
        checkout_payload.update({
            "metadata[destination_account]": destination_account,
            "metadata[platform_fee_amount]": str(platform_fee_amount or 0),
            "payment_intent_data[application_fee_amount]": str(platform_fee_amount or 0),
            "payment_intent_data[transfer_data][destination]": destination_account,
            "payment_intent_data[metadata][token]": token,
            "payment_intent_data[metadata][share_link_id]": str(link.id),
            "payment_intent_data[metadata][report_version_id]": str(report.id),
            "payment_intent_data[metadata][stripe_connect_mode]": connect_mode,
        })

    session = _stripe_api_request("POST", "/checkout/sessions", checkout_payload)

    db.execute(
        text(
            """
            UPDATE share_links
            SET stripe_checkout_session_id = :session_id,
                stripe_connect_account_id = :stripe_connect_account_id,
                stripe_connect_mode = :stripe_connect_mode,
                platform_fee_amount = :platform_fee_amount,
                platform_fee_currency = :platform_fee_currency
            WHERE id = :id
            """
        ),
        {
            "id": link.id,
            "session_id": session.get("id"),
            "stripe_connect_account_id": destination_account,
            "stripe_connect_mode": connect_mode,
            "platform_fee_amount": platform_fee_amount,
            "platform_fee_currency": currency if platform_fee_amount is not None else None,
        },
    )
    db.commit()
    _track_share_event(db, token, "payment_started", {
        "share_type": "trend",
        "stripe_session_id": session.get("id"),
        "amount": amount,
        "currency": currency,
        "stripe_connect_mode": connect_mode,
    })

    return RedirectResponse(url=session["url"], status_code=303)


@router.get("/share/{token}/payment-success")
def confirm_share_payment_success(
    token: str,
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    session = _stripe_api_request("GET", f"/checkout/sessions/{session_id}")

    if session.get("client_reference_id") != token:
        raise HTTPException(status_code=400, detail="Stripe session does not match this share link")

    if session.get("payment_status") == "paid" or session.get("status") == "complete":
        report = _get_report_for_share_or_404(db, link)
        metadata = session.get("metadata") or {}

        connect_mode = metadata.get("stripe_connect_mode")
        destination_account = metadata.get("destination_account")
        payment_intent_id = session.get("payment_intent")

        try:
            platform_fee_amount = int(metadata.get("platform_fee_amount") or 0) if destination_account else None
        except Exception:
            platform_fee_amount = None

        payment_state = _share_payment_state(db, link)
        platform_fee_currency = (payment_state.get("price_currency") or DEFAULT_SHARE_PRICE_CURRENCY).lower()

        _mark_share_paid(
            db,
            link,
            session_id,
            payment_intent_id=payment_intent_id,
            stripe_connect_account_id=destination_account,
            stripe_connect_mode=connect_mode,
            platform_fee_amount=platform_fee_amount,
            platform_fee_currency=platform_fee_currency if platform_fee_amount is not None else None,
        )

        log_action(
            db,
            "share_link_payment_completed",
            case_id=report.case_id,
            report_version_id=report.id,
            metadata_json={
                "token": token,
                "stripe_session_id": session_id,
                "stripe_payment_intent_id": payment_intent_id,
                "stripe_connect_mode": connect_mode,
                "destination_account": destination_account,
                "platform_fee_amount": platform_fee_amount,
            },
        )
        _track_share_event(db, token, "paid", {
            "share_type": "trend",
            "stripe_session_id": session_id,
            "stripe_payment_intent_id": payment_intent_id,
            "stripe_connect_mode": connect_mode,
        })
        return RedirectResponse(url=f"/share/{token}/trend", status_code=303)

    raise HTTPException(status_code=402, detail="Payment has not completed")


@router.get("/share/{token}/trend-data")
def get_shared_patient_trend_data(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response
    _enforce_password_gate(request, link)
    payment = _enforce_share_payment_or_402(db, link)

    report = _get_report_for_share_or_404(db, link)
    case = getattr(report, "case", None)
    if not case or not getattr(case, "patient_id", None):
        raise HTTPException(status_code=404, detail="Patient not found for shared report")

    from app.api.routes_patients import get_patient_trends

    owner = _trend_owner_from_report(report)
    trend_payload = get_patient_trends(
        patient_id=case.patient_id,
        include_archived=False,
        view_mode="client",
        db=db,
        current_user=owner,
    )

    patient = getattr(case, "patient", None)
    tenant = _tenant_theme_from_report(db, report)

    return JSONResponse({
        "viewer_mode": "patient",
        "share_type": "patient_trend",
        "tenant": tenant,
        "patient": {
            "id": str(case.patient_id),
            "display_name": _patient_display_name(patient),
        },
        "trend": trend_payload,
        "payment": payment,
    })




class ShareBundleCreate(BaseModel):
    report_version_ids: list[UUID] = Field(min_length=1)
    patient_id: UUID | None = None
    bundle_label: str | None = None
    requires_payment: bool = False
    price_amount: int | None = None
    price_currency: str = "gbp"
    expires_at: datetime | None = None
    password: str | None = None


@router.post("/api/share/bundles")
def create_share_bundle(
    payload: ShareBundleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_subscription_feature(db, current_user, "share_bundle")
    if payload.requires_payment:
        require_subscription_feature(db, current_user, "sell_shares")

    reports = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            ReportVersion.id.in_(payload.report_version_ids),
            Case.user_id == current_user.id,
        )
        .all()
    )

    found = {r.id for r in reports}
    missing = [str(x) for x in payload.report_version_ids if x not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Report(s) not found: {', '.join(missing)}")

    anchor_report = reports[0]

    link = create_share_link(
        db=db,
        report_version=anchor_report,
        patient_id=payload.patient_id,
        share_type="report_bundle",
        password=payload.password,
        expires_days=None,
    )

    if payload.expires_at:
        link.expires_at = payload.expires_at

    db.execute(
        text("""
        UPDATE share_links
        SET share_type = 'report_bundle',
            bundle_label = :bundle_label,
            requires_payment = :requires_payment,
            payment_status = :payment_status,
            price_amount = :price_amount,
            price_currency = :price_currency,
            access_label = :access_label
        WHERE id = :id
        """),
        {
            "id": link.id,
            "bundle_label": payload.bundle_label or "Client report bundle",
            "requires_payment": payload.requires_payment,
            "payment_status": "unpaid" if payload.requires_payment else "not_required",
            "price_amount": payload.price_amount if payload.requires_payment else None,
            "price_currency": payload.price_currency or DEFAULT_SHARE_PRICE_CURRENCY,
            "access_label": payload.bundle_label or "Client report bundle",
        },
    )

    for idx, report_id in enumerate(payload.report_version_ids):
        db.execute(
            text("""
            INSERT INTO share_link_items (share_link_id, report_version_id, item_order)
            VALUES (:share_link_id, :report_version_id, :item_order)
            ON CONFLICT (share_link_id, report_version_id) DO NOTHING
            """),
            {"share_link_id": link.id, "report_version_id": report_id, "item_order": idx},
        )

    db.commit()

    base_url = str(request.base_url).rstrip("/")
    return {
        "success": True,
        "token": link.token,
        "share_url": f"{base_url}/share/{link.token}/bundle",
        "report_count": len(payload.report_version_ids),
        "requires_payment": payload.requires_payment,
    }

@router.get("/share/{token}/bundle", response_class=HTMLResponse)
def access_shared_bundle(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    if link.password_hash and not _has_valid_share_cookie(request, token):
        response = templates.TemplateResponse(
            request=request,
            name="patient_trend_password_required.html",
            context={"request": request, "token": token},
        )
        _clear_share_cookie(response)
        return response

    payment = _share_payment_state(db, link)
    if payment.get("requires_payment") and not payment.get("is_unlocked"):
        report = _get_report_for_share_or_404(db, link)
        tenant = _tenant_theme_from_report(db, report)
        return templates.TemplateResponse(
            request=request,
            name="patient_trend_paywall.html",
            context={"request": request, "token": token, "tenant": tenant, "payment": payment},
        )

    return templates.TemplateResponse(
        request=request,
        name="share_bundle_view.html",
        context={"request": request, "token": token},
    )


@router.get("/share/{token}/bundle-data")
def get_shared_bundle_data(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    link, error_response = _validate_share_link_or_render(request, db, token)
    if error_response:
        return error_response

    _enforce_password_gate(request, link)
    payment = _enforce_share_payment_or_402(db, link)

    rows = db.execute(
        text("""
        SELECT r.id, r.case_id, r.report_type, r.generated_at, r.version_number, sli.item_order
        FROM share_link_items sli
        JOIN report_versions r ON r.id = sli.report_version_id
        WHERE sli.share_link_id = :share_link_id
        ORDER BY sli.item_order ASC, r.generated_at DESC
        """),
        {"share_link_id": link.id},
    ).mappings().all()

    items = []
    for row in rows:
        report = db.query(ReportVersion).filter(ReportVersion.id == row["id"]).first()
        if not report:
            continue

        if (row["report_type"] or "assessment") == "trend":
            items.append({
                "id": str(report.id),
                "type": "trend",
                "generated_at": report.generated_at.isoformat() if report.generated_at else None,
                "trend": getattr(report, "trend_payload_json", None) or (report.report_json or {}).get("trend"),
            })
        else:
            items.append({
                "id": str(report.id),
                "type": "assessment",
                "generated_at": report.generated_at.isoformat() if report.generated_at else None,
                "report": _public_report_payload(db, report, token),
            })

    return {"viewer_mode": "patient", "share_type": "report_bundle", "items": items, "payment": payment}