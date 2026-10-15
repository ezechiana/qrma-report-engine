
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_CSV = ROOT / "Final Aggregated Marker list v6.csv"
OUTPUT_JSON = ROOT / "marker_content_scaffold_v2.json"


def main():
    with SOURCE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    scaffold = {}
    for row in rows:
        scaffold[row["control_id"]] = {
            "control_id": row["control_id"],
            "original_name": row["original_name"],
            "clinical_label": row["clinical_label"],
            "canonical_system": row["canonical_system"],
            "original_report_category": row["original_report_category"],
            "clinical_meaning": row["clinical_meaning"],
            "why_it_matters": row["why_it_matters"],
            "functional_significance": row["functional_significance"],
            "low_interpretation": row["low_interpretation"],
            "high_interpretation": row["high_interpretation"],
            "pattern_links": row["pattern_links"],
            "recommendation_hint": row["recommendation_hint"],
            "pattern_cluster": row["pattern_cluster"],
        }

    OUTPUT_JSON.write_text(json.dumps(scaffold, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Built {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
