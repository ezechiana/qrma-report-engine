from __future__ import annotations

from typing import Any, Dict, List, Tuple
import logging
import os

logger = logging.getLogger(__name__)
SCORING_DEBUG = os.getenv("SCORING_DEBUG", "0") == "1"

NON_DISPLAY_SECTIONS = {"expert analysis", "hand analysis"}

# -----------------------------------------
# BASE SEVERITY BANDS
# -----------------------------------------

SEVERITY_BANDS = {
    "normal": (90, 100),
    "low_mild": (78, 89),
    "high_mild": (78, 89),
    "low_moderate": (58, 76),
    "high_moderate": (58, 76),
    "low_severe": (28, 55),
    "high_severe": (28, 55),
    "unknown": (70, 84),
}

BAND_CONFIG = [
    (85, "Robust functional balance", "#2e7d32"),
    (70, "Generally balanced", "#1565c0"),
    (55, "Needs focused support", "#e0b71a"),
    (0, "Priority review recommended", "#d6523c"),
]

# -----------------------------------------
# SECTION WEIGHTS (aggressive clinical prioritisation)
# Higher = stronger influence on overall score
# -----------------------------------------

SECTION_WEIGHTS: Dict[str, float] = {
    # highest-risk systems
    "cardiovascular and cerebrovascular": 2.10,
    "blood sugar": 2.00,
    "blood lipids": 1.95,
    "kidney function": 1.90,
    "endocrine system": 1.85,
    "thyroid": 1.80,
    "immune system": 1.75,
    "human immunity": 1.70,
    "human toxin": 1.70,
    "heavy metal": 1.70,
    "brain nerve": 1.70,

    # important organ/system burden
    "liver function": 1.75,
    "pancreatic function": 1.65,
    "lung function": 1.60,
    "respiratory function": 1.60,
    "gastrointestinal function": 1.55,
    "large intestine function": 1.45,
    "gallbladder function": 1.40,

    # musculoskeletal / structural
    "bone disease": 1.25,
    "bone mineral density": 1.20,
    "rheumatoid bone disease": 1.20,
    "bone growth index": 1.10,
    "channels and collaterals": 1.05,

    # reproductive / hormonal / developmental
    "male hormone": 1.35,
    "female hormone": 1.35,
    "male sexual function": 1.20,
    "sperm and semen": 1.15,
    "prostate": 1.25,
    "gynecology": 1.30,
    "menstrual cycle": 1.25,
    "breast": 1.15,
    "adolescent growth index": 1.25,
    "adolescent intelligence": 1.20,
    "adhd": 1.30,

    # metabolic nutrition layers
    "trace element": 1.10,
    "vitamin": 1.00,
    "amino acid": 1.00,
    "coenzyme": 1.00,
    "essential fatty acid": 1.20,
    "fatty acid": 1.05,
    "lecithin": 1.00,

    # lower-priority appearance / surface domains
    "skin": 0.30,
    "collagen": 0.55,
    "eye": 0.55,
    "obesity": 1.20,
    "basic physical quality": 1.00,
    "human consciousness level": 1.20,
    "pulse of heart and brain": 1.35,
    "element of human": 1.05,
    "allergy": 1.10,
}

DEFAULT_SECTION_WEIGHT = 1.0

# -----------------------------------------
# OPTIONAL MARKER WEIGHTS
# Use normalised keys; unknown markers default to 1.0
# -----------------------------------------

MARKER_WEIGHTS: Dict[str, float] = {
    # cardiovascular / circulation
    "arteriosclerosis index": 1.45,
    "blood viscosity": 1.30,
    "myocardial blood supply": 1.30,
    "left ventricular ejection": 1.30,
    "coronary artery elasticity": 1.30,
    "cerebral blood supply": 1.30,

    # metabolic
    "insulin secretion": 1.40,
    "glucose tolerance": 1.40,
    "blood lipid metabolism": 1.30,

    # endocrine / thyroid
    "t3": 1.20,
    "t4": 1.20,
    "thyroxine": 1.20,
    "thyroid hormone secretion": 1.40,

    # renal / detox / inflammatory
    "glomerular filtration": 1.50,
    "renal blood flow": 1.30,
    "heavy metal index": 1.30,
    "toxin metabolism": 1.25,

    # skin deliberately toned down
    "skin moisture": 0.50,
    "skin elasticity": 0.50,
    "collagen loss": 0.50,
}

