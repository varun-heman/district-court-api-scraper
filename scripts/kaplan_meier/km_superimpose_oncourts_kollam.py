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
from typing import Iterable


@dataclass(slots=True)
class SurvivalRow:
    dataset: str
    filing_date: date
    end_date: date
    event: int
    duration_days: int
    case_id: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Superimpose ONCourts, Rest of Kollam, Other Districts, and Kerala Combined KM curves."
    )
    parser.add_argument(
        "--oncourts-csv",
        default="data/kollam-lifecycle-kaplanmeier.csv",
        help="Path to ONCourts Kollam lifecycle CSV",
    )
    parser.add_argument(
        "--scraped-km-csv",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_case_rows.csv",
        help="Path to scraped KM case rows CSV",
    )
    parser.add_argument(
        "--district",
        default="Kollam",
        help="District name to treat as 'Kollam' bucket from scraped KM case rows CSV",
    )
    parser.add_argument(
        "--min-filing-year",
        type=int,
        default=2025,
        help="Minimum filing year filter",
    )
    parser.add_argument(
        "--min-filing-date",
        default="",
        help="Optional minimum filing date in YYYY-MM-DD. Applied in addition to min-filing-year.",
    )
    parser.add_argument(
        "--max-filing-year",
        type=int,
        default=0,
        help="Optional maximum filing year filter. Disabled when 0.",
    )
    parser.add_argument(
        "--max-filing-date",
        default="",
        help="Optional maximum filing date in YYYY-MM-DD. Applied in addition to max-filing-year.",
    )
    parser.add_argument(
        "--censor-date",
        default="2026-03-09",
        help="Censor date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--out-html",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_oncourts_restkollam_other_kerala.html",
        help="Output HTML path",
    )
    parser.add_argument(
        "--out-summary-csv",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_oncourts_restkollam_other_kerala_summary.csv",
        help="Output summary CSV path",
    )
    parser.add_argument(
        "--out-rows-csv",
        default="output/runs/kerala_ni138_phase6_combined_20260309/analysis/km_oncourts_restkollam_other_kerala_rows.csv",
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


def _km_curve(rows: Iterable[SurvivalRow]) -> tuple[list[int], list[float], int | None]:
    events_at: dict[int, int] = defaultdict(int)
    censor_at: dict[int, int] = defaultdict(int)
    rows_list = list(rows)
    for row in rows_list:
        if row.event:
            events_at[row.duration_days] += 1
        else:
            censor_at[row.duration_days] += 1

    n_at_risk = len(rows_list)
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


def _load_oncourts_rows(
    path: Path,
    *,
    min_filing_year: int,
    min_filing_date: date | None,
    max_filing_year: int | None,
    max_filing_date: date | None,
    censor_date: date,
) -> tuple[list[SurvivalRow], dict[str, int]]:
    rows: list[SurvivalRow] = []
    stats = {
        "rows_total": 0,
        "rows_kept": 0,
        "rows_bad_filing_date": 0,
        "rows_filtered_by_year": 0,
        "rows_filtered_by_date": 0,
        "rows_filtered_by_max_year": 0,
        "rows_filtered_by_max_date": 0,
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
            if max_filing_year and filing_dt.year > max_filing_year:
                stats["rows_filtered_by_max_year"] += 1
                continue
            if max_filing_date and filing_dt > max_filing_date:
                stats["rows_filtered_by_max_date"] += 1
                continue
            if filing_dt > censor_date:
                continue
            status = str(item.get("case_status", "")).strip().lower()
            relevant_dt = _parse_date(str(item.get("relevant_date", "")))
            event = 0
            end_dt = censor_date
            if status == "disposed":
                if relevant_dt is None:
                    # Disposed without a usable disposal date cannot be timed for KM.
                    # Keep as censored at censor date instead of guessing event time.
                    stats["rows_disposed_missing_relevant_date"] += 1
                elif relevant_dt < filing_dt:
                    stats["rows_bad_relevant_date"] += 1
                else:
                    event = 1
                    end_dt = min(relevant_dt, censor_date)
            duration_days = max((end_dt - filing_dt).days, 0)
            rows.append(
                SurvivalRow(
                    dataset="ONCourts",
                    filing_date=filing_dt,
                    end_date=end_dt,
                    event=event,
                    duration_days=duration_days,
                    case_id=str(item.get("filingnumber", "")).strip(),
                )
            )
            stats["rows_kept"] += 1
    return rows, stats


def _load_scraped_rows(
    path: Path,
    *,
    min_filing_year: int,
    min_filing_date: date | None,
    max_filing_year: int | None,
    max_filing_date: date | None,
    censor_date: date,
) -> tuple[list[SurvivalRow], dict[str, int]]:
    rows: list[SurvivalRow] = []
    stats = {
        "rows_total": 0,
        "rows_kept": 0,
        "rows_filtered_by_year": 0,
        "rows_filtered_by_date": 0,
        "rows_filtered_by_max_year": 0,
        "rows_filtered_by_max_date": 0,
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
            if max_filing_year and filing_dt.year > max_filing_year:
                stats["rows_filtered_by_max_year"] += 1
                continue
            if max_filing_date and filing_dt > max_filing_date:
                stats["rows_filtered_by_max_date"] += 1
                continue
            if filing_dt > censor_date:
                continue
            if end_dt > censor_date:
                end_dt = censor_date
            event = int(str(item.get("event", "0")).strip() or "0")
            duration_days = max((end_dt - filing_dt).days, 0)
            rows.append(
                SurvivalRow(
                    dataset="Scraped",
                    filing_date=filing_dt,
                    end_date=end_dt,
                    event=1 if event else 0,
                    duration_days=duration_days,
                    case_id=f"{str(item.get('district', '')).strip()}::{str(item.get('cino', '')).strip()}",
                )
            )
            stats["rows_kept"] += 1
    return rows, stats


def _write_rows_csv(path: Path, rows: list[SurvivalRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["dataset", "case_id", "filing_date", "end_date", "event", "duration_days"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "dataset": row.dataset,
                    "case_id": row.case_id,
                    "filing_date": row.filing_date.isoformat(),
                    "end_date": row.end_date.isoformat(),
                    "event": row.event,
                    "duration_days": row.duration_days,
                }
            )


def _build_html(
    *,
    censor_date: date,
    min_filing_year: int,
    curves: list[dict[str, object]],
    summary: list[dict[str, object]],
    completed_hist: list[dict[str, object]],
    notes: list[str],
) -> str:
    curves_json = json.dumps(curves, ensure_ascii=False)
    summary_json = json.dumps(summary, ensure_ascii=False)
    completed_json = json.dumps(completed_hist, ensure_ascii=False)
    notes_html = "".join(f"<li>{n}</li>" for n in notes)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ONCourts vs Rest of Kollam vs Other Districts vs Kerala</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #f8fafc;
      --ink: #0f172a;
      --muted: #475569;
      --card: #ffffff;
      --line: #e2e8f0;
    }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(circle at 20% 0%, #e0f2fe 0, transparent 34%),
                  radial-gradient(circle at 90% 15%, #dcfce7 0, transparent 38%),
                  var(--bg);
      color: var(--ink);
    }}
    .wrap {{ max-width: 1180px; margin: 24px auto 36px; padding: 0 16px; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin-bottom: 12px; box-shadow: 0 6px 24px rgba(15, 23, 42, 0.04); }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 10px; }}
    .tile {{ border: 1px solid var(--line); border-radius: 10px; padding: 10px; background: #f8fafc; }}
    .tile .k {{ font-size: 12px; color: var(--muted); }}
    .tile .v {{ font-size: 20px; font-weight: 700; margin-top: 2px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 8px; border-bottom: 1px solid var(--line); text-align: left; }}
    th {{ background: #f8fafc; }}
    .muted {{ color: var(--muted); font-size: 13px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h2 style="margin:0 0 6px">KM Superimpose: ONCourts vs Rest of Kollam vs Other Districts vs Kerala (Combined)</h2>
      <div class="muted">Filing year >= {min_filing_year} | Censor date: {censor_date.isoformat()}</div>
      <div class="kpis" id="kpis"></div>
    </div>
    <div class="card">
      <div id="plot-km" style="height: 560px;"></div>
    </div>
    <div class="card">
      <div style="font-weight:600; margin-bottom:6px;">Completed Cases Distribution (Duration in days)</div>
      <div id="plot-hist" style="height: 420px;"></div>
    </div>
    <div class="card">
      <table id="summary"></table>
    </div>
    <div class="card">
      <div><b>Notes</b></div>
      <ul>{notes_html}</ul>
    </div>
  </div>
  <script>
    const curves = {curves_json};
    const summary = {summary_json};
    const completed = {completed_json};
    const traces = curves.map((c) => ({{
      type: "scatter",
      mode: "lines",
      name: c.dataset,
      x: c.x_days,
      y: c.y_survival,
      line: {{
        width: c.dataset === "Kerala (Combined)" ? 4 : 3,
        dash: c.dataset === "ONCourts" ? "dot" : c.dataset === "Other Districts" ? "dash" : "solid"
      }},
      hovertemplate: "<b>%{{fullData.name}}</b><br>Days: %{{x}}<br>Survival: %{{y:.3f}}<extra></extra>",
    }}));
    const maxX = Math.max(...curves.flatMap(c => c.x_days), 1);
    Plotly.newPlot("plot-km", traces, {{
      template: "plotly_white",
      xaxis: {{title: "Days since filing"}},
      yaxis: {{title: "S(t): not yet disposed", range: [0, 1.02]}},
      margin: {{l: 60, r: 20, t: 20, b: 80}},
      legend: {{orientation: "h", y: -0.2}},
      shapes: [{{type: "line", x0: 0, x1: maxX, y0: 0.5, y1: 0.5, line: {{dash: "dot", color: "#9ca3af"}}}}],
    }}, {{responsive: true, displaylogo: false}});

    const histTraces = completed.map((c) => ({{
      type: "histogram",
      name: c.dataset,
      x: c.durations,
      opacity: c.dataset === "Kerala (Combined)" ? 0.45 : 0.62,
      hovertemplate: "<b>%{{fullData.name}}</b><br>Duration: %{{x}} days<br>Count: %{{y}}<extra></extra>",
    }}));
    Plotly.newPlot("plot-hist", histTraces, {{
      barmode: "overlay",
      template: "plotly_white",
      xaxis: {{title: "Days to completion"}},
      yaxis: {{title: "Completed case count"}},
      margin: {{l: 60, r: 20, t: 10, b: 60}},
      legend: {{orientation: "h", y: -0.2}},
    }}, {{responsive: true, displaylogo: false}});

    const totals = summary.reduce((acc, row) => {{
      acc.modeled += Number(row.modeled_cases || 0);
      acc.events += Number(row.events || 0);
      acc.censored += Number(row.censored || 0);
      return acc;
    }}, {{modeled: 0, events: 0, censored: 0}});
    const kpiData = [
      ["Datasets", summary.length],
      ["Modeled rows", totals.modeled],
      ["Completed events", totals.events],
      ["Censored rows", totals.censored],
    ];
    const kpiRoot = document.getElementById("kpis");
    kpiData.forEach(([k, v]) => {{
      const tile = document.createElement("div");
      tile.className = "tile";
      tile.innerHTML = `<div class="k">${{k}}</div><div class="v">${{v}}</div>`;
      kpiRoot.appendChild(tile);
    }});

    const headers = [
      "dataset",
      "modeled_cases",
      "events",
      "censored",
      "km_median_days",
      "km_median_months",
      "completed_event_median_days",
      "completed_event_median_months",
      "extrapolated_median_days",
      "extrapolated_median_months"
    ];
    const table = document.getElementById("summary");
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    headers.forEach((h) => {{
      const th = document.createElement("th");
      th.textContent = h;
      hr.appendChild(th);
    }});
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    summary.forEach((row) => {{
      const tr = document.createElement("tr");
      headers.forEach((h) => {{
        const td = document.createElement("td");
        td.textContent = row[h] ?? "";
        tr.appendChild(td);
      }});
      tbody.appendChild(tr);
    }});
    table.appendChild(tbody);
  </script>
</body>
</html>"""


def main() -> int:
    args = _parse_args()
    censor_date = datetime.strptime(args.censor_date, "%Y-%m-%d").date()
    min_filing_date = _parse_date(args.min_filing_date) if args.min_filing_date else None
    max_filing_year = args.max_filing_year if args.max_filing_year and args.max_filing_year > 0 else None
    max_filing_date = _parse_date(args.max_filing_date) if args.max_filing_date else None
    oncourts_csv = Path(args.oncourts_csv).resolve()
    scraped_csv = Path(args.scraped_km_csv).resolve()
    out_html = Path(args.out_html).resolve()
    out_summary_csv = Path(args.out_summary_csv).resolve()
    out_rows_csv = Path(args.out_rows_csv).resolve()

    on_rows, on_stats = _load_oncourts_rows(
        oncourts_csv,
        min_filing_year=args.min_filing_year,
        min_filing_date=min_filing_date,
        max_filing_year=max_filing_year,
        max_filing_date=max_filing_date,
        censor_date=censor_date,
    )
    scraped_rows, scraped_stats = _load_scraped_rows(
        scraped_csv,
        min_filing_year=args.min_filing_year,
        min_filing_date=min_filing_date,
        max_filing_year=max_filing_year,
        max_filing_date=max_filing_date,
        censor_date=censor_date,
    )

    district_norm = args.district.strip().lower()
    rest_kollam_rows: list[SurvivalRow] = []
    other_district_rows: list[SurvivalRow] = []
    kerala_combined_rows: list[SurvivalRow] = []
    for row in scraped_rows:
        district = row.case_id.split("::", 1)[0].strip().lower()
        base_case_id = row.case_id.split("::", 1)[1] if "::" in row.case_id else row.case_id
        base_row = SurvivalRow(
            dataset=row.dataset,
            filing_date=row.filing_date,
            end_date=row.end_date,
            event=row.event,
            duration_days=row.duration_days,
            case_id=base_case_id,
        )
        kerala_combined_rows.append(
            SurvivalRow(
                dataset="Kerala (Combined)",
                filing_date=base_row.filing_date,
                end_date=base_row.end_date,
                event=base_row.event,
                duration_days=base_row.duration_days,
                case_id=base_row.case_id,
            )
        )
        if district == district_norm:
            rest_kollam_rows.append(
                SurvivalRow(
                    dataset="Rest of Kollam",
                    filing_date=base_row.filing_date,
                    end_date=base_row.end_date,
                    event=base_row.event,
                    duration_days=base_row.duration_days,
                    case_id=base_row.case_id,
                )
            )
        else:
            other_district_rows.append(
                SurvivalRow(
                    dataset="Other Districts",
                    filing_date=base_row.filing_date,
                    end_date=base_row.end_date,
                    event=base_row.event,
                    duration_days=base_row.duration_days,
                    case_id=base_row.case_id,
                )
            )

    modeled_rows = on_rows + rest_kollam_rows + other_district_rows + kerala_combined_rows
    _write_rows_csv(out_rows_csv, modeled_rows)

    curves: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    for dataset_name, rows in (
        ("ONCourts", on_rows),
        ("Rest of Kollam", rest_kollam_rows),
        ("Other Districts", other_district_rows),
        ("Kerala (Combined)", kerala_combined_rows),
    ):
        x, y, med = _km_curve(rows)
        curves.append({"dataset": dataset_name, "x_days": x, "y_survival": y})
        events = sum(r.event for r in rows)
        completed_durations = sorted(r.duration_days for r in rows if r.event == 1)
        completed_event_median_days = median(completed_durations) if completed_durations else None

        # Extrapolated median using constant hazard estimate with censored person-time:
        # lambda_hat = events / total_person_time_days ; median = ln(2) / lambda_hat.
        total_person_time_days = sum(max(r.duration_days, 0) for r in rows)
        extrapolated_median_days = None
        if events > 0 and total_person_time_days > 0:
            lambda_hat = events / total_person_time_days
            if lambda_hat > 0:
                extrapolated_median_days = math.log(2.0) / lambda_hat

        summary_rows.append(
            {
                "dataset": dataset_name,
                "modeled_cases": len(rows),
                "events": events,
                "censored": len(rows) - events,
                "km_median_days": med if med is not None else "",
                "km_median_months": round((med / 30.4375), 2) if med is not None else "NR",
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
            }
        )

    out_summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_summary_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "dataset",
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

    notes = [
        "ONCourts rows filtered to the configured filing window.",
        "ONCourts disposed rows with missing relevant_date are treated as censored at censor date.",
        "Rest of Kollam and Other Districts are split from scraped KM rows using district name.",
        "Kerala (Combined) is all scraped rows.",
        "completed_event_median uses observed completed cases only (event==1).",
        "extrapolated_median uses constant hazard estimate: ln(2) / (events / total person-time).",
    ]
    completed_hist = []
    for dataset_name, rows in (
        ("ONCourts", on_rows),
        ("Rest of Kollam", rest_kollam_rows),
        ("Other Districts", other_district_rows),
        ("Kerala (Combined)", kerala_combined_rows),
    ):
        completed_hist.append(
            {
                "dataset": dataset_name,
                "durations": [row.duration_days for row in rows if row.event == 1],
            }
        )
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(
        _build_html(
            censor_date=censor_date,
            min_filing_year=args.min_filing_year,
            curves=curves,
            summary=summary_rows,
            completed_hist=completed_hist,
            notes=notes,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "out_html": str(out_html),
                "out_summary_csv": str(out_summary_csv),
                "out_rows_csv": str(out_rows_csv),
                "oncourts_stats": on_stats,
                "scraped_stats": scraped_stats,
                "rest_kollam_count": len(rest_kollam_rows),
                "other_district_count": len(other_district_rows),
                "kerala_combined_count": len(kerala_combined_rows),
                "summary": summary_rows,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
