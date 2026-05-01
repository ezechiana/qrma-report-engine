# app/data/vitalhealth_mechanism_map.py

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _links(*items: Tuple[str, int]) -> List[Dict[str, Any]]:
    return [{"name": name, "weight": weight} for name, weight in items]


SECTION_MECHANISM_MAP: Dict[str, Dict[str, Any]] = {
    "gastrointestinal function": {
        "mechanisms": _links(
            ("digestive_support", 5),
            ("microbiome_support", 3),
            ("mucosal_support", 3),
        ),
        "categories": _links(
            ("gut_microbiome", 5),
            ("foundation_nutrition", 2),
        ),
    },
    "large intestine function": {
        "mechanisms": _links(
            ("microbiome_support", 5),
            ("digestive_support", 4),
            ("binder_toxin", 2),
        ),
        "categories": _links(
            ("gut_microbiome", 5),
            ("detoxification", 2),
        ),
    },
    "liver function": {
        "mechanisms": _links(
            ("detox_phase_1", 5),
            ("detox_phase_2", 5),
            ("hepatic_support", 5),
            ("antioxidant", 2),
        ),
        "categories": _links(
            ("detoxification", 5),
        ),
    },
    "gallbladder function": {
        "mechanisms": _links(
            ("digestive_support", 3),
            ("hepatic_support", 4),
        ),
        "categories": _links(
            ("detoxification", 4),
            ("gut_microbiome", 2),
        ),
    },
    "heavy metal": {
        "mechanisms": _links(
            ("detox_phase_2", 5),
            ("antioxidant", 4),
            ("binder_toxin", 2),
        ),
        "categories": _links(
            ("detoxification", 5),
        ),
    },
    "human toxin": {
        "mechanisms": _links(
            ("detox_phase_2", 5),
            ("antioxidant", 3),
            ("binder_toxin", 2),
        ),
        "categories": _links(
            ("detoxification", 5),
        ),
    },
    "allergy": {
        "mechanisms": _links(
            ("immune_modulation", 5),
            ("anti_inflammatory", 4),
            ("antioxidant", 2),
        ),
        "categories": _links(
            ("immune_defense", 5),
        ),
    },
    "immune system": {
        "mechanisms": _links(
            ("immune_modulation", 5),
            ("anti_inflammatory", 2),
        ),
        "categories": _links(
            ("immune_defense", 5),
        ),
    },
    "lung function": {
        "mechanisms": _links(
            ("immune_modulation", 2),
            ("anti_inflammatory", 2),
        ),
        "categories": _links(
            ("immune_defense", 3),
        ),
    },
    "brain nerve": {
        "mechanisms": _links(
            ("cognitive_support", 5),
            ("membrane_support", 3),
            ("energy_stimulation", 2),
        ),
        "categories": _links(
            ("neuro_cognitive", 5),
            ("energy_performance", 2),
        ),
    },
    "cognitive function": {
        "mechanisms": _links(
            ("cognitive_support", 5),
            ("membrane_support", 2),
        ),
        "categories": _links(
            ("neuro_cognitive", 5),
        ),
    },
    "adhd": {
        "mechanisms": _links(
            ("cognitive_support", 5),
            ("microbiome_support", 2),
            ("membrane_support", 3),
        ),
        "categories": _links(
            ("neuro_cognitive", 5),
            ("gut_microbiome", 2),
        ),
    },
    "amino acid": {
        "mechanisms": _links(
            ("protein_nutrition", 5),
            ("mitochondrial_support", 2),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("foundation_nutrition", 5),
            ("energy_performance", 2),
        ),
    },
    "coenzyme": {
        "mechanisms": _links(
            ("mitochondrial_support", 5),
            ("energy_stimulation", 2),
            ("antioxidant", 2),
        ),
        "categories": _links(
            ("energy_performance", 4),
            ("foundation_nutrition", 3),
        ),
    },
    "vitamin": {
        "mechanisms": _links(
            ("protein_nutrition", 2),
            ("antioxidant", 4),
            ("immune_modulation", 2),
            ("metabolic_balance_support", 2),
        ),
        "categories": _links(
            ("foundation_nutrition", 5),
            ("immune_defense", 2),
        ),
    },
    "trace element": {
        "mechanisms": _links(
            ("protein_nutrition", 2),
            ("immune_modulation", 2),
            ("metabolic_balance_support", 3),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("foundation_nutrition", 5),
        ),
    },
    "essential fatty acid": {
        "mechanisms": _links(
            ("membrane_support", 5),
            ("anti_inflammatory", 4),
            ("cardiovascular_support", 2),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 4),
            ("neuro_cognitive", 3),
        ),
    },
    "fatty acid": {
        "mechanisms": _links(
            ("membrane_support", 5),
            ("anti_inflammatory", 4),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 4),
            ("neuro_cognitive", 3),
        ),
    },
    "obesity": {
        "mechanisms": _links(
            ("metabolic_balance_support", 5),
            ("fat_metabolism_support", 5),
            ("energy_stimulation", 2),
            ("appetite_regulation_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
            ("energy_performance", 2),
        ),
    },
    "cardiovascular and cerebrovascular": {
        "mechanisms": _links(
            ("cardiovascular_support", 5),
            ("circulatory_support", 4),
            ("vascular_resilience_support", 4),
            ("nitric_oxide_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
        ),
    },
    "blood lipids": {
        "mechanisms": _links(
            ("cardiovascular_support", 4),
            ("metabolic_balance_support", 4),
            ("anti_inflammatory", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
        ),
    },
    "pulse of heart and brain": {
        "mechanisms": _links(
            ("circulatory_support", 4),
            ("nitric_oxide_support", 2),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 4),
            ("neuro_cognitive", 2),
        ),
    },
    "kidney function": {
        "mechanisms": _links(
            ("renal_support", 5),
            ("fluid_balance_support", 5),
        ),
        "categories": _links(
            ("renal_urinary", 5),
        ),
    },
    "bone disease": {
        "mechanisms": _links(
            ("joint_cartilage_support", 4),
            ("connective_tissue_support", 3),
            ("anti_inflammatory", 2),
            ("bone_support", 3),
        ),
        "categories": _links(
            ("musculoskeletal_joint", 5),
        ),
    },
    "bone mineral density": {
        "mechanisms": _links(
            ("bone_support", 5),
            ("connective_tissue_support", 3),
        ),
        "categories": _links(
            ("musculoskeletal_joint", 5),
            ("foundation_nutrition", 2),
        ),
    },
    "rheumatoid bone disease": {
        "mechanisms": _links(
            ("joint_cartilage_support", 4),
            ("anti_inflammatory", 4),
            ("bone_support", 2),
        ),
        "categories": _links(
            ("musculoskeletal_joint", 5),
            ("immune_defense", 2),
        ),
    },
    "collagen": {
        "mechanisms": _links(
            ("collagen_support", 5),
            ("connective_tissue_support", 5),
            ("skin_support", 3),
        ),
        "categories": _links(
            ("musculoskeletal_joint", 4),
            ("skin_barrier", 3),
        ),
    },
    "skin": {
        "mechanisms": _links(
            ("skin_support", 4),
            ("anti_inflammatory", 3),
            ("antioxidant", 3),
        ),
        "categories": _links(
            ("skin_barrier", 5),
            ("immune_defense", 2),
        ),
    },
    "breast": {
        "mechanisms": _links(
            ("hormonal_support", 4),
            ("anti_inflammatory", 2),
        ),
        "categories": _links(
            ("hormonal_male_female", 4),
        ),
    },
    "gynecology": {
        "mechanisms": _links(
            ("hormonal_support", 5),
            ("anti_inflammatory", 2),
        ),
        "categories": _links(
            ("hormonal_male_female", 5),
        ),
    },
    "menstrual cycle": {
        "mechanisms": _links(
            ("hormonal_support", 5),
        ),
        "categories": _links(
            ("hormonal_male_female", 5),
        ),
    },
    "male hormone": {
        "mechanisms": _links(
            ("male_vitality_support", 5),
            ("hormonal_support", 5),
        ),
        "categories": _links(
            ("hormonal_male_female", 5),
        ),
    },
    "male sexual function": {
        "mechanisms": _links(
            ("male_vitality_support", 5),
            ("circulatory_support", 3),
            ("energy_stimulation", 2),
        ),
        "categories": _links(
            ("hormonal_male_female", 5),
            ("energy_performance", 2),
        ),
    },
    "prostate": {
        "mechanisms": _links(
            ("male_vitality_support", 4),
            ("anti_inflammatory", 3),
            ("circulatory_support", 2),
        ),
        "categories": _links(
            ("hormonal_male_female", 5),
            ("immune_defense", 2),
        ),
    },
    "thyroid": {
        "mechanisms": _links(
            ("metabolic_balance_support", 3),
            ("hormonal_support", 4),
            ("energy_stimulation", 2),
        ),
        "categories": _links(
            ("hormonal_male_female", 4),
            ("energy_performance", 2),
        ),
    },
    "growth and development": {
        "mechanisms": _links(
            ("protein_nutrition", 4),
            ("cognitive_support", 2),
            ("bone_support", 3),
        ),
        "categories": _links(
            ("foundation_nutrition", 5),
            ("neuro_cognitive", 2),
        ),
    },
}


PATTERN_MECHANISM_MAP: Dict[str, Dict[str, Any]] = {
    "absorption_assimilation": {
        "mechanisms": _links(
            ("digestive_support", 5),
            ("mucosal_support", 4),
            ("protein_nutrition", 4),
            ("microbiome_support", 2),
        ),
        "categories": _links(
            ("gut_microbiome", 5),
            ("foundation_nutrition", 4),
        ),
    },
    "toxic_burden": {
        "mechanisms": _links(
            ("detox_phase_1", 4),
            ("detox_phase_2", 5),
            ("antioxidant", 4),
            ("binder_toxin", 2),
        ),
        "categories": _links(
            ("detoxification", 5),
        ),
    },
    "inflammatory_barrier": {
        "mechanisms": _links(
            ("anti_inflammatory", 5),
            ("immune_modulation", 4),
            ("mucosal_support", 3),
            ("skin_support", 2),
        ),
        "categories": _links(
            ("immune_defense", 5),
            ("skin_barrier", 3),
            ("gut_microbiome", 2),
        ),
    },
    "neurocognitive_support": {
        "mechanisms": _links(
            ("cognitive_support", 5),
            ("membrane_support", 4),
            ("energy_stimulation", 2),
        ),
        "categories": _links(
            ("neuro_cognitive", 5),
            ("energy_performance", 2),
        ),
    },
    "mitochondrial_energy": {
        "mechanisms": _links(
            ("mitochondrial_support", 5),
            ("energy_stimulation", 3),
            ("protein_nutrition", 2),
        ),
        "categories": _links(
            ("energy_performance", 5),
            ("foundation_nutrition", 2),
        ),
    },
    "glycaemic_metabolic": {
        "mechanisms": _links(
            ("metabolic_balance_support", 5),
            ("fat_metabolism_support", 4),
            ("appetite_regulation_support", 2),
            ("cardiovascular_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
        ),
    },
    "lipid_transport_membrane": {
        "mechanisms": _links(
            ("membrane_support", 5),
            ("anti_inflammatory", 3),
            ("cardiovascular_support", 2),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 4),
            ("neuro_cognitive", 3),
        ),
    },
    "connective_tissue_repair": {
        "mechanisms": _links(
            ("connective_tissue_support", 5),
            ("collagen_support", 5),
            ("joint_cartilage_support", 3),
        ),
        "categories": _links(
            ("musculoskeletal_joint", 5),
            ("skin_barrier", 2),
        ),
    },
}


RECOMMENDATION_FAMILY_MECHANISM_MAP: Dict[str, Dict[str, Any]] = {
    "nutrient_repletion": {
        "mechanisms": _links(
            ("protein_nutrition", 5),
            ("antioxidant", 2),
            ("metabolic_balance_support", 2),
        ),
        "categories": _links(
            ("foundation_nutrition", 5),
        ),
    },
    "barrier_inflammation": {
        "mechanisms": _links(
            ("anti_inflammatory", 5),
            ("immune_modulation", 4),
            ("mucosal_support", 2),
            ("skin_support", 2),
        ),
        "categories": _links(
            ("immune_defense", 5),
            ("skin_barrier", 2),
        ),
    },
    "cardiovascular": {
        "mechanisms": _links(
            ("cardiovascular_support", 5),
            ("circulatory_support", 4),
            ("vascular_resilience_support", 4),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
        ),
    },
    "circulatory_microvascular": {
        "mechanisms": _links(
            ("circulatory_support", 5),
            ("vascular_resilience_support", 4),
            ("nitric_oxide_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
        ),
    },
    "metabolic": {
        "mechanisms": _links(
            ("metabolic_balance_support", 5),
            ("fat_metabolism_support", 4),
            ("appetite_regulation_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 5),
            ("energy_performance", 2),
        ),
    },
    "connective_tissue": {
        "mechanisms": _links(
            ("connective_tissue_support", 5),
            ("collagen_support", 4),
            ("joint_cartilage_support", 3),
        ),
        "categories": _links(
            ("musculoskeletal_joint", 5),
        ),
    },
    "neurocognitive": {
        "mechanisms": _links(
            ("cognitive_support", 5),
            ("membrane_support", 3),
            ("energy_stimulation", 2),
        ),
        "categories": _links(
            ("neuro_cognitive", 5),
            ("energy_performance", 2),
        ),
    },
    "growth_development": {
        "mechanisms": _links(
            ("protein_nutrition", 4),
            ("bone_support", 3),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("foundation_nutrition", 5),
        ),
    },
    "lipid_membrane": {
        "mechanisms": _links(
            ("membrane_support", 5),
            ("anti_inflammatory", 3),
            ("cognitive_support", 2),
        ),
        "categories": _links(
            ("cardiovascular_metabolic", 4),
            ("neuro_cognitive", 3),
        ),
    },
}


def _accumulate(weight_map: Dict[str, int], links: List[Dict[str, Any]]) -> None:
    for item in links or []:
        name = (item.get("name") or "").strip()
        weight = int(item.get("weight", 0) or 0)
        if not name or weight <= 0:
            continue
        weight_map[name] = weight_map.get(name, 0) + weight


def _sorted_links(weight_map: Dict[str, int]) -> List[Dict[str, Any]]:
    return [
        {"name": name, "weight": weight}
        for name, weight in sorted(weight_map.items(), key=lambda x: (-x[1], x[0]))
    ]


def resolve_section_mechanisms(section_names: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    mech_weights: Dict[str, int] = {}
    cat_weights: Dict[str, int] = {}

    for raw in section_names or []:
        key = (raw or "").strip().lower()
        config = SECTION_MECHANISM_MAP.get(key)
        if not config:
            continue
        _accumulate(mech_weights, config.get("mechanisms", []))
        _accumulate(cat_weights, config.get("categories", []))

    return {
        "mechanisms": _sorted_links(mech_weights),
        "categories": _sorted_links(cat_weights),
    }


def resolve_pattern_mechanisms(pattern_keys: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    mech_weights: Dict[str, int] = {}
    cat_weights: Dict[str, int] = {}

    for key in pattern_keys or []:
        config = PATTERN_MECHANISM_MAP.get((key or "").strip().lower())
        if not config:
            continue
        _accumulate(mech_weights, config.get("mechanisms", []))
        _accumulate(cat_weights, config.get("categories", []))

    return {
        "mechanisms": _sorted_links(mech_weights),
        "categories": _sorted_links(cat_weights),
    }


def resolve_recommendation_family_mechanisms(families: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    mech_weights: Dict[str, int] = {}
    cat_weights: Dict[str, int] = {}

    for key in families or []:
        config = RECOMMENDATION_FAMILY_MECHANISM_MAP.get((key or "").strip().lower())
        if not config:
            continue
        _accumulate(mech_weights, config.get("mechanisms", []))
        _accumulate(cat_weights, config.get("categories", []))

    return {
        "mechanisms": _sorted_links(mech_weights),
        "categories": _sorted_links(cat_weights),
    }


def merge_mechanism_sets(*sets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    mech_weights: Dict[str, int] = {}
    cat_weights: Dict[str, int] = {}

    for s in sets:
        _accumulate(mech_weights, s.get("mechanisms", []))
        _accumulate(cat_weights, s.get("categories", []))

    return {
        "mechanisms": _sorted_links(mech_weights),
        "categories": _sorted_links(cat_weights),
    }