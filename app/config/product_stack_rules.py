# app/config/product_stack_rules.py

from __future__ import annotations

from typing import Dict, List


# Products in the same family are considered overlapping unless there is a
# strong reason to keep more than one.
PRODUCT_FAMILIES: Dict[str, str] = {
    # Detox / glutathione family
    "GLUTATION PLUS+": "detox_glutathione",
    "V-GLUTATION": "detox_glutathione",

    # Foundation powder / nourishment family
    "V-DAILY": "foundation_powder",
    "VITAL PRO": "foundation_powder",
    "GENIUS SHAKE": "foundation_powder",
    "V-NOURISH+": "foundation_powder",

    # Stimulating performance / coffee family
    "V-NRGY": "stimulant_performance",
    "V-NRGY TROPICAL": "stimulant_performance",
    "V-THERMOKAFE": "stimulant_performance",
    "V-NEUROKAFE": "stimulant_performance",
    "V-LOVKAFE": "stimulant_performance",

    # Gut-support overlap family
    "SMART BIOTICS": "gut_support",
    "V-FORTYFLORA": "gut_support",

    # Detox/drainage overlap family
    "V-TE DETOX": "detox_drainage",
    "V-ORGANEX": "detox_drainage",
}


# Default max products from a family by strategy.
FAMILY_LIMITS_BY_STRATEGY: Dict[str, Dict[str, int]] = {
    "legacy_vitalhealth": {
        "detox_glutathione": 2,
        "foundation_powder": 2,
        "stimulant_performance": 2,
        "gut_support": 2,
        "detox_drainage": 2,
    },
    "mechanism_weighted": {
        "detox_glutathione": 1,
        "foundation_powder": 1,
        "stimulant_performance": 1,
        "gut_support": 1,
        "detox_drainage": 1,
    },
    "hybrid": {
        "detox_glutathione": 1,
        "foundation_powder": 1,
        "stimulant_performance": 1,
        "gut_support": 1,
        "detox_drainage": 1,
    },
}


# If a product has one of these focus areas, we allow an extra product from the
# same family because the use-case is more clearly differentiated.
JUSTIFIED_FAMILY_EXPANSION_BY_FOCUS: Dict[str, List[str]] = {
    "stimulant_performance": [
        "energy_stimulation",
        "cognitive_support",
        "male_vitality_support",
        "fat_metabolism_support",
    ],
    "gut_support": [
        "microbiome_support",
        "binder_toxin",
        "digestive_support",
    ],
    "detox_drainage": [
        "detox_phase_1",
        "detox_phase_2",
        "hepatic_support",
    ],
}


def get_product_family(product_name: str) -> str | None:
    return PRODUCT_FAMILIES.get((product_name or "").strip())


def get_family_limit(strategy: str, family: str) -> int:
    strategy_limits = FAMILY_LIMITS_BY_STRATEGY.get(strategy, FAMILY_LIMITS_BY_STRATEGY["hybrid"])
    return strategy_limits.get(family, 99)