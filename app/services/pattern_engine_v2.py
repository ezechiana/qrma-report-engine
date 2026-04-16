from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field
from collections import defaultdict

ConfidenceLevel = Literal["low", "medium", "high"]
SeverityBand = Literal["low", "moderate", "high"]

EXCLUDED_SECTIONS = {
    "expert analysis",
    "hand analysis",
}

SEVERITY_MULTIPLIER: Dict[str, float] = {
    "mildly reduced": 1.0,
    "mildly elevated": 1.0,
    "moderately reduced": 1.75,
    "moderately elevated": 1.75,
    "markedly reduced": 2.5,
    "markedly elevated": 2.5,
    "low mild": 1.0,
    "high mild": 1.0,
    "low moderate": 1.75,
    "high moderate": 1.75,
    "low severe": 2.5,
    "high severe": 2.5,
}

NORMAL_SEVERITIES = {"normal", "within range", "", None}


@dataclass
class EvidenceItem:
    section: str
    marker: Optional[str]
    severity: Optional[str]
    value: Optional[str] = None
    direction: Optional[str] = None
    weight: float = 1.0
    note: Optional[str] = None


@dataclass
class PatternRule:
    key: str
    label: str
    kind: str
    description: str
    include_sections: List[str] = field(default_factory=list)
    preferred_markers: List[str] = field(default_factory=list)
    marker_aliases: Dict[str, List[str]] = field(default_factory=dict)
    minimum_hits: int = 2
    minimum_cross_section_hits: int = 2
    base_score: float = 0.0
    section_bonus: float = 1.5
    preferred_marker_bonus: float = 0.35
    upstream_factors: List[str] = field(default_factory=list)
    downstream_impacts: List[str] = field(default_factory=list)
    suggested_focus_areas: List[str] = field(default_factory=list)
    profile_weighting: Dict[str, float] = field(default_factory=dict)


class PatternEvidence(BaseModel):
    section: str
    marker: Optional[str] = None
    severity: Optional[str] = None
    value: Optional[str] = None
    direction: Optional[str] = None
    weight: float = 1.0
    note: Optional[str] = None


class RootCausePattern(BaseModel):
    key: str
    label: str
    kind: str
    summary: str
    confidence: ConfidenceLevel
    severity: SeverityBand
    score: float
    evidence: List[PatternEvidence]
    upstream_factors: List[str] = Field(default_factory=list)
    downstream_impacts: List[str] = Field(default_factory=list)
    suggested_focus_areas: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PatternEngineV2Result(BaseModel):
    primary_patterns: List[RootCausePattern] = Field(default_factory=list)
    secondary_patterns: List[RootCausePattern] = Field(default_factory=list)
    suppressed_patterns: List[RootCausePattern] = Field(default_factory=list)
    debug: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

SECTION_NORMALISATION_MAP: Dict[str, str] = {
    "gastrointestinal function": "Gastrointestinal Function",
    "large intestine function": "Large Intestine Function",
    "trace element": "Trace Element",
    "vitamin": "Vitamin",
    "amino acid": "Amino Acid",
    "coenzyme": "Coenzyme",
    "heavy metal": "Heavy Metal",
    "human toxin": "Human Toxin",
    "allergy": "Allergy",
    "skin": "Skin",
    "lecithin": "Lecithin",
    "cognitive function": "Cognitive function",
    "growth and development": "Growth and development",
    "adhd": "ADHD",
    "essential fatty acid": "Essential Fatty Acid",
    "fatty acid": "Fatty acid",
    "liver function": "Liver Function",
    "gallbladder function": "Gallbladder Function",
    "kidney function": "Kidney Function",
    "obesity": "Obesity",
    "blood lipids": "Blood lipids",
    "cardiovascular and cerebrovascular": "Cardiovascular and Cerebrovascular",
}


def canonical_section_name(name: Optional[str]) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    return SECTION_NORMALISATION_MAP.get(raw.lower(), raw)


