# app/services/marker_library.py

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFINITION_FILE = ROOT / "app" / "generated" / "marker_definition_library_v1.json"
SCAFFOLD_FILE = ROOT / "app" / "generated" / "marker_content_scaffold.json"


DEFAULT_MARKER_CONTENT = {
    "what_it_means": "This marker is one of the scan indicators used by the analyser to build a functional picture of this section.",
    "why_it_matters": "It is usually more useful when interpreted alongside the wider pattern than in isolation.",
    "functional_significance": "This marker may help highlight relative weakness, excess, burden, or imbalance in the system being assessed.",
    "common_patterns": "Its value is usually strongest when interpreted as part of a clustered pattern rather than in isolation.",
    "recommendation_notes": "",
    "priority": "normal",
    "recommendations": {
        "normal": "This marker sits within the analyser's expected range in this scan.",
        "low_mild": "A mildly reduced result may suggest softer functional support or reserve in this area.",
        "low_moderate": "A more clearly reduced result may point toward weaker functional capacity or suboptimal support in this area.",
        "low_severe": "A markedly reduced result may indicate a stronger functional shortfall within the scan model.",
        "high_mild": "A mildly elevated result may suggest early strain, burden, or compensation in this area.",
        "high_moderate": "A more clearly elevated result may support a stronger pattern of stress, overload, or imbalance in this area.",
        "high_severe": "A markedly elevated result may indicate one of the stronger flagged patterns in this section.",
        "unknown": "This marker should be interpreted in the context of the wider pattern."
    }
}


