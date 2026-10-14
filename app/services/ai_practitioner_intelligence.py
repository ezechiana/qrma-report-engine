from __future__ import annotations
from datetime import datetime, timezone

import json
import os
import logging
import time
import uuid
from typing import Any

from openai import OpenAI

from app.models.schema import NarrativeBlock
from app.services.go360_ai_reference_context import attach_reference_context

_client = None
_logger = logging.getLogger("go360.ai")

def _ai_event(level, event, **fields):
    """Structured, content-free operational event. Never log prompts or clinical output."""
    try:
        safe = {"event": event, **fields}
        _logger.log(level, "GO360_AI_EVENT %s", json.dumps(safe, separators=(",", ":"), default=str))
    except Exception:
        pass



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
                    "go360_name": _safe_getattr(marker, "display_label", None) or _safe_getattr(marker, "source_name", ""),
                    "qrma_software_name": _safe_getattr(marker, "source_name", None),
                    "canonical_marker_id": _safe_getattr(marker, "canonical_marker_id", None) or _safe_getattr(marker, "go360_id", None),
                    "legacy_marker_id": _safe_getattr(marker, "legacy_marker_id", None) or _safe_getattr(marker, "marker_id", None),
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
        "top_markers": attach_reference_context(_collect_top_markers(report)),
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
- QRMA scan patterns are exploratory, not clinically validated diagnoses or proof of disease.
- Use only supplied evidence; do not invent measurements, marker identities, laboratory results, or clinical claims.
- Treat GO360 names as display labels and QRMA software names as source labels; use canonical IDs when present to distinguish identical names.
- Do not treat any experimental shadow scores as approved report evidence.
- Avoid exaggerated certainty.
- Avoid repeating raw marker names unnecessarily unless they are central.

EVIDENCE DISCIPLINE — MANDATORY:
- Separate OBSERVED scan inputs from POSSIBLE contextual interpretations and from RECOMMENDED verification steps. A reference-intelligence entry is background educational context, not patient-specific proof.
- Do not state or imply that QRMA establishes impairment, reduced blood flow, neuroinflammation, methylation dysfunction, nutrient deficiency, or any other physiological condition. Never present exploratory patterns as validated measurements or diagnoses.
- If a reference suggests a possible association (for example sleep, inflammation, endothelial function, or nutrition), explicitly frame it as a hypothesis to explore through history, symptoms and appropriate validated assessment, not as a causal finding.
- Prioritise findings according to supplied severity and section evidence only. If evidence is insufficient to rank them, say so; do not manufacture a hierarchy.
- When similarly named markers appear, preserve their distinct QRMA software names and canonical/legacy IDs in the interpretation where necessary; do not merge Memory Index(ZS) (M050) with Memory (M327).
- Priority actions must start with clinical context and verification where appropriate; do not recommend specific supplementation or targeted physiological interventions solely because of a scan marker.
- Avoid unqualified phrases such as "moderate impairment", "reduced limb circulation", "indicating oxygen delivery problems", "linked to neuroinflammation", or "methylation dysfunction" unless independently corroborated by supplied validated clinical evidence.
- Keep the existing JSON schema unchanged. Do not introduce extra keys or expose internal source-processing instructions in the final report.

SEVERITY AND EVIDENCE LANGUAGE — MANDATORY:
- The labels mild, moderate and severe refer ONLY to a QRMA scan-marker classification. They do NOT establish mild, moderate or severe physiological dysfunction, cognitive impairment, vascular disease or any other clinical condition.
- Describe a marker as a "mild/moderate/severe scan-marker variation" or "marker classified as moderate in the supplied scan", never as "moderate impairment", "moderate circulation issues", or "Memory shows mild impairment".
- Never claim or imply a marker directly measures peripheral blood flow, nutrient/oxygen delivery, memory performance, neuroinflammation, endothelial dysfunction or methylation status.
- For Memory Index(ZS) (M050) and Memory (M327), distinguish the two separate marker identities where discussing both. Neither marker alone establishes cognitive impairment.
- Do not infer a patient has symptoms or risk factors unless supplied; propose history-taking and validated assessment where appropriate.
- Example UNACCEPTABLE: "Limbs Circulation Limit reflects moderate issues with peripheral blood flow."
- Example ACCEPTABLE: "Limbs Circulation Limit has a moderate scan-marker classification; its clinical significance is uncertain and should be considered with symptoms, history and validated vascular assessment if indicated."
- Example UNACCEPTABLE: "Memory shows mild impairment, potentially linked to sleep and energy factors."
- Example ACCEPTABLE: "The Memory (M327) marker has a mild scan-marker variation. Any relevance to cognitive concerns requires clinical context; sleep and energy are possible areas to explore, not established causes."
- Avoid generic disclaimers in every sentence; be concise, useful and practitioner-facing while keeping uncertainty explicit.

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


def _validate_ai_response(payload: Any, features: dict, _audit: dict | None = None) -> dict:
    """Validate shape and types; fail closed to existing deterministic fallback."""
    def invalid(reason):
        if _audit is not None:
            _audit["invalid_reason"] = reason
        return _fallback_output(features)
    if not isinstance(payload, dict):
        return invalid("payload_not_object")
    narrative = payload.get("narrative")
    if not isinstance(narrative, dict):
        return invalid("narrative_not_object")
    if not isinstance(payload.get("practitioner_summary"), str) or not payload["practitioner_summary"].strip():
        return invalid("summary_missing")
    for key in ("key_patterns", "priority_actions"):
        value = payload.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return invalid(key + "_invalid")
    if not isinstance(narrative.get("headline"), str) or not isinstance(narrative.get("overview"), str):
        return invalid("narrative_text_invalid")
    if not isinstance(narrative.get("focus_areas"), list) or not all(isinstance(item, str) for item in narrative["focus_areas"]):
        return invalid("focus_areas_invalid")
    if narrative.get("tone") != "clinical":
        return invalid("tone_invalid")
    return {
        "practitioner_summary": payload["practitioner_summary"],
        "key_patterns": payload["key_patterns"][:6],
        "priority_actions": payload["priority_actions"][:6],
        "narrative": {
            "headline": narrative["headline"],
            "overview": narrative["overview"],
            "focus_areas": narrative["focus_areas"][:5],
            "tone": "clinical",
        },
    }


