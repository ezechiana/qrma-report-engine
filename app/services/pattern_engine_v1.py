from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PatternRule:
    pattern_id: str
    title: str
    description: str
    section_triggers: List[str] = field(default_factory=list)
    system_triggers: List[str] = field(default_factory=list)
    marker_keywords: List[str] = field(default_factory=list)
    min_section_hits: int = 1
    min_system_hits: int = 0
    max_section_score: Optional[int] = None
    max_system_score: Optional[int] = None
    weight: float = 1.0
    practitioner_summary_template: str = ""
    patient_summary_template: str = ""
    follow_up_focus: List[str] = field(default_factory=list)


@dataclass
class PatternMatch:
    pattern_id: str
    title: str
    confidence: float
    priority: str
    priority_rank: int
    matched_sections: List[str]
    matched_systems: List[str]
    matched_keywords: List[str]
    matched_signals: List[str]
    driver_sections: List[str]
    follow_up_focus: List[str]
    clinical_summary: str
    patient_summary: str


PATTERN_RULES: List[PatternRule] = [
    PatternRule(
        pattern_id="inflammatory_toxic_burden",
        title="Inflammatory and toxic burden pattern",
        description="Cross-system pattern suggestive of immune activation with toxic-load contribution.",
        section_triggers=[
            "Heavy Metal",
            "Human Toxin",
            "Immune System",
            "Human Immunity",
            "Allergy",
            "Gastrointestinal Function",
        ],
        system_triggers=[
            "Immune & Inflammatory",
            "Digestive",
        ],
        marker_keywords=[
            "inflammation",
            "toxin",
            "heavy metal",
            "immune",
            "allergy",
        ],
        min_section_hits=2,
        min_system_hits=1,
        max_section_score=70,
        max_system_score=80,
        weight=1.10,
        practitioner_summary_template=(
            "Findings suggest an inflammatory burden pattern with likely toxic-load contribution, "
            "with possible digestive involvement amplifying immune reactivity."
        ),
        patient_summary_template=(
            "This scan shows a pattern often seen when the body is dealing with inflammatory stress, "
            "sometimes alongside reduced tolerance to toxins or environmental triggers."
        ),
        follow_up_focus=[
            "detoxification support",
            "immune balance",
            "digestive contribution",
        ],
    ),
    PatternRule(
        pattern_id="digestive_dysfunction",
        title="Digestive dysfunction pattern",
        description="Clustered findings across digestive and assimilative sections.",
        section_triggers=[
            "Gastrointestinal Function",
            "Large Intestine Function",
            "Liver Function",
            "Gallbladder Function",
            "Pancreatic Function",
        ],
        system_triggers=[
            "Digestive",
            "Metabolic",
        ],
        marker_keywords=[
            "digestion",
            "absorption",
            "liver",
            "gallbladder",
            "pancreas",
            "intestine",
        ],
        min_section_hits=2,
        min_system_hits=1,
        max_section_score=72,
        max_system_score=80,
        weight=1.05,
        practitioner_summary_template=(
            "The scan suggests a digestive dysfunction pattern with likely overlap between digestion, "
            "assimilation, and hepatobiliary or pancreatic support."
        ),
        patient_summary_template=(
            "This pattern can be seen when digestion, absorption, or wider gut-related function is under strain."
        ),
        follow_up_focus=[
            "digestive function",
            "absorption",
            "hepatobiliary support",
            "pancreatic support",
        ],
    ),
    PatternRule(
        pattern_id="cardiometabolic_strain",
        title="Cardiometabolic strain pattern",
        description="Pattern linking circulation and metabolic regulation.",
        section_triggers=[
            "Cardiovascular and Cerebrovascular",
            "Blood Sugar",
            "Blood lipids",
            "Obesity",
            "Kidney Function",
        ],
        system_triggers=[
            "Cardiovascular",
            "Metabolic",
            "Renal & Fluid Balance",
        ],
        marker_keywords=[
            "arteriosclerosis",
            "viscosity",
            "glucose",
            "lipid",
            "circulation",
        ],
        min_section_hits=2,
        min_system_hits=2,
        max_section_score=75,
        max_system_score=80,
        weight=1.20,
        practitioner_summary_template=(
            "The scan suggests a cardiometabolic strain pattern, with overlap between circulation, "
            "metabolic regulation, and possibly renal handling."
        ),
        patient_summary_template=(
            "This dashboard shows a pattern where blood sugar, circulation, and metabolic stress may be interacting."
        ),
        follow_up_focus=[
            "blood sugar regulation",
            "lipid balance",
            "circulatory support",
            "renal contribution",
        ],
    ),
    PatternRule(
        pattern_id="hormonal_endocrine_dysregulation",
        title="Hormonal and endocrine dysregulation pattern",
        description="Pattern linking endocrine control with reproductive or thyroid burden.",
        section_triggers=[
            "Endocrine System",
            "Thyroid",
            "Female Hormone",
            "Male Hormone",
            "Menstrual cycle",
            "Gynecology",
            "Breast",
            "Prostate",
            "Male Sexual Function",
            "Sperm and semen",
        ],
        system_triggers=[
            "Endocrine & Hormonal",
            "Metabolic",
        ],
        marker_keywords=[
            "thyroid",
            "hormone",
            "endocrine",
            "menstrual",
            "prostate",
            "fertility",
        ],
        min_section_hits=2,
        min_system_hits=1,
        max_section_score=75,
        max_system_score=84,
        weight=1.10,
        practitioner_summary_template=(
            "The scan suggests an endocrine-hormonal pattern, potentially involving thyroid regulation, "
            "reproductive signalling, or broader hormonal balance."
        ),
        patient_summary_template=(
            "This pattern may reflect strain in the body’s hormonal regulation, including thyroid or reproductive balance."
        ),
        follow_up_focus=[
            "thyroid review",
            "hormonal balance",
            "reproductive health context",
            "metabolic contribution",
        ],
    ),
    PatternRule(
        pattern_id="bone_connective_stress",
        title="Bone and connective tissue stress pattern",
        description="Pattern across bone, connective tissue, and structural support sections.",
        section_triggers=[
            "Bone Disease",
            "Bone Mineral Density",
            "Rheumatoid Bone Disease",
            "Bone Growth Index",
            "Collagen",
            "Channels and collaterals",
        ],
        system_triggers=[
            "Musculoskeletal",
        ],
        marker_keywords=[
            "bone",
            "collagen",
            "joint",
            "density",
            "rheumatoid",
        ],
        min_section_hits=2,
        min_system_hits=1,
        max_section_score=75,
        max_system_score=78,
        weight=1.05,
        practitioner_summary_template=(
            "The scan suggests a musculoskeletal and connective-tissue stress pattern, "
            "with bone integrity and inflammatory joint burden potentially contributing."
        ),
        patient_summary_template=(
            "This pattern may reflect strain in bone, joint, or connective-tissue support."
        ),
        follow_up_focus=[
            "bone support",
            "connective tissue support",
            "inflammatory joint burden",
        ],
    ),
    PatternRule(
        pattern_id="skin_barrier_inflammation",
        title="Skin-barrier inflammatory pattern",
        description="Pattern where skin findings cluster with immune or digestive contribution.",
        section_triggers=[
            "Skin",
            "Allergy",
            "Immune System",
            "Gastrointestinal Function",
        ],
        system_triggers=[
            "Skin & Barrier",
            "Immune & Inflammatory",
            "Digestive",
        ],
        marker_keywords=[
            "skin",
            "barrier",
            "allergy",
            "inflammation",
        ],
        min_section_hits=2,
        min_system_hits=1,
        max_section_score=78,
        max_system_score=80,
        weight=0.95,
        practitioner_summary_template=(
            "Skin findings appear to cluster with inflammatory and possibly digestive contribution, "
            "rather than existing as an isolated surface-level issue."
        ),
        patient_summary_template=(
            "This pattern suggests the skin findings may be linked to wider inflammation or gut-related stress."
        ),
        follow_up_focus=[
            "skin barrier support",
            "immune modulation",
            "digestive contribution",
        ],
    ),
    PatternRule(
        pattern_id="neurocognitive_stress",
        title="Neurocognitive stress pattern",
        description="Pattern across nervous system, cognitive, and nutrient-linked sections.",
        section_triggers=[
            "Brain Nerve",
            "Human Consciousness Level",
            "ADHD",
            "Adolescent Intelligence",
            "Amino Acid",
            "Vitamin",
            "Trace Element",
        ],
        system_triggers=[
            "Neurocognitive",
            "Metabolic",
        ],
        marker_keywords=[
            "brain",
            "cognitive",
            "neuro",
            "attention",
            "memory",
            "amino",
            "vitamin",
        ],
        min_section_hits=2,
        min_system_hits=1,
        max_section_score=78,
        max_system_score=85,
        weight=1.00,
        practitioner_summary_template=(
            "The scan suggests a neurocognitive stress pattern, with likely contribution from nervous-system burden "
            "and nutrient-related support demands."
        ),
        patient_summary_template=(
            "This pattern may reflect a combination of nervous-system strain and reduced nutritional support for focus or cognition."
        ),
        follow_up_focus=[
            "nervous system support",
            "cognitive function",
            "nutrient support",
        ],
    ),
    PatternRule(
        pattern_id="renal_fluid_regulation",
        title="Renal and fluid regulation pattern",
        description="Pattern linking renal handling and broader fluid or toxic burden.",
        section_triggers=[
            "Kidney Function",
            "Human Toxin",
            "Heavy Metal",
        ],
        system_triggers=[
            "Renal & Fluid Balance",
            "Immune & Inflammatory",
        ],
        marker_keywords=[
            "renal",
            "kidney",
            "fluid",
            "toxin",
        ],
        min_section_hits=1,
        min_system_hits=1,
        max_section_score=75,
        max_system_score=85,
        weight=1.00,
        practitioner_summary_template=(
            "The scan suggests a renal-fluid regulation pattern, with possible overlap between kidney handling, "
            "fluid balance, and toxic burden."
        ),
        patient_summary_template=(
            "This pattern may reflect stress in kidney-related regulation and the body’s handling of fluid or waste."
        ),
        follow_up_focus=[
            "renal support",
            "fluid balance",
            "toxic burden context",
        ],
    ),
]


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _priority_from_confidence(confidence: float) -> Tuple[str, int]:
    if confidence >= 0.75:
        return "high", 1
    if confidence >= 0.55:
        return "medium", 2
    return "low", 3