FALLBACK_LIBRARY = {
    "Blood Viscosity": {
        "what_it_means": "A general indicator of how easily blood moves through the circulation.",
        "why_it_matters": "If blood flow is less efficient, tissues may receive oxygen and nutrients less effectively.",
        "functional_significance": "This marker contributes to the scan's picture of circulatory fluidity and vascular workload.",
        "common_patterns": "Higher values may cluster with vascular strain, reduced circulation efficiency, or broader cardiovascular burden patterns.",
        "recommendation_notes": "Best read alongside vascular resistance, perfusion, blood fat, and stroke volume markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly lower result may reflect a thinner-flow pattern in the analyser model and is usually less significant on its own.",
            "low_moderate": "A more clearly reduced result may reflect altered flow characteristics in the scan model, though interpretation should remain cautious.",
            "low_severe": "A markedly reduced result may indicate an unusual flow pattern in the scan model and should be correlated carefully with the wider cardiovascular picture.",
            "high_mild": "A mildly elevated result may fit an early pattern of increased circulatory strain or thicker-flow dynamics.",
            "high_moderate": "A moderately elevated result may support a clearer pattern of increased blood flow resistance or cardiovascular burden.",
            "high_severe": "A markedly elevated result may indicate one of the more significant circulation-related flags in the scan.",
            "unknown": "This marker should be interpreted in the context of the wider cardiovascular pattern."
        }
    },
    "Vascular Resistance": {
        "what_it_means": "A marker intended to reflect the resistance blood meets as it moves through the vessels.",
        "why_it_matters": "Higher resistance may place more workload on the circulatory system.",
        "functional_significance": "This contributes to the scan's picture of vascular tone, flow efficiency, and cardiac workload.",
        "common_patterns": "Higher values may cluster with perfusion strain, blood pressure tendencies, or reduced vascular flexibility.",
        "recommendation_notes": "Interpret alongside vascular elasticity, perfusion markers, and broader cardiovascular findings.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may reflect lower vascular resistance within the scan model.",
            "low_moderate": "A more clearly reduced result may suggest altered vascular tone in the analyser model.",
            "low_severe": "A markedly reduced result may indicate a stronger deviation in vascular resistance within the scan model.",
            "high_mild": "A mildly elevated result may be consistent with increased vascular tension or early circulatory strain.",
            "high_moderate": "A moderately elevated result may support a stronger pattern of vascular resistance and circulatory workload.",
            "high_severe": "A markedly elevated result may indicate one of the more significant vascular strain signals in this section.",
            "unknown": "This marker should be interpreted in the context of the wider cardiovascular pattern."
        }
    },
    "Stroke Volume": {
        "what_it_means": "A marker intended to estimate the amount of blood moved with each heartbeat.",
        "why_it_matters": "It contributes to the scan's picture of cardiac pumping efficiency.",
        "functional_significance": "This helps build the model of cardiac output and circulation effectiveness.",
        "common_patterns": "Lower values may appear with reduced pump efficiency, lower output, or broader cardiovascular strain patterns.",
        "recommendation_notes": "Interpret alongside effective pump power, perfusion pressure, and myocardial demand markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may fit a pattern of softer cardiac output efficiency.",
            "low_moderate": "A moderately reduced result may support a clearer pattern of reduced pump output in the analyser model.",
            "low_severe": "A markedly reduced result may indicate a stronger signal of reduced output efficiency in this section.",
            "high_mild": "A mildly elevated result may reflect stronger cardiac output in the scan model.",
            "high_moderate": "A moderately elevated result may indicate stronger output dynamics in this model, though not always as a concern on its own.",
            "high_severe": "A markedly elevated result should be interpreted alongside the rest of the cardiac pattern rather than alone.",
            "unknown": "This marker should be interpreted in the context of the wider cardiovascular pattern."
        }
    },
    "Coronary Perfusion Pressure": {
        "what_it_means": "A marker intended to reflect the pressure associated with blood supply to the heart muscle.",
        "why_it_matters": "It is one of the scan's circulation-related indicators for cardiac workload and perfusion balance.",
        "functional_significance": "This contributes to the scan's estimate of how efficiently the heart muscle may be supplied within its model.",
        "common_patterns": "Higher values may cluster with cardiovascular strain or altered perfusion dynamics.",
        "recommendation_notes": "Interpret alongside vascular resistance, coronary elasticity, and pump-related markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may suggest softer perfusion pressure in the scan model.",
            "low_moderate": "A more clearly reduced result may support reduced cardiac perfusion dynamics within the model.",
            "low_severe": "A markedly reduced result may indicate one of the stronger low-perfusion flags in this section.",
            "high_mild": "A mildly elevated result may fit a pattern of altered cardiac perfusion pressure.",
            "high_moderate": "A moderately elevated result may support a clearer pattern of cardiovascular strain or altered perfusion balance.",
            "high_severe": "A markedly elevated result may indicate one of the stronger circulation-related findings in this section.",
            "unknown": "This marker should be interpreted in the context of the wider cardiovascular pattern."
        }
    },
    "Gastric Absorption Function Coefficient": {
        "what_it_means": "A scan-derived marker used to estimate stomach-related absorption efficiency.",
        "why_it_matters": "It contributes to the software's wider picture of digestive function.",
        "functional_significance": "This marker helps model upper digestive efficiency and the stomach's contribution to nutrient handling.",
        "common_patterns": "Lower values may cluster with reduced digestive efficiency, poor tolerance, or wider upper gastrointestinal weakness patterns.",
        "recommendation_notes": "Interpret alongside pepsin secretion and gastric peristalsis.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may fit a pattern of softer upper digestive efficiency.",
            "low_moderate": "A moderately reduced result may support reduced absorption efficiency in the upper digestive pattern.",
            "low_severe": "A markedly reduced result may indicate one of the stronger digestive weakness signals in this section.",
            "high_mild": "A mildly elevated result may indicate relative compensation or increased activity in the scan model.",
            "high_moderate": "A moderately elevated result may reflect stronger digestive activity within the model, though interpretation depends on the wider pattern.",
            "high_severe": "A markedly elevated result should be interpreted alongside the full digestive context rather than alone.",
            "unknown": "This marker should be interpreted in the context of the wider digestive pattern."
        }
    },
    "Small Intestine Absorption Function Coefficient": {
        "what_it_means": "A scan-derived marker used to estimate nutrient absorption in the small intestine.",
        "why_it_matters": "Absorption is central to energy, nutrient status, and digestive resilience.",
        "functional_significance": "This contributes to the scan's picture of intestinal handling and nutrient uptake.",
        "common_patterns": "Lower values may cluster with digestive weakness, micronutrient shortfalls, and fatigue-related patterns.",
        "recommendation_notes": "Interpret alongside motility, gastric absorption, vitamins, trace elements, and amino acids.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may support a softer absorption pattern.",
            "low_moderate": "A moderately reduced result may point more clearly toward reduced intestinal absorption efficiency.",
            "low_severe": "A markedly reduced result may indicate one of the stronger digestive handling flags in this section.",
            "high_mild": "A mildly elevated result may reflect stronger absorptive activity in the model.",
            "high_moderate": "A moderately elevated result may reflect increased activity or compensation within the analyser model.",
            "high_severe": "A markedly elevated result should be interpreted alongside the broader digestive pattern.",
            "unknown": "This marker should be interpreted in the context of the wider digestive pattern."
        }
    },
    "Liver Fat Content": {
        "what_it_means": "A marker intended to reflect patterns associated with fat accumulation in the liver.",
        "why_it_matters": "It can be useful when thinking about metabolic balance, diet, and liver workload.",
        "functional_significance": "This contributes to the scan's estimate of metabolic load and hepatic fat-related strain.",
        "common_patterns": "Higher values may cluster with obesity markers, blood lipids, sugar-handling stress, or broader metabolic burden.",
        "recommendation_notes": "Interpret alongside liver function markers, blood lipids, obesity section, and digestive efficiency.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly lower result is generally less concerning in this model.",
            "low_moderate": "A moderately lower result is not usually a major concern unless the wider liver picture is weak.",
            "low_severe": "A markedly lower result is generally less clinically dominant than an elevated value in this context.",
            "high_mild": "A mildly elevated result may fit an early pattern of metabolic strain or liver burden.",
            "high_moderate": "A moderately elevated result may support a stronger pattern of metabolic load or fatty liver tendency within the scan model.",
            "high_severe": "A markedly elevated result may indicate one of the strongest metabolic-liver burden findings in this section.",
            "unknown": "This marker should be interpreted in the context of the wider liver and metabolic pattern."
        }
    },
    "Iron": {
        "what_it_means": "A mineral involved in oxygen transport, energy production, and red blood cell function.",
        "why_it_matters": "Low iron status may be relevant where fatigue, poor stamina, or reduced oxygen-carrying capacity are concerns.",
        "functional_significance": "This contributes to the scan's picture of mineral sufficiency, oxygenation support, and resilience.",
        "common_patterns": "Lower values may cluster with fatigue patterns, low vitality, reduced mineral reserve, or broader absorption issues.",
        "recommendation_notes": "Interpret alongside copper, vitamin C, digestive absorption, and general vitality markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may fit a softer iron support pattern.",
            "low_moderate": "A moderately reduced result may support a clearer pattern of reduced iron-related reserve or oxygen transport support.",
            "low_severe": "A markedly reduced result may indicate one of the stronger mineral insufficiency findings in this scan.",
            "high_mild": "A mildly elevated result may reflect increased iron burden within the analyser model and should be interpreted carefully.",
            "high_moderate": "A moderately elevated result may suggest a stronger iron-loading pattern in the scan model.",
            "high_severe": "A markedly elevated result should be interpreted cautiously and always in wider context.",
            "unknown": "This marker should be interpreted in the context of the wider mineral pattern."
        }
    },
    "Copper": {
        "what_it_means": "A trace mineral involved in enzyme activity, connective tissue health, iron regulation, and antioxidant systems.",
        "why_it_matters": "Copper balance matters for resilience, energy production, and mineral interactions.",
        "functional_significance": "This contributes to the scan's view of trace mineral sufficiency and metabolic cofactor balance.",
        "common_patterns": "Low values may cluster with reduced mineral reserve, antioxidant weakness, or wider nutrient insufficiency patterns.",
        "recommendation_notes": "Interpret alongside iron, collagen, and general vitality markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may fit a softer trace mineral support pattern.",
            "low_moderate": "A moderately reduced result may point more clearly toward reduced trace mineral reserve.",
            "low_severe": "A markedly reduced result may indicate one of the stronger trace mineral insufficiency findings in this scan.",
            "high_mild": "A mildly elevated result may reflect imbalance rather than benefit if the wider mineral picture is uneven.",
            "high_moderate": "A moderately elevated result may suggest mineral imbalance within the scan model.",
            "high_severe": "A markedly elevated result should be interpreted as part of the wider trace element pattern rather than in isolation.",
            "unknown": "This marker should be interpreted in the context of the wider mineral pattern."
        }
    },
    "Vitamin C": {
        "what_it_means": "A vitamin associated with antioxidant defence, collagen support, and immune resilience.",
        "why_it_matters": "It can be relevant to recovery, tissue repair, and tolerance of physical or oxidative stress.",
        "functional_significance": "This contributes to the scan's picture of antioxidant reserve, tissue repair support, and resilience.",
        "common_patterns": "Lower values may cluster with fatigue, stress, slower recovery, collagen weakness, or immune strain patterns.",
        "recommendation_notes": "Interpret alongside immune markers, collagen, fatigue-related markers, and trace elements.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may suggest softer antioxidant or tissue-support reserve.",
            "low_moderate": "A moderately reduced result may point toward weaker antioxidant resilience or slower repair support in this scan model.",
            "low_severe": "A markedly reduced result may indicate one of the stronger vitamin insufficiency patterns in this section.",
            "high_mild": "A mildly elevated result is usually less concerning than a low value in this context.",
            "high_moderate": "A moderately elevated result is not usually interpreted as dominant unless the wider context suggests imbalance.",
            "high_severe": "A markedly elevated result is rarely the main clinical focus on its own in this model.",
            "unknown": "This marker should be interpreted in the context of the wider vitamin pattern."
        }
    },
    "Vitamin D3": {
        "what_it_means": "A vitamin-related marker associated with immune balance, bone health, and regulatory resilience.",
        "why_it_matters": "Vitamin D status is often relevant in energy, immunity, and long-term structural health.",
        "functional_significance": "This contributes to the scan's picture of regulatory resilience, immune balance, and structural support.",
        "common_patterns": "Lower values may cluster with immune weakness, bone density issues, fatigue, or endocrine softness.",
        "recommendation_notes": "Interpret alongside immune, bone, endocrine, and vitality-related markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may suggest softer vitamin D-related resilience.",
            "low_moderate": "A moderately reduced result may support a clearer pattern of reduced regulatory or structural support.",
            "low_severe": "A markedly reduced result may indicate one of the stronger vitamin insufficiency signals in this scan.",
            "high_mild": "A mildly elevated result is not usually the main concern in isolation within this model.",
            "high_moderate": "A moderately elevated result is usually secondary to the wider context rather than a dominant finding on its own.",
            "high_severe": "A markedly elevated result should be interpreted cautiously but is not usually the primary concern in this scan model.",
            "unknown": "This marker should be interpreted in the context of the wider vitamin pattern."
        }
    },
    "Coenzyme Q10": {
        "what_it_means": "A coenzyme involved in cellular energy production, especially in energy-demanding tissues such as the heart and muscles.",
        "why_it_matters": "It may be relevant where fatigue, stamina, or cardiovascular resilience are part of the picture.",
        "functional_significance": "This contributes to the scan's estimate of mitochondrial support and energy efficiency.",
        "common_patterns": "Lower values may cluster with fatigue, low stamina, cardiac softness, or reduced metabolic reserve.",
        "recommendation_notes": "Interpret alongside fatigue, cardiovascular, and coenzyme-related markers.",
        "priority": "high",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may fit a softer energy-support pattern.",
            "low_moderate": "A moderately reduced result may support reduced mitochondrial or energy reserve in this scan model.",
            "low_severe": "A markedly reduced result may indicate one of the stronger low-energy support findings in this section.",
            "high_mild": "A mildly elevated result is generally more reassuring in this model unless other markers suggest imbalance.",
            "high_moderate": "A moderately elevated result is usually not a dominant concern on its own.",
            "high_severe": "A markedly elevated result should still be interpreted in overall context rather than isolation.",
            "unknown": "This marker should be interpreted in the context of the wider coenzyme pattern."
        }
    },
    "Pantothenic acid": {
        "what_it_means": "A B-vitamin-related cofactor involved in energy metabolism and stress response pathways.",
        "why_it_matters": "It may be relevant where energy production and adrenal resilience are concerns.",
        "functional_significance": "This contributes to the scan's picture of metabolic cofactor support and adaptive resilience.",
        "common_patterns": "Lower values may cluster with fatigue, stress load, low resilience, or weaker vitamin support patterns.",
        "recommendation_notes": "Interpret alongside coenzymes, endocrine markers, and fatigue-related findings.",
        "priority": "medium",
        "recommendations": {
            "normal": "This marker sits within the analyser's expected range in this scan.",
            "low_mild": "A mildly reduced result may suggest softer metabolic cofactor support.",
            "low_moderate": "A moderately reduced result may point more clearly toward weaker energy-support pathways in this model.",
            "low_severe": "A markedly reduced result may indicate one of the stronger cofactor insufficiency patterns in this section.",
            "high_mild": "A mildly elevated result is usually not the main concern unless the broader pattern is unusual.",
            "high_moderate": "A moderately elevated result is rarely dominant on its own in this model.",
            "high_severe": "A markedly elevated result should be interpreted as part of the wider coenzyme pattern.",
            "unknown": "This marker should be interpreted in the context of the wider coenzyme pattern."
        }
    },
}


