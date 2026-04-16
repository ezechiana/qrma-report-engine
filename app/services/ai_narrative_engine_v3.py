# app/services/ai_narrative_engine_v3.py

from __future__ import annotations

from typing import Any, Dict, List


SOURCE_LABELS = {
    "natural_approaches": "Natural Approaches",
    "vitalhealth": "VitalHealth",
    "custom": "Custom plan",
    "system": "System guidance",
}


PATTERN_NAME_REWRITES = {
    "absorption and assimilation strain": "reduced digestive and absorptive efficiency",
    "toxic burden and detoxification strain": "increased detoxification demand and toxic-load pressure",
    "inflammatory and barrier dysfunction pattern": "barrier stress with inflammatory reactivity",
    "neurocognitive support pattern": "higher neurocognitive support needs",
    "mitochondrial and energy-production strain": "reduced mitochondrial and energy-production efficiency",
    "glycaemic and metabolic strain": "metabolic strain affecting glycaemic regulation",
    "lipid transport and membrane strain": "membrane and lipid-transport strain",
    "connective tissue and repair strain": "connective-tissue and repair strain",
    "toxic burden and detoxification strain": "toxic-load pressure",
    "inflammatory and barrier dysfunction pattern": "barrier-related inflammatory stress",
    "neurocognitive support pattern": "neurocognitive support needs",
    "mitochondrial and energy-production strain": "reduced energy-production efficiency",
}


PHRASE_REWRITES = {
    "supported particularly by": "most evident across",
    "suggests overlap with": "also shows overlap with",
    "included to support": "included to strengthen",
    "within the wider": "within the broader",
    "suggested based on overlapping findings across": "suggested because related findings appear across",
    "the scan shows overlapping findings across": "related findings appear across",
}


def _clean(text: str | None) -> str:
    raw = text or ""
    for bad in [
        "\uFFFE", "\uFEFF", "\ufffe", "\ufeff",
        "\u2060", "\u200b", "\u200c", "\u200d",
        "\xad", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014",
        "\u00a0", "\u2028", "\u2029", "\uFFFD"
    ]:
        raw = raw.replace(bad, " ")
    raw = raw.replace("barrier inflammatory stress", "barrier and inflammatory stress")
    return " ".join(raw.strip().split())



def _lower_first(text: str) -> str:
    if not text:
        return text
    return text[0].lower() + text[1:]


