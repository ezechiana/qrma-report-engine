import re
from bs4 import BeautifulSoup
from datetime import datetime

from app.models.schema import ParsedReport, Patient, ReportSection, ParameterResult
from app.services.category_completeness_validator import attach_category_completeness


SECTION_TITLE_FALLBACKS = [
    "Gynecology",
    "Breast",
    "Prostate",
    "Vitamin",
    "Amino Acid",
    "Coenzyme",
    "Thyroid",
    "Allergy",
    "Obesity",
    "Skin",
    "Eye",
    "Collagen",
    "Lecithin",
    "Fatty acid",
    "Essential Fatty Acid",
    "ADHD",
    "Adolescent Intelligence",
    "Adolescent Growth Index",
    "Expert analysis",
    "Hand analysis",
]

EXPECTED_CATEGORIES_MALE = [
    "Cardiovascular and Cerebrovascular",
    "Gastrointestinal Function",
    "Large Intestine Function",
    "Liver Function",
    "Gallbladder Function",
    "Pancreatic Function",
    "Kidney Function",
    "Lung Function",
    "Brain Nerve",
    "Bone Disease",
    "Bone Mineral Density",
    "Rheumatoid Bone Disease",
    "Bone Growth Index",
    "Blood Sugar",
    "Trace Element",
    "Vitamin",
    "Amino Acid",
    "Coenzyme",
    "Essential Fatty Acid",
    "Endocrine System",
    "Immune System",
    "Thyroid",
    "Human Toxin",
    "Heavy Metal",
    "Basic Physical Quality",
    "Allergy",
    "Obesity",
    "Skin",
    "Eye",
    "Collagen",
    "Channels and collaterals",
    "Pulse of heart and brain",
    "Blood lipids",
    "Prostate",
    "Male Sexual Function",
    "Sperm and semen",
    "Male Hormone",
    "Human Immunity",
    "Human Consciousness Level",
    "Respiratory Function",
    "Lecithin",
    "Fatty acid",
    "Element of Human",
    "Expert analysis",
    "Hand analysis",
]

EXPECTED_CATEGORIES_FEMALE = [
    "Cardiovascular and Cerebrovascular",
    "Gastrointestinal Function",
    "Large Intestine Function",
    "Liver Function",
    "Gallbladder Function",
    "Pancreatic Function",
    "Kidney Function",
    "Lung Function",
    "Brain Nerve",
    "Bone Disease",
    "Bone Mineral Density",
    "Rheumatoid Bone Disease",
    "Bone Growth Index",
    "Blood Sugar",
    "Trace Element",
    "Vitamin",
    "Amino Acid",
    "Coenzyme",
    "Essential Fatty Acid",
    "Endocrine System",
    "Immune System",
    "Thyroid",
    "Human Toxin",
    "Heavy Metal",
    "Basic Physical Quality",
    "Allergy",
    "Obesity",
    "Skin",
    "Eye",
    "Collagen",
    "Channels and collaterals",
    "Pulse of heart and brain",
    "Blood lipids",
    "Gynecology",
    "Breast",
    "Menstrual cycle",
    "Female Hormone",
    "Human Immunity",
    "Human Consciousness Level",
    "Respiratory Function",
    "Lecithin",
    "Fatty acid",
    "Element of Human",
    "Expert analysis",
    "Hand analysis",
]

EXPECTED_CATEGORIES_CHILD = [
    "Trace Element",
    "Vitamin",
    "Amino Acid",
    "Coenzyme",
    "Essential Fatty Acid",
    "ADHD",
    "Adolescent Intelligence",
    "Adolescent Growth Index",
    "Lecithin",
    "Fatty acid",
]

NON_STANDARD_REPORTS = {"expert analysis", "hand analysis"}


def _search(pattern: str, text: str):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def _to_float(value: str | None):
    if value is None:
        return None
    value = value.strip().replace("cm", "").replace("kg", "").strip()
    try:
        return float(value)
    except Exception:
        return None


def _normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _norm_key(text: str) -> str:
    return re.sub(r"[\s\-_]+", "", (text or "").strip().lower())


