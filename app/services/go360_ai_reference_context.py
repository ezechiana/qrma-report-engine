"""B.3.13.4: bounded Desk Reference intelligence context, never scoring."""
import csv
from functools import lru_cache
from pathlib import Path

MASTER = Path(__file__).resolve().parents[1] / "data" / "marker_intelligence_master.csv"

@lru_cache(maxsize=1)
def _registry():
    with MASTER.open(encoding="utf-8-sig", newline="") as fh:
        return {row["control_id"].strip().upper(): row for row in csv.DictReader(fh) if row.get("control_id")}

def attach_reference_context(markers):
    """Copy markers; enrich only when legacy ID AND QRMA source name agree."""
    try:
        registry = _registry()
    except (OSError, ValueError, KeyError):
        return markers
    output = []
    for marker in markers:
        item = dict(marker)
        mid = str(item.get("legacy_marker_id") or "").strip().upper()
        source = str(item.get("qrma_software_name") or "").strip()
        row = registry.get(mid)
        if row and source and row.get("original_name", "").strip().casefold() == source.casefold():
            item["reference_intelligence"] = {
                "clinical_meaning": row.get("clinical_meaning", "")[:500],
                "functional_significance": row.get("functional_significance", "")[:500],
                "pattern_links": row.get("pattern_links", "")[:500],
                "recommendation_hint": row.get("recommendation_hint", "")[:400],
                "source": "go360_marker_intelligence_master",
                "source_control_id": mid,
            }
        output.append(item)
    return output