def _coerce_json_response(content: str, features: dict, _audit: dict | None = None) -> dict:
    # Accept only a complete JSON object or a single complete Markdown JSON fence.
    # Never extract arbitrary embedded JSON or repair truncated responses.
    if isinstance(content, str):
        candidate = content.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            first_break = candidate.find("\n")
            if first_break != -1:
                language = candidate[3:first_break].strip().lower()
                if language in ("", "json"):
                    candidate = candidate[first_break + 1:-3].strip()
        content = candidate
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        if _audit is not None:
            _audit["invalid_reason"] = "json_parse_failed"
        return _fallback_output(features)
    return _validate_ai_response(payload, features, _audit=_audit)


def generate_practitioner_intelligence(report, model: str = "gpt-4o-mini", *, provenance_sink=None) -> dict:
    features = _build_features(report)
    correlation_id = (provenance_sink or {}).get("correlation_id") or uuid.uuid4().hex[:12]
    def record(status, fallback_used, reason_code=None):
        if provenance_sink is not None:
            provenance_sink.update({
                "schema_version": 1, "status": status, "model": model,
                "fallback_used": fallback_used, "correlation_id": correlation_id,
                "execution_timestamp": datetime.now(timezone.utc).isoformat(),
                "reason_code": reason_code,
            })
    # Initialise conservatively before any client setup or request.
    record("AI_SKIPPED", True, "client_unconfigured")
    try:
        client = _get_client()
    except Exception:
        record("AI_SKIPPED", True, "client_initialisation_failed")
        return _fallback_output(features)
    if client is None:
        _ai_event(logging.WARNING, "GO360_AI_SKIPPED", operation="practitioner_intelligence", model=model,
                  correlation_id=correlation_id, reason="api_key_missing")
        _ai_event(logging.WARNING, "GO360_AI_FALLBACK_ACTIVATED", operation="practitioner_intelligence",
                  correlation_id=correlation_id, reason="api_key_missing")
        record("AI_SKIPPED", True, "client_unconfigured")
        return _fallback_output(features)

    started = time.monotonic()
    _ai_event(logging.INFO, "GO360_OPENAI_REQUEST_STARTED", operation="practitioner_intelligence",
              model=model, correlation_id=correlation_id)
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.55,
            response_format={"type": "json_object"},
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
        duration_ms = round((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage", None)
        _ai_event(logging.INFO, "GO360_OPENAI_REQUEST_SUCCESS", operation="practitioner_intelligence",
                  model=model, correlation_id=correlation_id, duration_ms=duration_ms,
                  provider_request_id=getattr(response, "_request_id", None),
                  input_tokens=getattr(usage, "prompt_tokens", None),
                  output_tokens=getattr(usage, "completion_tokens", None),
                  total_tokens=getattr(usage, "total_tokens", None))
        content = response.choices[0].message.content or ""
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        is_fenced = isinstance(content, str) and content.strip().startswith("```")
        _ai_event(logging.INFO, "GO360_OPENAI_RESPONSE_METADATA", operation="practitioner_intelligence",
                  model=model, correlation_id=correlation_id,
                  finish_reason=finish_reason if finish_reason in ("stop", "length", "content_filter", "tool_calls", "function_call") else "other_or_missing",
                  content_empty=not bool(content), content_chars=len(content), markdown_fence_detected=is_fenced)
        audit = {}
        result = _coerce_json_response(content, features, _audit=audit)
        if audit.get("invalid_reason"):
            record("AI_INVALID_RESPONSE", True, audit["invalid_reason"])
            _ai_event(logging.WARNING, "GO360_OPENAI_RESPONSE_INVALID", operation="practitioner_intelligence",
                      model=model, correlation_id=correlation_id, reason=audit["invalid_reason"])
            _ai_event(logging.WARNING, "GO360_AI_FALLBACK_ACTIVATED", operation="practitioner_intelligence",
                      correlation_id=correlation_id, reason="invalid_response")
        else:
            record("AI_VALIDATED", False)
            _ai_event(logging.INFO, "GO360_AI_OUTPUT_VALIDATED", operation="practitioner_intelligence",
                      correlation_id=correlation_id)
        result.setdefault("practitioner_summary", "")
        result.setdefault("key_patterns", [])
        result.setdefault("priority_actions", [])
        result.setdefault("narrative", {})
        return result
    except Exception as exc:
        record("AI_REQUEST_FAILED", True, "request_or_processing_exception")
        _ai_event(logging.ERROR, "GO360_OPENAI_REQUEST_FAILED", operation="practitioner_intelligence",
                  model=model, correlation_id=correlation_id,
                  duration_ms=round((time.monotonic() - started) * 1000),
                  error_type=type(exc).__name__)
        _ai_event(logging.WARNING, "GO360_AI_FALLBACK_ACTIVATED", operation="practitioner_intelligence",
                  correlation_id=correlation_id, reason="request_or_processing_failed")
        return _fallback_output(features)


def apply_practitioner_intelligence(report, model: str = "gpt-4o-mini", *, provenance_sink=None):
    result = generate_practitioner_intelligence(report, model=model, provenance_sink=provenance_sink)

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
