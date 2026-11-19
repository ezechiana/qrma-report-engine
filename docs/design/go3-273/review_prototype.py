"""Offline design interaction checks, not production browser or authority tests.

Run from the repository root with Playwright/Chromium available. Evidence uses
only synthetic labels/IDs. No authentication, backend or object store is used.
"""
import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"


def main():
    EVIDENCE.mkdir(exist_ok=True)
    checks, errors, network = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"], executable_path=os.getenv("GO360_DESIGN_CHROMIUM") or None)
        browser_version = browser.version
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, reduced_motion="reduce")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: network.append(request.url) if request.url.startswith(("http:", "https:")) else None)
        page.goto((HERE / "prototype.html").as_uri())
        expect(page.locator(".clinic")).to_have_count(2)
        expect(page.locator(".clinic h3").first).to_have_text("Harbour Clinic")
        assert len(set(page.locator(".clinic .ref").all_text_contents())) == 2
        page.screenshot(path=str(EVIDENCE / "desktop.png"), full_page=True)
        checks.append("Same-named clinics have distinct visible read-only references")

        page.locator(".account summary").click()
        page.get_by_role("link", name="Organisations & clinics", exact=True).click()
        expect(page.locator("#main")).to_be_focused()
        checks.append("Account entry moves focus to the administrative page")

        page.locator("#create-clinic").click()
        expect(page.locator("#record-name")).to_be_focused()
        for _ in range(12):
            page.keyboard.press("Tab")
            assert page.evaluate("document.getElementById('creation').contains(document.activeElement)")
        page.keyboard.press("Escape")
        expect(page.locator("#creation")).not_to_be_visible()
        expect(page.locator("#create-clinic")).to_be_focused()
        checks.append("Dialog initial focus, native focus containment, Escape and focus restoration")

        page.locator("#create-clinic").click()
        page.locator("#save").click()
        expect(page.locator("#name-error")).to_be_visible()
        expect(page.locator("#record-name")).to_have_attribute("aria-invalid", "true")
        expect(page.locator("#record-name")).to_be_focused()
        page.locator("#record-name").fill("x" * 256)
        page.locator("#save").click()
        assert len(page.evaluate("designReview.snapshot().requests")) == 0
        page.locator("#record-name").fill("Accessible clinic")
        page.screenshot(path=str(EVIDENCE / "creation-dialog.png"), full_page=True)
        page.locator("#cancel").click()
        checks.append("Blank and overlength names fail locally without a synthetic request")

        page.locator("#outcome").select_option("lost-after")
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Harbour Clinic")
        page.locator("#save").click()
        expect(page.locator("#save-error")).to_contain_text("couldn’t confirm")
        assert page.locator("#record-name").evaluate("el => el.readOnly")
        page.screenshot(path=str(EVIDENCE / "uncertain-save.png"), full_page=True)
        page.locator("#cancel").click()
        expect(page.locator("#organisation")).to_be_disabled()
        page.locator("#create-clinic").click()
        page.locator("#save").click()
        expect(page.locator("#creation")).not_to_be_visible()
        expect(page.locator(".clinic")).to_have_count(3)
        state = page.evaluate("designReview.snapshot()")
        assert state["committed"] == 1
        assert len(state["requests"]) == 2
        assert state["requests"][0] == state["requests"][1]
        checks.append("Lost response after commit, close/resume and retry reuse the exact intent; one creation")

        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Harbour Clinic")
        page.locator("#save").click()
        expect(page.locator(".clinic")).to_have_count(4)
        state = page.evaluate("designReview.snapshot()")
        assert state["committed"] == 2
        assert state["requests"][-1]["key"] != state["requests"][0]["key"]
        checks.append("Separate same-name creation uses a fresh key and a distinct record")

        page.locator("#reset").click()
        page.locator("#outcome").select_option("lost-before")
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Retry clinic")
        page.locator("#save").click()
        expect(page.locator("#save-error")).to_be_visible()
        assert page.evaluate("designReview.snapshot().committed") == 0
        page.locator("#save").click()
        expect(page.locator("#creation")).not_to_be_visible()
        state = page.evaluate("designReview.snapshot()")
        assert state["committed"] == 1 and state["requests"][0] == state["requests"][1]
        checks.append("Timeout before commit retries the same intent and creates once")

        page.locator("#reset").click()
        page.locator("#outcome").select_option("refresh")
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Saved clinic")
        page.locator("#save").click()
        expect(page.locator("#page-status")).to_contain_text("saved")
        expect(page.locator(".clinic")).to_have_count(3)
        page.get_by_role("button", name="Refresh list", exact=True).click()
        assert len(page.evaluate("designReview.snapshot().requests")) == 1
        checks.append("Confirmed save remains visible when refresh fails; refresh does not repeat creation")

        page.locator("#reset").click()
        page.locator("#outcome").select_option("validation")
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Correctable clinic")
        page.locator("#save").click()
        expect(page.locator("#name-error")).to_be_visible()
        expect(page.locator("#record-name")).to_be_editable()
        expect(page.locator("#record-name")).to_have_value("Correctable clinic")
        page.locator("#cancel").click()
        checks.append("Known validation refusal preserves input and makes it editable")

        page.locator("#reset").click()
        page.locator("#outcome").select_option("conflict")
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Conflicting clinic")
        page.locator("#save").click()
        expect(page.locator("#save-error")).to_contain_text("conflicts")
        expect(page.locator("#save")).to_be_disabled()
        page.locator("#cancel").click()
        old_key = page.evaluate("designReview.snapshot().intent.key")
        page.get_by_role("button", name="Start a separate creation", exact=True).click()
        expect(page.locator("#record-name")).to_be_empty()
        expect(page.locator("#dialog-description")).to_contain_text("earlier request")
        page.locator("#record-name").fill("Separate clinic")
        page.locator("#save").click()
        expect(page.locator("#creation")).not_to_be_visible()
        assert page.evaluate("designReview.snapshot().requests.at(-1).key") != old_key
        checks.append("Conflict blocks blind retry; only explicit separate creation starts a new intent")

        page.locator("#reset").click()
        page.locator("#outcome").select_option("forbidden")
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("Revoked clinic")
        page.locator("#save").click()
        expect(page.locator("#save-error")).to_contain_text("access has changed")
        expect(page.locator("#save")).to_be_disabled()
        page.locator("#cancel").click()
        expect(page.locator(".clinic")).to_have_count(0)
        expect(page.locator("#registry")).to_contain_text("don’t have access")
        assert page.evaluate("designReview.snapshot().orgs.length") == 0
        checks.append("Synthetic access revocation clears affected data and stops mutation")

        page.locator("#reset").click()
        page.locator("#organisation").select_option("91c2d600-4521-4310-9827-c22ae0150002")
        expect(page.locator(".clinic")).to_have_count(0)
        expect(page.locator("#registry")).to_contain_text("No clinics in this organisation")
        checks.append("Changing organisation clears the previous clinic rows")

        for scene in ("choose", "org-empty", "clinic-empty", "loading", "clinic-loading", "org-error", "clinic-error", "denied", "unverified", "missing", "signed-out", "platform", "support"):
            page.locator("#scene").select_option(scene)
            assert page.locator("h1").count() == 1
            if scene in ("loading", "denied", "unverified", "missing", "signed-out", "org-error"):
                expect(page.locator("#create-org")).to_be_hidden()
                expect(page.locator(".clinic")).to_have_count(0)
            if scene in ("clinic-error", "clinic-loading"):
                expect(page.locator("#create-clinic")).to_be_disabled()
            if scene == "org-empty":
                page.locator("#first-org").click()
                page.locator("#record-name").fill("First organisation")
                page.locator("#save").click()
                expect(page.locator("#creation")).not_to_be_visible()
                assert page.evaluate("designReview.snapshot().orgs.length") == 1
                assert page.evaluate("designReview.snapshot().clinics.length") == 0
        checks.append("Thirteen additional page states render; organisation creation does not auto-create a clinic")

        page.locator("#reset").click()
        page.locator("#create-clinic").click()
        page.locator("#record-name").fill("😀" * 255)
        page.locator("#save").click()
        expect(page.locator("#creation")).not_to_be_visible()
        checks.append("255 Unicode code points accepted consistently with the API boundary")

        for width in (390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            page.locator("#reset").click()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if width == 390:
                page.screenshot(path=str(EVIDENCE / "mobile.png"), full_page=True)
            page.locator("#create-clinic").click()
            assert page.locator("#creation").evaluate("el => el.scrollWidth <= el.clientWidth")
            if width == 390:
                page.screenshot(path=str(EVIDENCE / "mobile-dialog.png"), full_page=False)
            page.keyboard.press("Escape")
        checks.append("390px and 320px page/dialog layouts have no horizontal overflow")

        assert not errors, errors
        assert not network, network
        browser.close()
    report = {"artifact": "GO3-273 draft prototype v1", "prototype_sha256": hashlib.sha256((HERE / "prototype.html").read_bytes()).hexdigest(),
              "browser_version": browser_version, "browser_path_override": bool(os.getenv("GO360_DESIGN_CHROMIUM")),
              "checks": checks, "count": len(checks), "page_errors": errors, "http_requests": network,
              "limits": "Offline synthetic interactions only; not production authentication, API, race, storage, screen-reader or UAT evidence."}
    (EVIDENCE / "review-checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
