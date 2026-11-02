"""Public search discovery policy; this does not replace application authentication."""
from __future__ import annotations

import os

APP_ORIGIN = "https://app.go360.io"
MARKETING_ORIGIN = "https://www.go360.io"
PRODUCTION_HOSTS = {"app.go360.io", "www.go360.io", "go360.io"}
NONPRODUCTION = {"local", "dev", "development", "test", "testing", "staging", "stage", "preview", "qa", "sandbox"}


def request_host(request) -> str:
    return request.headers.get("host", "").split(":", 1)[0].lower().rstrip(".")


def production_request(request) -> bool:
    if request_host(request) not in PRODUCTION_HOSTS:
        return False
    # Check configured values only, avoiding application defaults that may say development.
    for name in ("RAILWAY_ENVIRONMENT_NAME", "GO360_ENV", "APP_ENV"):
        value = os.getenv(name, "").strip().lower()
        if value in NONPRODUCTION or value.startswith(("staging-", "preview-", "pr-")):
            return False
    return True


def public_content(request) -> bool:
    if not production_request(request):
        return False
    path = request.url.path
    if request_host(request) == "app.go360.io":
        return (path == "/kb" or path.startswith("/kb/")) and path.rstrip("/") != "/kb/search"
    return path in {"/", "/terms", "/privacy"}


def prepare_search_state(request) -> None:
    request.state.search_noindex = not public_content(request)
    request.state.search_canonical_url = ""
    if public_content(request):
        if request_host(request) == "app.go360.io":
            path = "/kb/" if request.url.path in {"/kb", "/kb/"} else request.url.path
            request.state.search_canonical_url = APP_ORIGIN + path
        else:
            request.state.search_canonical_url = MARKETING_ORIGIN + request.url.path


def apply_search_headers(request, response) -> None:
    if not public_content(request) or response.status_code != 200:
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    else:
        response.headers["X-Robots-Tag"] = "index, follow, max-image-preview:large"
        canonical = getattr(request.state, "search_canonical_url", "")
        if canonical:
            response.headers.append("Link", f'<{canonical}>; rel="canonical"')


def robots_body(request) -> str:
    if not production_request(request):
        # Crawling is allowed so crawlers can see the noindex response header.
        return "User-agent: *\nDisallow:\n"
    if request_host(request) == "app.go360.io":
        return ("User-agent: *\nAllow: /kb$\nAllow: /kb/\nAllow: /static/\n"
                "Allow: /sitemap.xml\nDisallow: /\n"
                f"Sitemap: {APP_ORIGIN}/sitemap.xml\n")
    return ("User-agent: *\nDisallow:\n"
            f"Sitemap: {MARKETING_ORIGIN}/sitemap.xml\n"
            f"Sitemap: {APP_ORIGIN}/sitemap.xml\n")
