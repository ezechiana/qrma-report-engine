




# app/services/product_mapping_builder.py

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


def build_complete_product_mapping(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapping: List[Dict[str, Any]] = []

    for p in products:
        source = p.get("source")
        source_label = p.get("source_label") or display_source_label(source)

        sections = p.get("supporting_sections", []) or []
        markers = p.get("supporting_markers", []) or []

        # For NA/custom products, where sections/markers may be empty,
        # fall back to pattern/focus so the table still says something meaningful.
        if not sections:
            pattern_alignment = p.get("pattern_alignment")
            focus_area = p.get("focus_area")

            fallback_sections = []
            if pattern_alignment:
                fallback_sections.append(pattern_alignment)
            if focus_area:
                fallback_sections.append(str(focus_area).replace("_", " "))

            sections = fallback_sections

        mapping.append({
            "name": p.get("name"),
            "source": source,
            "source_label": source_label,
            "sections": sections,
            "markers": markers,
            "in_protocol": True,
        })

    return mapping