from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from app.models.schema import NarrativeBlock

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        _client = OpenAI(api_key=api_key)
    return _client


def _safe_getattr(obj: Any, name: str, default=None):
    return getattr(obj, name, default)


def _severity_rank(severity: str | None) -> int:
    severity = severity or "unknown"
    if "severe" in severity:
        return 4
    if "moderate" in severity:
        return 3
    if "mild" in severity:
        return 2
    if severity == "normal":
        return 0
    return 1


def _collect_top_sections(report, limit: int = 6) -> list[dict]:
    sections = list(_safe_getattr(report, "sections", []) or [])
    sections = [s for s in sections if _safe_getattr(s, "abnormal_count", 0) > 0]
    sections = sorted(
        sections,
        key=lambda s: (_safe_getattr(s, "section_score", 0), _safe_getattr(s, "abnormal_count", 0)),
        reverse=True,
    )
    output = []
    for section in sections[:limit]:
        title = _safe_getattr(section, "display_title", None) or _safe_getattr(section, "source_title", "")
        output.append(
            {
                "title": title,
                "abnormal_count": _safe_getattr(section, "abnormal_count", 0),
                "section_score": _safe_getattr(section, "section_score", 0),
                "priority": _safe_getattr(section, "priority", "normal"),
            }
        )
    return output


def _collect_top_markers(report, limit: int = 18) -> list[dict]:
    markers = []
    for section in list(_safe_getattr(report, "sections", []) or []):
        title = _safe_getattr(section, "display_title", None) or _safe_getattr(section, "source_title", "")
        for marker in list(_safe_getattr(section, "parameters", []) or []):
            severity = _safe_getattr(marker, "severity", None)
            if not severity or severity == "normal":
                continue
            markers.append(
                {
                    "section": title,
                    "name": _safe_getattr(marker, "display_label", None) or _safe_getattr(marker, "source_name", ""),
                    "severity": severity,
                    "pattern_cluster": _safe_getattr(marker, "pattern_cluster", None),
                    "value": _safe_getattr(marker, "actual_value_text", None),
                    "range": _safe_getattr(marker, "normal_range_text", None),
                    "what_it_means": _safe_getattr(marker, "what_it_means", None),
                }
            )
    markers = sorted(
        markers,
        key=lambda m: (_severity_rank(m["severity"]), m["section"], m["name"]),
        reverse=True,
    )
    return markers[:limit]


def _collect_pattern_clusters(report, limit: int = 8) -> list[str]:
    clusters = {}
    for section in list(_safe_getattr(report, "sections", []) or []):
        for marker in list(_safe_getattr(section, "parameters", []) or []):
            cluster = _safe_getattr(marker, "pattern_cluster", None)
            if not cluster:
                continue
            clusters[cluster] = clusters.get(cluster, 0) + 1
    ranked = sorted(clusters.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in ranked[:limit]]


def _collect_scores(report) -> dict:
    return {
        "overall_scan_score": _safe_getattr(report, "overall_scan_score", None),
        "overall_summary": _safe_getattr(report, "overall_summary", None),
    }


def _build_features(report) -> dict:
    return {
        "report_profile": _safe_getattr(report, "report_profile", None),
        "scores": _collect_scores(report),
        "top_sections": _collect_top_sections(report),
        "top_markers": _collect_top_markers(report),
        "pattern_clusters": _collect_pattern_clusters(report),
        "existing_clinical_recommendations": list(_safe_getattr(report, "clinical_recommendations", []) or [])[:6],
    }


