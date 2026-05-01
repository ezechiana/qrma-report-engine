# app/services/clinical_context.py

from __future__ import annotations

from typing import Any, Dict, List


def _clean_str(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_list(values: Any) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    for item in values:
        text = _clean_str(item)
        if text and text not in out:
            out.append(text)
    return out


def normalize_clinical_context(context: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Accepts either:
      {"conditions": [...], ...}
    or:
      {"clinical_context": {"conditions": [...], ...}}
    """
    context = context or {}

    if isinstance(context.get("clinical_context"), dict):
        context = context["clinical_context"] or {}

    custom_items = []
    for item in context.get("custom_recommendations", []) or []:
        if not isinstance(item, dict):
            continue
        custom_items.append(
            {
                "name": _clean_str(item.get("name")),
                "type": _clean_str(item.get("type")),
                "source": _clean_str(item.get("source")) or "custom_practitioner",
                "focus_area": _clean_str(item.get("focus_area")),
                "notes": _clean_str(item.get("notes")),
            }
        )

    return {
        "conditions": _clean_list(context.get("conditions")),
        "symptoms": _clean_list(context.get("symptoms")),
        "goals": _clean_list(context.get("goals")),
        "contraindications": [x.lower() for x in _clean_list(context.get("contraindications"))],
        "current_supplements": _clean_list(context.get("current_supplements")),
        "priority_focus": _clean_list(context.get("priority_focus")),
        "notes": _clean_str(context.get("notes")),
        "custom_recommendations": custom_items,
    }


def contraindicates_stimulants(context: Dict[str, Any]) -> bool:
    contraindications = {x.lower() for x in context.get("contraindications", [])}
    return "stimulants" in contraindications or "caffeine" in contraindications


def contraindicates_blood_thinners(context: Dict[str, Any]) -> bool:
    contraindications = {x.lower() for x in context.get("contraindications", [])}
    return (
        "blood thinners" in contraindications
        or "blood-thinners" in contraindications
        or "anticoagulants" in contraindications
    )


def context_focus_boosts(context: Dict[str, Any]) -> Dict[str, int]:
    boosts: Dict[str, int] = {}

    for goal in context.get("goals", []):
        key = goal.lower()
        if "energy" in key:
            boosts["mitochondrial_support"] = boosts.get("mitochondrial_support", 0) + 2
            boosts["cognitive_support"] = boosts.get("cognitive_support", 0) + 1
        if "gut" in key:
            boosts["digestive_support"] = boosts.get("digestive_support", 0) + 2
            boosts["microbiome_support"] = boosts.get("microbiome_support", 0) + 2
            boosts["mucosal_support"] = boosts.get("mucosal_support", 0) + 1
        if "weight" in key:
            boosts["metabolic_balance_support"] = boosts.get("metabolic_balance_support", 0) + 2
            boosts["fat_metabolism_support"] = boosts.get("fat_metabolism_support", 0) + 2

    for focus in context.get("priority_focus", []):
        key = focus.lower()
        if "gut" in key:
            boosts["digestive_support"] = boosts.get("digestive_support", 0) + 2
            boosts["mucosal_support"] = boosts.get("mucosal_support", 0) + 1
        if "thyroid" in key:
            boosts["hormonal_support"] = boosts.get("hormonal_support", 0) + 2
            boosts["metabolic_balance_support"] = boosts.get("metabolic_balance_support", 0) + 1
        if "cognitive" in key or "brain" in key:
            boosts["cognitive_support"] = boosts.get("cognitive_support", 0) + 2
            boosts["membrane_support"] = boosts.get("membrane_support", 0) + 1
        if "detox" in key:
            boosts["detox_phase_1"] = boosts.get("detox_phase_1", 0) + 2
            boosts["detox_phase_2"] = boosts.get("detox_phase_2", 0) + 2
            boosts["hepatic_support"] = boosts.get("hepatic_support", 0) + 1
        if "prostate" in key:
            boosts["male_vitality_support"] = boosts.get("male_vitality_support", 0) + 2
            boosts["hormonal_support"] = boosts.get("hormonal_support", 0) + 1
        if "immunity" in key or "immune" in key:
            boosts["immune_modulation"] = boosts.get("immune_modulation", 0) + 2
            boosts["anti_inflammatory"] = boosts.get("anti_inflammatory", 0) + 1

    return boosts