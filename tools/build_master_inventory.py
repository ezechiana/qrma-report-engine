# tools/build_master_inventory.py

import json
import re
from pathlib import Path
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "sample_data"


# -----------------------------
# 1. CONFIGURED CATEGORY LISTS
# -----------------------------

MALE_CONFIGURED = [
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

FEMALE_CONFIGURED = [
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

CHILD_CONFIGURED = [
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


# -----------------------------
# 2. PARSER HELPERS
# -----------------------------

def clean_text(value):
    if value is None:
        return None
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def is_actual_results_header_row(cells):
    if len(cells) < 4:
        return False

    joined = " ".join(clean_text(cell.get_text(" ", strip=True)) or "" for cell in cells).lower()
    return (
        "testing item" in joined
        and "normal range" in joined
        and "actual measurement value" in joined
    )


def extract_section_title_from_table(table):
    text = clean_text(table.get_text(" ", strip=True))
    if not text:
        return None

    match = re.search(r"\((.*?)\)\s*Analysis Report Card", text, re.IGNORECASE)
    if match:
        return clean_text(match.group(1))

    return None


def parse_parameter_table(table):
    parameters = []
    rows = table.find_all("tr")
    header_found = False

    for row in rows:
        cols = row.find_all("td")
        if not cols:
            continue

        if is_actual_results_header_row(cols):
            header_found = True
            continue

        if not header_found:
            continue

        if len(cols) < 4:
            continue

        source_name = clean_text(cols[0].get_text(" ", strip=True))
        if not source_name:
            continue

        parameters.append(source_name)

    return parameters


def decode_file(path: Path) -> str:
    content = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


def parse_html_export(path: Path):
    html = decode_file(path)
    soup = BeautifulSoup(html, "lxml")

    all_tables = soup.find_all("table")
    sections = []
    current_section_title = None
    i = 0

    while i < len(all_tables):
        table = all_tables[i]

        detected_title = extract_section_title_from_table(table)
        if detected_title:
            current_section_title = detected_title

        table_text = clean_text(table.get_text(" ", strip=True)) or ""

        if "Actual Testing Results" in table_text:
            for j in range(i + 1, min(i + 8, len(all_tables))):
                candidate = all_tables[j]
                params = parse_parameter_table(candidate)
                if params:
                    sections.append(
                        {
                            "section": current_section_title or "Unknown Section",
                            "markers": params,
                        }
                    )
                    i = j
                    break

        i += 1

    return sections


def parse_male_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = []
    for section in data["sections"]:
        sections.append(
            {
                "section": section["source_title"],
                "markers": [p["source_name"] for p in section["parameters"]],
            }
        )
    return sections


# -----------------------------
# 3. BUILD INVENTORY
# -----------------------------

def as_inventory(profile: str, sections):
    return {
        "profile": profile,
        "section_count": len(sections),
        "sections": [
            {
                "section": s["section"],
                "marker_count": len(s["markers"]),
                "markers": s["markers"],
            }
            for s in sections
        ],
    }


def main():
    male_json = DATA_DIR / "male_report.json"
    female_html = DATA_DIR / "female_report.htm"
    child_html = DATA_DIR / "child_report.htm"

    male_sections = parse_male_json(male_json)
    female_sections = parse_html_export(female_html)
    child_sections = parse_html_export(child_html)

    male_inventory = as_inventory("male", male_sections)
    female_inventory = as_inventory("female", female_sections)
    child_inventory = as_inventory("child", child_sections)

    configured_union = sorted(set(MALE_CONFIGURED) | set(FEMALE_CONFIGURED) | set(CHILD_CONFIGURED))
    parsed_union = sorted(
        set(s["section"] for s in male_sections)
        | set(s["section"] for s in female_sections)
        | set(s["section"] for s in child_sections)
    )
    missing_from_samples = [name for name in configured_union if name not in parsed_union]

    combined_marker_registry = {}
    for profile_name, inventory in [
        ("male", male_inventory),
        ("female", female_inventory),
        ("child", child_inventory),
    ]:
        for section in inventory["sections"]:
            section_name = section["section"]
            entry = combined_marker_registry.setdefault(
                section_name,
                {
                    "profiles": [],
                    "markers": [],
                },
            )
            if profile_name not in entry["profiles"]:
                entry["profiles"].append(profile_name)

            for marker in section["markers"]:
                if marker not in entry["markers"]:
                    entry["markers"].append(marker)

    outputs_dir = ROOT / "app" / "generated"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    configured_payload = {
        "male_configured_count": len(MALE_CONFIGURED),
        "female_configured_count": len(FEMALE_CONFIGURED),
        "child_configured_count": len(CHILD_CONFIGURED),
        "unique_configured_count": len(configured_union),
        "male": MALE_CONFIGURED,
        "female": FEMALE_CONFIGURED,
        "child": CHILD_CONFIGURED,
        "unique_categories": configured_union,
    }

    parsed_payload = {
        "male": male_inventory,
        "female": female_inventory,
        "child": child_inventory,
        "unique_parsed_category_count": len(parsed_union),
        "unique_parsed_categories": parsed_union,
        "combined_marker_registry": combined_marker_registry,
    }

    missing_payload = {
        "configured_unique_count": len(configured_union),
        "parsed_unique_count": len(parsed_union),
        "missing_from_sample_exports_count": len(missing_from_samples),
        "missing_from_sample_exports": missing_from_samples,
    }

    (outputs_dir / "configured_categories.json").write_text(
        json.dumps(configured_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "parsed_inventory.json").write_text(
        json.dumps(parsed_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "missing_from_samples.json").write_text(
        json.dumps(missing_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("Done.")
    print(f"Configured unique categories: {len(configured_union)}")
    print(f"Parsed unique categories: {len(parsed_union)}")
    print(f"Missing from sample exports: {len(missing_from_samples)}")
    print("Output files written to:")
    print(outputs_dir / "configured_categories.json")
    print(outputs_dir / "parsed_inventory.json")
    print(outputs_dir / "missing_from_samples.json")

if __name__ == "__main__":
    main()