import re
from bs4 import BeautifulSoup
from app.models.schema import ParsedReport, Patient, ReportSection, ParameterResult
from app.services.category_completeness_validator import attach_category_completeness


# ==============================
# ✅ FULL CATEGORY GUARANTEE
# ==============================

EXPECTED_CATEGORIES_FULL = [
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
    "Gynecology",
    "Menstrual cycle",
    "Breast",
]


EXCLUDED_CATEGORIES = {
    "Expert analysis",
    "Hand analysis",
}


def ensure_all_categories_present(sections):
    existing = {s.source_title for s in sections}

    for category in EXPECTED_CATEGORIES_FULL:
        if category not in existing:
            sections.append(
                ReportSection(
                    source_title=category,
                    original_report_category=f"({category}) Analysis Report Card",
                    parameters=[]
                )
            )

    return sections


# ==============================
# 🔧 UTILITIES (UNCHANGED)
# ==============================

def _normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _search(pattern: str, text: str):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def _to_float(value):
    try:
        return float(value)
    except:
        return None


# ==============================
# 👤 PATIENT EXTRACTION (FIXED HTML ISSUE)
# ==============================

def _extract_patient(text: str) -> Patient:
    clean = _normalise_whitespace(text)

    name = _search(r"Name:\s*(.+?)(?:Sex:|Age:|Figure:|Testing Time:)", clean)

    if name:
        name = re.sub(r"<[^>]+>", " ", name)
        name = re.sub(r"</?td[^>]*>", " ", name, flags=re.IGNORECASE)
        name = _normalise_whitespace(name)

    sex = _search(r"Sex:\s*([A-Za-z]+)", clean)
    age_raw = _search(r"Age:\s*(\d+)", clean)

    age = int(age_raw) if age_raw else None

    figure = _search(r"Figure:\s*([^\n\r]+?)(?:Testing Time:|$)", clean)

    height = None
    weight = None

    if figure:
        h = re.search(r"([\d\.]+)\s*cm", figure)
        w = re.search(r"([\d\.]+)\s*kg", figure)

        if h:
            height = float(h.group(1))
        if w:
            weight = float(w.group(1))

    testing_time = _search(r"Testing\s*Time:\s*(.+)", clean)

    scan_date, scan_time = None, None
    if testing_time and " " in testing_time:
        parts = testing_time.split()
        if len(parts) >= 2:
            scan_date, scan_time = parts[0], parts[1]

    return Patient(
        full_name=name,
        sex=sex,
        age=age,
        height_cm=height,
        weight_kg=weight,
        scan_date=scan_date,
        scan_time=scan_time,
    )


# ==============================
# 🧠 SECTION SPLITTING (CRITICAL)
# ==============================

def _split_html_into_page_chunks(html: str):
    cleaned = html.replace("\r\n", "\n")

    parts = re.split(
        r"(?=\([^)]+\)\s*Analysis Report Card)",
        cleaned,
        flags=re.IGNORECASE,
    )

    return [p.strip() for p in parts if "Analysis Report Card" in p]


# ==============================
# 🏷️ TITLE EXTRACTION (FIXED)
# ==============================

def _extract_section_title(text: str):
    clean = _normalise_whitespace(text)

    match = re.search(r"\((.*?)\)\s*Analysis Report Card", clean)
    if match:
        return match.group(1).strip()

    return None


# ==============================
# 📊 TABLE PARSER
# ==============================

def _parse_standard_section_tables(soup, title):
    parameters = []

    for row in soup.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        name = _normalise_whitespace(cols[0].get_text())
        normal = _normalise_whitespace(cols[1].get_text())
        actual = _normalise_whitespace(cols[2].get_text())

        if not name:
            continue

        try:
            numeric = float(actual)
        except:
            numeric = None

        parameters.append(
            ParameterResult(
                source_title=title,
                source_name=name,
                normal_range_text=normal,
                actual_value_text=actual,
                actual_value_numeric=numeric,
                result_image_code=None,
            )
        )

    return parameters


# ==============================
# 🚀 MAIN PARSER
# ==============================

def parse_html_report(html: str) -> ParsedReport:

    soup = BeautifulSoup(html, "lxml")

    patient = _extract_patient(soup.get_text("\n", strip=True))

    sections = []

    chunks = _split_html_into_page_chunks(html)

    for chunk in chunks:

        soup = BeautifulSoup(chunk, "lxml")
        text = soup.get_text("\n", strip=True)

        title = _extract_section_title(text)

        if not title:
            continue

        # 🚫 Remove unwanted categories
        if title in EXCLUDED_CATEGORIES:
            continue

        params = _parse_standard_section_tables(soup, title)

        sections.append(
            ReportSection(
                source_title=title,
                original_report_category=f"({title}) Analysis Report Card",
                parameters=params
            )
        )

    # ✅ GUARANTEE ALL 45 CATEGORIES
    sections = ensure_all_categories_present(sections)

    # 🧪 DEBUG
    print("\n=== CATEGORY VALIDATION ===")
    for s in sections:
        print("-", s.source_title)
    print(f"TOTAL: {len(sections)}")

    report = ParsedReport(
        patient=patient,
        sections=sections
    )

    report = attach_category_completeness(report)

    return report