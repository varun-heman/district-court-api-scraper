"""
Sample 200 CNRs per district from the Haryana NI-138 discovery run, disposed cases only.
Run this after the haryana_ni138_discovery batch-scrape and export-format steps.
Outputs configs/runtime/haryana_sample_cnrs.txt for use with the hearings config.
"""
import json
import random
from collections import defaultdict
from pathlib import Path

CASE_DETAILS_JSONL = Path("output/runs/haryana_ni138_discovery/normalized/case_details.jsonl")
OUTPUT_FILE = Path("configs/runtime/haryana_sample_cnrs.txt")
SAMPLE_PER_DISTRICT = 200
SEED = 42


def main():
    by_district: dict[str, list[str]] = defaultdict(list)

    print(f"Reading {CASE_DETAILS_JSONL} ...")
    with CASE_DETAILS_JSONL.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cnr = (row.get("cino") or row.get("cnr") or "").strip()
            district = (row.get("source_district") or "").strip()
            status = (row.get("source_status") or "").strip().lower()
            if cnr and district and status == "disposed":
                by_district[district].append(cnr)

    rng = random.Random(SEED)
    sampled: list[str] = []

    for district, cnrs in sorted(by_district.items()):
        cnrs = list(dict.fromkeys(cnrs))  # dedupe
        n = min(SAMPLE_PER_DISTRICT, len(cnrs))
        chosen = rng.sample(cnrs, n)
        sampled.extend(chosen)
        print(f"  {district}: {n} sampled (from {len(cnrs)} disposed)")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(sampled) + "\n", encoding="utf-8")
    print(f"\nTotal sampled: {len(sampled)} CNRs → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
