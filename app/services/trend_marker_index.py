def _normalise_label(key: str) -> str:
    if not key:
        return ""
    return key.replace("_", " ").strip().title()


def build_marker_index(trend_payload: dict) -> dict:
    """
    SAFE additive layer.

    Handles:
    - markers as dicts
    - markers as strings
    - missing markers
    - chart-only markers
    """

    index = {}

    if not isinstance(trend_payload, dict):
        return index

    # --- Chart data (time-series)
    chart_series = (
        trend_payload.get("chart_data", {})
        .get("series", {})
        or {}
    )

    # --- Marker evidence
    raw_markers = trend_payload.get("markers", []) or []

    # --- NORMALISE markers into dict form
    normalised_markers = []

    for m in raw_markers:

        # CASE 1: already dict ?
        if isinstance(m, dict):
            normalised_markers.append(m)
            continue

        # CASE 2: string ? ? convert
        if isinstance(m, str):
            normalised_markers.append({
                "key": m,
                "label": _normalise_label(m),
                "category": "Uncategorised",
                "latest": None,
                "change": None,
                "status": None,
            })
            continue

        # CASE 3: unknown type ? ignore
        continue

    # --- Build index from markers
    for m in normalised_markers:
        key = (
            m.get("key")
            or m.get("marker")
            or m.get("name")
        )

        if not key:
            continue

        index[key] = {
            "key": key,
            "label": m.get("label") or _normalise_label(key),
            "category": m.get("category") or "Uncategorised",
            "points": chart_series.get(key, []),
            "latest": m.get("latest"),
            "change": m.get("change"),
            "status": m.get("status"),
        }

    # --- Backfill from chart series
    for key, series in chart_series.items():
        if key not in index:
            index[key] = {
                "key": key,
                "label": _normalise_label(key),
                "category": "Uncategorised",
                "points": series or [],
                "latest": None,
                "change": None,
                "status": None,
            }

    return index