def _extract_patient(text: str) -> Patient:
    clean = _normalise_whitespace(text)

    name = _search(r"Name:\s*(.+?)(?:Sex:|Age:|Figure:|Testing Time:)", clean)
    if name:
        name = re.sub(r"<[^>]+>", " ", name)
        name = re.sub(r"</?td[^>]*>", " ", name, flags=re.IGNORECASE)
        name = _normalise_whitespace(name)

    sex = _search(r"Sex:\s*([A-Za-z]+)", clean)

    age_raw = _search(r"Age:\s*(\d+)", clean)
    age = int(age_raw) if age_raw and age_raw.isdigit() else None

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

    testing_time = _search(
        r"Testing\s*Time:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}\s+[0-9]{1,2}:[0-9]{2})",
        clean,
    )

    scan_date = None
    scan_time = None
    if testing_time and " " in testing_time:
        parts = testing_time.split()
        if len(parts) >= 2:
            scan_date = parts[0]
            scan_time = parts[1]

    return Patient(
        full_name=name,
        sex=sex,
        age=age,
        height_cm=height_cm,
        weight_kg=weight_kg,
        scan_date=scan_date,
        scan_time=scan_time,
    )


def _extract_result_image_code(cell) -> str | None:
    img = cell.find("img")
    if not img:
        return None
    src = img.get("src", "")
    code_match = re.search(r"(YC\d+)", src, flags=re.IGNORECASE)
    return code_match.group(1).upper() if code_match else None


def _looks_like_header_row(header_text: str) -> bool:
    text = header_text.lower()
    return (
        "testing item" in text
        and "normal range" in text
        and "actual" in text
        and ("testing result" in text or "result" in text)
    )


def _extract_section_title(page_text: str):
    clean = _normalise_whitespace(page_text)

    match = re.search(
        r"\(\s*([^)]*?)\s*\)\s*Analysis\s*Report\s*Card",
        clean,
        flags=re.IGNORECASE,
    )
    if match:
        return _normalise_whitespace(match.group(1))

    match = re.search(r"\b(Expert analysis)\s+Report\b", clean, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"\b(Hand analysis)\s+Report\b", clean, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    lower_clean = clean.lower()
    for title in SECTION_TITLE_FALLBACKS:
        if title.lower() in lower_clean:
            return title

    return None


def _split_html_into_page_chunks(html: str):
    """
    Prefer page DIV extraction first, then fall back to title-based splitting.
    This is more robust for QRMA HTML where some section titles are split by tags.
    """
    soup = BeautifulSoup(html, "lxml")

    page_divs = []
    for div in soup.find_all("div"):
        style = (div.get("style") or "").lower().replace(" ", "")
        if "page-break-after:always" in style:
            page_divs.append(str(div))

    if page_divs:
        return page_divs

    cleaned = html.replace("\r\n", "\n")

    split_pattern = r"""
        (?=
            \(
                [^)]+
            \)\s*Analysis\s*Report\s*Card
        )
        |
        (?=
            Expert\s+analysis\s+Report
        )
        |
        (?=
            Hand\s+analysis\s+Report
        )
    """

    parts = re.split(split_pattern, cleaned, flags=re.IGNORECASE | re.VERBOSE)
    chunks = []

    for part in parts:
        piece = part.strip()
        if not piece:
            continue

        title = _extract_section_title(piece)
        if not title:
            continue

        if (
            "Analysis Report Card" in piece
            or any(t.lower() in piece.lower() for t in SECTION_TITLE_FALLBACKS)
        ):
            chunks.append(piece)

    return chunks


def _parse_standard_section_tables(page_soup: BeautifulSoup, source_title: str):
    parameters = []

    for table in page_soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_text = _normalise_whitespace(
            " ".join(cell.get_text(" ", strip=True) for cell in rows[0].find_all(["td", "th"]))
        )

        if not _looks_like_header_row(header_text):
            continue

        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue

            source_name = _normalise_whitespace(cols[0].get_text(" ", strip=True))
            if not source_name:
                continue

            normal_range_text = _normalise_whitespace(cols[1].get_text(" ", strip=True))
            actual_value_text = _normalise_whitespace(cols[2].get_text(" ", strip=True))

            try:
                numeric_value = float(actual_value_text)
            except Exception:
                numeric_value = None

            parameters.append(
                ParameterResult(
                    source_title=source_title,
                    source_name=source_name,
                    normal_range_text=normal_range_text,
                    actual_value_text=actual_value_text,
                    actual_value_numeric=numeric_value,
                    result_image_code=_extract_result_image_code(cols[3]),
                )
            )

        if parameters:
            break

    return parameters


def _parse_reference_standard_table(page_soup: BeautifulSoup) -> dict:
    results = {}
    target_table = None

    for table in page_soup.find_all("table"):
        text = _normalise_whitespace(table.get_text(" ", strip=True)).lower()
        if "reference standard" in text and "normal(-)" in text:
            target_table = table
            break

    if not target_table:
        return results

    current_marker = None

    for row in target_table.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) != 3:
            continue

        left = _normalise_whitespace(cols[0].get_text(" ", strip=True))
        mid = _normalise_whitespace(cols[1].get_text(" ", strip=True))
        right = _normalise_whitespace(cols[2].get_text(" ", strip=True))

        if not mid and not right:
            continue

        if left and left.lower() != "reference standard:":
            current_marker = left.rstrip(":")
            results[_norm_key(current_marker)] = {
                "marker_name": current_marker,
                "bands_raw": [b for b in [mid, right] if b],
            }
        elif current_marker:
            results[_norm_key(current_marker)]["bands_raw"].extend([b for b in [mid, right] if b])

    return results


