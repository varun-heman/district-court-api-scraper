"""
Sample N CNRs per district from cases.csv, disposed cases only.
Outputs a text file of CNR numbers for use with allowed_case_keys_path.
"""
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path

CASES_CSV = Path("output/runs/punjab_ni138_2023_2024_2025_2026/exports/cases.csv")
OUTPUT_FILE = Path("configs/runtime/punjab_sample_cnrs.txt")
SAMPLE_PER_DISTRICT = 100
SEED = 42


def main():
    by_district_status: dict[tuple[str, str], list[str]] = defaultdict(list)

    with CASES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cnr = row.get("cnr", "").strip()
            district = row.get("District", "").strip()
            status = row.get("Status", "").strip() or "Unknown"
            if cnr and district and status == "Disposed":
                by_district_status[(district, status)].append(cnr)

    # Group by district (disposed only, so status key is always "Disposed")
    by_district: dict[str, list[str]] = defaultdict(list)
    for (district, status), cnrs in by_district_status.items():
        by_district[district].extend(cnrs)

    rng = random.Random(SEED)
    sampled: list[str] = []

    for district, cnrs in sorted(by_district.items()):
        n = min(SAMPLE_PER_DISTRICT, len(cnrs))
        chosen = rng.sample(cnrs, n)
        sampled.extend(chosen)
        print(f"{district}: {n} sampled (from {len(cnrs)} total)")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(sampled) + "\n", encoding="utf-8")
    print(f"\nTotal sampled: {len(sampled)} CNRs → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
