from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any


@dataclass(slots=True)
class SurvivalRow:
    dataset: str
    district: str
    case_id: str
    filing_date: date
    end_date: date
    event: int
    duration_days: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute KM data for ONCourts + district-wise scraped cohorts with optional Kerala combined."
    )
    parser.add_argument(
        "--oncourts-csv",
        default="data/kollam-lifecycle-kaplanmeier.csv",
        help="ONCourts CSV path",
    )
    parser.add_argument(
        "--scraped-km-csv",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_case_rows.csv",
        help="Scraped KM rows CSV path",
    )
    parser.add_argument(
        "--kollam-name",
        default="Kollam",
        help="District label for Kollam in scraped data",
    )
    parser.add_argument(
        "--min-filing-year",
        type=int,
        default=2025,
        help="Keep rows with filing year >= this value",
    )
    parser.add_argument(
        "--min-filing-date",
        default="",
        help="Optional minimum filing date in YYYY-MM-DD. Applied in addition to min-filing-year.",
    )
    parser.add_argument(
        "--censor-date",
        default="2026-03-10",
        help="Censor date YYYY-MM-DD",
    )
    parser.add_argument(
        "--out-json",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_toggle_data.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--out-summary-csv",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_toggle_summary.csv",
        help="Output summary CSV path",
    )
    parser.add_argument(
        "--out-rows-csv",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_toggle_rows.csv",
        help="Output modeled rows CSV path",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _format_date_label(value: date | None) -> str:
    if value is None:
        return ""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _km_curve(rows: list[SurvivalRow]) -> tuple[list[int], list[float], int | None]:
    events_at: dict[int, int] = defaultdict(int)
    censor_at: dict[int, int] = defaultdict(int)
    for row in rows:
        if row.event:
            events_at[row.duration_days] += 1
        else:
            censor_at[row.duration_days] += 1

    n_at_risk = len(rows)
    survival = 1.0
    xs: list[int] = [0]
    ys: list[float] = [1.0]
    median_days: int | None = None
    for t in sorted(set(events_at) | set(censor_at)):
        d = events_at.get(t, 0)
        c = censor_at.get(t, 0)
        if d > 0 and n_at_risk > 0:
            survival *= 1.0 - (d / n_at_risk)
            xs.append(t)
            ys.append(survival)
            if median_days is None and survival <= 0.5:
                median_days = t
        n_at_risk -= d + c
    return xs, ys, median_days


def _series_summary(rows: list[SurvivalRow]) -> dict[str, Any]:
    events = sum(r.event for r in rows)
    censored = len(rows) - events
    _, _, km_median_days = _km_curve(rows)

    completed_durations = sorted(r.duration_days for r in rows if r.event == 1)
    completed_event_median_days = median(completed_durations) if completed_durations else None

    total_person_time = sum(max(r.duration_days, 0) for r in rows)
    extrapolated_median_days = None
    if events > 0 and total_person_time > 0:
        lambda_hat = events / total_person_time
        if lambda_hat > 0:
            extrapolated_median_days = math.log(2.0) / lambda_hat

    return {
        "modeled_cases": len(rows),
        "events": events,
        "censored": censored,
        "km_median_days": km_median_days if km_median_days is not None else "",
        "km_median_months": round((km_median_days / 30.4375), 2) if km_median_days is not None else "NR",
        "completed_event_median_days": round(completed_event_median_days, 1)
        if completed_event_median_days is not None
        else "",
        "completed_event_median_months": round((completed_event_median_days / 30.4375), 2)
        if completed_event_median_days is not None
        else "NR",
        "extrapolated_median_days": round(extrapolated_median_days, 1)
        if extrapolated_median_days is not None
        else "",
        "extrapolated_median_months": round((extrapolated_median_days / 30.4375), 2)
        if extrapolated_median_days is not None
        else "NR",
        "completed_durations": completed_durations,
    }


def _load_oncourts(
    path: Path,
    *,
    min_filing_year: int,
    min_filing_date: date | None,
    censor_date: date,
) -> tuple[list[SurvivalRow], dict[str, int]]:
    rows: list[SurvivalRow] = []
    stats = {
        "rows_total": 0,
        "rows_kept": 0,
        "rows_bad_filing_date": 0,
        "rows_filtered_by_year": 0,
        "rows_filtered_by_date": 0,
        "rows_disposed_missing_relevant_date": 0,
        "rows_bad_relevant_date": 0,
    }
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for item in reader:
            stats["rows_total"] += 1
            filing_dt = _parse_date(str(item.get("filing_date", "")))
            if filing_dt is None:
                stats["rows_bad_filing_date"] += 1
                continue
            if filing_dt.year < min_filing_year:
                stats["rows_filtered_by_year"] += 1
                continue
            if min_filing_date and filing_dt < min_filing_date:
                stats["rows_filtered_by_date"] += 1
                continue
            if filing_dt > censor_date:
                continue
            status = str(item.get("case_status", "")).strip().lower()
            relevant_dt = _parse_date(str(item.get("relevant_date", "")))
            event = 0
            end_dt = censor_date
            if status == "disposed":
                if relevant_dt is None:
                    stats["rows_disposed_missing_relevant_date"] += 1
                elif relevant_dt < filing_dt:
                    stats["rows_bad_relevant_date"] += 1
                else:
                    event = 1
                    end_dt = min(relevant_dt, censor_date)
            rows.append(
                SurvivalRow(
                    dataset="ONCourts",
                    district="Kollam",
                    case_id=str(item.get("filingnumber", "")).strip(),
                    filing_date=filing_dt,
                    end_date=end_dt,
                    event=event,
                    duration_days=max((end_dt - filing_dt).days, 0),
                )
            )
            stats["rows_kept"] += 1
    return rows, stats


def _load_scraped(
    path: Path,
    *,
    min_filing_year: int,
    min_filing_date: date | None,
    censor_date: date,
) -> tuple[list[SurvivalRow], dict[str, int]]:
    rows: list[SurvivalRow] = []
    stats = {
        "rows_total": 0,
        "rows_kept": 0,
        "rows_filtered_by_year": 0,
        "rows_filtered_by_date": 0,
        "filtered_by_excluded_disposal": 0,
    }
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for item in reader:
            stats["rows_total"] += 1
            filing_dt = _parse_date(str(item.get("filing_date", "")))
            end_dt = _parse_date(str(item.get("end_date", "")))
            if filing_dt is None or end_dt is None:
                continue
            if filing_dt.year < min_filing_year:
                stats["rows_filtered_by_year"] += 1
                continue
            if min_filing_date and filing_dt < min_filing_date:
                stats["rows_filtered_by_date"] += 1
                continue
            if filing_dt > censor_date:
                continue
            if end_dt > censor_date:
                end_dt = censor_date
            district = str(item.get("district", "")).strip()
            rows.append(
                SurvivalRow(
                    dataset="Scraped",
                    district=district,
                    case_id=str(item.get("cino", "")).strip(),
                    filing_date=filing_dt,
                    end_date=end_dt,
                    event=int(str(item.get("event", "0")).strip() or "0"),
                    duration_days=max((end_dt - filing_dt).days, 0),
                )
            )
            stats["rows_kept"] += 1
    sidecar = path.with_name(path.name + ".stats.json")
    if sidecar.exists():
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        model_stats = payload.get("stats") or {}
        stats["filtered_by_excluded_disposal"] = int(model_stats.get("filtered_by_excluded_disposal", 0) or 0)
    return rows, stats


def _write_rows(path: Path, rows: list[SurvivalRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["dataset", "district", "case_id", "filing_date", "end_date", "event", "duration_days"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "dataset": row.dataset,
                    "district": row.district,
                    "case_id": row.case_id,
                    "filing_date": row.filing_date.isoformat(),
                    "end_date": row.end_date.isoformat(),
                    "event": row.event,
                    "duration_days": row.duration_days,
                }
            )


def main() -> int:
    args = _parse_args()
    censor_date = datetime.strptime(args.censor_date, "%Y-%m-%d").date()
    min_filing_date = _parse_date(args.min_filing_date) if args.min_filing_date else None
    oncourts_csv = Path(args.oncourts_csv).resolve()
    scraped_csv = Path(args.scraped_km_csv).resolve()
    out_json = Path(args.out_json).resolve()
    out_summary_csv = Path(args.out_summary_csv).resolve()
    out_rows_csv = Path(args.out_rows_csv).resolve()

    on_rows, on_stats = _load_oncourts(
        oncourts_csv,
        min_filing_year=args.min_filing_year,
        min_filing_date=min_filing_date,
        censor_date=censor_date,
    )
    scraped_rows, scraped_stats = _load_scraped(
        scraped_csv,
        min_filing_year=args.min_filing_year,
        min_filing_date=min_filing_date,
        censor_date=censor_date,
    )

    kollam_norm = args.kollam_name.strip().lower()
    rest_kollam_rows = [r for r in scraped_rows if r.district.strip().lower() == kollam_norm]
    other_by_district: dict[str, list[SurvivalRow]] = defaultdict(list)
    for row in scraped_rows:
        if row.district.strip().lower() == kollam_norm:
            continue
        other_by_district[row.district].append(row)

    # Optional combined series for toggle.
    kerala_combined_rows = on_rows + scraped_rows

    # Ordered series list for UI.
    series_rows: list[tuple[str, list[SurvivalRow], bool]] = [
        ("ONCourts", on_rows, True),
        ("Rest of Kollam", rest_kollam_rows, True),
    ]
    for district in sorted(other_by_district):
        series_rows.append((district, other_by_district[district], True))
    series_rows.append(("Kerala (Combined)", kerala_combined_rows, False))

    # Write row-level modeled data (for any downstream custom plotting).
    labeled_rows: list[SurvivalRow] = []
    for label, rows, _ in series_rows:
        for row in rows:
            labeled_rows.append(
                SurvivalRow(
                    dataset=label,
                    district=row.district,
                    case_id=row.case_id,
                    filing_date=row.filing_date,
                    end_date=row.end_date,
                    event=row.event,
                    duration_days=row.duration_days,
                )
            )
    _write_rows(out_rows_csv, labeled_rows)

    # Build series payload and summary rows.
    series_payload: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for label, rows, default_visible in series_rows:
        x, y, _ = _km_curve(rows)
        metrics = _series_summary(rows)
        series_payload.append(
            {
                "dataset": label,
                "default_visible": default_visible,
                "x_days": x,
                "y_survival": y,
                "completed_durations": metrics.pop("completed_durations"),
                **metrics,
            }
        )
        summary_rows.append(
            {
                "dataset": label,
                "default_visible": int(default_visible),
                **{k: v for k, v in metrics.items()},
            }
        )

    out_summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_summary_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "dataset",
                "default_visible",
                "modeled_cases",
                "events",
                "censored",
                "km_median_days",
                "km_median_months",
                "completed_event_median_days",
                "completed_event_median_months",
                "extrapolated_median_days",
                "extrapolated_median_months",
            ],
        )
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "censor_date": censor_date.isoformat(),
            "min_filing_year": args.min_filing_year,
            "min_filing_date": min_filing_date.isoformat() if min_filing_date else "",
            "min_filing_label": _format_date_label(min_filing_date),
            "kollam_name": args.kollam_name,
            "excluded_scraped_disposal_groups": ["transferred / made over"],
        },
        "source_stats": {
            "oncourts": on_stats,
            "scraped": scraped_stats,
            "rest_kollam_count": len(rest_kollam_rows),
            "other_districts_count": {k: len(v) for k, v in sorted(other_by_district.items())},
            "kerala_combined_count": len(kerala_combined_rows),
        },
        "series": series_payload,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    print(
        json.dumps(
            {
                "out_json": str(out_json),
                "out_summary_csv": str(out_summary_csv),
                "out_rows_csv": str(out_rows_csv),
                "series_count": len(series_payload),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