def _build_prompt(features: dict) -> str:
    return f"""
You are an experienced naturopathic and functional medicine practitioner writing a premium practitioner-facing scan interpretation.

STYLE REQUIREMENTS:
- Write in calm, confident, clinically coherent prose.
- Sound like a thoughtful practitioner, not a machine summariser.
- Focus on pattern synthesis, hierarchy, and root-cause thinking.
- Keep language cautious and compliant: use phrases like "suggests", "may reflect", "is consistent with".
- Do not diagnose.
- Avoid exaggerated certainty.
- Avoid repeating raw marker names unnecessarily unless they are central.

BRAND DIRECTION:
- Tone should align with a premium natural medicine clinic.
- Emphasise interconnected systems, restoration of balance, and foundational support.
- Highlight what appears most important first.

TASK:
Using the structured scan data below, produce STRICT JSON with these keys only:
{{
  "practitioner_summary": "2 short paragraphs max",
  "key_patterns": ["3 to 6 concise bullet-style strings"],
  "priority_actions": ["3 to 6 concise action-oriented strings"],
  "narrative": {{
    "headline": "short headline",
    "overview": "1 short paragraph",
    "focus_areas": ["2 to 5 short strings"],
    "tone": "clinical"
  }}
}}

INPUT DATA:
{json.dumps(features, ensure_ascii=False, indent=2)}
""".strip()


def _fallback_output(features: dict) -> dict:
    top_sections = features.get("top_sections", [])
    section_names = [s["title"] for s in top_sections[:3]]
    if section_names:
        joined = ", ".join(section_names)
        summary = (
            f"The scan suggests that the most relevant areas for follow-up are {joined}. "
            f"These patterns appear more significant than the background variation seen elsewhere in the report."
        )
    else:
        summary = (
            "The scan highlights functional patterns that may benefit from further practitioner review, "
            "with attention placed on the most repeated and highest-priority abnormalities."
        )

    key_patterns = [f"Pattern emphasis in {name}" for name in section_names[:4]] or [
        "Repeated functional abnormalities across priority sections",
        "Need to review the main drivers rather than isolated markers",
    ]
    priority_actions = [
        "Review the most abnormal sections first and prioritise foundational support.",
        "Interpret findings alongside symptoms, history, and clinical context.",
        "Use the scan to guide next-step testing or supportive interventions where appropriate.",
    ]

    return {
        "practitioner_summary": summary,
        "key_patterns": key_patterns[:6],
        "priority_actions": priority_actions[:6],
        "narrative": {
            "headline": "Practitioner scan interpretation",
            "overview": summary,
            "focus_areas": section_names[:4] or ["Priority section review"],
            "tone": "clinical",
        },
    }


def _coerce_json_response(content: str, features: dict) -> dict:
    try:
        return json.loads(content)
    except Exception:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start:end+1])
            except Exception:
                pass
    return _fallback_output(features)


def generate_practitioner_intelligence(report, model: str = "gpt-4o-mini") -> dict:
    features = _build_features(report)
    client = _get_client()
    if client is None:
        return _fallback_output(features)

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.55,
            messages=[
                {
                    "role": "system",
                    "content": "You are a clinical reasoning assistant that returns valid JSON only."
                },
                {
                    "role": "user",
                    "content": _build_prompt(features)
                },
            ],
        )
        content = response.choices[0].message.content or ""
        result = _coerce_json_response(content, features)
        result.setdefault("practitioner_summary", "")
        result.setdefault("key_patterns", [])
        result.setdefault("priority_actions", [])
        result.setdefault("narrative", {})
        return result
    except Exception:
        return _fallback_output(features)


def apply_practitioner_intelligence(report, model: str = "gpt-4o-mini"):
    result = generate_practitioner_intelligence(report, model=model)

    setattr(report, "practitioner_summary", result.get("practitioner_summary"))
    setattr(report, "key_patterns", result.get("key_patterns", []))
    setattr(report, "priority_actions", result.get("priority_actions", []))

    narrative = result.get("narrative") or {}
    try:
        report.narrative = narrative if isinstance(narrative, NarrativeBlock) else NarrativeBlock(**narrative)
    except Exception:
        try:
            report.narrative = NarrativeBlock(
                headline=narrative.get("headline", "Practitioner scan interpretation"),
                overview=narrative.get("overview", result.get("practitioner_summary", "")),
                focus_areas=narrative.get("focus_areas", []),
                tone=narrative.get("tone", "clinical"),
            )
        except Exception:
            pass

    return report
