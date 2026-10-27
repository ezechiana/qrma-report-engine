"""Versioned, conservative fingerprint of source assessment data (never HTML bytes)."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation


def text(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def number(value):
    try:
        result = Decimal(str(value).strip())
        if not result.is_finite():
            return None
        if result == 0:
            return "0"
        rendered = format(result, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    except (InvalidOperation, ValueError, TypeError):
        return None


def assessment_fingerprint(report, scan_metadata=None):
    """None means insufficient source identity/data; SHA detection still applies.

    Includes all measured rows, sorted without deduplication. No rounding, fuzzy
    matching, mutable patient-record fields, interpretation or image assets.
    """
    patient = report.patient
    name, sex = text(patient.full_name), text(patient.sex)
    sex = {"m": "male", "f": "female"}.get(sex, sex)
    if name in {"", "unknown", "n/a", "none", "patient", "anonymous"}:
        return None
    if sex not in {"male", "female"} or not patient.age or patient.age <= 0:
        return None
    metadata = scan_metadata or {}
    date = metadata.get("scan_date") or patient.scan_date
    time = metadata.get("scan_time") or patient.scan_time
    stamp = None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            stamp = datetime.strptime(f"{date} {time}", fmt).isoformat()
            break
        except ValueError:
            pass
    if stamp is None:
        return None
    rows = []
    for section in report.sections:
        for parameter in section.parameters:
            # Prefer exact source decimal text to a potentially rounded float.
            value = text(parameter.actual_value_text)
            if not value and text(section.source_title) in {"expert analysis", "hand analysis"}:
                continue  # Parser also emits narrative/advice rows, not measurements.
            numeric = number(value)
            if not value or not text(section.source_title) or not text(parameter.source_name):
                return None
            rows.append([text(section.source_title), text(parameter.source_name),
                         ["number", numeric] if numeric is not None else ["text", value]])
    if not rows:
        return None
    identity = [name, sex, patient.age, text(patient.date_of_birth),
                number(patient.height_cm), number(patient.weight_kg), stamp]
    payload = json.dumps(["go360-assessment-v1", identity, sorted(rows)],
                         ensure_ascii=False, separators=(",", ":"))
    return "v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_html(file_bytes):
    from bs4 import BeautifulSoup, UnicodeDammit
    from app.parser.html_parser import parse_html_report

    html = UnicodeDammit(file_bytes, is_html=True).unicode_markup
    if html is None:
        return None
    report = parse_html_report(html)
    # The full parser currently retains minute precision only. Supplement its
    # parsed metadata with source seconds, without changing the proven parser.
    source_text = BeautifulSoup(html, 'lxml').get_text(' ', strip=True)
    times = set(re.findall(
        r'Testing\s*Time:\s*(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}(?::\d{2})?)',
        source_text, flags=re.IGNORECASE))
    if len(times) > 1:
        return None  # Conflicting source timestamps are ambiguous.
    metadata = dict(zip(('scan_date', 'scan_time'), next(iter(times)))) if times else {}
    return assessment_fingerprint(report, metadata)