def normalise_marker_name(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def normalise_severity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return str(value).strip().replace("_", " ").lower()


def severity_multiplier(severity: Optional[str]) -> float:
    sev = normalise_severity(severity)
    if sev in NORMAL_SEVERITIES:
        return 0.0
    return SEVERITY_MULTIPLIER.get(sev or "", 0.75)


def infer_direction(severity: Optional[str]) -> Optional[str]:
    sev = normalise_severity(severity)
    if not sev or sev in NORMAL_SEVERITIES:
        return None
    if "reduced" in sev or sev.startswith("low"):
        return "low"
    if "elevated" in sev or sev.startswith("high"):
        return "high"
    return None


def safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    return getattr(obj, attr, default)


# ---------------------------------------------------------------------------
# First 4 rules
# ---------------------------------------------------------------------------

ABSORPTION_ASSIMILATION_RULE = PatternRule(
    key="absorption_assimilation",
    label="Absorption and assimilation strain",
    kind="absorption_assimilation",
    description=(
        "Suggests digestive, gastric, intestinal, or mucosal inefficiency contributing "
        "to broader nutrient insufficiency patterns."
    ),
    include_sections=[
        "Gastrointestinal Function",
        "Large Intestine Function",
        "Liver Function",
        "Gallbladder Function",
        "Trace Element",
        "Vitamin",
        "Amino Acid",
        "Coenzyme",
    ],
    preferred_markers=[
        "Gastric Absorption Function Coefficient",
        "Small Intestine Absorption Function Coefficient",
        "Small Intestine Motility",
        "Small Intestine Peristalsis Function Coefficient",
        "Colon Absorption",
        "Gut Microbiome",
        "Pepsin Secretion Coefficient",
        "Zinc",
        "Iron",
        "Vitamin C",
        "Vitamin B6",
        "Vitamin B12",
        "Lysine",
        "Threonine",
        "Leucine",
        "Valine",
        "Nicotinamide",
    ],
    marker_aliases={
        "Small Intestine Motility": ["small intestine peristalsis function coefficient"],
        "Colon Absorption": ["colon absorption"],
        "Gut Microbiome": ["intestinal bacteria coefficient", "gut microbiome"],
    },
    minimum_hits=4,
    minimum_cross_section_hits=2,
    base_score=1.5,
    section_bonus=1.5,
    preferred_marker_bonus=0.4,
    upstream_factors=[
        "gastric digestion strain",
        "small-intestinal absorptive inefficiency",
        "digestive-inflammatory burden",
        "mucosal dysfunction",
    ],
    downstream_impacts=[
        "micronutrient insufficiency",
        "repair deficits",
        "immune vulnerability",
        "fatigue and reduced resilience",
        "cognitive support needs",
    ],
    suggested_focus_areas=[
        "digestive support",
        "absorptive support",
        "mucosal repair",
        "foundational nutrient repletion",
    ],
    profile_weighting={"child": 1.15, "male": 1.0, "female": 1.0},
)

TOXIC_BURDEN_RULE = PatternRule(
    key="toxic_burden",
    label="Toxic burden and detoxification strain",
    kind="toxic_burden",
    description=(
        "Suggests heavy-metal, environmental, or detoxification strain contributing to "
        "oxidative stress, immune pressure, and neuro-metabolic load."
    ),
    include_sections=[
        "Heavy Metal",
        "Human Toxin",
        "Liver Function",
        "Gallbladder Function",
        "Kidney Function",
        "Coenzyme",
        "Trace Element",
    ],
    preferred_markers=[
        "Mercury",
        "Lead",
        "Cadmium",
        "Toxic Pesticide Residue",
        "Electromagnetic Radiation",
        "Detoxification Function",
        "Bile Secretion Function",
        "Glutathione",
        "Selenium",
        "Uric acid Index",
        "Proteinuria Index",
    ],
    minimum_hits=3,
    minimum_cross_section_hits=2,
    base_score=1.0,
    section_bonus=1.75,
    preferred_marker_bonus=0.45,
    upstream_factors=[
        "environmental toxic exposure",
        "higher detoxification demand",
        "oxidative stress load",
        "detox reserve pressure",
    ],
    downstream_impacts=[
        "fatigue",
        "immune reactivity",
        "neurocognitive burden",
        "metabolic strain",
    ],
    suggested_focus_areas=[
        "exposure review",
        "detoxification support",
        "antioxidant reserve",
        "hepato-biliary support",
    ],
    profile_weighting={"child": 0.9, "male": 1.0, "female": 1.0},
)

INFLAMMATORY_BARRIER_RULE = PatternRule(
    key="inflammatory_barrier",
    label="Inflammatory and barrier dysfunction pattern",
    kind="inflammatory_barrier",
    description=(
        "Suggests barrier instability and inflammatory signalling across skin, allergy, gut, "
        "and immune interfaces."
    ),
    include_sections=[
        "Skin",
        "Allergy",
        "Gastrointestinal Function",
        "Large Intestine Function",
        "Immune Function",
        "Liver Function",
        "Essential Fatty Acid",
        "Fatty acid",
    ],
    preferred_markers=[
        "Skin Moisture Index",
        "Skin Grease Index",
        "Skin Collagen Index",
        "Chemical Sensitivity",
        "Dust allergy index",
        "Injection allergy index",
        "Gut Microbiome",
        "Colon Absorption",
        "Arachidonic acid",
        "Unsaturated fatty acid index",
    ],
    marker_aliases={
        "Gut Microbiome": ["intestinal bacteria coefficient", "gut microbiome"],
        "Colon Absorption": ["colon absorption"],
    },
    minimum_hits=4,
    minimum_cross_section_hits=2,
    base_score=1.25,
    section_bonus=1.5,
    preferred_marker_bonus=0.4,
    upstream_factors=[
        "barrier instability",
        "immune irritation",
        "microbiome imbalance",
        "inflammatory trigger exposure",
    ],
    downstream_impacts=[
        "skin reactivity",
        "immune stress",
        "digestive sensitivity",
        "repair burden",
    ],
    suggested_focus_areas=[
        "barrier repair",
        "immune-modulatory support",
        "trigger reduction",
        "gut-skin axis support",
    ],
    profile_weighting={"child": 0.95, "male": 1.0, "female": 1.05},
)

NEUROCOGNITIVE_SUPPORT_RULE = PatternRule(
    key="neurocognitive_support",
    label="Neurocognitive support pattern",
    kind="neurocognitive_support",
    description=(
        "Suggests nutrient-linked or systems-linked pressure affecting cognition, developmental "
        "performance, attention, or nervous-system resilience."
    ),
    include_sections=[
        "Cognitive function",
        "ADHD",
        "Trace Element",
        "Vitamin",
        "Amino Acid",
        "Lecithin",
        "Fatty acid",
        "Essential Fatty Acid",
        "Growth and development",
    ],
    preferred_markers=[
        "Observation",
        "Memory",
        "Thinking Ability",
        "Resilience",
        "Reasoning Ability",
        "GE neurotransmitters",
        "Vitamin B6",
        "Vitamin B12",
        "Vitamin C",
        "Iron",
        "Zinc",
        "Brain phospholipid index",
        "Phospholipid index",
        "Unsaturated fatty acid index",
        "Arachidonic acid",
    ],
    minimum_hits=4,
    minimum_cross_section_hits=2,
    base_score=1.5,
    section_bonus=1.75,
    preferred_marker_bonus=0.45,
    upstream_factors=[
        "micronutrient insufficiency",
        "membrane support needs",
        "neurotransmitter support needs",
        "developmental resilience pressure",
    ],
    downstream_impacts=[
        "cognitive inefficiency",
        "attention strain",
        "developmental support need",
        "reduced resilience under load",
    ],
    suggested_focus_areas=[
        "neurocognitive nutrient support",
        "membrane and phospholipid support",
        "trace element repletion",
        "developmental support review",
    ],
    profile_weighting={"child": 1.25, "male": 1.0, "female": 1.0},
)

MITOCHONDRIAL_ENERGY_RULE = PatternRule(
    key="mitochondrial_energy",
    label="Mitochondrial and energy-production strain",
    kind="mitochondrial_energy",
    description=(
        "Suggests reduced mitochondrial efficiency, cofactor insufficiency, or energy-production strain "
        "contributing to fatigue, low resilience, and slower recovery."
    ),
    include_sections=[
        "Coenzyme",
        "Vitamin",
        "Amino Acid",
        "Trace Element",
        "Basic Physical Quality",
        "Liver Function",
        "Obesity",
    ],
    preferred_markers=[
        "Coenzyme Q10",
        "Pantothenic acid",
        "Vitamin B1",
        "Vitamin B2",
        "Vitamin B3",
        "Vitamin B12",
        "Iron",
        "Magnesium",
        "Potassium",
        "Tryptophan",
        "Valine",
        "Hypoxia",
        "Mental Power",
        "Abnormal lipid metabolism coefficient",
    ],
    minimum_hits=4,
    minimum_cross_section_hits=2,
    base_score=1.5,
    section_bonus=1.6,
    preferred_marker_bonus=0.45,
    upstream_factors=[
        "cofactor insufficiency",
        "mitochondrial strain",
        "reduced oxidative phosphorylation efficiency",
        "lower metabolic reserve",
    ],
    downstream_impacts=[
        "fatigue",
        "poor stress tolerance",
        "slower recovery",
        "cognitive drag",
    ],
    suggested_focus_areas=[
        "mitochondrial support",
        "cofactor repletion",
        "energy-production support",
        "metabolic resilience",
    ],
    profile_weighting={"child": 0.9, "male": 1.0, "female": 1.0},
)



GLYCAEMIC_METABOLIC_RULE = PatternRule(
    key="glycaemic_metabolic",
    label="Glycaemic and metabolic strain",
    kind="glycaemic_metabolic",
    description=(
        "Suggests impaired metabolic flexibility, blood-sugar handling, lipid regulation, or insulin-linked strain."
    ),
    include_sections=[
        "Blood Sugar",
        "Pancreatic Function",
        "Obesity",
        "Blood lipids",
        "Trace Element",
        "Vitamin",
        "Liver Function",
        "Kidney Function",
    ],
    preferred_markers=[
        "Blood Sugar Coefficient",
        "Coefficient of Insulin Secretion",
        "Insulin",
        "Glucagon",
        "Hyperinsulinemia coefficient",
        "Abnormal lipid metabolism coefficient",
        "Triglyceride content of abnormal coefficient",
        "Brown adipose tissue abnormalities coefficient",
        "Uric acid Index",
        "Triglyceride(TG)",
        "Neutral fat(MB)",
    ],
    minimum_hits=4,
    minimum_cross_section_hits=2,
    base_score=1.5,
    section_bonus=1.6,
    preferred_marker_bonus=0.45,
    upstream_factors=[
        "reduced metabolic flexibility",
        "insulin-related strain",
        "lipid-regulation stress",
        "hepatic-metabolic burden",
    ],
    downstream_impacts=[
        "weight dysregulation",
        "fatigue",
        "vascular strain",
        "renal-metabolic burden",
    ],
    suggested_focus_areas=[
        "glycaemic regulation",
        "metabolic support",
        "lipid regulation",
        "weight-balance support",
    ],
    profile_weighting={"child": 0.6, "male": 1.1, "female": 1.0},
)


LIPID_TRANSPORT_MEMBRANE_RULE = PatternRule(
    key="lipid_transport_membrane",
    label="Lipid transport and membrane integrity pattern",
    kind="lipid_transport_membrane",
    description=(
        "Suggests phospholipid, fatty-acid, or membrane-structure support needs affecting signalling, "
        "barrier stability, and neurocognitive resilience."
    ),
    include_sections=[
        "Lecithin",
        "Essential Fatty Acid",
        "Fatty acid",
        "Skin",
        "Brain Nerve",
        "Cognitive function",
    ],
    preferred_markers=[
        "Phospholipid index",
        "Brain phospholipid index",
        "Linoleic acid",
        "α-Linolenic acid",
        "γ-Linolenic acid",
        "Arachidonic acid",
        "Skin Moisture Index",
        "Skin Collagen Index",
        "Memory",
    ],
    minimum_hits=3,
    minimum_cross_section_hits=2,
    base_score=1.25,
    section_bonus=1.5,
    preferred_marker_bonus=0.4,
    upstream_factors=[
        "fatty-acid insufficiency",
        "phospholipid support needs",
        "membrane signalling strain",
    ],
    downstream_impacts=[
        "barrier instability",
        "neurocognitive drag",
        "reduced cellular resilience",
    ],
    suggested_focus_areas=[
        "membrane support",
        "phospholipid support",
        "essential fatty acid balance",
        "barrier resilience",
    ],
    profile_weighting={"child": 1.1, "male": 1.0, "female": 1.0},
)



CONNECTIVE_TISSUE_REPAIR_RULE = PatternRule(
    key="connective_tissue_repair",
    label="Connective-tissue and repair pattern",
    kind="connective_tissue_repair",
    description=(
        "Suggests reduced repair capacity across collagen, structural tissue, bone, and recovery pathways."
    ),
    include_sections=[
        "Skin",
        "Bone Mineral Density",
        "Rheumatoid Bone Disease",
        "Bone Growth Index",
        "Trace Element",
        "Vitamin",
        "Amino Acid",
    ],
    preferred_markers=[
        "Skin Collagen Index",
        "Vitamin C",
        "Vitamin K",
        "Zinc",
        "Copper",
        "Lysine",
        "Threonine",
        "Bone Mineral Density",
        "Osteocalcin",
        "Bone alkaline phosphatase",
    ],
    minimum_hits=4,
    minimum_cross_section_hits=2,
    base_score=1.5,
    section_bonus=1.5,
    preferred_marker_bonus=0.45,
    upstream_factors=[
        "collagen insufficiency",
        "repair nutrient deficiency",
        "bone-remodelling strain",
    ],
    downstream_impacts=[
        "slower tissue recovery",
        "weaker structural resilience",
        "skin and connective-tissue fragility",
    ],
    suggested_focus_areas=[
        "repair support",
        "connective-tissue support",
        "collagen support",
        "bone-supportive nutrition",
    ],
    profile_weighting={"child": 0.8, "male": 1.0, "female": 1.1},
)



RULES: List[PatternRule] = [
    ABSORPTION_ASSIMILATION_RULE,
    TOXIC_BURDEN_RULE,
    INFLAMMATORY_BARRIER_RULE,
    NEUROCOGNITIVE_SUPPORT_RULE,
    MITOCHONDRIAL_ENERGY_RULE,
    GLYCAEMIC_METABOLIC_RULE,
    LIPID_TRANSPORT_MEMBRANE_RULE,
    CONNECTIVE_TISSUE_REPAIR_RULE,
]

# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

def _marker_matches_rule(item: EvidenceItem, rule: PatternRule) -> bool:
    marker_norm = normalise_marker_name(item.marker)
    if not marker_norm:
        return False

    for preferred in rule.preferred_markers:
        if marker_norm == normalise_marker_name(preferred):
            return True

    for canonical, aliases in rule.marker_aliases.items():
        names = [canonical, *aliases]
        for n in names:
            if marker_norm == normalise_marker_name(n):
                return True

    return False


def _normalised_marker_label(item: EvidenceItem, rule: PatternRule) -> str:
    marker_norm = normalise_marker_name(item.marker)
    if not marker_norm:
        return item.marker or ""

    for preferred in rule.preferred_markers:
        if marker_norm == normalise_marker_name(preferred):
            return preferred

    for canonical, aliases in rule.marker_aliases.items():
        names = [canonical, *aliases]
        for n in names:
            if marker_norm == normalise_marker_name(n):
                return canonical

    return item.marker or ""


def collect_flagged_evidence(report: Any) -> List[EvidenceItem]:
    evidence: List[EvidenceItem] = []

    for section in safe_get(report, "sections", []) or []:
        raw_title = safe_get(section, "display_title") or safe_get(section, "source_title")
        section_name = canonical_section_name(raw_title)

        # 🔥 SKIP NON-CLINICAL SECTIONS
        if (section_name or "").strip().lower() in EXCLUDED_SECTIONS:
            continue

        for param in safe_get(section, "parameters", []) or []:
            raw_sev = safe_get(param, "severity")
            sev = normalise_severity(raw_sev)

            # skip normal AND unknown
            if sev in NORMAL_SEVERITIES or sev == "unknown":
                continue

            marker_name = (
                safe_get(param, "display_label")
                or safe_get(param, "clinical_label")
                or safe_get(param, "source_name")
            )

            evidence.append(
                EvidenceItem(
                    section=section_name,
                    marker=marker_name,
                    severity=sev,
                    value=safe_get(param, "actual_value_text"),
                    direction=infer_direction(sev),
                    weight=1.0,
                )
            )

    return evidence


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _rule_profile_modifier(rule: PatternRule, report: Any) -> float:
    profile = (
        str(
            safe_get(report, "report_profile")
            or safe_get(safe_get(report, "patient"), "profile")
            or safe_get(safe_get(report, "patient"), "sex")
            or ""
        )
        .strip()
        .lower()
    )

    if "child" in profile:
        return rule.profile_weighting.get("child", 1.0)
    if "male" in profile:
        return rule.profile_weighting.get("male", 1.0)
    if "female" in profile:
        return rule.profile_weighting.get("female", 1.0)
    return 1.0


def match_rule(rule: PatternRule, evidence: List[EvidenceItem], report: Any) -> Dict[str, Any]:
    matched: List[EvidenceItem] = []
    unique_sections = set()
    preferred_marker_hits = 0

    for item in evidence:
        section_hit = item.section in rule.include_sections
        marker_hit = _marker_matches_rule(item, rule)

        if not (section_hit or marker_hit):
            continue

        weighted = EvidenceItem(
            section=item.section,
            marker=_normalised_marker_label(item, rule),
            severity=item.severity,
            value=item.value,
            direction=item.direction,
            note=item.note,
            weight=1.0,
        )

        if marker_hit:
            weighted.weight += rule.preferred_marker_bonus
            preferred_marker_hits += 1

        weighted.weight *= severity_multiplier(item.severity)
        # prevent duplicates (same marker + section)
        if not any(
          m.marker == weighted.marker and m.section == weighted.section
            for m in matched
        ):
         matched.append(weighted)



        unique_sections.add(item.section)

    profile_modifier = _rule_profile_modifier(rule, report)
    score = sum(m.weight for m in matched)
    score += rule.base_score
    score += len(unique_sections) * rule.section_bonus
    score *= profile_modifier
    score = min(score, 25)  # cap to prevent inflation
    return {
        "matched": matched,
        "unique_sections": sorted(unique_sections),
        "preferred_marker_hits": preferred_marker_hits,
        "score": round(score, 2),
        "profile_modifier": profile_modifier,
    }


def confidence_for(score: float, unique_section_count: int, preferred_marker_hits: int) -> ConfidenceLevel:
    if score >= 11 and unique_section_count >= 3 and preferred_marker_hits >= 2:
        return "high"
    if score >= 6 and unique_section_count >= 2:
        return "medium"
    return "low"


def severity_for(score: float) -> SeverityBand:
    if score >= 13:
        return "high"
    if score >= 7:
        return "moderate"
    return "low"


def normalise_pattern_section_name(name: str) -> str:
    mapping = {
        "Adolescent Intelligence": "Cognitive function",
        "adolescent intelligence": "Cognitive function",
        "Adolescent Growth Index": "Growth and development",
        "adolescent growth index": "Growth and development",
        "ADHD": "Neurodevelopment",
    }
    return mapping.get(name, name)



def summary_for(rule: PatternRule, confidence: ConfidenceLevel, severity: SeverityBand, unique_sections: List[str]) -> str:
    normalised_sections = [normalise_pattern_section_name(x) for x in unique_sections]
    section_text = ", ".join(normalised_sections[:4])

    if rule.key == "absorption_assimilation":
        return (
            f"Supported particularly by {section_text}, and suggesting reduced digestive "
            f"and absorptive efficiency."
        )

    if rule.key == "toxic_burden":
        return (
            f"Supported particularly by {section_text}, and suggesting increased toxic-load "
            f"or clearance pressure."
        )

    if rule.key == "inflammatory_barrier":
        return (
            f"Supported particularly by {section_text}, and suggesting barrier stress "
            f"and inflammatory reactivity."
        )

    if rule.key == "neurocognitive_support":
        return (
            f"Supported particularly by {section_text}, and suggesting increased "
            f"neurocognitive support needs."
        )

    return f"Supported particularly by {section_text}."

# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------

def run_pattern_engine_v2(report: Any) -> PatternEngineV2Result:
    evidence = collect_flagged_evidence(report)

    primary_patterns: List[RootCausePattern] = []
    secondary_patterns: List[RootCausePattern] = []
    suppressed_patterns: List[RootCausePattern] = []

    debug: Dict[str, Any] = {
        "evidence_count": len(evidence),
        "rules_evaluated": [],
    }

    for rule in RULES:
        result = match_rule(rule, evidence, report)
        matched = result["matched"]
        unique_sections = result["unique_sections"]
        preferred_marker_hits = result["preferred_marker_hits"]
        score = result["score"]

        debug["rules_evaluated"].append({
            "rule": rule.key,
            "label": rule.label,
            "match_count": len(matched),
            "unique_sections": unique_sections,
            "preferred_marker_hits": preferred_marker_hits,
            "score": score,
            "profile_modifier": result["profile_modifier"],
        })

        if len(matched) < rule.minimum_hits or len(unique_sections) < rule.minimum_cross_section_hits:
            if matched:
                suppressed_patterns.append(
                    RootCausePattern(
                        key=rule.key,
                        label=rule.label,
                        kind=rule.kind,
                        summary=f"{rule.label} showed partial support but did not meet activation thresholds.",
                        confidence="low",
                        severity="low",
                        score=score,
                        evidence=[
                            PatternEvidence(
                                section=m.section,
                                marker=m.marker,
                                severity=m.severity,
                                value=m.value,
                                direction=m.direction,
                                weight=m.weight,
                                note=m.note,
                            ) for m in matched
                        ],
                        upstream_factors=rule.upstream_factors,
                        downstream_impacts=rule.downstream_impacts,
                        suggested_focus_areas=rule.suggested_focus_areas,
                        metadata={
                            "activated": False,
                            "section_count": len(unique_sections),
                            "minimum_hits": rule.minimum_hits,
                            "minimum_cross_section_hits": rule.minimum_cross_section_hits,
                        },
                    )
                )
            continue

        confidence = confidence_for(score, len(unique_sections), preferred_marker_hits)
        severity = severity_for(score)

        pattern = RootCausePattern(
            key=rule.key,
            label=rule.label,
            kind=rule.kind,
            summary=summary_for(rule, confidence, severity, unique_sections),
            confidence=confidence,
            severity=severity,
            score=score,
            evidence=[
                PatternEvidence(
                    section=m.section,
                    marker=m.marker,
                    severity=m.severity,
                    value=m.value,
                    direction=m.direction,
                    weight=round(m.weight, 3),
                    note=m.note,
                ) for m in matched
            ],
            upstream_factors=rule.upstream_factors,
            downstream_impacts=rule.downstream_impacts,
            suggested_focus_areas=rule.suggested_focus_areas,
            metadata={
                "activated": True,
                "section_count": len(unique_sections),
                "preferred_marker_hits": preferred_marker_hits,
                "supporting_sections": unique_sections,
            },
        )

        if confidence == "high" or severity == "high":
            primary_patterns.append(pattern)
        else:
            secondary_patterns.append(pattern)

    primary_patterns.sort(key=lambda p: p.score, reverse=True)
    secondary_patterns.sort(key=lambda p: p.score, reverse=True)
    suppressed_patterns.sort(key=lambda p: p.score, reverse=True)

    return PatternEngineV2Result(
        primary_patterns=primary_patterns,
        secondary_patterns=secondary_patterns,
        suppressed_patterns=suppressed_patterns,
        debug=debug,
    )


# ---------------------------------------------------------------------------
# Compatibility helper
# ---------------------------------------------------------------------------

def attach_pattern_engine_v2_output(report: Any) -> Any:
    """
    Convenience helper if you want to enrich the existing report object in-place.
    """
    result = run_pattern_engine_v2(report)

    setattr(report, "pattern_engine_v2", result)
    setattr(report, "patterns", result.primary_patterns + result.secondary_patterns)

    if result.primary_patterns:
        setattr(report, "primary_pattern", result.primary_patterns[0])
    else:
        setattr(report, "primary_pattern", None)

    setattr(
        report,
        "contributing_patterns",
        result.secondary_patterns[:3],
    )

    setattr(
        report,
        "detected_patterns",
        [p.label for p in (result.primary_patterns + result.secondary_patterns)],
    )

    return report


__all__ = [
    "PatternEvidence",
    "RootCausePattern",
    "PatternEngineV2Result",
    "run_pattern_engine_v2",
    "attach_pattern_engine_v2_output",
]

# ---------------------------------------------------------------------------
# V3 PATTERN RANKING ENGINE
# Stronger differentiation, explicit ranking, safer clinical prioritisation
# ---------------------------------------------------------------------------




V3_PRIMARY_PATTERN_LIMIT = 2
V3_SECONDARY_PATTERN_LIMIT = 3

V3_SUPPRESSION_THRESHOLD = 42.0
V3_SECONDARY_THRESHOLD = 56.0
V3_PRIMARY_THRESHOLD = 70.0

V3_KIND_WEIGHT = {
    "absorption_assimilation": 1.15,
    "toxic_burden": 1.10,
    "inflammatory_barrier": 1.05,
    "neurocognitive_support": 1.00,
    "mitochondrial_energy": 1.05,
    "glycaemic_metabolic": 1.12,
    "lipid_transport_membrane": 0.95,
    "connective_tissue_repair": 0.92,
}

V3_MAX_EVIDENCE_ITEMS_PER_PATTERN = 8


def _v3_report_profile(report: Any) -> str:
    sex = (safe_get(safe_get(report, "patient", None), "sex", None) or "").strip().lower()
    age = safe_get(safe_get(report, "patient", None), "age", None)

    try:
        age_num = int(age) if age is not None else None
    except Exception:
        age_num = None

    if age_num is not None and age_num < 18:
        return "child"
    if sex == "male":
        return "male"
    if sex == "female":
        return "female"
    return "female"


def _v3_visible_sections(report: Any) -> list[Any]:
    sections = []
    for section in safe_get(report, "sections", []) or []:
        title = canonical_section_name(
            safe_get(section, "display_title", None) or safe_get(section, "source_title", None)
        )
        if not title:
            continue
        if title.strip().lower() in EXCLUDED_SECTIONS:
            continue
        params = safe_get(section, "parameters", []) or []
        if not params:
            continue
        sections.append(section)
    return sections


def _v3_section_lookup(report: Any) -> dict[str, Any]:
    out = {}
    for section in _v3_visible_sections(report):
        title = canonical_section_name(
            safe_get(section, "display_title", None) or safe_get(section, "source_title", None)
        )
        if title:
            out[title] = section
    return out


def _v3_marker_matches(rule: PatternRule, marker_name: str) -> bool:
    nm = normalise_marker_name(marker_name)
    if not nm:
        return False

    for preferred in rule.preferred_markers:
        if nm == normalise_marker_name(preferred):
            return True

        aliases = rule.marker_aliases.get(preferred, [])
        for alias in aliases:
            if nm == normalise_marker_name(alias):
                return True

    return False


def _v3_collect_pattern_evidence(report: Any, rule: PatternRule) -> tuple[list[EvidenceItem], dict[str, Any]]:
    section_map = _v3_section_lookup(report)
    included_sections = set(rule.include_sections or [])

    evidence: list[EvidenceItem] = []
    matched_sections: set[str] = set()
    preferred_marker_hits = 0
    abnormal_hits = 0

    for section_title in included_sections:
        section = section_map.get(section_title)
        if not section:
            continue

        section_hit = False
        for marker in safe_get(section, "parameters", []) or []:
            marker_name = (
                safe_get(marker, "display_name", None)
                or safe_get(marker, "source_name", None)
                or safe_get(marker, "name", None)
                or ""
            ).strip()

            severity = normalise_severity(safe_get(marker, "severity", None))
            sev_mult = severity_multiplier(severity)

            if sev_mult <= 0:
                continue

            section_hit = True
            abnormal_hits += 1

            weight = sev_mult
            if _v3_marker_matches(rule, marker_name):
                weight += rule.preferred_marker_bonus
                preferred_marker_hits += 1

            evidence.append(
                EvidenceItem(
                    section=section_title,
                    marker=marker_name or None,
                    severity=severity,
                    value=safe_get(marker, "actual_value_text", None) or safe_get(marker, "actual_value_numeric", None),
                    direction=infer_direction(severity),
                    weight=round(weight, 3),
                )
            )

        if section_hit:
            matched_sections.add(section_title)

    evidence.sort(
        key=lambda x: (
            x.weight,
            1 if x.marker and _v3_marker_matches(rule, x.marker) else 0,
            x.section,
            x.marker or "",
        ),
        reverse=True,
    )

    evidence = evidence[:V3_MAX_EVIDENCE_ITEMS_PER_PATTERN]

    stats = {
        "matched_sections": sorted(matched_sections),
        "cross_section_hits": len(matched_sections),
        "preferred_marker_hits": preferred_marker_hits,
        "abnormal_hits": abnormal_hits,
    }
    return evidence, stats


def _v3_pattern_score(rule: PatternRule, evidence: list[EvidenceItem], stats: dict[str, Any], profile: str) -> float:
    if not evidence:
        return 0.0

    evidence_weight = sum(item.weight for item in evidence)
    cross_section_hits = stats.get("cross_section_hits", 0)
    preferred_marker_hits = stats.get("preferred_marker_hits", 0)
    abnormal_hits = stats.get("abnormal_hits", 0)

    base = rule.base_score * 8.0
    evidence_component = evidence_weight * 8.5
    cross_section_component = max(0, cross_section_hits - 1) * (rule.section_bonus * 3.0)
    preferred_component = preferred_marker_hits * 2.0

    profile_multiplier = rule.profile_weighting.get(profile, 1.0)
    kind_multiplier = V3_KIND_WEIGHT.get(rule.kind, 1.0)

    raw = (base + evidence_component + cross_section_component + preferred_component)
    raw *= profile_multiplier
    raw *= kind_multiplier

    # Small penalty for broad but weak evidence sets
    if abnormal_hits >= 4 and evidence_weight / max(abnormal_hits, 1) < 1.2:
        raw *= 0.92

    # Small reward for compact, coherent evidence
    if cross_section_hits >= rule.minimum_cross_section_hits and preferred_marker_hits >= 2:
        raw *= 1.05

    return round(min(raw, 100.0), 2)


def _v3_pattern_confidence(score: float, stats: dict[str, Any]) -> ConfidenceLevel:
    cross_section_hits = stats.get("cross_section_hits", 0)
    preferred_marker_hits = stats.get("preferred_marker_hits", 0)

    if score >= 74 and cross_section_hits >= 3 and preferred_marker_hits >= 2:
        return "high"
    if score >= 56 and cross_section_hits >= 2:
        return "medium"
    return "low"


def _v3_pattern_severity(score: float) -> SeverityBand:
    if score >= 70:
        return "high"
    if score >= 56:
        return "moderate"
    return "low"


def _v3_supporting_marker_names(evidence: list[EvidenceItem], limit: int = 3) -> list[str]:
    names = []
    for item in evidence:
        if item.marker and item.marker not in names:
            names.append(item.marker)
        if len(names) >= limit:
            break
    return names


def _v3_build_pattern_summary(rule: PatternRule, evidence: list[EvidenceItem], stats: dict[str, Any]) -> str:
    marker_names = _v3_supporting_marker_names(evidence, limit=3)
    marker_text = ", ".join(marker_names) if marker_names else "multiple abnormal markers"

    if stats.get("cross_section_hits", 0) >= 3:
        return (
            f"{rule.description} Driven across {stats['cross_section_hits']} related sections, "
            f"led by {marker_text}."
        )

    return f"{rule.description} Led primarily by {marker_text}."


def _v3_convert_evidence(evidence: list[EvidenceItem]) -> list[PatternEvidence]:
    return [
        PatternEvidence(
            section=item.section,
            marker=item.marker,
            severity=item.severity,
            value=str(item.value) if item.value is not None else None,
            direction=item.direction,
            weight=item.weight,
            note=item.note,
        )
        for item in evidence
    ]


def _v3_evaluate_rule(report: Any, rule: PatternRule, profile: str) -> RootCausePattern | None:
    evidence, stats = _v3_collect_pattern_evidence(report, rule)

    if stats["abnormal_hits"] < rule.minimum_hits:
        return None
    if stats["cross_section_hits"] < rule.minimum_cross_section_hits:
        return None

    score = _v3_pattern_score(rule, evidence, stats, profile)
    if score <= 0:
        return None

    confidence = _v3_pattern_confidence(score, stats)
    severity = _v3_pattern_severity(score)
    summary = _v3_build_pattern_summary(rule, evidence, stats)

    return RootCausePattern(
        key=rule.key,
        label=rule.label,
        kind=rule.kind,
        summary=summary,
        confidence=confidence,
        severity=severity,
        score=score,
        evidence=_v3_convert_evidence(evidence),
        upstream_factors=list(rule.upstream_factors or []),
        downstream_impacts=list(rule.downstream_impacts or []),
        suggested_focus_areas=list(rule.suggested_focus_areas or []),
        metadata={
            "matched_sections": stats["matched_sections"],
            "cross_section_hits": stats["cross_section_hits"],
            "preferred_marker_hits": stats["preferred_marker_hits"],
            "abnormal_hits": stats["abnormal_hits"],
        },
    )


def detect_root_cause_patterns_v3(report: Any) -> PatternEngineV2Result:
    profile = _v3_report_profile(report)

    rules: list[PatternRule] = [
        ABSORPTION_ASSIMILATION_RULE,
        TOXIC_BURDEN_RULE,
        INFLAMMATORY_BARRIER_RULE,
        NEUROCOGNITIVE_SUPPORT_RULE,
        MITOCHONDRIAL_ENERGY_RULE,
        GLYCAEMIC_METABOLIC_RULE,
        LIPID_TRANSPORT_MEMBRANE_RULE,
        CONNECTIVE_TISSUE_REPAIR_RULE,
    ]

    matches: list[RootCausePattern] = []
    debug_rows: list[dict[str, Any]] = []

    for rule in rules:
        pattern = _v3_evaluate_rule(report, rule, profile)
        if pattern is not None:
            matches.append(pattern)
            debug_rows.append({
                "key": pattern.key,
                "score": pattern.score,
                "severity": pattern.severity,
                "confidence": pattern.confidence,
                "matched_sections": pattern.metadata.get("matched_sections", []),
            })
        else:
            debug_rows.append({
                "key": rule.key,
                "score": 0,
                "severity": None,
                "confidence": None,
                "matched_sections": [],
            })

    # Strong differentiation: highest score first, then confidence, then evidence breadth
    matches.sort(
        key=lambda p: (
            p.score,
            {"high": 3, "medium": 2, "low": 1}.get(p.confidence, 0),
            len(p.metadata.get("matched_sections", [])),
            len(p.evidence),
        ),
        reverse=True,
    )

    primary_patterns: list[RootCausePattern] = []
    secondary_patterns: list[RootCausePattern] = []
    suppressed_patterns: list[RootCausePattern] = []

    for idx, pattern in enumerate(matches):
        if pattern.score < V3_SUPPRESSION_THRESHOLD:
            suppressed_patterns.append(pattern)
            continue

        if len(primary_patterns) < V3_PRIMARY_PATTERN_LIMIT and pattern.score >= V3_PRIMARY_THRESHOLD:
            primary_patterns.append(pattern)
            continue

        if len(secondary_patterns) < V3_SECONDARY_PATTERN_LIMIT and pattern.score >= V3_SECONDARY_THRESHOLD:
            secondary_patterns.append(pattern)
            continue

        suppressed_patterns.append(pattern)

    # Safety rule: always surface at least one primary if anything meaningful was found
    if not primary_patterns and matches:
        top = matches[0]
        if top.score >= V3_SECONDARY_THRESHOLD:
            primary_patterns = [top]
            secondary_patterns = [p for p in matches[1:] if p.score >= V3_SECONDARY_THRESHOLD][:V3_SECONDARY_PATTERN_LIMIT]
            suppressed_patterns = [p for p in matches if p not in primary_patterns and p not in secondary_patterns]

    return PatternEngineV2Result(
        primary_patterns=primary_patterns,
        secondary_patterns=secondary_patterns,
        suppressed_patterns=suppressed_patterns,
        debug={
            "profile": profile,
            "evaluated_rules": debug_rows,
        },
    )


def _v3_pattern_to_legacy_dict(pattern: RootCausePattern) -> dict[str, Any]:
    return {
        "key": pattern.key,
        "title": pattern.label,
        "priority": "high" if pattern.severity == "high" else ("medium" if pattern.severity == "moderate" else "low"),
        "confidence": pattern.confidence,
        "clinical_summary": pattern.summary,
        "patient_summary": pattern.summary,
        "follow_up_focus": list(pattern.suggested_focus_areas or []),
        "matched_systems": list(pattern.metadata.get("matched_sections", [])),
        "driver_sections": list(pattern.metadata.get("matched_sections", [])),
        "score": pattern.score,
        "kind": pattern.kind,
    }


def attach_pattern_engine_v3_output(report: Any) -> Any:
    """
    Parallel V3 attach function.
    Keeps report-builder integration easy and rollback-safe.
    """
    result = detect_root_cause_patterns_v3(report)

    primary = result.primary_patterns
    secondary = result.secondary_patterns
    all_surfaceable = primary + secondary

    # Backward-friendly fields
    report.primary_patterns = primary
    report.secondary_patterns = secondary
    report.suppressed_patterns = result.suppressed_patterns

    report.primary_pattern = primary[0] if primary else None
    report.contributing_patterns = secondary[:3] if secondary else []

    # For downstream template compatibility
    report.detected_patterns = [_v3_pattern_to_legacy_dict(p) for p in all_surfaceable]

    # Preserve `patterns` as a simple surfaceable list
    report.patterns = all_surfaceable

    report.pattern_engine_debug = result.debug

    return report