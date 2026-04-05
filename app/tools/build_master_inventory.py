
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_CSV = ROOT / "Final Aggregated Marker list v6.csv"
OUTPUT_JSON = ROOT / "configured_categories_v2.json"


def main():
    with SOURCE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    categories = {}
    for row in rows:
        cat = row["original_report_category"]
        categories.setdefault(cat, {"canonical_systems": set(), "markers": []})
        categories[cat]["canonical_systems"].add(row["canonical_system"])
        categories[cat]["markers"].append({
            "control_id": row["control_id"],
            "original_name": row["original_name"],
            "clinical_label": row["clinical_label"],
        })

    payload = {
        "category_count": len(categories),
        "categories": {
            k: {
                "canonical_systems": sorted(v["canonical_systems"]),
                "marker_count": len(v["markers"]),
                "markers": v["markers"],
            }
            for k, v in sorted(categories.items())
        },
    }

    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Built {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