DEFAULT_MARKER_WEIGHT = 1.0

# -----------------------------------------
# BODY SYSTEM GROUPING
# -----------------------------------------

BODY_SYSTEM_RULES = {
    "Cardiovascular": {
        "titles": {
            "cardiovascular and cerebrovascular",
            "blood lipids",
            "pulse of heart and brain",
        }
    },
    "Metabolic": {
        "titles": {
            "blood sugar",
            "pancreatic function",
            "obesity",
            "essential fatty acid",
            "fatty acid",
            "lecithin",
            "trace element",
            "vitamin",
            "amino acid",
            "coenzyme",
        }
    },
    "Digestive": {
        "titles": {
            "gastrointestinal function",
            "large intestine function",
            "liver function",
            "gallbladder function",
        }
    },
    "Renal & Fluid Balance": {"titles": {"kidney function"}},
    "Respiratory": {"titles": {"lung function", "respiratory function"}},
    "Neurocognitive": {"titles": {"brain nerve", "human consciousness level", "adhd", "adolescent intelligence"}},
    "Immune & Inflammatory": {"titles": {"immune system", "human immunity", "allergy", "human toxin", "heavy metal"}},
    "Endocrine & Hormonal": {
        "titles": {
            "endocrine system",
            "thyroid",
            "female hormone",
            "male hormone",
            "menstrual cycle",
            "gynecology",
            "breast",
            "male sexual function",
            "sperm and semen",
            "prostate",
            "adolescent growth index",
        }
    },
    "Musculoskeletal": {
        "titles": {
            "bone disease",
            "bone mineral density",
            "rheumatoid bone disease",
            "bone growth index",
            "collagen",
            "channels and collaterals",
        }
    },
    "Skin & Barrier": {"titles": {"skin", "eye"}},
}

# -----------------------------------------
# PATTERN MULTIPLIERS
# Aggressive risk escalation when multiple risky systems are abnormal
# -----------------------------------------

PATTERN_RULES = [
    {
        "name": "Cardio-Metabolic Risk",
        "sections": {"cardiovascular and cerebrovascular", "blood sugar", "blood lipids"},
        "min_flagged_sections": 2,
        "multiplier": 1.22,
    },
    {
        "name": "Cardio-Renal Risk",
        "sections": {"cardiovascular and cerebrovascular", "kidney function", "blood sugar"},
        "min_flagged_sections": 2,
        "multiplier": 1.18,
    },
    {
        "name": "Detox-Inflammatory Burden",
        "sections": {"liver function", "kidney function", "human toxin", "heavy metal", "immune system"},
        "min_flagged_sections": 2,
        "multiplier": 1.16,
    },
    {
        "name": "Endocrine Instability",
        "sections": {"endocrine system", "thyroid", "blood sugar", "male hormone", "female hormone", "menstrual cycle"},
        "min_flagged_sections": 2,
        "multiplier": 1.15,
    },
    {
        "name": "Child Development Pattern",
        "sections": {"adhd", "adolescent intelligence", "adolescent growth index", "essential fatty acid"},
        "min_flagged_sections": 2,
        "multiplier": 1.14,
    },
]

MAX_PATTERN_MULTIPLIER = 1.35
MAX_SECTION_SHARE = 0.28  # no single section contributes >28% of weighted total


# -----------------------------------------
# HELPER  TO GET DISPLAY SECTIONS FOR BODY SYSTEMS
# -----------------------------------------


def get_body_system_display_sections(system_key: str) -> list[str]:
    """
    Returns clean, user-friendly section names for dashboard display.
    Uses Natural Approaches terminology and aligns EXACTLY with BODY_SYSTEM_RULES.
    """

    DISPLAY_MAP = {
        "Cardiovascular": [
            "Cardiovascular function",
            "Blood lipids",
            "Circulation (pulse dynamics)",
        ],

        "Respiratory": [
            "Lung function",
            "Respiratory function",
        ],

        "Digestive": [
            "Gastrointestinal function",
            "Large intestine function",
            "Liver function",
            "Gallbladder function",
        ],

        "Renal & Fluid Balance": [
            "Kidney function",
        ],

        "Neurocognitive": [
            "Brain function",
            "Cognitive function",
            "Neurodevelopment (ADHD / adolescent)",
        ],

        "Immune & Inflammatory": [
            "Immune function",
            "Inflammatory activity",
            "Allergy response",
            "Toxin load",
            "Heavy metal burden",
        ],

        "Endocrine & Hormonal": [
            "Endocrine regulation",
            "Thyroid function",
            "Hormonal balance",
            "Reproductive health",
        ],

        "Musculoskeletal": [
            "Bone health",
            "Joint & connective tissue",
            "Structural integrity",
        ],

        "Skin & Barrier": [
            "Skin integrity",
            "Barrier function",
            "Ocular surface",
        ],

        "Metabolic": [
            "Blood sugar regulation",
            "Metabolic function",
            "Nutrient status",
            "Fat metabolism",
        ],

        "Other": [
            "Other functional markers",
        ],
    }

    return DISPLAY_MAP.get(system_key, [])