_DEFINITION_CACHE = None
_SCAFFOLD_CACHE = None


def _load_definition_library():
    global _DEFINITION_CACHE
    if _DEFINITION_CACHE is not None:
        return _DEFINITION_CACHE

    if DEFINITION_FILE.exists():
        try:
            _DEFINITION_CACHE = json.loads(DEFINITION_FILE.read_text(encoding="utf-8"))
            return _DEFINITION_CACHE
        except Exception:
            pass

    _DEFINITION_CACHE = {}
    return _DEFINITION_CACHE


def _load_scaffold():
    global _SCAFFOLD_CACHE
    if _SCAFFOLD_CACHE is not None:
        return _SCAFFOLD_CACHE

    if SCAFFOLD_FILE.exists():
        try:
            _SCAFFOLD_CACHE = json.loads(SCAFFOLD_FILE.read_text(encoding="utf-8"))
            return _SCAFFOLD_CACHE
        except Exception:
            pass

    _SCAFFOLD_CACHE = {}
    return _SCAFFOLD_CACHE


def _deep_copy(data: dict) -> dict:
    return json.loads(json.dumps(data))


def _merge_content(base: dict, override: dict | None) -> dict:
    result = _deep_copy(base)

    if not override:
        return result

    for key, value in override.items():
        if key == "recommendations" and isinstance(value, dict):
            result.setdefault("recommendations", {})
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, str) and sub_value.strip():
                    result["recommendations"][sub_key] = sub_value.strip()
        elif isinstance(value, str) and value.strip():
            result[key] = value.strip()
        elif key == "priority" and value:
            result[key] = value

    return result


def get_marker_content(marker_name: str, section_name: str | None = None) -> dict:
    definition_library = _load_definition_library()
    scaffold = _load_scaffold()

    definition_entry = None
    if section_name and section_name in definition_library:
        definition_entry = definition_library[section_name].get(marker_name)

    scaffold_entry = None
    if section_name and section_name in scaffold:
        scaffold_entry = scaffold[section_name].get(marker_name)

    fallback = FALLBACK_LIBRARY.get(marker_name, DEFAULT_MARKER_CONTENT)

    merged = _merge_content(fallback, definition_entry)
    merged = _merge_content(merged, scaffold_entry)

    merged.setdefault("recommendations", DEFAULT_MARKER_CONTENT["recommendations"])
    return merged


def interpret_marker_result(marker_name: str, severity: str | None, section_name: str | None = None) -> str:
    content = get_marker_content(marker_name, section_name)
    recommendations = content.get("recommendations", {})

    if not severity:
        severity = "unknown"

    return recommendations.get(
        severity,
        "This marker should be interpreted in the context of the wider pattern."
    )