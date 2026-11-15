"""Guard the share-helper extraction: same routes, same helper objects."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from app.api import routes_share, share_helpers


EXPECTED_ROUTES = {
    ("/api/billing/stripe-connect/account-link", "POST"),
    ("/api/billing/stripe-connect/refresh", "POST"),
    ("/api/billing/stripe-connect/status", "GET"),
    ("/api/patients/{patient_id}/trend-share-links", "POST"),
    ("/api/reports/{report_version_id}/share-links", "POST"),
    ("/api/share-links", "GET"),
    ("/api/share-links/{share_id}/extend-expiry", "POST"),
    ("/api/share-links/{share_id}/revoke", "POST"),
    ("/api/share-links/{token}/payment-settings", "POST"),
    ("/api/share/bundles", "POST"),
    ("/api/share/create", "POST"),
    ("/api/share/list", "GET"),
    ("/api/share/revoke/{share_id}", "POST"),
    ("/debug/share-links", "GET"),
    ("/share/{token}", "GET"),
    ("/share/{token}/bundle", "GET"),
    ("/share/{token}/bundle-data", "GET"),
    ("/share/{token}/checkout", "POST"),
    ("/share/{token}/data", "GET"),
    ("/share/{token}/html", "GET"),
    ("/share/{token}/payment-success", "GET"),
    ("/share/{token}/pdf", "GET"),
    ("/share/{token}/trend", "GET"),
    ("/share/{token}/trend-data", "GET"),
    ("/share/{token}/trend/verify-password", "POST"),
    ("/share/{token}/verify-password", "POST"),
}

HELPERS_USED_BY_ROUTES = (
    "_calculate_platform_fee",
    "_clear_share_cookie",
    "_connect_destination_for_report",
    "_enforce_password_gate",
    "_enforce_share_payment_or_402",
    "_ensure_practitioner_settings",
    "_ensure_share_link_events_table",
    "_get_practitioner_connect_state",
    "_get_report_for_share_or_404",
    "_has_valid_share_cookie",
    "_latest_ready_report_for_patient",
    "_mark_share_paid",
    "_patient_display_name",
    "_public_report_payload",
    "_set_share_cookie",
    "_set_share_payment_config",
    "_share_payment_state",
    "_stripe_api_request",
    "_sync_connect_account_status",
    "_tenant_theme_from_report",
    "_track_share_event",
    "_trend_owner_from_report",
    "_validate_share_link_or_render",
)


def test_share_routes_unchanged():
    found = {
        (route.path, ",".join(sorted(route.methods or [])))
        for route in routes_share.router.routes
    }
    assert found == EXPECTED_ROUTES


def test_route_module_uses_extracted_helpers():
    for name in HELPERS_USED_BY_ROUTES:
        assert getattr(routes_share, name) is getattr(share_helpers, name)


def test_share_cookie_still_round_trips():
    token = "share-token"
    value = share_helpers._cookie_value_for_token(token)

    class Request:
        def __init__(self, cookies):
            self.cookies = cookies

    assert share_helpers._has_valid_share_cookie(
        Request({share_helpers.SHARE_ACCESS_COOKIE_NAME: value}),
        token,
    )
    assert not share_helpers._has_valid_share_cookie(Request({}), token)
    assert share_helpers._format_money(2500, "gbp") == "GBP 25"
