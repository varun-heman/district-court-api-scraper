"""Generate a batch config with one target per district for a given state."""
import json
import sys
from pathlib import Path


def generate(district_courts_path: Path, state_name: str, act_ids: list, section: str, years: list, output_path: Path):
    district_courts = json.loads(district_courts_path.read_text(encoding="utf-8"))
    state_block = district_courts.get(state_name, {})
    districts = list(state_block.get("districts", {}).keys())

    targets = [
        {
            "state": state_name,
            "district": district,
            "court_complexes": "all",
            "case_types": [
                {
                    "act_ids": act_ids,
                    "section": section,
                    "statuses": ["Pending", "Disposed"]
                }
            ]
        }
        for district in districts
    ]

    config = {
        "run_id": f"{state_name.lower()}_ni138_{'_'.join(years)}",
        "workers": 4,
        "target_cnr_years": years,
        "district_courts_config_path": str(district_courts_path.resolve()),
        "targets": targets
    }

    output_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Generated {len(targets)} district targets -> {output_path}")


if __name__ == "__main__":
    generate(
        district_courts_path=Path("configs/punjab_district_courts.json"),
        state_name="Punjab",
        act_ids=["732"],
        section="138",
        years=["2023", "2024", "2025", "2026"],
        output_path=Path("configs/punjab_ni138.json"),
    )
