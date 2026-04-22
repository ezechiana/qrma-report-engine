from __future__ import annotations

import os
import re
import uuid
from pathlib import Path


TEMP_UPLOAD_DIR = Path(os.getenv("TEMP_UPLOAD_DIR", "/tmp/qrma_imports"))
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

from app.services.storage_service import upload_bytes, download_bytes


def save_temp_html(file_bytes: bytes) -> tuple[str, str]:
    temp_id = str(uuid.uuid4())
    path = TEMP_UPLOAD_DIR / f"{temp_id}.html"
    path.write_bytes(file_bytes)
    return temp_id, str(path)


def load_temp_html(temp_id: str) -> bytes:
    path = TEMP_UPLOAD_DIR / f"{temp_id}.html"
    return path.read_bytes()


def save_case_html(case_id: str, user_id: str, file_bytes: bytes) -> str:
    """
    Persist the uploaded QRMA HTML against the case using S3 storage.

    Key shape:
      cases/<user_id>/<case_id>/raw_scan.html
    """
    key = f"cases/{user_id}/{case_id}/raw_scan.html"
    return upload_bytes(key, file_bytes, "text/html; charset=utf-8")


def load_case_html(path: str) -> bytes:
    """
    `path` is now the S3 object key stored in the DB.
    """
    return download_bytes(path)


def parse_qrma_html(file_bytes: bytes) -> dict:
    """
    MVP import parser for pre-filling patient metadata from QRMA HTML.

    This parser is intentionally tolerant. It only tries to extract:
    - name
    - DOB
    - age
    - sex
    - height
    - weight
    - scan date / time

    It does NOT build the full clinical report object.
    The full report generation should use the legacy engine adapter in report_service.py.
    """
    import re
    from datetime import datetime

    text = file_bytes.decode("utf-8", errors="ignore")

    def find(patterns: list[str]) -> str | None:
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if m:
                value = m.group(1).strip()
                value = re.sub(r"<[^>]+>", "", value).strip()
                if value:
                    return value
        return None

    name = find([
        r"Name[:\s]*</?[^>]*>\s*([^<\n\r]+)",
        r"Name[:\s]+([^\n\r<]+)",
    ])
    sex = find([
        r"Sex[:\s]*</?[^>]*>\s*([^<\n\r]+)",
        r"Sex[:\s]+([^\n\r<]+)",
    ])
    age = find([
        r"Age[:\s]*</?[^>]*>\s*([^<\n\r]+)",
        r"Age[:\s]+([^\n\r<]+)",
    ])
    dob = find([
        r"DOB[:\s]*</?[^>]*>\s*([^<\n\r]+)",
        r"Date of Birth[:\s]*</?[^>]*>\s*([^<\n\r]+)",
    ])
    height = find([
        r"Height[:\s]*</?[^>]*>\s*([^<\n\r]+)",
        r"Height[:\s]+([^\n\r<]+)",
    ])
    weight = find([
        r"Weight[:\s]*</?[^>]*>\s*([^<\n\r]+)",
        r"Weight[:\s]+([^\n\r<]+)",
    ])
    
    def normalise_whitespace(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    clean_text = normalise_whitespace(re.sub(r"<[^>]+>", " ", text))

    testing_time = None
    testing_time_match = re.search(
        r"Testing\s*Time:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)",
        clean_text,
        flags=re.IGNORECASE,
    )
    if testing_time_match:
        testing_time = testing_time_match.group(1).strip()

    scan_date = None
    scan_time = None

    if testing_time and " " in testing_time:
        parts = testing_time.split()
        if len(parts) >= 2:
            scan_date = parts[0]
            scan_time = parts[1]

    # fallback legacy fields if Testing Time is missing
    if not scan_date:
        legacy_scan_date = re.search(
            r"Scan\s*Date:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
            clean_text,
            flags=re.IGNORECASE,
        )
        if legacy_scan_date:
            scan_date = legacy_scan_date.group(1).strip()

    if not scan_time:
        legacy_scan_time = re.search(
            r"Scan\s*Time:\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)",
            clean_text,
            flags=re.IGNORECASE,
        )
        if legacy_scan_time:
            scan_time = legacy_scan_time.group(1).strip()


    def parse_float(value: str | None) -> float | None:
        if not value:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        return float(m.group(0)) if m else None

    first_name = None
    last_name = None
    full_name = name
    if name:
        parts = name.split()
        if len(parts) >= 2:
            first_name = parts[0]
            last_name = " ".join(parts[1:])
        elif len(parts) == 1:
            first_name = parts[0]
            last_name = ""

    parsed_scan_datetime = None

    if scan_date and scan_time:
        for fmt in (
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                parsed_scan_datetime = datetime.strptime(f"{scan_date} {scan_time}", fmt)
                break
            except Exception:
                continue


    return {
        "source_patient_data": {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "date_of_birth": dob,
            "age": int(age) if age and str(age).isdigit() else None,
            "sex": sex,
            "height_cm": parse_float(height),
            "weight_kg": parse_float(weight),
        },
        "scan_metadata": {
            "scan_date": scan_date,
            "scan_time": scan_time,
            "scan_datetime": parsed_scan_datetime,
        },
    }