def _index_section_cards(section_score_cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {_norm(card.get("title")): card for card in section_score_cards}


def _index_system_cards(system_score_cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {_norm(card.get("title")): card for card in system_score_cards}


def _section_presence_hits(
    rule: PatternRule,
    section_card_index: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], float, List[str]]:
    matched_sections: List[str] = []
    matched_signals: List[str] = []
    score = 0.0

    for title in rule.section_triggers:
        card = section_card_index.get(_norm(title))
        if not card:
            continue

        matched_sections.append(title)
        score += 1.0

        if rule.max_section_score is not None and card.get("score", 100) <= rule.max_section_score:
            score += 0.75
            matched_signals.append(f"{title} score {card.get('score')}")

        flagged = card.get("flagged_count", 0)
        if flagged >= 3:
            score += 0.50
            matched_signals.append(f"{title} flagged markers: {flagged}")

    max_possible = max(len(rule.section_triggers) * 2.25, 1.0)
    return matched_sections, min(score / max_possible, 1.0), matched_signals


def _system_presence_hits(
    rule: PatternRule,
    system_card_index: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], float, List[str]]:
    matched_systems: List[str] = []
    matched_signals: List[str] = []
    score = 0.0

    for title in rule.system_triggers:
        card = system_card_index.get(_norm(title))
        if not card:
            continue

        matched_systems.append(title)
        score += 1.0

        if rule.max_system_score is not None and card.get("score", 100) <= rule.max_system_score:
            score += 0.75
            matched_signals.append(f"{title} system score {card.get('score')}")

        flagged = card.get("flagged_count", 0)
        if flagged >= 5:
            score += 0.40
            matched_signals.append(f"{title} flagged markers: {flagged}")

    max_possible = max(len(rule.system_triggers) * 2.15, 1.0)
    return matched_systems, min(score / max_possible, 1.0), matched_signals


