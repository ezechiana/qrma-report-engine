import os
from typing import Dict, Any, List

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _extract_report_features(report) -> Dict[str, Any]:
    """
    Extract structured, safe inputs for AI.
    No raw guessing — only curated signals.
    """

    abnormal_markers = []
    systems = []
    patterns = set()

    for section in report.sections:
        if section.abnormal_count > 0:
            systems.append({
                "system": section.display_title or section.source_title,
                "abnormal_count": section.abnormal_count,
                "score": section.section_score,
            })

        for p in section.parameters:
            if p.is_abnormal:
                abnormal_markers.append({
                    "name": p.display_label or p.source_name,
                    "severity": p.severity,
                    "system": section.display_title or section.source_title,
                    "cluster": getattr(p, "pattern_cluster", None),
                })

                if getattr(p, "pattern_cluster", None):
                    patterns.add(p.pattern_cluster)

    return {
        "systems": sorted(systems, key=lambda x: x["score"], reverse=True)[:5],
        "abnormal_markers": abnormal_markers[:20],  # limit tokens
        "patterns": list(patterns),
    }


def _build_prompt(features: Dict[str, Any]) -> str:
    return f"""
You are an experienced functional medicine practitioner reviewing a wellness scan.
Your goal is to provide a clear, insightful, and clinically intelligent interpretation.

STYLE:
- Write like a practitioner explaining patterns, not listing data
- Focus on root causes and system interactions
- Avoid generic phrasing
- Avoid repeating marker names unnecessarily
- Sound natural, confident, and human

CLINICAL APPROACH:
- Identify 2–3 dominant system patterns
- Explain how they connect (e.g. gut → immune → cardiovascular)
- Highlight underlying drivers (e.g. inflammation, detox burden, nutrient deficiency)
- Do NOT diagnose
- Use language like: "suggests", "consistent with", "pattern indicates"

INPUT DATA:

Systems:
{features['systems']}

Abnormal Markers:
{features['abnormal_markers']}

Detected Pattern Clusters:
{features['patterns']}

OUTPUT FORMAT (STRICT JSON):

{{
  "practitioner_summary": "...",
  "key_patterns": ["...", "..."],
  "priority_actions": ["...", "..."]
}}
"""


def generate_practitioner_insights(report) -> Dict[str, Any]:
    """
    Main entry point for AI practitioner layer
    """

    try:
        features = _extract_report_features(report)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=[
                {"role": "system", "content": "You are a clinical reasoning assistant."},
                {"role": "user", "content": _build_prompt(features)}
            ],
        )

        content = response.choices[0].message.content

        import json
        parsed = json.loads(content)

        return parsed

    except Exception as e:
        print(f"[AI ERROR] Practitioner insights failed: {e}")

        # SAFE FALLBACK
        return {
            "practitioner_summary": "The scan highlights areas of imbalance that may benefit from further evaluation and targeted support.",
            "key_patterns": [],
            "priority_actions": [],
        }