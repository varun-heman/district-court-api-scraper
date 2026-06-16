"""
Converts the discovery.json output from `district-court-api-scraper discover`
into the district_courts_config JSON format required by batch-scrape configs.

Usage (from repo root):
    python scripts/convert_discovery_to_district_config.py \
        --discovery output/runs/<run_id>/normalized/discovery.json \
        --state-name Haryana \
        --state-code 14 \
        --output configs/haryana_district_courts.json
"""
import argparse
import json
from pathlib import Path


def convert(discovery_path: Path, state_name: str, state_code: str, output_path: Path) -> None:
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))

    districts_out: dict = {}
    for district_row in discovery.get("districts", []):
        dist_item = district_row.get("district", {})
        dist_name: str = dist_item.get("text", "").strip()
        dist_code: str = str(dist_item.get("value", "")).strip()
        if not dist_name or not dist_code:
            continue

        court_complexes: dict = {}
        for complex_entry in district_row.get("complexes", []):
            complex_item = complex_entry.get("complex", {})
            court_name: str = complex_item.get("text", "").strip()
            crtvalue: str = str(complex_item.get("value", "")).strip()
            if not court_name or not crtvalue:
                continue

            # Build establishments from the discovery if present
            establishments: dict = {}
            for est in complex_entry.get("establishments", []):
                est_name = est.get("text", "").strip()
                est_val = str(est.get("value", "")).strip()
                if est_name and est_val:
                    establishments[est_name] = {"estvalue": est_val}

            court_complexes[court_name] = {
                "crtvalue": crtvalue,
                **({"establishments": establishments} if establishments else {}),
            }

        districts_out[dist_name] = {
            "dstvalue": dist_code,
            "court_complexes": court_complexes,
        }

    output = {
        state_name: {
            "stvalue": state_code,
            "districts": districts_out,
        }
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    total_complexes = sum(len(d["court_complexes"]) for d in districts_out.values())
    print(f"Written: {output_path}")
    print(f"  {len(districts_out)} districts, {total_complexes} court complexes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert discovery.json to district_courts_config format")
    parser.add_argument("--discovery", required=True, help="Path to discovery.json")
    parser.add_argument("--state-name", required=True, help="State name (e.g. Haryana)")
    parser.add_argument("--state-code", required=True, help="eCourts state code (e.g. 14)")
    parser.add_argument("--output", required=True, help="Output path for district config JSON")
    args = parser.parse_args()

    convert(
        discovery_path=Path(args.discovery),
        state_name=args.state_name,
        state_code=str(args.state_code),
        output_path=Path(args.output),
    )


if __name__ == "__main__":
    main()