def _sentence(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""
    if text.endswith((".", "!", "?")):
        return text
    return text + "."


def _apply_phrase_rewrites(text: str) -> str:
    out = text
    for old, new in PHRASE_REWRITES.items():
        out = out.replace(old, new)
        out = out.replace(old.capitalize(), new.capitalize())
    return _clean(out)


def _compress_list(items: List[str], limit: int = 3) -> str:
    cleaned = [_clean(i) for i in items if _clean(i)]
    cleaned = cleaned[:limit]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _rewrite_pattern_label(label: str | None) -> str:
    key = _clean(label).lower()
    return PATTERN_NAME_REWRITES.get(key, _clean(label))


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        key = _clean(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_clean(item))
    return out


def build_overall_summary_v3(report: Any, fallback: str | None = None) -> str:
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    if not primary:
        return _sentence(_apply_phrase_rewrites(fallback or ""))

    primary_text = _rewrite_pattern_label(getattr(primary, "label", "the main pattern"))
    contributing = []
    for p in patterns[1:3]:
        label = getattr(p, "label", None)
        if label:
            contributing.append(_rewrite_pattern_label(label))

    if contributing:
        contrib_text = _compress_list(contributing, limit=2)
        text = (
            f"The scan is most suggestive of {primary_text}, with likely downstream effects on repair, resilience, "
            f"and wider function. There is also a secondary picture of {contrib_text}."
        )
    else:
        text = (
            f"The scan is most suggestive of {primary_text}, with likely downstream effects on repair, resilience, "
            f"and wider function."
        )

    return _sentence(_apply_phrase_rewrites(text))


def build_practitioner_overview_v3(report: Any, fallback: str | None = None) -> str:
    primary = getattr(report, "primary_pattern", None)
    if not primary:
        return _sentence(_apply_phrase_rewrites(fallback or ""))

    follow_up = getattr(primary, "suggested_focus_areas", None) or getattr(primary, "follow_up_focus", None) or []
    focus_text = _compress_list([str(x).replace("_", " ") for x in follow_up], limit=3)

    primary_label = _clean(getattr(primary, "label", ""))
    primary_rewrite = _rewrite_pattern_label(primary_label)

    if "absorption" in primary_label.lower() or "assimilation" in primary_label.lower():
        text = (
            f"Taken together, the findings point most strongly to {primary_rewrite}. "
            f"Priority should be given to {focus_text or 'digestive support and absorptive repair'}."
        )
    elif "toxic" in primary_label.lower() or "detox" in primary_label.lower():
        text = (
            f"Taken together, the findings point to {primary_rewrite}, with implications for resilience, recovery, and inflammatory load. "
            f"Priority should be given to {focus_text or 'detoxification support and exposure review'}."
        )
    else:
        text = (
            f"Taken together, the findings point most strongly to {primary_rewrite}. "
            f"Priority should be given to {focus_text or 'the main follow-up themes identified in the scan'}."
        )

    return _sentence(_apply_phrase_rewrites(text))


def build_primary_pattern_summary_v3(primary_pattern: Any) -> str:
    if not primary_pattern:
        return ""

    label = (
        getattr(primary_pattern, "label", None)
        or getattr(primary_pattern, "title", None)
        or ""
    )
    supported_by = (
        getattr(primary_pattern, "supported_by_sections", None)
        or getattr(primary_pattern, "supporting_sections", None)
        or getattr(primary_pattern, "follow_up_focus", None)
        or []
    )
    supported_text = _compress_list(supported_by, limit=4)

    rewritten = _rewrite_pattern_label(label)

    if supported_text:
        text = f"This pattern points to {rewritten}, most evident across {supported_text}."
    else:
        text = f"This pattern points to {rewritten}."

    return _sentence(_apply_phrase_rewrites(text))


def build_contributing_pattern_summary_v3(pattern: Any) -> str:
    if not pattern:
        return ""

    label = (
        getattr(pattern, "label", None)
        or getattr(pattern, "title", None)
        or ""
    )
    sections = (
        getattr(pattern, "supported_by_sections", None)
        or getattr(pattern, "supporting_sections", None)
        or getattr(pattern, "follow_up_focus", None)
        or []
    )
    sections_text = _compress_list(sections, limit=3)

    rewritten = _rewrite_pattern_label(label)

    if sections_text:
        text = f"{rewritten.capitalize()}, most evident across {sections_text}."
    else:
        text = f"{rewritten.capitalize()}."

    return _sentence(_apply_phrase_rewrites(text))


def build_clinical_recommendation_summary_v3(title: str, summary: str, rationale: str | None = None) -> str:
    title_key = _clean(title).lower()

    custom_map = {
        "nutrient repletion": "Priority should be given to restoring nutrient sufficiency and absorptive capacity.",
        "inflammation and barrier support": "Priority should be given to reducing inflammatory load while strengthening barrier integrity.",
        "cardiovascular support": "Support should focus on vascular resilience, circulation, and wider cardiovascular efficiency.",
        "circulatory and microvascular support": "Support should focus on circulation and microvascular delivery.",
        "cognitive function": "Support should focus on cognitive function and the broader drivers influencing it.",
        "growth and development": "Support should focus on growth and developmental support in the context of the wider scan picture.",
        "cell membrane integrity and lipid transport": "Support should focus on membrane integrity and lipid transport.",
        "metabolic regulation and weight balance": "Support should focus on metabolic balance and weight regulation.",
        "connective tissue and structural support": "Support should focus on connective-tissue integrity and structural resilience.",
        "visual and microvascular support": "Support should focus on visual and microvascular function.",
    }

    if title_key in custom_map:
        return _sentence(custom_map[title_key])

    base = _clean(summary)
    base = _apply_phrase_rewrites(base)

    # compress common long openings
    replacements = {
        "Address ": "Prioritise ",
        "Provide targeted support for ": "Support ",
        "Review ": "Review ",
    }
    for old, new in replacements.items():
        if base.startswith(old):
            base = new + base[len(old):]
            break

    return _sentence(base)


def build_product_display_rationale_v3(product: Dict[str, Any]) -> str:
    source = _clean(product.get("source", "")).lower()
    pattern_alignment = _clean(product.get("pattern_alignment"))
    focus_area = _clean(str(product.get("focus_area", "")).replace("_", " "))
    supporting_sections = _dedupe_keep_order(product.get("supporting_sections", []) or [])
    supporting_markers = _dedupe_keep_order(product.get("supporting_markers", []) or [])
    fallback = _clean(product.get("rationale"))

    if source == "natural_approaches":
        if pattern_alignment and focus_area:
            return _sentence(
                f"Included to strengthen {focus_area} within the broader {_lower_first(pattern_alignment)} picture"
            )
        if focus_area:
            return _sentence(f"Included to strengthen {focus_area}")
        if pattern_alignment:
            return _sentence(f"Included because it aligns with the broader {_lower_first(pattern_alignment)} picture")
        return _sentence(fallback or "Included as part of the structured support plan")

    if source == "vitalhealth":
        if supporting_sections:
            section_text = _compress_list(supporting_sections, limit=4)
            return _sentence(f"Suggested because related findings appear across {section_text}")
        if supporting_markers:
            marker_text = _compress_list(supporting_markers, limit=4)
            return _sentence(f"Suggested in view of markers including {marker_text}")
        return _sentence(fallback or "Suggested from the mapped scan findings")

    if source == "custom":
        return _sentence(fallback or "Added as a practitioner-selected support option")

    return _sentence(_apply_phrase_rewrites(fallback or "Included as part of the recommendation set"))


def enrich_protocol_plan_with_narrative_v3(report: Any, protocol_plan: Dict[str, Any]) -> Dict[str, Any]:
    if not protocol_plan:
        return protocol_plan

    enriched = dict(protocol_plan)
    enriched["section_heading"] = "Recommended support plan"

    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    primary_text = _rewrite_pattern_label(getattr(primary, "label", "")) if primary else ""
    contributing = [
        _rewrite_pattern_label(getattr(p, "label", ""))
        for p in patterns[1:3]
        if getattr(p, "label", None)
    ]
    contributing_text = _compress_list(contributing, limit=2)

    if primary_text and contributing_text:
            section_intro = (
                f"This plan is organised around {primary_text}, with additional support for {contributing_text}."
            )

    elif primary_text:
        section_intro = f"This plan is organised around {primary_text}."
    else:
        section_intro = "This plan groups the most relevant support options into a staged structure."

    enriched["section_intro"] = _sentence(_apply_phrase_rewrites(section_intro))

    phase_summaries = {
        "foundation": "This phase focuses on core digestive, nutritional, and repair support.",
        "targeted": "This phase adds more specific support aligned with the strongest secondary patterns.",
        "optional": "This phase includes broader adjunctive or optional support options.",
    }

    phases = []
    for phase in enriched.get("phases", []):
        phase_copy = dict(phase)
        phase_key = _clean(phase_copy.get("key", "")).lower()
        phase_copy["summary"] = phase_summaries.get(phase_key, phase_copy.get("summary", ""))

        products = []
        for product in phase_copy.get("products", []):
            item = dict(product)
            source = _clean(item.get("source", "")).lower()
            item["source_label"] = SOURCE_LABELS.get(source, item.get("source_label") or "Other")
            item["display_rationale"] = build_product_display_rationale_v3(item)
            products.append(item)

        phase_copy["products"] = products
        phases.append(phase_copy)

    enriched["phases"] = phases
    return enriched


def _extract_related_markers_from_rationale(rationale: str | None) -> List[Dict[str, str]]:
    text = _clean(rationale)
    if not text or "led by" not in text.lower():
        return []

    lower = text.lower()
    idx = lower.find("led by")
    marker_text = text[idx + len("led by"):].strip()

    if marker_text.endswith("."):
        marker_text = marker_text[:-1]

    raw_parts = [p.strip() for p in marker_text.split(",") if p.strip()]
    return [{"name": part} for part in raw_parts[:4]]


def _extract_related_section_from_rationale(rationale: str | None) -> str | None:
    text = _clean(rationale)
    if not text:
        return None

    if " contains " in text:
        return text.split(" contains ", 1)[0].strip()

    return None


def _family_from_recommendation_title(title: str | None) -> str:
    key = _clean(title).lower()
    return {
        "nutrient repletion": "nutrient_repletion",
        "inflammation and barrier support": "barrier_inflammation",
        "cardiovascular support": "cardiovascular",
        "circulatory and microvascular support": "circulatory_microvascular",
        "visual and microvascular support": "circulatory_microvascular",
        "metabolic regulation and weight balance": "metabolic",
        "connective tissue and structural support": "connective_tissue",
        "cognitive function": "neurocognitive",
        "growth and development": "growth_development",
        "cell membrane integrity and lipid transport": "lipid_membrane",
        "protein metabolism and neurotransmitter support": "nutrient_repletion",
        "mineral balance and cofactor support": "nutrient_repletion",
        "vitamin sufficiency and antioxidant support": "nutrient_repletion",
        "essential fatty acid and membrane balance": "lipid_membrane",
    }.get(key, key.replace(" ", "_"))


def _recommendation_pattern_alignment(report: Any, family: str) -> str | None:
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    ranked_patterns = []
    if primary:
        ranked_patterns.append(primary)
    ranked_patterns.extend([p for p in patterns if p is not primary])

    for idx, pattern in enumerate(ranked_patterns[:4]):
        key = _clean(getattr(pattern, "key", None))
        if not key:
            continue

        boost_map = {
            "absorption_assimilation": {
                "nutrient_repletion": 3.0,
                "barrier_inflammation": 1.0,
                "lipid_membrane": 0.5,
                "neurocognitive": 0.5,
            },
            "toxic_burden": {
                "barrier_inflammation": 1.5,
                "nutrient_repletion": 1.0,
                "circulatory_microvascular": 0.5,
            },
            "inflammatory_barrier": {
                "barrier_inflammation": 3.0,
                "nutrient_repletion": 0.75,
                "lipid_membrane": 1.0,
            },
            "neurocognitive_support": {
                "neurocognitive": 3.0,
                "nutrient_repletion": 1.0,
                "lipid_membrane": 1.5,
                "growth_development": 1.0,
            },
            "mitochondrial_energy": {
                "nutrient_repletion": 2.0,
                "metabolic": 1.5,
                "neurocognitive": 0.5,
            },
            "glycaemic_metabolic": {
                "metabolic": 4.0,
                "cardiovascular": 1.5,
                "nutrient_repletion": 1.0,
            },
            "lipid_transport_membrane": {
                "lipid_membrane": 3.0,
                "neurocognitive": 1.0,
                "barrier_inflammation": 1.0,
            },
            "connective_tissue_repair": {
                "connective_tissue": 3.0,
                "nutrient_repletion": 1.0,
                "barrier_inflammation": 0.5,
            },
        }.get(key, {})

        if family in boost_map:
            label = getattr(pattern, "label", None) or ""
            return _rewrite_pattern_label(label)

    return None


def build_clinical_recommendation_summary_v3(
    title: str,
    summary: str,
    rationale: str | None = None,
    pattern_alignment: str | None = None,
) -> str:
    title_key = _clean(title).lower()
    pattern_text = _clean(pattern_alignment)

    custom_map = {
        "nutrient repletion": (
            "Given the dominant pattern, priority should be given to restoring nutrient sufficiency "
            "and absorptive capacity."
        ),
        "inflammation and barrier support": (
            "Given the broader scan picture, priority should be given to reducing inflammatory load "
            "while strengthening barrier integrity."
        ),
        "cardiovascular support": (
            "Support should focus on vascular resilience, circulation, and wider cardiovascular efficiency."
        ),
        "circulatory and microvascular support": (
            "Support should focus on circulation and microvascular delivery."
        ),
        "cognitive function": (
            "Support should focus on cognitive function and the broader drivers influencing it."
        ),
        "growth and development": (
            "Support should focus on growth and developmental support in the context of the wider scan picture."
        ),
        "cell membrane integrity and lipid transport": (
            "Support should focus on membrane integrity and lipid transport."
        ),
        "metabolic regulation and weight balance": (
            "Support should focus on metabolic balance and weight regulation."
        ),
        "connective tissue and structural support": (
            "Support should focus on connective-tissue integrity and structural resilience."
        ),
        "visual and microvascular support": (
            "Support should focus on visual and microvascular function."
        ),
        "protein metabolism and neurotransmitter support": (
            "Support should focus on protein metabolism, neurotransmitter building blocks, and wider nutrient resilience."
        ),
        "mineral balance and cofactor support": (
            "Support should focus on mineral sufficiency and cofactor availability for wider metabolic and repair pathways."
        ),
        "vitamin sufficiency and antioxidant support": (
            "Support should focus on vitamin sufficiency and antioxidant resilience."
        ),
        "essential fatty acid and membrane balance": (
            "Support should focus on essential fatty acid balance and membrane support."
        ),
    }

    if title_key in custom_map:
        base = custom_map[title_key]
    else:
        base = _clean(summary)
        base = _apply_phrase_rewrites(base)

        replacements = {
            "Address ": "Prioritise ",
            "Provide targeted support for ": "Support ",
            "Review ": "Review ",
        }
        for old, new in replacements.items():
            if base.startswith(old):
                base = new + base[len(old):]
                break

    if pattern_text and title_key in {"nutrient repletion", "inflammation and barrier support", "metabolic regulation and weight balance", "connective tissue and structural support"}:
        base = f"{base[:-1] if base.endswith('.') else base}, particularly in the context of { _lower_first(pattern_text) }"

    return _sentence(base)


def build_clinical_recommendation_rationale_v3(
    title: str,
    rationale: str | None = None,
    related_markers: List[Dict[str, str]] | None = None,
    related_section: str | None = None,
    pattern_alignment: str | None = None,
) -> str:
    markers = [m.get("name", "") for m in (related_markers or []) if m.get("name")]
    marker_text = _compress_list(markers, limit=4)
    section_text = _clean(related_section)
    pattern_text = _clean(pattern_alignment)

    if pattern_text and marker_text:
        return _sentence(
            f"Suggested because this aligns with {_lower_first(pattern_text)} and is supported by markers including {marker_text}"
        )

    if pattern_text and section_text:
        return _sentence(
            f"Suggested because this aligns with {_lower_first(pattern_text)} and is supported by findings in {section_text}"
        )

    if section_text and marker_text:
        return _sentence(
            f"Suggested because related findings appear in {section_text}, led by {marker_text}"
        )

    if marker_text:
        return _sentence(
            f"Suggested in view of markers including {marker_text}"
        )

    if rationale:
        return _sentence(_apply_phrase_rewrites(rationale))

    return _sentence("Suggested because it fits the strongest pattern emerging from the scan")


def rewrite_clinical_recommendations_v3(report: Any, clinical_recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []

    for rec in clinical_recommendations or []:
        item = dict(rec)

        family = _family_from_recommendation_title(item.get("title", ""))
        pattern_alignment = _recommendation_pattern_alignment(report, family)
        related_markers = _extract_related_markers_from_rationale(item.get("rationale"))
        related_section = _extract_related_section_from_rationale(item.get("rationale"))

        item["pattern_alignment"] = pattern_alignment
        item["related_markers"] = related_markers
        item["related_section"] = related_section

        item["summary"] = build_clinical_recommendation_summary_v3(
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            rationale=item.get("rationale"),
            pattern_alignment=pattern_alignment,
        )

        item["display_rationale"] = build_clinical_recommendation_rationale_v3(
            title=item.get("title", ""),
            rationale=item.get("rationale"),
            related_markers=related_markers,
            related_section=related_section,
            pattern_alignment=pattern_alignment,
        )

        out.append(item)

    return out


def rewrite_at_a_glance_v3(
    report: Any,
    overall_summary: str | None,
    practitioner_summary: str | None,
    primary_pattern: Dict[str, Any] | None,
    contributing_patterns: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    updated_primary = dict(primary_pattern) if primary_pattern else None
    updated_contributing = [dict(p) for p in (contributing_patterns or [])]

    if updated_primary:
        updated_primary["clinical_summary"] = build_primary_pattern_summary_v3(
            type("Obj", (), updated_primary)()
        )

    for i, p in enumerate(updated_contributing):
        p["clinical_summary"] = build_contributing_pattern_summary_v3(type("Obj", (), p)())
        updated_contributing[i] = p

    return {
        "overall_summary": build_overall_summary_v3(report, overall_summary),
        "practitioner_summary": build_practitioner_overview_v3(report, practitioner_summary),
        "primary_pattern": updated_primary,
        "contributing_patterns": updated_contributing,
    }