def _attach_reference_bands(parameters, reference_map: dict):
    for param in parameters:
        hit = reference_map.get(_norm_key(param.source_name))
        if hit:
            param.reference_bands_raw = hit.get("bands_raw", [])
    return parameters


BODY_COMP_PATTERNS = [
    (r"\(1\)\s*Intracellular\s*Fluid\s*\(L\)", "1.The componential analysis of body: (1)Intracellular Fluid (L)"),
    (r"\(2\)\s*Extracellular\s*Fluid\s*\(L\)", "1.The componential analysis of body: (2) Extracellular Fluid(L)"),
    (r"\(3\)\s*Protein\s*\(Kg\)", "1.The componential analysis of body: (3)Protein(Kg)"),
    (r"\(4\)\s*Inorganic\s*substance\s*\(Kg\)", "1.The componential analysis of body: (4)Inorganic substance(Kg)"),
    (r"\(5\)\s*Body\s*fat\s*\(Kg\)", "1.The componential analysis of body: (5)Body fat (Kg)"),
]

FAT_ANALYSIS_LABELS = [
    ("1.Height(Cm)", r"1\.\s*Height\(Cm\)"),
    ("2.Weight(Kg)", r"2\.\s*Weight\(Kg\)"),
    ("3.Muscle mass", r"3\.\s*Muscle\s*mass"),
    ("4.Body fat content", r"4\.\s*Body\s*fat\s*content"),
    ("5.Body fat percentage", r"5\.\s*Body fat percentage"),
    ("6.Ratio of abdominal fat", r"6\.\s*Ratio\s*of\s*abdominal\s*fat"),
]

NOURISHMENT_LABELS = [
    "Obesity degree of body(ODB)",
    "Body mass index (BMI)",
    "Basal metabolism rate(BMR)",
    "Body cell mass (BCM)",
]

WEIGHT_CONTROL_LABELS = [
    "Target weight",
    "Weight control",
    "Fat control",
    "Muscle control",
]

BODY_FORM_LABEL = "Body form assessment"


