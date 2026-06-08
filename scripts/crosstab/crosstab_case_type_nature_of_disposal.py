from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path


BLANK_LABEL = "[blank]"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a case_type x nature_of_disposal crosstab from canonical case_details."
    )
    parser.add_argument(
        "--run-root",
        default="output/runs/kerala_ni138_phase6_combined_20260309",
        help="Run root containing normalized/case_details.jsonl",
    )
    parser.add_argument(
        "--out-csv",
        default="analysis/crosstab_case_type_nature_of_disposal_2025plus_cc_st.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--out-cases-csv",
        default="analysis/cases_for_crosstab_case_type_nature_of_disposal_2025plus_cc_st.csv",
        help="Output row-level case list CSV path",
    )
    parser.add_argument(
        "--min-filing-date",
        default="2025-01-01",
        help="Minimum filing date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--include-case-prefixes",
        default="CC,ST",
        help="Comma-separated case prefixes to include",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text or text == "-":
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


def _case_prefix(case_number: str) -> str:
    match = re.match(r"^\s*([A-Za-z]+)", case_number or "")
    return match.group(1).upper() if match else ""


def _norm_cell(value: str) -> str:
    text = (value or "").strip()
    return text if text else BLANK_LABEL


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    out_csv = Path(args.out_csv).resolve()
    out_cases_csv = Path(args.out_cases_csv).resolve()
    case_details_path = run_root / "normalized" / "case_details.jsonl"
    min_filing_date = datetime.strptime(args.min_filing_date, "%Y-%m-%d").date()
    include_case_prefixes = {
        token.strip().upper()
        for token in str(args.include_case_prefixes).split(",")
        if token.strip()
    }

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    row_totals: Counter[str] = Counter()
    col_totals: Counter[str] = Counter()
    case_rows: list[dict[str, str | int]] = []
    seen: set[tuple[str, str]] = set()

    stats = {
        "total_case_details": 0,
        "deduped_cases": 0,
        "filtered_missing_filing_date": 0,
        "filtered_before_min_filing_date": 0,
        "filtered_by_case_prefix": 0,
        "kept_cases": 0,
    }

    with case_details_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            stats["total_case_details"] += 1
            entry = json.loads(line)
            district = str(entry.get("source_district", "")).strip()
            cino = str(entry.get("cino", "")).strip()
            if not district or not cino:
                continue
            dedupe_key = (district, cino)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            stats["deduped_cases"] += 1

            case_number = str(entry.get("case_number", "")).strip()
            if include_case_prefixes and _case_prefix(case_number) not in include_case_prefixes:
                stats["filtered_by_case_prefix"] += 1
                continue

            summary = entry.get("summary") or {}
            case_details = summary.get("case_details") or {}
            case_status = summary.get("case_status") or {}
            filing_dt = _parse_date(str(case_details.get("Filing Date", "")))
            if filing_dt is None:
                stats["filtered_missing_filing_date"] += 1
                continue
            if filing_dt < min_filing_date:
                stats["filtered_before_min_filing_date"] += 1
                continue

            case_type = _norm_cell(str(case_details.get("Case Type", "")))
            disposal = _norm_cell(str(case_status.get("Nature of Disposal", "")))
            counts[case_type][disposal] += 1
            row_totals[case_type] += 1
            col_totals[disposal] += 1
            case_rows.append(
                {
                    "district": district,
                    "cino": cino,
                    "case_number": case_number,
                    "filing_date": filing_dt.isoformat(),
                    "decision_date": str(case_status.get("Decision Date", "")).strip(),
                    "case_status": str(case_status.get("Case Status", "")).strip(),
                    "case_type": case_type,
                    "nature_of_disposal": disposal,
                    "raw_file": str(entry.get("raw_file", "")).strip(),
                }
            )
            stats["kept_cases"] += 1

    case_types = sorted(row_totals)
    disposal_values = sorted(col_totals)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "case_type",
                *disposal_values,
                "Total",
            ]
        )
        for case_type in case_types:
            writer.writerow(
                [
                    case_type,
                    *[counts[case_type].get(disposal, 0) for disposal in disposal_values],
                    row_totals[case_type],
                ]
            )
        writer.writerow(
            [
                "Total",
                *[col_totals.get(disposal, 0) for disposal in disposal_values],
                sum(row_totals.values()),
            ]
        )

    out_cases_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_cases_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "district",
                "cino",
                "case_number",
                "filing_date",
                "decision_date",
                "case_status",
                "case_type",
                "nature_of_disposal",
                "raw_file",
            ],
        )
        writer.writeheader()
        for row in case_rows:
            writer.writerow(row)

    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "case_details_path": str(case_details_path),
                "out_csv": str(out_csv),
                "out_cases_csv": str(out_cases_csv),
                "min_filing_date": min_filing_date.isoformat(),
                "include_case_prefixes": sorted(include_case_prefixes),
                "row_count": len(case_types),
                "column_count": len(disposal_values),
                "stats": stats,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