# -----------------------------------------
# HELPERS
# -----------------------------------------

def _log_debug(message: str) -> None:
    if SCORING_DEBUG:
        logger.warning(message)


def is_hidden_section(title: str | None) -> bool:
    return (title or "").strip().lower() in NON_DISPLAY_SECTIONS


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _parse_range(range_text: str | None) -> Tuple[float | None, float | None]:
    if not range_text:
        return None, None

    text = str(range_text).strip().replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in text.split("-")]
    if len(parts) != 2:
        return None, None

    return _safe_float(parts[0]), _safe_float(parts[1])


def _marker_position_factor(marker) -> float:
    """
    0..1 factor showing where the result sits relative to the reference range.
    """
    severity = getattr(marker, "severity", None) or "unknown"
    value = _safe_float(getattr(marker, "actual_value_numeric", None))
    low, high = _parse_range(getattr(marker, "normal_range_text", None))

    if value is None or low is None or high is None:
        return 0.5

    span = max(high - low, 1e-9)

    if severity == "normal":
        midpoint = (low + high) / 2
        half_span = max((high - low) / 2, 1e-9)
        distance = abs(value - midpoint) / half_span
        return _clamp(1.0 - (distance * 0.55), 0.0, 1.0)

    if severity.startswith("high"):
        distance = max(0.0, value - high)
        if severity == "high_mild":
            return _clamp(1.0 - (distance / (span * 1.10)), 0.0, 1.0)
        if severity == "high_moderate":
            return _clamp(0.82 - (distance / (span * 1.85)), 0.0, 1.0)
        if severity == "high_severe":
            return _clamp(0.58 - (distance / (span * 2.75)), 0.0, 1.0)

    if severity.startswith("low"):
        distance = max(0.0, low - value)
        if severity == "low_mild":
            return _clamp(1.0 - (distance / (span * 1.10)), 0.0, 1.0)
        if severity == "low_moderate":
            return _clamp(0.82 - (distance / (span * 1.85)), 0.0, 1.0)
        if severity == "low_severe":
            return _clamp(0.58 - (distance / (span * 2.75)), 0.0, 1.0)

    return 0.5


def _section_weight(title: str | None) -> float:
    return SECTION_WEIGHTS.get(_norm(title), DEFAULT_SECTION_WEIGHT)


def _marker_weight(marker_name: str | None) -> float:
    return MARKER_WEIGHTS.get(_norm(marker_name), DEFAULT_MARKER_WEIGHT)


def score_marker(marker) -> int:
    severity = getattr(marker, "severity", None) or "unknown"
    low, high = SEVERITY_BANDS.get(severity, SEVERITY_BANDS["unknown"])
    factor = _marker_position_factor(marker)
    score = round(low + ((high - low) * factor))

    if getattr(marker, "marker_priority", None) == "high":
        score -= 2

    return max(0, min(100, score))


def score_to_band(score: float) -> Dict[str, str]:
    for threshold, label, color in BAND_CONFIG:
        if score >= threshold:
            return {"label": label, "color": color}
    return {"label": "General", "color": "#1565c0"}


def _count_severities(parameters: List[Any]) -> Dict[str, int]:
    mild = 0
    moderate = 0
    severe = 0
    normal = 0

    for p in parameters:
        sev = (getattr(p, "severity", None) or "normal")
        if sev == "normal":
            normal += 1
        elif sev.endswith("mild"):
            mild += 1
        elif sev.endswith("moderate"):
            moderate += 1
        elif sev.endswith("severe"):
            severe += 1
        else:
            mild += 1

    return {
        "normal_count": normal,
        "mild_count": mild,
        "moderate_count": moderate,
        "severe_count": severe,
        "flagged_count": mild + moderate + severe,
        "within_count": normal,
    }

