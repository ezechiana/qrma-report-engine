from __future__ import annotations

from typing import List

from app.models.schema import ParsedReport, DetectedPattern, ParameterResult
from app.services.pattern_engine_v1 import detect_patterns as detect_patterns_v1
from app.services.scoring_engine import compute_scan_scores


class PatternEngine:
    """
    Compatibility wrapper.

    - Keeps the existing public interface intact
    - Preserves current marker-based patterns as a fallback
    - Adds the new scoring-aware v1 pattern engine safely
    - Computes scores internally so no route/pipeline changes are required
    """

    def detect_patterns(self, report: ParsedReport) -> List[DetectedPattern]:
        patterns: List[DetectedPattern] = []

        # -----------------------------
        # Legacy / marker-based patterns
        # -----------------------------
        patterns.extend(self._detect_basic_patterns(report))

        # -----------------------------
        # Advanced v1 patterns
        # -----------------------------
        try:
            visible_sections = [
                s for s in getattr(report, "sections", []) or []
                if getattr(s, "parameters", None)
                and len(s.parameters) > 0
                and not self._is_hidden_section(
                    getattr(s, "display_title", None) or getattr(s, "source_title", None)
                )
            ]

            scan_scores = compute_scan_scores(visible_sections)

            # optional: attach for downstream use
            try:
                report.section_score_cards = scan_scores.get("section_score_cards", [])
                report.system_score_cards = scan_scores.get("system_score_cards", [])
                report.body_system_cards = scan_scores.get("body_system_cards", [])
                report.overall_scan_score = scan_scores.get("overall_score", 0)
            except Exception as e:
                print(f"[PATTERN_ENGINE ERROR] {e}")

            advanced_patterns = detect_patterns_v1(
                report=report,
                section_score_cards=scan_scores.get("section_score_cards", []),
                system_score_cards=scan_scores.get("system_score_cards", []),
            )

            patterns.extend(self._convert_advanced_patterns(advanced_patterns))

            # optional: keep raw advanced patterns too
            try:
                report.detected_patterns = advanced_patterns
                report.primary_pattern = advanced_patterns[0] if advanced_patterns else None
                report.contributing_patterns = advanced_patterns[1:4] if len(advanced_patterns) > 1 else []
            except Exception:
                pass

        except Exception:
            # Fail safe: never let pattern logic break report generation
            pass

        deduped: List[DetectedPattern] = []
        seen = set()
        for p in patterns:
            name = getattr(p, "name", None)
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            deduped.append(p)

        try:
            report.patterns = deduped
        except Exception:
            pass

        return deduped

    def _detect_basic_patterns(self, report: ParsedReport) -> List[DetectedPattern]:
        patterns: List[DetectedPattern] = []

        # --- DIGESTIVE PATTERN ---
        digestive_markers = self._get_markers(report, [
            "Gastric Absorption Function Coefficient",
            "Small Intestine Absorption Function Coefficient",
            "Intestinal bacteria coefficient",
            "Large intestine peristalsis function coefficient",
        ])

        if len(digestive_markers) >= 2:
            patterns.append(
                DetectedPattern(
                    name="Digestive Efficiency Pattern",
                    strength="STRONG" if len(digestive_markers) >= 3 else "MODERATE",
                    confidence=0.85,
                    description="Reduced digestive efficiency affecting absorption and gut function.",
                    systems=["Digestive"],
                    supporting_markers=digestive_markers,
                )
            )

        # --- NUTRIENT DEPLETION ---
        vitamin_markers = self._get_markers(report, [
            "Vitamin C",
            "Vitamin E",
            "Vitamin D3",
            "Vitamin B1",
            "Vitamin A",
        ])

        if len(vitamin_markers) >= 3:
            patterns.append(
                DetectedPattern(
                    name="Nutrient Depletion Pattern",
                    strength="STRONG",
                    confidence=0.90,
                    description="Broad micronutrient depletion affecting energy and recovery.",
                    systems=["Metabolic"],
                    supporting_markers=vitamin_markers,
                )
            )

        # --- METABOLIC ---
        metabolic_markers = self._get_markers(report, [
            "Triglyceride content of abnormal coefficient",
            "Abnormal lipid metabolism coefficient",
            "Liver Fat Content",
        ])

        if len(metabolic_markers) >= 2:
            patterns.append(
                DetectedPattern(
                    name="Metabolic Dysfunction Pattern",
                    strength="MODERATE",
                    confidence=0.80,
                    description="Reduced efficiency in lipid metabolism and energy regulation.",
                    systems=["Metabolic"],
                    supporting_markers=metabolic_markers,
                )
            )

        # --- DETOX ---
        detox_markers = self._get_markers(report, [
            "Lead",
            "Mercury",
            "Cadmium",
            "Aluminum",
            "Uric acid Index",
        ])

        if len(detox_markers) >= 2:
            patterns.append(
                DetectedPattern(
                    name="Detoxification Burden Pattern",
                    strength="MODERATE",
                    confidence=0.85,
                    description="Signs of increased toxic load and detox demand.",
                    systems=["Liver", "Kidney"],
                    supporting_markers=detox_markers,
                )
            )

        return patterns

    def _convert_advanced_patterns(self, patterns_v1: List[dict]) -> List[DetectedPattern]:
        converted: List[DetectedPattern] = []

        for p in patterns_v1:
            priority = str(p.get("priority", "medium")).upper()

            converted.append(
                DetectedPattern(
                    name=p.get("title", "Detected Pattern"),
                    strength=priority,
                    confidence=float(p.get("confidence", 0.0) or 0.0),
                    description=p.get("clinical_summary", ""),
                    systems=p.get("matched_systems", []) or [],
                    supporting_markers=p.get("driver_sections", []) or [],
                )
            )

        return converted

    def _is_hidden_section(self, title: str | None) -> bool:
        return (title or "").strip().lower() in {"expert analysis", "hand analysis"}

    def _marker_name(self, marker: ParameterResult) -> str:
        return (
            getattr(marker, "name", None)
            or getattr(marker, "source_name", None)
            or getattr(marker, "clinical_label", None)
            or ""
        )

    def _status_text(self, marker: ParameterResult) -> str:
        return str(getattr(marker, "status", "") or "").strip().lower()

    def _is_abnormal(self, marker: ParameterResult) -> bool:
        status = self._status_text(marker)
        if status:
            return status not in {"within range", "normal", "in range"}
        return bool(getattr(marker, "is_abnormal", False))

    def _get_markers(self, report: ParsedReport, names: List[str]) -> List[str]:
        found: List[str] = []
        wanted = set(names)

        for section in getattr(report, "sections", []) or []:
            for marker in getattr(section, "parameters", []) or []:
                marker_name = self._marker_name(marker)
                if not marker_name:
                    continue

                if marker_name in wanted and self._is_abnormal(marker):
                    if marker_name not in found:
                        found.append(marker_name)

        return found


