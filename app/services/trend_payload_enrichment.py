from __future__ import annotations

from typing import Any


def _safe_key(value: Any) -> str:
    return str(value or "").strip()


def _humanize_key(key: str) -> str:
    return _safe_key(key).replace("_", " ").replace("-", " ").strip().title()


def _as_points(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if isinstance(item, (int, float)):
            out.append({"label": str(index + 1), "value": float(item)})
        elif isinstance(item, dict):
            if item.get("value") is not None:
                out.append(item)
    return out


def _series_map(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Normalise object-map or array-of-series payloads to {key: points}."""
    out: dict[str, list[dict[str, Any]]] = {}
    if isinstance(value, dict):
        for key, series in value.items():
            if isinstance(series, list):
                points = _as_points(series)
            elif isinstance(series, dict):
                points = _as_points(
                    series.get("points")
                    or series.get("values")
                    or series.get("series")
                    or series.get("trend")
                    or series.get("history")
                    or series.get("data")
                )
            else:
                points = []
            if key and points:
                out[str(key)] = points
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("label") or item.get("name") or item.get("system") or item.get("marker")
            points = _as_points(item.get("points") or item.get("values") or item.get("series") or item.get("trend") or item.get("history") or item.get("data"))
            if key and points:
                out[str(key)] = points
    return out


def _category_from_marker_row(row: dict[str, Any]) -> str:
    return (
        row.get("category")
        or row.get("category_name")
        or row.get("qrma_category")
        or row.get("system_category")
        or row.get("system_group")
        or row.get("group")
        or row.get("clinical_area")
        or row.get("area")
        or row.get("system")
        or row.get("source_category")
        or "Uncategorised"
    )


def _find_marker_summary(trend: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("trend_summary", "marker_changes", "marker_evidence"):
        value = trend.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _build_marker_options(trend: dict[str, Any], markers: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    option_map: dict[str, dict[str, Any]] = {}

    # Start with explicit options, when present.
    for opt in trend.get("marker_options") or []:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or opt.get("name") or opt.get("label") or "").strip()
        if not key:
            continue
        option_map[key] = {
            "key": key,
            "label": opt.get("label") or _humanize_key(key),
            "category": opt.get("category") or opt.get("qrma_category") or opt.get("system") or "Uncategorised",
        }

    # Enrich using trend summary rows.
    for row in _find_marker_summary(trend):
        key = str(row.get("key") or row.get("marker") or row.get("label") or "").strip()
        if not key:
            continue
        existing = option_map.get(key, {})
        option_map[key] = {
            "key": key,
            "label": row.get("label") or existing.get("label") or _humanize_key(key),
            "category": _category_from_marker_row(row) or existing.get("category") or "Uncategorised",
        }

    # Finally include all marker series.
    for key, points in markers.items():
        existing = option_map.get(key, {})
        category = existing.get("category") or "Uncategorised"
        for p in reversed(points or []):
            if isinstance(p, dict):
                category = _category_from_marker_row(p) or category
                if category and category != "Uncategorised":
                    break
        option_map[key] = {
            "key": key,
            "label": existing.get("label") or _humanize_key(key),
            "category": category or "Uncategorised",
        }

    return sorted(option_map.values(), key=lambda x: (str(x.get("category") or ""), str(x.get("label") or "")))


def _build_system_options(trend: dict[str, Any], systems: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    explicit = trend.get("system_options")
    if isinstance(explicit, list) and explicit:
        return [
            {"key": str(x.get("key") or x.get("label")), "label": x.get("label") or _humanize_key(str(x.get("key") or x.get("label")))}
            for x in explicit if isinstance(x, dict) and (x.get("key") or x.get("label"))
        ]
    return [{"key": k, "label": _humanize_key(k)} for k in sorted(systems.keys())]


def enrich_trend_payload(trend_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Add a stable chart + selector hierarchy contract to a trend payload.

    This is intentionally backward-compatible with the older patient-detail trend payload.
    Existing keys are preserved; new keys are additive:
      - trend_chart
      - trend_hierarchy
      - marker_options with category
      - system_options
    """
    trend = dict(trend_payload or {})

    health = _as_points(trend.get("health_index") or trend.get("healthIndex") or trend.get("health_index_trend"))
    weight = _as_points(trend.get("weight_kg") or trend.get("weight") or trend.get("weight_trend"))
    clinical_areas = _series_map(trend.get("system_group_averages") or trend.get("clinical_areas") or trend.get("clinical_area_trends"))
    systems = _series_map(trend.get("systems") or trend.get("system_trends"))
    markers = _series_map(trend.get("markers") or trend.get("marker_trends"))

    system_groups = trend.get("system_groups") if isinstance(trend.get("system_groups"), dict) else {}
    system_options = _build_system_options(trend, systems)
    marker_options = _build_marker_options(trend, markers)

    categories: dict[str, list[dict[str, Any]]] = {}
    marker_map: dict[str, dict[str, Any]] = {}
    for opt in marker_options:
        key = str(opt.get("key") or "")
        if not key:
            continue
        category = str(opt.get("category") or "Uncategorised")
        marker_obj = {
            "key": key,
            "label": opt.get("label") or _humanize_key(key),
            "category": category,
            "points": markers.get(key, []),
        }
        marker_map[key] = marker_obj
        categories.setdefault(category, []).append({"key": key, "label": marker_obj["label"]})

    trend["health_index"] = health
    trend["weight_kg"] = weight
    trend["systems"] = systems
    trend["markers"] = markers
    trend["system_group_averages"] = clinical_areas
    trend["system_groups"] = system_groups
    trend["system_options"] = system_options
    trend["marker_options"] = marker_options

    trend["trend_chart"] = {
        "health_index": health,
        "weight_kg": weight,
        "clinical_areas": clinical_areas,
        "systems": systems,
        "markers": markers,
    }
    trend["trend_hierarchy"] = {
        "clinical_areas": {k: {"key": k, "label": k, "points": v} for k, v in clinical_areas.items()},
        "system_groups": system_groups,
        "systems": {k: {"key": k, "label": _humanize_key(k), "points": v} for k, v in systems.items()},
        "categories": categories,
        "markers": marker_map,
    }
    return trend