# -----------------------------------------
# SECTION SCORING
# -----------------------------------------

def compute_section_score(section) -> Dict[str, Any] | None:
    parameters = list(getattr(section, "parameters", []) or [])
    title = getattr(section, "display_title", None) or getattr(section, "source_title", "")

    if not parameters:
        return None

    marker_scores: List[float] = []
    marker_weight_total = 0.0

    for p in parameters:
        raw_score = score_marker(p)
        mw = _marker_weight(getattr(p, "source_name", None))
        marker_scores.append(raw_score * mw)
        marker_weight_total += mw

    weighted_marker_average = (
        sum(marker_scores) / marker_weight_total
        if marker_weight_total > 0
        else 0.0
    )

    counts = _count_severities(parameters)
    sw = _section_weight(title)

    # --- Severity-driven risk score ---
    risk_score = (
        counts["mild_count"] * 1.0
        + counts["moderate_count"] * 2.5
        + counts["severe_count"] * 4.5
    )

    # --- Normalize by section size to prevent large sections dominating ---
    normalised_risk = risk_score / max(len(parameters), 1)

    # --- Apply clinical importance ---
    weighted_risk = normalised_risk * sw

    # --- Convert to score impact ---
    risk_penalty = weighted_risk * 12.0

    # --- Final score ---
    score = round(_clamp(weighted_marker_average - risk_penalty, 0, 100))
    band = score_to_band(score)

    _log_debug(
        "[SECTION SCORE v2] "
        f"title={title} "
        f"weight={sw:.2f} "
        f"markers={len(parameters)} "
        f"normal={counts['normal_count']} "
        f"mild={counts['mild_count']} "
        f"moderate={counts['moderate_count']} "
        f"severe={counts['severe_count']} "
        f"weighted_marker_avg={weighted_marker_average:.2f} "
        f"risk_score={risk_score:.2f} "
        f"normalised_risk={normalised_risk:.3f} "
        f"weighted_risk={weighted_risk:.3f} "
        f"risk_penalty={risk_penalty:.2f} "
        f"final_score={score}"
    )

    return {
        "title": title,
        "score": score,
        "band_label": band["label"],
        "gauge_color": band["color"],
        "flagged_count": counts["flagged_count"],
        "within_count": counts["within_count"],
        "total_count": len(parameters),
        "normal_count": counts["normal_count"],
        "mild_count": counts["mild_count"],
        "moderate_count": counts["moderate_count"],
        "severe_count": counts["severe_count"],
        "section_weight": sw,
    }

# -----------------------------------------
# BODY SYSTEM SCORING
# -----------------------------------------

def _body_system_for_title(title: str | None) -> str:
    norm = _norm(title)
    for body_system, cfg in BODY_SYSTEM_RULES.items():
        if norm in cfg["titles"]:
            return body_system
    return "Other"

def get_body_system_display_sections_from_titles(section_titles: List[str]) -> List[str]:
    """
    Build a clean, Natural Approaches style 'Includes' list
    from the actual section titles present in the grouped bucket.
    This avoids showing child-only or irrelevant labels on adult reports.
    """

    DISPLAY_MAP = {
        "cardiovascular and cerebrovascular": "Cardiovascular function",
        "blood lipids": "Blood lipids",
        "pulse of heart and brain": "Circulation (pulse dynamics)",

        "lung function": "Lung function",
        "respiratory function": "Respiratory function",

        "gastrointestinal function": "Gastrointestinal function",
        "large intestine function": "Large intestine function",
        "liver function": "Liver function",
        "gallbladder function": "Gallbladder function",
        "pancreatic function": "Pancreatic function",

        "kidney function": "Kidney function",

        "brain nerve": "Brain function",
        "human consciousness level": "Cognitive function",
        "adhd": "Neurodevelopment (ADHD)",
        "adolescent intelligence": "Adolescent cognitive development",
        "adolescent growth index": "Adolescent growth",

        "immune system": "Immune function",
        "human immunity": "Immune resilience",
        "allergy": "Allergy response",
        "human toxin": "Toxin load",
        "heavy metal": "Heavy metal burden",

        "endocrine system": "Endocrine regulation",
        "thyroid": "Thyroid function",
        "female hormone": "Female hormonal balance",
        "male hormone": "Male hormonal balance",
        "menstrual cycle": "Menstrual health",
        "gynecology": "Female reproductive health",
        "breast": "Breast health",
        "male sexual function": "Male sexual health",
        "sperm and semen": "Male fertility",
        "prostate": "Prostate health",

        "bone disease": "Bone health",
        "bone mineral density": "Bone mineral density",
        "rheumatoid bone disease": "Joint inflammation",
        "bone growth index": "Bone remodelling",
        "collagen": "Connective tissue",
        "channels and collaterals": "Structural circulation",

        "skin": "Skin integrity",
        "eye": "Ocular surface",

        "blood sugar": "Blood sugar regulation",
        "obesity": "Metabolic function",
        "trace element": "Trace mineral status",
        "vitamin": "Vitamin status",
        "amino acid": "Amino acid status",
        "coenzyme": "Coenzyme status",
        "essential fatty acid": "Essential fatty acid status",
        "fatty acid": "Fat metabolism",
        "lecithin": "Lipid transport support",
    }

    cleaned = []
    seen = set()

    for title in section_titles:
        key = (title or "").strip().lower()
        display = DISPLAY_MAP.get(key)
        if not display:
            continue
        if display in seen:
            continue
        seen.add(display)
        cleaned.append(display)

    return cleaned

