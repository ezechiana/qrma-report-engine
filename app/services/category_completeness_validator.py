from __future__ import annotations

from typing import Dict, List, Set

from app.models.schema import ParsedReport


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

NON_STANDARD_REPORTS = {"expert analysis", "hand analysis"}


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _canonical_map(items: List[str]) -> Dict[str, str]:
    return {_norm(item): item for item in items}


MALE_CANON = _canonical_map(MALE_CONFIGURED)
FEMALE_CANON = _canonical_map(FEMALE_CONFIGURED)
CHILD_CANON = _canonical_map(CHILD_CONFIGURED)


def _infer_profile(report: ParsedReport) -> str:
    """
    Final profile used for completeness validation.

    Order of precedence:
    1. child if parser marked child, or child-only sections are present, or age <= 12
    2. female if parser marked female, female-only sections are present, or patient sex is female
    3. male if parser marked male, male-only sections are present, or patient sex is male
    4. fallback: male
    """
    parser_profile = _norm(getattr(report, "report_profile", None))
    patient_sex = _norm(getattr(report.patient, "sex", None))
    patient_age = getattr(report.patient, "age", None)

    section_titles = {_norm(getattr(s, "source_title", None)) for s in report.sections}

    child_markers = {"adhd", "adolescent intelligence", "adolescent growth index"}
    female_markers = {"gynecology", "breast", "menstrual cycle", "female hormone"}
    male_markers = {"prostate", "male sexual function", "sperm and semen", "male hormone"}

    if (
        parser_profile == "child"
        or (patient_age is not None and patient_age <= 12)
        or any(name in section_titles for name in child_markers)
    ):
        return "child"

    if (
        parser_profile == "female"
        or patient_sex == "female"
        or any(name in section_titles for name in female_markers)
    ):
        return "female"

    if (
        parser_profile == "male"
        or patient_sex == "male"
        or any(name in section_titles for name in male_markers)
    ):
        return "male"

    return "male"


def _expected_categories(profile: str) -> List[str]:
    if profile == "child":
        return CHILD_CONFIGURED
    if profile == "female":
        return FEMALE_CONFIGURED
    return MALE_CONFIGURED


def _canon_for_profile(profile: str) -> Dict[str, str]:
    if profile == "child":
        return CHILD_CANON
    if profile == "female":
        return FEMALE_CANON
    return MALE_CANON


def _matched_categories(report: ParsedReport, profile: str) -> Set[str]:
    """
    A category counts as matched when:
    - it exists as a section and has real parsed parameters, OR
    - it is a non-standard report (Expert/Hand) and the section exists

    Empty placeholder sections appended by the parser do NOT count as matched.
    """
    canon = _canon_for_profile(profile)
    matched: Set[str] = set()

    for section in report.sections:
        source_title = (getattr(section, "source_title", None) or "").strip()
        if not source_title:
            continue

        source_title_norm = _norm(source_title)
        params = getattr(section, "parameters", None) or []

        canonical_name = canon.get(source_title_norm)
        if not canonical_name:
            continue

        if source_title_norm in NON_STANDARD_REPORTS:
            matched.add(canonical_name)
            continue

        if len(params) > 0:
            matched.add(canonical_name)

    return matched


def attach_category_completeness(report: ParsedReport) -> ParsedReport:
    profile = _infer_profile(report)
    expected = _expected_categories(profile)
    matched = _matched_categories(report, profile)

    missing_categories = [name for name in expected if name not in matched]
    matched_count = len(expected) - len(missing_categories)
    expected_count = len(expected)
    completeness_percent = round((matched_count / expected_count) * 100, 1) if expected_count else 0.0

    payload: Dict[str, object] = {
        "profile": profile,
        "expected_count": expected_count,
        "matched_count": matched_count,
        "missing_count": len(missing_categories),
        "missing_categories": missing_categories,
        "completeness_percent": completeness_percent,
        "expected_categories": expected,
        "matched_categories": [name for name in expected if name in matched],
        "status": "complete" if matched_count == expected_count else "incomplete",
    }

    report.category_completeness = payload

    try:
        report.report_profile = profile
    except Exception:
        pass

    return report