def _marker_keyword_hits(
    report: Any,
    rule: PatternRule,
) -> Tuple[List[str], float, List[str]]:
    matched_keywords: List[str] = []
    matched_signals: List[str] = []

    if not rule.marker_keywords:
        return matched_keywords, 0.0, matched_signals

    haystacks: List[str] = []
    for section in getattr(report, "sections", []) or []:
        for marker in getattr(section, "parameters", []) or []:
            parts = [
                getattr(marker, "source_name", ""),
                getattr(marker, "display_label", ""),
                getattr(marker, "what_it_means", ""),
                getattr(marker, "why_it_matters", ""),
                getattr(marker, "functional_significance", ""),
                getattr(marker, "common_patterns", ""),
            ]
            joined = " ".join([p for p in parts if p]).lower()
            if joined:
                haystacks.append(joined)

    keyword_hits = 0
    for keyword in rule.marker_keywords:
        keyword_norm = _norm(keyword)
        if any(keyword_norm in hay for hay in haystacks):
            matched_keywords.append(keyword)
            matched_signals.append(f"Keyword signal: {keyword}")
            keyword_hits += 1

    max_possible = max(len(rule.marker_keywords), 1)
    return matched_keywords, min(keyword_hits / max_possible, 1.0), matched_signals


def _severity_signal(
    matched_sections: List[str],
    section_card_index: Dict[str, Dict[str, Any]],
) -> Tuple[float, List[str]]:
    score = 0.0
    matched_signals: List[str] = []

    for title in matched_sections:
        card = section_card_index.get(_norm(title))
        if not card:
            continue

        severe = card.get("severe_count", 0)
        moderate = card.get("moderate_count", 0)
        mild = card.get("mild_count", 0)

        section_signal = (severe * 1.0) + (moderate * 0.5) + (mild * 0.15)
        if section_signal > 0:
            matched_signals.append(
                f"{title} severity mix: severe={severe}, moderate={moderate}, mild={mild}"
            )

        score += section_signal

    normalised = min(score / 6.0, 1.0)
    return normalised, matched_signals


