
import json
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_CSV = ROOT / "Final Aggregated Marker list v6.csv"
OUTPUT_JSON = ROOT / "marker_definition_library_v2.json"
OUTPUT_MASTER_CSV = ROOT / "marker_intelligence_master_v2.csv"
OUTPUT_TRACEABILITY_CSV = ROOT / "marker_traceability_v2.csv"


def main():
    with SOURCE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    library = {
        "metadata": {
            "source_file": SOURCE_CSV.name,
            "marker_count": len(rows),
            "schema_version": "v2",
        },
        "by_control_id": {},
        "by_report_category": {},
    }

    traceability = []

    for row in rows:
        control_id = row["control_id"]
        report_category = row["original_report_category"]
        canonical_system = row["canonical_system"]
        original_name = row["original_name"]

        entry = {
            "control_id": control_id,
            "canonical_system": canonical_system,
            "original_name": original_name,
            "clinical_label": row["clinical_label"],
            "original_report_category": report_category,
            "clinical_meaning": row["clinical_meaning"],
            "why_it_matters": row["why_it_matters"],
            "functional_significance": row["functional_significance"],
            "low_interpretation": row["low_interpretation"],
            "high_interpretation": row["high_interpretation"],
            "pattern_links": row["pattern_links"],
            "recommendation_hint": row["recommendation_hint"],
            "pattern_cluster": row["pattern_cluster"],
        }

        library["by_control_id"][control_id] = entry
        library["by_report_category"].setdefault(report_category, []).append(control_id)

        traceability.append({
            "control_id": control_id,
            "original_report_category": report_category,
            "canonical_system": canonical_system,
            "original_name": original_name,
            "clinical_label": row["clinical_label"],
        })

    OUTPUT_JSON.write_text(json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTPUT_MASTER_CSV.write_text(SOURCE_CSV.read_text(encoding="utf-8-sig"), encoding="utf-8")
    with OUTPUT_TRACEABILITY_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(traceability[0].keys()))
        writer.writeheader()
        writer.writerows(traceability)

    print(f"Built {OUTPUT_JSON}")
    print(f"Built {OUTPUT_MASTER_CSV}")
    print(f"Built {OUTPUT_TRACEABILITY_CSV}")


if __name__ == "__main__":
    main()