def _find_measurement_after_label(page_text: str, label_pattern: str):
    pattern = label_pattern + r".*?([0-9]+(?:\.[0-9]+)?(?:\s*[A-Za-z/%²]+)?)"
    m = re.search(pattern, page_text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_body_composition_section(page_soup: BeautifulSoup, source_title: str):
    parameters = []
    page_text = _normalise_whitespace(page_soup.get_text("\n", strip=True))

    for pattern, label in BODY_COMP_PATTERNS:
        m = re.search(pattern + r"\s*([0-9]+(?:\.[0-9]+)?)", page_text, flags=re.IGNORECASE)
        if m:
            parameters.append(
                ParameterResult(
                    source_title=source_title,
                    source_name=label,
                    actual_value_text=m.group(1),
                )
            )

    for label, pattern in FAT_ANALYSIS_LABELS:
        parameters.append(
            ParameterResult(
                source_title=source_title,
                source_name=f"2.Fat analysis: {label}",
                actual_value_text=_find_measurement_after_label(page_text, pattern),
            )
        )

    for label in NOURISHMENT_LABELS:
        parameters.append(
            ParameterResult(
                source_title=source_title,
                source_name=label,
                actual_value_text=_find_measurement_after_label(page_text, re.escape(label)),
            )
        )

    for label in WEIGHT_CONTROL_LABELS:
        parameters.append(
            ParameterResult(
                source_title=source_title,
                source_name=label,
                actual_value_text=_find_measurement_after_label(page_text, re.escape(label)),
            )
        )

    parameters.append(
        ParameterResult(
            source_title=source_title,
            source_name=BODY_FORM_LABEL,
            actual_value_text=_find_measurement_after_label(page_text, r"Body\s*form\s*Assessment"),
        )
    )

    return parameters


def _parse_loose_section(page_soup: BeautifulSoup, source_title: str):
    parameters = []

    for table in page_soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            cell_texts = [_normalise_whitespace(td.get_text(" ", strip=True)) for td in cols]
            if not any(cell_texts):
                continue

            first = cell_texts[0]
            skip_tokens = {
                "reference standard:",
                "parameter description",
                "actual testing results",
                "testing item",
                "normal range",
                "actual measurement value",
                "testing result",
            }
            if first.lower() in skip_tokens:
                continue

            if len(cols) >= 4:
                source_name = cell_texts[0]
                normal_range_text = cell_texts[1]
                actual_value_text = cell_texts[2]

                if source_name and source_name.lower() not in skip_tokens:
                    try:
                        numeric_value = float(actual_value_text)
                    except Exception:
                        numeric_value = None

                    parameters.append(
                        ParameterResult(
                            source_title=source_title,
                            source_name=source_name,
                            normal_range_text=normal_range_text or None,
                            actual_value_text=actual_value_text or None,
                            actual_value_numeric=numeric_value,
                            result_image_code=_extract_result_image_code(cols[3]),
                        )
                    )
                    continue

            source_name = cell_texts[0]
            actual_value_text = cell_texts[1] if len(cell_texts) >= 2 else None

            if not source_name or len(source_name) < 2:
                continue

            if source_name.lower().startswith("the test results for reference only"):
                continue

            parameters.append(
                ParameterResult(
                    source_title=source_title,
                    source_name=source_name,
                    actual_value_text=actual_value_text or None,
                    normal_range_text=None,
                    actual_value_numeric=None,
                    result_image_code=None,
                )
            )

    deduped = []
    seen = set()
    for p in parameters:
        key = _norm_key(p.source_name)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    return deduped


def _detect_report_profile(patient: Patient, sections: list[ReportSection]) -> str:
    section_titles = {s.source_title for s in sections}
    patient_sex = (patient.sex or "").strip().lower()

    if (
        "Adolescent Intelligence" in section_titles
        or "Adolescent Growth Index" in section_titles
        or "ADHD" in section_titles
        or (patient.age is not None and patient.age <= 12)
    ):
        return "child"

    if (
        "Gynecology" in section_titles
        or "Breast" in section_titles
        or "Menstrual cycle" in section_titles
        or "Female Hormone" in section_titles
        or patient_sex == "female"
    ):
        return "female"

    return "male"


def _expected_categories_for_profile(profile: str) -> list[str]:
    if profile == "child":
        return EXPECTED_CATEGORIES_CHILD
    if profile == "female":
        return EXPECTED_CATEGORIES_FEMALE
    return EXPECTED_CATEGORIES_MALE


def ensure_expected_categories_present(sections: list[ReportSection], profile: str):
    expected = _expected_categories_for_profile(profile)
    existing = {s.source_title for s in sections}

    for category in expected:
        if category not in existing:
            sections.append(
                ReportSection(
                    source_title=category,
                    original_report_category=(
                        f"{category} Report"
                        if category.lower() in NON_STANDARD_REPORTS
                        else f"({category}) Analysis Report Card"
                    ),
                    parameters=[],
                )
            )
    return sections


def parse_html_report(html: str) -> ParsedReport:
    full_soup = BeautifulSoup(html, "lxml")
    patient_text = full_soup.get_text("\n", strip=True)
    patient = _extract_patient(patient_text)

    sections = []
    page_chunks = _split_html_into_page_chunks(html)

    for chunk in page_chunks:
        page_soup = BeautifulSoup(chunk, "lxml")
        page_text = page_soup.get_text("\n", strip=True)
        source_title = _extract_section_title(page_text)

        if not source_title:
            continue

        if source_title.lower() == "element of human":
            params = _parse_body_composition_section(page_soup, source_title)
        else:
            params = _parse_standard_section_tables(page_soup, source_title)
            if params:
                params = _attach_reference_bands(params, _parse_reference_standard_table(page_soup))
            if not params:
                params = _parse_loose_section(page_soup, source_title)

        original_category = (
            f"{source_title} Report"
            if source_title.lower() in NON_STANDARD_REPORTS
            else f"({source_title}) Analysis Report Card"
        )

        sections.append(
            ReportSection(
                source_title=source_title,
                original_report_category=original_category,
                parameters=params or [],
            )
        )

    profile = _detect_report_profile(patient, sections)
    sections = ensure_expected_categories_present(sections, profile)

    report = ParsedReport(patient=patient, sections=sections)
    try:
        report.report_profile = profile
    except Exception:
        pass

    report = attach_category_completeness(report)
    return report