def compute_body_system_scores(sections: List[Any]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}

    for section in sections:
        title = getattr(section, "display_title", None) or getattr(section, "source_title", "")
        if (
            is_hidden_section(title)
            or not getattr(section, "parameters", None)
            or len(section.parameters) == 0
        ):
            continue

        section_card = compute_section_score(section)
        if section_card is None:
            continue

        body_system = _body_system_for_title(title)

        bucket = buckets.setdefault(
            body_system,
            {
                "body_system": body_system,
                "weighted_score_total": 0.0,
                "weighted_marker_total": 0.0,
                "section_titles": [],
                "flagged_count": 0,
                "within_count": 0,
                "normal_count": 0,
                "mild_count": 0,
                "moderate_count": 0,
                "severe_count": 0,
            },
        )

        effective_weight = section_card["total_count"] * section_card["section_weight"]
        bucket["weighted_score_total"] += section_card["score"] * effective_weight
        bucket["weighted_marker_total"] += effective_weight
        bucket["section_titles"].append(title)
        bucket["flagged_count"] += section_card["flagged_count"]
        bucket["within_count"] += section_card["within_count"]
        bucket["normal_count"] += section_card["normal_count"]
        bucket["mild_count"] += section_card["mild_count"]
        bucket["moderate_count"] += section_card["moderate_count"]
        bucket["severe_count"] += section_card["severe_count"]

    cards: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        denom = bucket["weighted_marker_total"] or 1.0
        weighted_average = bucket["weighted_score_total"] / denom

        burden_penalty = (
            bucket["mild_count"] * 0.04
            + bucket["moderate_count"] * 0.10
            + bucket["severe_count"] * 0.20
        )

        score = round(_clamp(weighted_average - burden_penalty, 0, 100))
        band = score_to_band(score)

        cards.append(
            {
                "title": bucket["body_system"],
                "score": score,
                "band_label": band["label"],
                "gauge_color": band["color"],
                "flagged_count": bucket["flagged_count"],
                "within_count": bucket["within_count"],
                "total_count": bucket["flagged_count"] + bucket["within_count"],
                "normal_count": bucket["normal_count"],
                "mild_count": bucket["mild_count"],
                "moderate_count": bucket["moderate_count"],
                "severe_count": bucket["severe_count"],
                "section_titles": bucket["section_titles"],
                "included_sections": get_body_system_display_sections_from_titles(bucket["section_titles"]),
            }
        )

    return sorted(cards, key=lambda c: c["score"])

# -----------------------------------------
# PATTERN ESCALATION
# -----------------------------------------

def _flagged_section_titles(section_cards: List[Dict[str, Any]]) -> set[str]:
    flagged = set()
    for card in section_cards:
        if card["flagged_count"] > 0:
            flagged.add(_norm(card["title"]))
    return flagged


def _pattern_multiplier(section_cards: List[Dict[str, Any]]) -> Tuple[float, List[str]]:
    flagged_titles = _flagged_section_titles(section_cards)
    multiplier = 1.0
    triggered: List[str] = []

    for rule in PATTERN_RULES:
        hits = len(rule["sections"].intersection(flagged_titles))
        if hits >= rule["min_flagged_sections"]:
            multiplier *= rule["multiplier"]
            triggered.append(rule["name"])

    multiplier = min(multiplier, MAX_PATTERN_MULTIPLIER)
    return multiplier, triggered

