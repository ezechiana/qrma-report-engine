from __future__ import annotations

import os
import re
import uuid
import hashlib
from pathlib import Path

from app.services.storage_service import upload_bytes, download_bytes


TEMP_UPLOAD_DIR = Path(os.getenv("TEMP_UPLOAD_DIR", "/tmp/qrma_imports"))
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def compute_html_hash(file_bytes: bytes) -> str:
    """
    Stable fingerprint of uploaded HTML.
    """
    return hashlib.sha256(file_bytes).hexdigest()


def save_temp_html(file_bytes: bytes) -> tuple[str, str]:
    temp_id = str(uuid.uuid4())
    path = TEMP_UPLOAD_DIR / f"{temp_id}.html"
    path.write_bytes(file_bytes)
    return temp_id, str(path)


def load_temp_html(temp_id: str) -> bytes:
    path = TEMP_UPLOAD_DIR / f"{temp_id}.html"
    return path.read_bytes()


def save_case_html(
    case_id: str,
    user_id: str,
    file_bytes: bytes,
    db=None,
    allow_duplicate: bool = False,
) -> str:
    """
    Persist the uploaded QRMA HTML against the case using S3 storage.
    Also prevents duplicate uploads using SHA256 hash unless allow_duplicate=True.
    Returns the FINAL stored S3 key.
    """
    html_hash = compute_html_hash(file_bytes)

    if db is not None and not allow_duplicate:
        from app.db.models import Case

        existing = (
            db.query(Case)
            .filter(
                Case.user_id == user_id,
                Case.raw_scan_hash == html_hash,
            )
            .first()
        )

        if existing:
            raise ValueError("This scan has already been imported.")

    key = f"cases/{user_id}/{case_id}/raw_scan.html"

    # IMPORTANT: store the normalized/final S3 key returned by upload_bytes
    stored_key = upload_bytes(key, file_bytes, "text/html; charset=utf-8")

    if db is not None:
        from app.db.models import Case

        case = db.query(Case).filter(Case.id == case_id).first()
        if case:
            case.raw_scan_hash = html_hash
            db.commit()

    return stored_key


def load_case_html(path: str) -> bytes:
    """
    `path` is now the S3 object key stored in the DB.
    """
    return download_bytes(path)


def _search(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def _normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace("cm", "").replace("kg", "").strip()
    try:
        return float(value)
    except Exception:
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None


def parse_qrma_html(file_bytes: bytes) -> dict:
    """
    MVP import parser for pre-filling patient metadata from QRMA HTML.

    Uses the same patient-extraction pattern as the fuller HTML parser:
    Name / Sex / Age / Figure / Testing Time
    """
    from datetime import datetime

    text = file_bytes.decode("utf-8", errors="ignore")
    clean = _normalise_whitespace(re.sub(r"<[^>]+>", " ", text))

    name = _search(r"Name:\s*(.+?)(?:Sex:|Age:|Figure:|Testing Time:)", clean)
    if name:
        name = _normalise_whitespace(name)

    sex = _search(r"Sex:\s*([A-Za-z]+)", clean)

    age_raw = _search(r"Age:\s*(\d+)", clean)
    age = int(age_raw) if age_raw and age_raw.isdigit() else None

    dob = None
    dob = (
        _search(r"DOB:\s*([^\s]+)", clean)
        or _search(r"Date of Birth:\s*([^\s]+)", clean)
    )

    figure = _search(r"Figure:\s*([^\n\r]+?)(?:Testing Time:|$)", clean)

    height_cm = None
    weight_kg = None

    if figure:
        height_match = re.search(r"([\d\.]+)\s*cm", figure, flags=re.IGNORECASE)
        weight_match = re.search(r"([\d\.]+)\s*kg", figure, flags=re.IGNORECASE)

        if height_match:
            height_cm = _to_float(height_match.group(1))
        if weight_match:
            weight_kg = _to_float(weight_match.group(1))

    # fallback explicit labels if Figure did not provide values
    if height_cm is None:
        explicit_height = (
            _search(r"Height:\s*([^\s]+(?:\s*cm)?)", clean)
            or _search(r"Height\s+([^\s]+(?:\s*cm)?)", clean)
        )
        height_cm = _to_float(explicit_height)

    if weight_kg is None:
        explicit_weight = (
            _search(r"Weight:\s*([^\s]+(?:\s*kg)?)", clean)
            or _search(r"Weight\s+([^\s]+(?:\s*kg)?)", clean)
        )
        weight_kg = _to_float(explicit_weight)

    testing_time = _search(
        r"Testing\s*Time:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)",
        clean,
    )

    scan_date = None
    scan_time = None
    parsed_scan_datetime = None

    if testing_time and " " in testing_time:
        parts = testing_time.split()
        if len(parts) >= 2:
            scan_date = parts[0]
            scan_time = parts[1]

    if not scan_date:
        legacy_scan_date = _search(
            r"Scan\s*Date:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
            clean,
        )
        if legacy_scan_date:
            scan_date = legacy_scan_date

    if not scan_time:
        legacy_scan_time = _search(
            r"Scan\s*Time:\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)",
            clean,
        )
        if legacy_scan_time:
            scan_time = legacy_scan_time

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

    return {
        "source_patient_data": {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "date_of_birth": dob,
            "age": age,
            "sex": sex,
            "height_cm": height_cm,
            "weight_kg": weight_kg,
        },
        "scan_metadata": {
            "scan_date": scan_date,
            "scan_time": scan_time,
            "scan_datetime": parsed_scan_datetime,
        },
    }