def _driver_sections(
    matched_sections: List[str],
    section_card_index: Dict[str, Dict[str, Any]],
    top_n: int = 4,
) -> List[str]:
    ranked: List[Tuple[int, int, int, str]] = []

    for title in matched_sections:
        card = section_card_index.get(_norm(title))
        if not card:
            continue
        ranked.append(
            (
                card.get("severe_count", 0),
                card.get("moderate_count", 0),
                card.get("flagged_count", 0),
                title,
            )
        )

    ranked.sort(reverse=True)
    return [item[3] for item in ranked[:top_n]]


def evaluate_rule(
    rule: PatternRule,
    report: Any,
    section_score_cards: List[Dict[str, Any]],
    system_score_cards: List[Dict[str, Any]],
) -> Optional[PatternMatch]:
    section_card_index = _index_section_cards(section_score_cards)
    system_card_index = _index_system_cards(system_score_cards)

    matched_sections, section_hit_score, section_signals = _section_presence_hits(rule, section_card_index)
    matched_systems, system_hit_score, system_signals = _system_presence_hits(rule, system_card_index)
    matched_keywords, keyword_score, keyword_signals = _marker_keyword_hits(report, rule)
    severity_score, severity_signals = _severity_signal(matched_sections, section_card_index)

    if len(matched_sections) < rule.min_section_hits:
        return None
    if len(matched_systems) < rule.min_system_hits:
        return None

    confidence = (
        section_hit_score * 0.35
        + system_hit_score * 0.25
        + severity_score * 0.20
        + keyword_score * 0.20
    )
    confidence *= rule.weight
    confidence = min(confidence, 1.0)

    if confidence < 0.40:
        return None

    priority, priority_rank = _priority_from_confidence(confidence)
    matched_signals = section_signals + system_signals + keyword_signals + severity_signals
    drivers = _driver_sections(matched_sections, section_card_index)

    return PatternMatch(
        pattern_id=rule.pattern_id,
        title=rule.title,
        confidence=round(confidence, 3),
        priority=priority,
        priority_rank=priority_rank,
        matched_sections=matched_sections,
        matched_systems=matched_systems,
        matched_keywords=matched_keywords,
        matched_signals=matched_signals,
        driver_sections=drivers,
        follow_up_focus=rule.follow_up_focus,
        clinical_summary=rule.practitioner_summary_template,
        patient_summary=rule.patient_summary_template,
    )


def detect_patterns(
    report: Any,
    section_score_cards: List[Dict[str, Any]],
    system_score_cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    matches: List[PatternMatch] = []

    for rule in PATTERN_RULES:
        match = evaluate_rule(rule, report, section_score_cards, system_score_cards)
        if match:
            matches.append(match)

    matches.sort(key=lambda m: (m.priority_rank, -m.confidence, -len(m.driver_sections)))

    return [
        {
            "pattern_id": m.pattern_id,
            "title": m.title,
            "confidence": m.confidence,
            "priority": m.priority,
            "priority_rank": m.priority_rank,
            "matched_sections": m.matched_sections,
            "matched_systems": m.matched_systems,
            "matched_keywords": m.matched_keywords,
            "matched_signals": m.matched_signals,
            "driver_sections": m.driver_sections,
            "follow_up_focus": m.follow_up_focus,
            "clinical_summary": m.clinical_summary,
            "patient_summary": m.patient_summary,
        }
        for m in matches
    ]


def apply_pattern_engine(
    report: Any,
    section_score_cards: List[Dict[str, Any]],
    system_score_cards: List[Dict[str, Any]],
) -> Any:
    patterns = detect_patterns(report, section_score_cards, system_score_cards)

    try:
        report.detected_patterns = patterns
        report.primary_pattern = patterns[0] if patterns else None
        report.contributing_patterns = patterns[1:4] if len(patterns) > 1 else []
    except Exception:
        pass

    return report