
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCAFFOLD_FILE = ROOT / "marker_content_scaffold_v2.json"
OUTPUT_FILE = ROOT / "marker_content_scaffold_v2.csv"


def main():
    scaffold = json.loads(SCAFFOLD_FILE.read_text(encoding="utf-8"))
    fields = [
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
    ]
    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for control_id in sorted(scaffold.keys()):
            row = {k: scaffold[control_id].get(k, "") for k in fields}
            writer.writerow(row)
    print(f"Built {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