# -----------------------------------------
# OVERALL SCORE
# -----------------------------------------

def compute_scan_scores(sections: List[Any]) -> Dict[str, Any]:
    visible_sections = [
        section
        for section in sections
        if getattr(section, "parameters", None)
        and len(section.parameters) > 0
        and not is_hidden_section(
            getattr(section, "display_title", None) or getattr(section, "source_title", None)
        )
    ]

    section_cards: List[Dict[str, Any]] = []
    for section in visible_sections:
        card = compute_section_score(section)
        if card is not None:
            section_cards.append(card)

    if not section_cards:
        band = score_to_band(0)
        return {
            "overall_score": 0,
            "overall_band_label": band["label"],
            "overall_gauge_color": band["color"],
            "system_score_cards": [],
            "section_score_cards": [],
            "body_system_cards": [],
            "triggered_patterns": [],
        }

    # Build body-system rollups first because these are the cards the user actually sees
    body_system_cards = compute_body_system_scores(visible_sections)

    # Keep section-level data for detail views and diagnostics
    raw_contributions: List[Tuple[Dict[str, Any], float]] = []
    for card in section_cards:
        contribution = card["total_count"] * card["section_weight"]
        raw_contributions.append((card, contribution))

    total_contribution = sum(c for _, c in raw_contributions) or 1.0

    capped_contributions: List[Tuple[Dict[str, Any], float]] = []
    max_allowed = total_contribution * MAX_SECTION_SHARE
    for card, contribution in raw_contributions:
        capped_contributions.append((card, min(contribution, max_allowed)))

    capped_total = sum(c for _, c in capped_contributions) or 1.0

    weighted_average = sum(
        card["score"] * contribution for card, contribution in capped_contributions
    ) / capped_total

    total_mild = sum(card["mild_count"] for card in section_cards)
    total_moderate = sum(card["moderate_count"] for card in section_cards)
    total_severe = sum(card["severe_count"] for card in section_cards)

    pattern_multiplier, triggered_patterns = _pattern_multiplier(section_cards)

    # --- NEW OVERALL SCORE MODEL ---
    # Anchor overall score to the visible body-system cards instead of the hidden section model
    if body_system_cards:
        visible_system_average = sum(card["score"] for card in body_system_cards) / len(body_system_cards)
        min_body_system_score = min(card["score"] for card in body_system_cards)
    else:
        visible_system_average = weighted_average
        min_body_system_score = min(card["score"] for card in section_cards)

    # Apply only a light penalty so the overall score remains interpretable relative to the visible cards
    burden_penalty = (
        total_mild * 0.02
        + total_moderate * 0.08
        + total_severe * 0.18
    )

    # Convert pattern multiplier into a modest downward adjustment
    pattern_penalty = (pattern_multiplier - 1.0) * 8.0

    adjusted_score = visible_system_average - burden_penalty - pattern_penalty
    overall_score = round(_clamp(adjusted_score, 0, 100))

    # Guardrail: overall score should not drift too far below the lowest visible system card
    lower_guardrail = max(0, min_body_system_score - 5)
    overall_score = max(overall_score, lower_guardrail)

    overall_band = score_to_band(overall_score)

    _log_debug(
        "[OVERALL SCORE] "
        f"visible_sections={len(section_cards)} "
        f"visible_system_average={visible_system_average:.2f} "
        f"weighted_average={weighted_average:.2f} "
        f"burden_penalty={burden_penalty:.2f} "
        f"pattern_penalty={pattern_penalty:.2f} "
        f"triggered_patterns={triggered_patterns} "
        f"overall_score={overall_score}"
    )

    for card, contribution in capped_contributions:
        _log_debug(
            "[SECTION CONTRIBUTION] "
            f"title={card['title']} "
            f"score={card['score']} "
            f"section_weight={card['section_weight']:.2f} "
            f"flagged={card['flagged_count']} "
            f"contribution={contribution:.2f}"
        )

    return {
        "overall_score": overall_score,
        "overall_band_label": overall_band["label"],
        "overall_gauge_color": overall_band["color"],
        "system_score_cards": body_system_cards,
        "section_score_cards": sorted(section_cards, key=lambda c: c["score"]),
        "body_system_cards": body_system_cards,
        "triggered_patterns": triggered_patterns,
    }