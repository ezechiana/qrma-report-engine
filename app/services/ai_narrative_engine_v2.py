# app/services/ai_narrative_engine_v2.py

from __future__ import annotations

from typing import Any, Dict, List


SOURCE_LABELS = {
    "natural_approaches": "Natural Approaches",
    "vitalhealth": "VitalHealth",
    "custom": "Custom plan",
    "system": "System guidance",
}


def display_source_label(source: str | None) -> str:
    return SOURCE_LABELS.get((source or "").strip().lower(), (source or "Other").replace("_", " ").title())


def build_protocol_section_intro(report: Any, protocol_plan: Dict[str, Any]) -> str:
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    if not primary:
        return (
            "These product suggestions are organised into a staged support plan, "
            "grouping foundational, targeted, and optional support options for easier review."
        )

    primary_name = getattr(primary, "label", "the primary pattern").lower()
    contributing = [getattr(p, "label", "").lower() for p in patterns[1:3] if getattr(p, "label", None)]

    if contributing:
        contrib_text = " and ".join(contributing[:2])
        return (
            f"These product suggestions are organised around the primary pattern of {primary_name}, "
            f"with additional support reflecting overlap with {contrib_text}. "
            f"They are grouped into a staged plan so they are easier to review clinically and tailor in follow-up."
        )

    return (
        f"These product suggestions are organised around the primary pattern of {primary_name}. "
        "They are grouped into a staged plan so they are easier to review clinically and tailor in follow-up."
    )


def build_phase_summary(phase_key: str, products: List[Dict[str, Any]]) -> str:
    if phase_key == "foundation":
        return (
            "This phase focuses on core digestive, nutritional, and repair support before broader add-ons are introduced."
        )
    if phase_key == "targeted":
        return (
            "This phase adds more specific support options aligned with the strongest secondary patterns and follow-up priorities."
        )
    if phase_key == "optional":
        return (
            "This phase includes broader adjunctive or optimisation options that may be considered depending on clinical context."
        )
    return "This phase groups related support options for easier review."


def build_product_display_rationale(product: Dict[str, Any]) -> str:
    source = (product.get("source") or "").strip().lower()
    rationale = (product.get("rationale") or "").strip()

    pattern_alignment = product.get("pattern_alignment")
    focus_area = product.get("focus_area")
    sections = product.get("supporting_sections") or []

    if source == "natural_approaches":
        if pattern_alignment and focus_area:
            return (
                f"Included to support {focus_area.replace('_', ' ')} within the wider "
                f"{pattern_alignment.lower()} picture."
            )
        if pattern_alignment:
            return f"Included because it aligns with the wider {pattern_alignment.lower()} picture."
        if focus_area:
            return f"Included to support {focus_area.replace('_', ' ')}."
        return rationale or "Included as part of the Natural Approaches support plan."

    if source == "vitalhealth":
        if sections:
            section_text = ", ".join(sections[:4])
            return f"Suggested because the scan shows overlapping findings across {section_text}."
        return rationale or "Suggested from the VitalHealth mapping layer based on flagged section overlap."

    if source == "custom":
        return rationale or "Added as a custom practitioner recommendation."

    return rationale or "Included as part of the structured recommendation set."


def enrich_protocol_plan_with_narrative(report: Any, protocol_plan: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(protocol_plan)
    enriched["section_heading"] = "Recommended support plan"
    enriched["section_intro"] = build_protocol_section_intro(report, protocol_plan)

    phases = []
    for phase in protocol_plan.get("phases", []):
        phase_copy = dict(phase)
        phase_copy["summary"] = build_phase_summary(phase_copy.get("key", ""), phase_copy.get("products", []))

        enriched_products = []
        for product in phase_copy.get("products", []):
            item = dict(product)
            item["source_label"] = display_source_label(item.get("source"))
            item["display_rationale"] = build_product_display_rationale(item)
            enriched_products.append(item)

        phase_copy["products"] = enriched_products
        phases.append(phase_copy)

    enriched["phases"] = phases
    return enriched
