#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _selector_key(item: dict[str, Any]) -> str:
    vh = item.get("view_history") or {}
    district = str(item.get("source_district", "")).strip()
    court = str(item.get("source_court_complex_name", "")).strip()
    case_no = str(vh.get("case_no", "")).strip()
    cino = str(vh.get("cino", "")).strip()
    return "|".join([district, court, case_no, cino])


def _cnr_year(cino: str) -> int | None:
    if len(cino) < 4:
        return None
    tail = cino[-4:]
    if not tail.isdigit():
        return None
    return int(tail)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare per-district random sample selector keys from case_list_items.jsonl")
    parser.add_argument("--case-list-jsonl", required=True)
    parser.add_argument("--output-keys", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--min-year", type=int, default=2025)
    parser.add_argument("--per-district", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    rows = _read_jsonl(Path(args.case_list_jsonl))

    by_district: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        vh = row.get("view_history") or {}
        cino = str(vh.get("cino", ""))
        year = _cnr_year(cino)
        if year is None or year < args.min_year:
            continue
        key = _selector_key(row)
        if key in seen:
            continue
        seen.add(key)
        district = str(row.get("source_district", "")).strip() or "UNKNOWN"
        by_district[district].append(row)

    selected_keys: list[str] = []
    summary_rows: list[dict[str, Any]] = []
    for district in sorted(by_district.keys()):
        pool = by_district[district]
        sample_n = min(args.per_district, len(pool))
        chosen = random.sample(pool, sample_n)
        keys = [_selector_key(item) for item in chosen]
        selected_keys.extend(keys)
        summary_rows.append(
            {
                "district": district,
                "available_eligible_cases": len(pool),
                "selected_sample_cases": sample_n,
            }
        )

    out_path = Path(args.output_keys)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(selected_keys) + ("\n" if selected_keys else ""), encoding="utf-8")

    summary = {
        "source_case_list_jsonl": args.case_list_jsonl,
        "output_keys": str(out_path),
        "min_year": args.min_year,
        "per_district": args.per_district,
        "seed": args.seed,
        "district_count": len(summary_rows),
        "total_selected": len(selected_keys),
        "districts": summary_rows,
    }
    Path(args.summary_out).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
