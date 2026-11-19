"""Capture the real offline QRMA build while replacing only object storage."""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.parser.html_parser import parse_html_report
from app.services import pdf_service, report_builder

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app/tests/fixtures/qrma/ResultOne.htm"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def baseline_fixture_bytes(raw):
    # The reviewed cp1252 export and its existing baseline use CRLF. Git's
    # checkout conversion must not change embedded source HTML in this test.
    return raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_fixture_baseline_is_independent_of_checkout_newlines(newline):
    raw = SOURCE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    assert digest(baseline_fixture_bytes(raw)) == "8c0bdcb469ee4c2a7334ead1a3923141f7c5f772fbce402c35169d8c14be15eb"


def json_digest(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def baseline_for_sources(profiles, root):
    matches = []
    for name, profile in profiles.items():
        source_files = {
            path: digest((root / path).read_text(encoding="utf-8").replace("\r\n", "\n").encode())
            for path in profile["source_files"]
        }
        if source_files == profile["source_files"]:
            matches.append((name, profile["outputs"]))
    assert len(matches) == 1, "QRMA source revision needs baseline review before promotion"
    return matches[0]


def test_unreviewed_renderer_cannot_reuse_a_baseline(tmp_path):
    profiles = json.loads(Path(__file__).with_name("qrma_baseline.json").read_text())
    for path in next(iter(profiles.values()))["source_files"]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
    parser = tmp_path / "app/parser/html_parser.py"
    with parser.open("a", encoding="utf-8") as output:
        output.write("\n# Synthetic unreviewed revision\n")
    with pytest.raises(AssertionError, match="needs baseline review"):
        baseline_for_sources(profiles, tmp_path)


def test_qrma_build_matches_baseline(monkeypatch, record_property):
    # Match the reviewed baseline explicitly, independent of a developer .env.
    monkeypatch.setenv("REPORT_RECOMMENDATION_MODE", "vitalhealth_clinical_optimised")
    fixed_time = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(report_builder, "datetime", SimpleNamespace(now=lambda zone: fixed_time.astimezone(zone)))
    profiles = json.loads(Path(__file__).with_name("qrma_baseline.json").read_text())
    profile_name, baseline = baseline_for_sources(profiles, ROOT)
    raw = baseline_fixture_bytes(SOURCE.read_bytes())
    parsed = parse_html_report(raw.decode("cp1252"))
    parsed.patient.full_name = "Alex Morgan"
    parsed_sha256 = json_digest(parsed.model_dump(mode="json"))
    artifacts = {}

    def save_html(html, key):
        artifacts["html"] = html.encode()
        return key

    def save_pdf_bytes(key, content, content_type):
        assert content_type == "application/pdf"
        artifacts["pdf"] = content
        return key

    monkeypatch.setattr(report_builder, "save_html", save_html)
    monkeypatch.setattr(pdf_service, "upload_bytes", save_pdf_bytes)
    result = asyncio.run(report_builder.build_report(
        parsed, html_filename="synthetic/clinic_a/report-v1.html",
        pdf_filename="synthetic/clinic_a/report-v1.pdf",
    ))
    assert result["build_warnings"] == []
    assert result["html_path"] == "synthetic/clinic_a/report-v1.html"
    assert result["pdf_path"] == "synthetic/clinic_a/report-v1.pdf"
    assert artifacts["pdf"].startswith(b"%PDF-")
    assert artifacts["pdf"].rstrip().endswith(b"%%EOF")
    assert len(artifacts["pdf"]) > 1000
    observed = {
        "source_sha256": digest(raw),
        "parsed_sha256": parsed_sha256,
        "viewer_sha256": json_digest(result["viewer_payload"]),
        "html_sha256": digest(artifacts["html"]),
    }
    if os.getenv("GO360_S0_ARTIFACT_DIR"):
        output = Path(os.environ["GO360_S0_ARTIFACT_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "viewer.json").write_text(json.dumps(result["viewer_payload"], sort_keys=True, indent=2), encoding="utf-8")
        (output / "report.html").write_bytes(artifacts["html"])
    assert observed == baseline
    record_property("qrma_fixture_version", "s0-v1")
    record_property("qrma_baseline_profile", profile_name)
    record_property("qrma_pdf_sha256", digest(artifacts["pdf"]))
    record_property("qrma_pdf_bytes", len(artifacts["pdf"]))
    for key, value in observed.items():
        record_property(key, value)
