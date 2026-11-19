"""Probe native session-storage behavior using synthetic data on loopback only."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright


def main():
    url = os.environ["GO360_READINESS_PREVIEW_URL"]
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Use a synthetic loopback preview only")
    key = "go360.registry.intent.v1:00000000-0000-4000-8000-000000000001"
    intent = {"version": 1, "actor_id": "00000000-0000-4000-8000-000000000001",
              "kind": "clinic", "organisation_id": "00000000-0000-4000-8000-000000000002",
              "key": str(uuid4()), "payload": {"display_name": "Synthetic readiness clinic"}, "state": "unknown"}
    value = json.dumps(intent, sort_keys=True)
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("GO360_DESIGN_CHROMIUM") or None)
        version = browser.version
        context = browser.new_context()
        context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"} else route.abort())
        page = context.new_page()
        page.goto(url)
        page.evaluate("([key,value]) => sessionStorage.setItem(key,value)", [key, value])
        assert page.evaluate("key => sessionStorage.getItem(key)", key) == value
        checks.append("Session storage supports exact synthetic intent round-trip")
        page.reload()
        assert page.evaluate("key => sessionStorage.getItem(key)", key) == value
        checks.append("Same-tab reload preserves the original pending key/payload")
        with page.expect_popup() as popup_info:
            page.evaluate("url => window.open(url, '_blank')", url)
        popup = popup_info.value
        popup.wait_for_load_state()
        assert popup.evaluate("key => sessionStorage.getItem(key)", key) == value
        checks.append("Same-origin opener-created tab can copy the pending intent unchanged")
        page.evaluate("key => sessionStorage.removeItem(key)", key)
        assert page.evaluate("key => sessionStorage.getItem(key)", key) is None
        assert popup.evaluate("key => sessionStorage.getItem(key)", key) == value
        checks.append("Clearing the opener does NOT clear its copied tab: explicit cross-tab invalidation is needed")
        fresh = browser.new_context()
        fresh_page = fresh.new_page()
        fresh_page.goto(url)
        assert fresh_page.evaluate("key => sessionStorage.getItem(key)", key) is None
        checks.append("A fresh browser context has no pending journal")
        fresh.close()
        context.close()
        browser.close()
    report = {"issue": "GO3-188", "browser_version": version, "checks": checks, "count": len(checks),
              "tuple_sha256": hashlib.sha256(value.encode()).hexdigest(),
              "limits": "Native browser storage primitives only. No production helper, authentication, authority or privacy sign-off is asserted."}
    (Path(__file__).parent / "storage-checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
