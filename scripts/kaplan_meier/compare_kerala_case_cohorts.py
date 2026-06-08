from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from disposal_filters import tag_disposal  # noqa: E402
from km_by_district import _build_survival_rows, _parse_date  # noqa: E402


CASE_COLUMNS = [
    "dataset",
    "district",
    "cino",
    "case_number",
    "case_prefix",
    "case_number_year",
    "cnr_year",
    "filing_date",
    "decision_date",
    "decision_source",
    "event",
    "duration_days",
    "current_status",
    "nature_of_disposal",
    "disposal_primary",
    "disposal_secondary",
]

SUMMARY_COLUMNS = [
    "dataset",
    "district",
    "cases",
    "events",
    "censored",
    "event_rate",
    "pending_rate",
    "median_duration_days_all",
    "median_duration_days_events",
    "median_duration_days_pending",
]

DISPOSAL_COLUMNS = [
    "dataset",
    "district",
    "disposal_secondary",
    "cases",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Kerala case-detail cohorts across two datasets.")
    parser.add_argument("--baseline-jsonl", required=True)
    parser.add_argument("--baseline-label", default="baseline_2025")
    parser.add_argument("--baseline-case-years", default="2025")
    parser.add_argument("--baseline-cnr-years", default="")
    parser.add_argument("--treatment-jsonl", required=True)
    parser.add_argument("--treatment-label", default="new_2023_2024")
    parser.add_argument("--treatment-case-years", default="")
    parser.add_argument("--treatment-cnr-years", default="2023,2024")
    parser.add_argument("--prefixes", default="CC,ST")
    parser.add_argument("--censor-date", default="")
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def _parse_csv_set(raw: str) -> set[str]:
    return {token.strip() for token in str(raw).split(",") if token.strip()}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _case_prefix(case_number: str) -> str:
    match = re.match(r"^\s*([A-Za-z]+)", case_number or "")
    return match.group(1).upper() if match else ""


def _case_number_year(case_number: str) -> str:
    match = re.search(r"/(\d{4})$", str(case_number).strip())
    return match.group(1) if match else ""


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    case_years: set[str],
    cnr_years: set[str],
    prefixes: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        district = str(row.get("source_district", "")).strip()
        cino = str(row.get("cino", "")).strip()
        case_number = str(row.get("case_number", "")).strip().upper()
        if not district or not cino or not case_number:
            continue
        if prefixes and _case_prefix(case_number) not in prefixes:
            continue
        if case_years and _case_number_year(case_number) not in case_years:
            continue
        if cnr_years and cino[-4:] not in cnr_years:
            continue
        key = f"{district}|{cino}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _resolve_censor_date(raw: str) -> date:
    if raw:
        return date.fromisoformat(raw)
    return date.today()


def _derive_current_status(row: dict[str, Any], *, event: int) -> str:
    summary = row.get("summary") or {}
    case_status = summary.get("case_status") or {}
    raw_status = str(case_status.get("Case Status", case_status.get("Case Stage", ""))).strip()
    if raw_status:
        return raw_status
    return "Disposed" if event else "Pending"


def _build_case_export_rows(
    rows: list[dict[str, Any]],
    *,
    dataset_label: str,
    censor_date: date,
    prefixes: set[str],
) -> list[dict[str, Any]]:
    survival_rows, _stats = _build_survival_rows(
        rows,
        censor_date,
        min_filing_year=None,
        min_filing_date=None,
        include_case_prefixes=prefixes,
    )
    by_key = {(str(row.get("source_district", "")).strip(), str(row.get("cino", "")).strip()): row for row in rows}
    export_rows: list[dict[str, Any]] = []
    for surv in survival_rows:
        source_row = by_key.get((surv.district, surv.cino))
        if source_row is None:
            continue
        disposal = tag_disposal(surv.nature_of_disposal)
        export_rows.append(
            {
                "dataset": dataset_label,
                "district": surv.district,
                "cino": surv.cino,
                "case_number": surv.case_number,
                "case_prefix": _case_prefix(surv.case_number),
                "case_number_year": _case_number_year(surv.case_number),
                "cnr_year": surv.cino[-4:] if len(surv.cino) >= 4 else "",
                "filing_date": surv.filing_date.isoformat(),
                "decision_date": surv.decision_date,
                "decision_source": surv.decision_source,
                "event": surv.event,
                "duration_days": surv.duration_days,
                "current_status": _derive_current_status(source_row, event=surv.event),
                "nature_of_disposal": surv.nature_of_disposal,
                "disposal_primary": disposal.primary,
                "disposal_secondary": disposal.secondary_group,
            }
        )
    export_rows.sort(key=lambda row: (row["dataset"], row["district"], row["cino"]))
    return export_rows


def _median(values: list[int]) -> str:
    if not values:
        return ""
    return str(int(round(statistics.median(values))))


def _build_summary_rows(case_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in case_rows:
        grouped[(str(row["dataset"]), str(row["district"]))].append(row)
    summary_rows: list[dict[str, Any]] = []
    for (dataset, district), rows in sorted(grouped.items()):
        durations_all = [int(row["duration_days"]) for row in rows]
        durations_events = [int(row["duration_days"]) for row in rows if int(row["event"]) == 1]
        durations_pending = [int(row["duration_days"]) for row in rows if int(row["event"]) == 0]
        cases = len(rows)
        events = len(durations_events)
        censored = len(durations_pending)
        summary_rows.append(
            {
                "dataset": dataset,
                "district": district,
                "cases": cases,
                "events": events,
                "censored": censored,
                "event_rate": f"{(events / cases):.4f}" if cases else "",
                "pending_rate": f"{(censored / cases):.4f}" if cases else "",
                "median_duration_days_all": _median(durations_all),
                "median_duration_days_events": _median(durations_events),
                "median_duration_days_pending": _median(durations_pending),
            }
        )
    return summary_rows


def _build_disposal_rows(case_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    for row in case_rows:
        key = (
            str(row["dataset"]),
            str(row["district"]),
            str(row["disposal_secondary"] or "[blank]"),
        )
        counter[key] += 1
    out: list[dict[str, Any]] = []
    for (dataset, district, disposal_secondary), count in sorted(counter.items()):
        out.append(
            {
                "dataset": dataset,
                "district": district,
                "disposal_secondary": disposal_secondary,
                "cases": count,
            }
        )
    return out


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    prefixes = {token.strip().upper() for token in str(args.prefixes).split(",") if token.strip()}
    censor_date = _resolve_censor_date(args.censor_date)

    baseline_rows = _filter_rows(
        _read_jsonl(Path(args.baseline_jsonl).resolve()),
        case_years=_parse_csv_set(args.baseline_case_years),
        cnr_years=_parse_csv_set(args.baseline_cnr_years),
        prefixes=prefixes,
    )
    treatment_rows = _filter_rows(
        _read_jsonl(Path(args.treatment_jsonl).resolve()),
        case_years=_parse_csv_set(args.treatment_case_years),
        cnr_years=_parse_csv_set(args.treatment_cnr_years),
        prefixes=prefixes,
    )

    case_rows = _build_case_export_rows(
        baseline_rows,
        dataset_label=str(args.baseline_label),
        censor_date=censor_date,
        prefixes=prefixes,
    ) + _build_case_export_rows(
        treatment_rows,
        dataset_label=str(args.treatment_label),
        censor_date=censor_date,
        prefixes=prefixes,
    )
    case_rows.sort(key=lambda row: (row["dataset"], row["district"], row["cino"]))

    summary_rows = _build_summary_rows(case_rows)
    disposal_rows = _build_disposal_rows(case_rows)

    case_csv = out_dir / "cohort_case_rows.csv"
    summary_csv = out_dir / "cohort_summary_by_district.csv"
    disposal_csv = out_dir / "cohort_disposal_summary.csv"
    meta_json = out_dir / "cohort_compare_meta.json"

    _write_csv(case_csv, CASE_COLUMNS, case_rows)
    _write_csv(summary_csv, SUMMARY_COLUMNS, summary_rows)
    _write_csv(disposal_csv, DISPOSAL_COLUMNS, disposal_rows)

    meta = {
        "baseline_jsonl": str(Path(args.baseline_jsonl).resolve()),
        "baseline_label": args.baseline_label,
        "baseline_rows": len(baseline_rows),
        "treatment_jsonl": str(Path(args.treatment_jsonl).resolve()),
        "treatment_label": args.treatment_label,
        "treatment_rows": len(treatment_rows),
        "case_rows_csv": str(case_csv),
        "summary_csv": str(summary_csv),
        "disposal_csv": str(disposal_csv),
        "censor_date": censor_date.isoformat(),
        "prefixes": sorted(prefixes),
    }
    meta_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
