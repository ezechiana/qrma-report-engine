"""Smoke-test the practitioner pages against a running app.

The HTML templates keep fallback copy such as "Could not load patients." inside
<script> blocks. This checks the visible page and the data request that fills it.

Run after a change, against the app you are using:

    $env:GO360_SMOKE_EMAIL="you@example.com"
    $env:GO360_SMOKE_PASSWORD="your-password"
    python tests/test_practitioner_page_smoke.py

Or the same variables with:

    pytest tests/test_practitioner_page_smoke.py -q
"""

from __future__ import annotations

import os
import re
import sys

import httpx
import pytest


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
PAGES = (
    ("/app/patients", "/api/patients"),
    ("/app/cases", "/api/cases"),
    ("/app/reports", "/api/reports?include_archived=true"),
    ("/app/share-links", "/api/share-links"),
)
ERROR_TEXT = ("could not load", "internal server error")


def visible_markup(html: str) -> str:
    """Drop script and style blocks so fallback copy in JavaScript is not a failure."""
    without_scripts = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r"<style\b[^>]*>.*?</style>", " ", without_scripts, flags=re.IGNORECASE | re.DOTALL)


def _marker(text: str) -> str | None:
    lowered = text.lower()
    for marker in ERROR_TEXT:
        if marker in lowered:
            return marker
    return None


def page_problems(response: httpx.Response) -> list[str]:
    if response.status_code >= 500:
        return [f"HTTP {response.status_code}"]
    if response.status_code != 200:
        return [f"HTTP {response.status_code}"]
    marker = _marker(visible_markup(response.text))
    if marker:
        return [f"visible page contains {marker!r}"]
    return []


def data_problems(response: httpx.Response) -> list[str]:
    if response.status_code >= 500:
        return [f"HTTP {response.status_code}"]
    if response.status_code != 200:
        return [f"HTTP {response.status_code} (the page would show “Could not load”)"]
    marker = _marker(response.text)
    if marker:
        return [f"response contains {marker!r}"]
    try:
        response.json()
    except ValueError:
        return ["response was not JSON"]
    return []


def run_smoke(base_url: str, email: str, password: str) -> list[str]:
    failures: list[str] = []
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0, follow_redirects=False) as client:
            login = client.post("/auth/login", json={"email": email, "password": password})
            if login.status_code != 200:
                return [f"POST /auth/login -> HTTP {login.status_code}"]
            for page, data_url in PAGES:
                page_response = client.get(page)
                for problem in page_problems(page_response):
                    failures.append(f"GET {page} {problem}")
                data_response = client.get(data_url)
                for problem in data_problems(data_response):
                    failures.append(f"GET {data_url} {problem}")
    except httpx.HTTPError as exc:
        return [f"could not reach {base_url}: {exc.__class__.__name__}"]
    return failures


def test_script_fallback_text_is_not_a_page_error():
    response = httpx.Response(
        200,
        text="<div id='patients-error' style='display:none;'></div><script>show('Could not load patients.');</script>",
    )
    assert page_problems(response) == []


def test_visible_load_error_and_server_error_fail():
    visible = httpx.Response(200, text="<tr><td>Could not load shares.</td></tr>")
    crashed = httpx.Response(500, text="Internal Server Error")
    assert page_problems(visible) == ["visible page contains 'could not load'"]
    assert page_problems(crashed) == ["HTTP 500"]
    assert data_problems(crashed) == ["HTTP 500"]


def test_data_request_must_return_json():
    ok = httpx.Response(200, json={"items": []})
    denied = httpx.Response(401, json={"detail": "Not authenticated."})
    assert data_problems(ok) == []
    assert data_problems(denied) == ["HTTP 401 (the page would show “Could not load”)"]


@pytest.mark.skipif(
    not (os.getenv("GO360_SMOKE_EMAIL") and os.getenv("GO360_SMOKE_PASSWORD")),
    reason="Set GO360_SMOKE_EMAIL and GO360_SMOKE_PASSWORD to smoke-test the running app.",
)
def test_practitioner_pages_load():
    failures = run_smoke(
        os.getenv("GO360_SMOKE_BASE_URL", DEFAULT_BASE_URL),
        os.environ["GO360_SMOKE_EMAIL"],
        os.environ["GO360_SMOKE_PASSWORD"],
    )
    assert failures == []


def main() -> int:
    email = os.getenv("GO360_SMOKE_EMAIL", "")
    password = os.getenv("GO360_SMOKE_PASSWORD", "")
    if not email or not password:
        print(
            "Set GO360_SMOKE_EMAIL and GO360_SMOKE_PASSWORD, then run:\n"
            "  python tests/test_practitioner_page_smoke.py",
            file=sys.stderr,
        )
        return 2
    base_url = os.getenv("GO360_SMOKE_BASE_URL", DEFAULT_BASE_URL)
    failures = run_smoke(base_url, email, password)
    if failures:
        print(f"Practitioner page smoke failed against {base_url}:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"Practitioner pages loaded from {base_url}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
