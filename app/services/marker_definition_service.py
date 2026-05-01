from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "app" / "data" / "marker_intelligence_master.csv"
DEBUG_MATCHING = os.getenv("DEBUG_MARKER_MATCHING", "false").lower() == "true"


# =========================================================
# NORMALISATION HELPERS
# =========================================================

def _normalize(value: Optional[str]) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.casefold()


def _normalize_compact(value: Optional[str]) -> str:
    return re.sub(r"[\s\-_]+", "", _normalize(value))


def _report_category_variants(value: Optional[str]) -> List[str]:
    text = _normalize(value)
    if not text:
        return []

    variants = {text}

    if "analysis report card" in text:
        inner = text.replace("analysis report card", "").strip()
        inner = inner.strip("() ").strip()
        if inner:
            variants.add(inner)
            variants.add(f"({inner}) analysis report card")
    else:
        variants.add(f"({text}) analysis report card")

    return [v for v in variants if v]


def _marker_variants(value: Optional[str]) -> List[str]:
    text = _normalize(value)
    compact = _normalize_compact(value)
    variants = {text}
    if compact:
        variants.add(compact)
    return [v for v in variants if v]


def _resolve_csv_path() -> Path:
    env = os.getenv("MARKER_DEFINITION_CSV")
    if env:
        env_path = Path(env)
        if not env_path.is_absolute():
            env_path = ROOT / env
        return env_path
    return DEFAULT_CSV


# =========================================================
# LOAD INDEX (FIXED VERSION)
# =========================================================

@lru_cache(maxsize=1)
def load_marker_definition_index() -> Dict[str, object]:
    csv_path = _resolve_csv_path()

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Marker definition CSV not found at {csv_path}. "
            f"Expected dataset at app/data/marker_intelligence_master.csv"
        )

    required = {
        "control_id",
        "canonical_system",
        "original_name",
        "clinical_label",
        "original_report_category",
        "clinical_meaning",
        "why_it_matters",
        "functional_significance",
        "low_interpretation",
        "high_interpretation",
        "pattern_links",
        "recommendation_hint",
        "pattern_cluster",
    }

    by_exact: Dict[Tuple[str, str], Dict[str, str]] = {}

    # 🔥 FIX: allow multiple rows per marker
    by_marker: Dict[str, List[Dict[str, str]]] = {}

    rows: List[Dict[str, str]] = []

    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "marker_intelligence_master.csv missing columns: "
                + ", ".join(sorted(missing))
            )

        for row in reader:
            rows.append(row)

            category_variants = _report_category_variants(row.get("original_report_category"))
            marker_variants = _marker_variants(row.get("original_name"))

            for c in category_variants:
                for m in marker_variants:
                    by_exact[(c, m)] = row

            for m in marker_variants:
                by_marker.setdefault(m, []).append(row)

    print(f"[INFO] Loaded {len(rows)} marker definitions from {csv_path}")

    return {
        "rows": rows,
        "by_exact": by_exact,
        "by_marker": by_marker,
        "csv_path": str(csv_path),
    }


# =========================================================
# MATCHING ENGINE (FIXED)
# =========================================================

def get_marker_definition(
    index: Dict[str, object],
    system: Optional[str],
    marker_name: Optional[str],
) -> Optional[Dict[str, str]]:
    if not marker_name:
        return None

    by_exact = index["by_exact"]  # type: ignore[index]
    by_marker = index["by_marker"]  # type: ignore[index]

    category_candidates = _report_category_variants(system)
    marker_candidates = _marker_variants(marker_name)

    # -----------------------------------------------------
    # 1. STRICT MATCH (category + marker)
    # -----------------------------------------------------
    for c in category_candidates:
        for m in marker_candidates:
            row = by_exact.get((c, m))
            if row:
                if DEBUG_MATCHING:
                    print(f"[DEBUG] Exact match: {marker_name} → {c}")
                return row

    # -----------------------------------------------------
    # 2. CATEGORY-AWARE NAME MATCH (NEW)
    # -----------------------------------------------------
    for m in marker_candidates:
        rows = by_marker.get(m, [])
        for row in rows:
            row_category = _normalize(row.get("original_report_category"))
            if any(row_category == c for c in category_candidates):
                if DEBUG_MATCHING:
                    print(f"[DEBUG] Category-aware match: {marker_name}")
                return row

    # -----------------------------------------------------
    # 3. NAME MATCH (FALLBACK)
    # -----------------------------------------------------
    for m in marker_candidates:
        rows = by_marker.get(m, [])
        if rows:
            if DEBUG_MATCHING:
                print(f"[DEBUG] Name-only match: {marker_name}")
            return rows[0]  # safest fallback

    # -----------------------------------------------------
    # 4. CATEGORY-AWARE FUZZY MATCH (NEW)
    # -----------------------------------------------------
    marker_norm = _normalize(marker_name)
    marker_compact = _normalize_compact(marker_name)

    best_row = None
    best_score = 0

    for m, rows in by_marker.items():
        for row in rows:
            row_category = _normalize(row.get("original_report_category"))

            # Only consider compatible categories first
            if not any(row_category == c for c in category_candidates):
                continue

            m_norm = _normalize(m)
            m_compact = _normalize_compact(m)

            score = _fuzzy_score(marker_norm, marker_compact, m_norm, m_compact)

            if score > best_score:
                best_score = score
                best_row = row

    if best_score >= 20:
        if DEBUG_MATCHING:
            print(f"[DEBUG] Category fuzzy match ({best_score}): {marker_name}")
        return best_row

    # -----------------------------------------------------
    # 5. GLOBAL FUZZY (LAST RESORT)
    # -----------------------------------------------------
    for m, rows in by_marker.items():
        for row in rows:
            m_norm = _normalize(m)
            m_compact = _normalize_compact(m)

            score = _fuzzy_score(marker_norm, marker_compact, m_norm, m_compact)

            if score > best_score:
                best_score = score
                best_row = row

    if best_score >= 20:
        if DEBUG_MATCHING:
            print(f"[DEBUG] Global fuzzy match ({best_score}): {marker_name}")
        return best_row

    if DEBUG_MATCHING:
        print(f"[DEBUG] No match: {marker_name}")

    return None


# =========================================================
# FUZZY SCORING (EXTRACTED)
# =========================================================

def _fuzzy_score(a_norm: str, a_compact: str, b_norm: str, b_compact: str) -> int:
    if a_norm == b_norm:
        return 100
    if a_compact and b_compact == a_compact:
        return 95
    if a_norm and (a_norm in b_norm or b_norm in a_norm):
        return 80

    words_a = set(a_norm.split())
    words_b = set(b_norm.split())
    overlap = len(words_a & words_b)
    return overlap * 10