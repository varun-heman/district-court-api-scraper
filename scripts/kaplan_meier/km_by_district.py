from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from district_court_api_scraper.parsers import parse_case_detail_summary  # noqa: E402
from disposal_filters import tag_disposal  # noqa: E402


DATE_DD_MM_YYYY = "%d-%m-%Y"


@dataclass(slots=True)
class CaseSurvivalRow:
    district: str
    cino: str
    case_number: str
    filing_date: date
    end_date: date
    event: int
    duration_days: int
    decision_date: str
    decision_source: str
    nature_of_disposal: str
    disposal_primary: str
    disposal_secondary: str
    excluded_disposal: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate district-wise KM curves from run artifacts.")
    parser.add_argument(
        "--run-root",
        required=True,
        help="Run root path (e.g., output/runs/<run_id>)",
    )
    parser.add_argument(
        "--out-html",
        default="",
        help="Output HTML path (default: <run_root>/analysis/km_districts.html)",
    )
    parser.add_argument(
        "--out-medians-csv",
        default="",
        help="Output medians CSV path (default: <run_root>/analysis/km_medians.csv)",
    )
    parser.add_argument(
        "--out-cases-csv",
        default="",
        help="Output modeled cases CSV path (default: <run_root>/analysis/km_case_rows.csv)",
    )
    parser.add_argument(
        "--min-cases-per-district",
        type=int,
        default=1,
        help="Minimum modeled cases needed to include district in chart/table.",
    )
    parser.add_argument(
        "--censor-date",
        default="",
        help="Optional censor date override in YYYY-MM-DD (default: infer from run id or today's date).",
    )
    parser.add_argument(
        "--min-filing-year",
        type=int,
        default=0,
        help="Optional minimum filing year filter (e.g., 2025). Disabled when 0.",
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
        help="Optional maximum filing year filter (e.g., 2024). Disabled when 0.",
    )
    parser.add_argument(
        "--max-filing-date",
        default="",
        help="Optional maximum filing date in YYYY-MM-DD. Applied in addition to max-filing-year.",
    )
    parser.add_argument(
        "--include-case-prefixes",
        default="",
        help="Comma-separated case number prefixes to include (e.g., CC,ST). Empty = all.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text or text == "-":
        return None
    # Common numeric formats.
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # Human-readable formats like "16th January 2026".
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


def _infer_censor_date(run_root: Path, explicit: str | None) -> date:
    if explicit:
        return datetime.strptime(explicit, "%Y-%m-%d").date()
    m = re.search(r"(20\d{2})(\d{2})(\d{2})$", run_root.name)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return date.today()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


DISPOSED_RE = re.compile(
    r"dispos|convict|acquit|abated|dismiss|withdraw|settled|closed|compoun|uncontested|contested",
    re.IGNORECASE,
)


def _parse_history_disposal_date(history_rows: list[dict[str, Any]]) -> tuple[str | None, str]:
    # Find the latest history row that explicitly indicates disposal-like outcome.
    candidate_dt: date | None = None
    source = ""
    for row in history_rows:
        if not isinstance(row, dict):
            continue
        purpose = str(row.get("purpose_of_hearing", "")).strip()
        if not DISPOSED_RE.search(purpose):
            continue
        dt_raw = str(row.get("business_on_date", "")).strip() or str(row.get("hearing_date", "")).strip()
        dt = _parse_date(dt_raw)
        if dt is None:
            continue
        if candidate_dt is None or dt > candidate_dt:
            candidate_dt = dt
            source = "history_disposed_purpose"
    if candidate_dt is None:
        return None, ""
    return candidate_dt.strftime(DATE_DD_MM_YYYY), source


def _extract_case_fields(entry: dict[str, Any]) -> tuple[str | None, str | None, str, list[dict[str, Any]]]:
    summary = entry.get("summary") or {}
    case_details = summary.get("case_details") or {}
    case_status = summary.get("case_status") or {}
    history_rows = summary.get("history_rows") or []

    filing_date = str(case_details.get("Filing Date", "")).strip()
    decision_date = str(case_status.get("Decision Date", "")).strip()
    decision_source = "case_status"

    if not filing_date:
        raw_file = Path(str(entry.get("raw_file", "")))
        if raw_file.exists():
            parsed = parse_case_detail_summary(raw_file.read_text(encoding="utf-8", errors="ignore"))
            case_details = parsed.get("case_details") or {}
            case_status = parsed.get("case_status") or {}
            history_rows = parsed.get("history_rows") or []
            filing_date = str(case_details.get("Filing Date", "")).strip()
            decision_date = str(case_status.get("Decision Date", "")).strip()
            decision_source = "reparsed_case_status" if decision_date else decision_source

    if not decision_date:
        decision_date, decision_source = _parse_history_disposal_date(history_rows)

    normalized_history: list[dict[str, Any]] = []
    for row in history_rows:
        if isinstance(row, dict):
            normalized_history.append(row)
    return filing_date or None, decision_date or None, decision_source, normalized_history


def _case_prefix(case_number: str) -> str:
    m = re.match(r"^\s*([A-Za-z]+)", case_number or "")
    return m.group(1).upper() if m else ""


def _allowed_case_prefix(case_number: str, allowed_prefixes: set[str] | None) -> bool:
    if not allowed_prefixes:
        return True
    return _case_prefix(case_number) in allowed_prefixes


def _build_survival_rows(
    case_details_rows: list[dict[str, Any]],
    censor_date: date,
    *,
    min_filing_year: int | None = None,
    min_filing_date: date | None = None,
    max_filing_year: int | None = None,
    max_filing_date: date | None = None,
    include_case_prefixes: set[str] | None = None,
) -> tuple[list[CaseSurvivalRow], dict[str, int]]:
    output: list[CaseSurvivalRow] = []
    stats = {
        "total_case_details": len(case_details_rows),
        "missing_filing_date": 0,
        "invalid_filing_date": 0,
        "filtered_by_min_filing_year": 0,
        "filtered_by_min_filing_date": 0,
        "filtered_by_max_filing_year": 0,
        "filtered_by_max_filing_date": 0,
        "filtered_by_case_prefix": 0,
        "filtered_by_excluded_disposal": 0,
        "invalid_decision_date": 0,
        "decision_before_filing": 0,
    }
    seen: set[str] = set()
    for entry in case_details_rows:
        district = str(entry.get("source_district", "")).strip()
        cino = str(entry.get("cino", "")).strip()
        case_number = str(entry.get("case_number", "")).strip()
        if not district or not cino:
            continue
        key = f"{district}|{cino}"
        if key in seen:
            continue
        seen.add(key)
        if not _allowed_case_prefix(case_number, include_case_prefixes):
            stats["filtered_by_case_prefix"] += 1
            continue

        filing_raw, decision_raw, decision_source, history_rows = _extract_case_fields(entry)
        summary = entry.get("summary") or {}
        case_status = summary.get("case_status") or {}
        disposal_tag = tag_disposal(str(case_status.get("Nature of Disposal", "")).strip())
        if disposal_tag.secondary_group == "transferred / made over":
            stats["filtered_by_excluded_disposal"] += 1
            continue
        if not filing_raw:
            stats["missing_filing_date"] += 1
            continue
        filing_dt = _parse_date(filing_raw)
        if filing_dt is None:
            stats["invalid_filing_date"] += 1
            continue
        if min_filing_year and filing_dt.year < min_filing_year:
            stats["filtered_by_min_filing_year"] += 1
            continue
        if min_filing_date and filing_dt < min_filing_date:
            stats["filtered_by_min_filing_date"] += 1
            continue
        if max_filing_year and filing_dt.year > max_filing_year:
            stats["filtered_by_max_filing_year"] += 1
            continue
        if max_filing_date and filing_dt > max_filing_date:
            stats["filtered_by_max_filing_date"] += 1
            continue
        if filing_dt > censor_date:
            continue

        event = 0
        end_dt = censor_date
        if decision_raw:
            decision_dt = _parse_date(decision_raw)
            if decision_dt is None:
                # Fallback: detect disposal date from history when decision date text is non-standard.
                fallback_raw, fallback_source = _parse_history_disposal_date(history_rows)
                if fallback_raw:
                    decision_dt = _parse_date(fallback_raw)
                    if decision_dt is not None:
                        decision_source = fallback_source or decision_source
                if decision_dt is None:
                    stats["invalid_decision_date"] += 1
            elif decision_dt < filing_dt:
                stats["decision_before_filing"] += 1
            if decision_dt is not None and decision_dt >= filing_dt:
                event = 1
                end_dt = min(decision_dt, censor_date)
                if end_dt < filing_dt:
                    event = 0
                    end_dt = censor_date

        duration_days = max((end_dt - filing_dt).days, 0)
        output.append(
            CaseSurvivalRow(
                district=district,
                cino=cino,
                case_number=case_number,
                filing_date=filing_dt,
                end_date=end_dt,
                event=event,
                duration_days=duration_days,
                decision_date=decision_raw or "",
                decision_source=decision_source if event else "",
                nature_of_disposal=disposal_tag.raw,
                disposal_primary=disposal_tag.primary,
                disposal_secondary=disposal_tag.secondary_group,
                excluded_disposal=0,
            )
        )
    return output, stats


def _km_curve(rows: list[CaseSurvivalRow]) -> tuple[list[int], list[float], int | None]:
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
            survival *= (1.0 - (d / n_at_risk))
            xs.append(t)
            ys.append(survival)
            if median_days is None and survival <= 0.5:
                median_days = t
        n_at_risk -= d + c
    return xs, ys, median_days


def _write_rows_csv(path: Path, rows: list[CaseSurvivalRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "district",
                "cino",
                "case_number",
                "filing_date",
                "end_date",
                "event",
                "duration_days",
                "decision_date",
                "decision_source",
                "nature_of_disposal",
                "disposal_primary",
                "disposal_secondary",
                "excluded_disposal",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "district": row.district,
                    "cino": row.cino,
                    "case_number": row.case_number,
                    "filing_date": row.filing_date.isoformat(),
                    "end_date": row.end_date.isoformat(),
                    "event": row.event,
                    "duration_days": row.duration_days,
                    "decision_date": row.decision_date,
                    "decision_source": row.decision_source,
                    "nature_of_disposal": row.nature_of_disposal,
                    "disposal_primary": row.disposal_primary,
                    "disposal_secondary": row.disposal_secondary,
                    "excluded_disposal": row.excluded_disposal,
                }
            )


def _write_medians_csv(path: Path, medians: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "district",
                "modeled_cases",
                "events",
                "censored",
                "km_median_days",
                "km_median_months",
            ],
        )
        writer.writeheader()
        for row in medians:
            writer.writerow(row)


def _write_stats_sidecar(
    path: Path,
    *,
    run_root: Path,
    censor_date: date,
    stats: dict[str, int],
    min_filing_year: int | None,
    min_filing_date: date | None,
    max_filing_year: int | None,
    max_filing_date: date | None,
    include_case_prefixes: set[str],
) -> None:
    payload = {
        "run_root": str(run_root),
        "censor_date": censor_date.isoformat(),
        "stats": stats,
        "min_filing_year": min_filing_year or "",
        "min_filing_date": min_filing_date.isoformat() if min_filing_date else "",
        "max_filing_year": max_filing_year or "",
        "max_filing_date": max_filing_date.isoformat() if max_filing_date else "",
        "include_case_prefixes": sorted(include_case_prefixes),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_html(
    *,
    run_id: str,
    censor_date: date,
    curves: list[dict[str, Any]],
    medians: list[dict[str, Any]],
    stats: dict[str, int],
) -> str:
    curves_json = json.dumps(curves, ensure_ascii=False)
    medians_json = json.dumps(medians, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kaplan-Meier by District</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #f6f4ef;
      --card: #ffffff;
      --ink: #1f2937;
      --muted: #6b7280;
      --accent: #0f766e;
      --border: #e5e7eb;
    }}
    body {{
      margin: 0;
      background: radial-gradient(circle at 15% 15%, #e3f7f1 0, transparent 40%),
                  radial-gradient(circle at 85% 0%, #fef3c7 0, transparent 45%),
                  var(--bg);
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
    }}
    .wrap {{
      max-width: 1200px;
      margin: 24px auto 40px;
      padding: 0 18px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: 0 8px 30px rgba(15, 23, 42, 0.05);
      padding: 16px 18px;
      margin-bottom: 14px;
    }}
    .kpi {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 12px;
    }}
    .kpi .tile {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      background: #fafafa;
    }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 8px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }}
    th {{ background: #f9fafb; position: sticky; top: 0; }}
    .scroll {{ max-height: 360px; overflow: auto; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Section 138: Kaplan-Meier Survival by District</h1>
      <div class="muted">Run: <b>{run_id}</b> | Censor date: <b>{censor_date.isoformat()}</b></div>
    </div>

    <div class="card kpi">
      <div class="tile"><div class="muted">Case details rows</div><div><b>{stats.get("total_case_details", 0):,}</b></div></div>
      <div class="tile"><div class="muted">Modeled rows (with filing date)</div><div><b>{sum(m['modeled_cases'] for m in medians):,}</b></div></div>
      <div class="tile"><div class="muted">Missing filing date</div><div><b>{stats.get("missing_filing_date", 0):,}</b></div></div>
      <div class="tile"><div class="muted">Invalid decision dates</div><div><b>{stats.get("invalid_decision_date", 0):,}</b></div></div>
    </div>

    <div class="card">
      <h2>KM Curves (all districts + Kerala overall)</h2>
      <div id="km-plot" style="height: 640px;"></div>
    </div>

    <div class="card">
      <h2>KM Median (50% disposal time)</h2>
      <div class="scroll">
        <table id="median-table"></table>
      </div>
    </div>
  </div>
  <script>
    const curves = {curves_json};
    const medians = {medians_json};
    const traces = curves.map((c) => ({{
      type: "scatter",
      mode: "lines",
      name: c.district,
      x: c.x_days,
      y: c.y_survival,
      line: {{
        width: c.district === "Kerala (overall)" ? 4 : 2,
        dash: c.district === "Kerala (overall)" ? "dash" : "solid"
      }},
      hovertemplate: "<b>%{{fullData.name}}</b><br>Days: %{{x}}<br>Survival: %{{y:.3f}}<extra></extra>",
    }}));
    const layout = {{
      template: "plotly_white",
      legend: {{orientation: "h", y: -0.22}},
      xaxis: {{title: "Days since filing"}},
      yaxis: {{title: "S(t): not yet disposed", range: [0, 1.02]}},
      margin: {{l: 60, r: 20, t: 20, b: 100}},
      shapes: [{{
        type: "line", x0: 0, x1: Math.max(...curves.flatMap(c => c.x_days), 1),
        y0: 0.5, y1: 0.5, line: {{dash: "dot", color: "#9ca3af"}}
      }}]
    }};
    Plotly.newPlot("km-plot", traces, layout, {{responsive: true, displaylogo: false}});

    const headers = ["district", "modeled_cases", "events", "censored", "km_median_days", "km_median_months"];
    const table = document.getElementById("median-table");
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
    medians.forEach((row) => {{
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
</html>
"""


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    analysis_dir = run_root / "analysis"
    out_html = Path(args.out_html).resolve() if args.out_html else analysis_dir / "km_districts.html"
    out_medians_csv = (
        Path(args.out_medians_csv).resolve() if args.out_medians_csv else analysis_dir / "km_medians.csv"
    )
    out_cases_csv = Path(args.out_cases_csv).resolve() if args.out_cases_csv else analysis_dir / "km_case_rows.csv"
    censor_date = _infer_censor_date(run_root, args.censor_date or None)

    case_details_path = run_root / "normalized" / "case_details.jsonl"
    case_details_rows = _load_jsonl(case_details_path)
    min_filing_year = args.min_filing_year if args.min_filing_year and args.min_filing_year > 0 else None
    min_filing_date = _parse_date(args.min_filing_date) if args.min_filing_date else None
    max_filing_year = args.max_filing_year if args.max_filing_year and args.max_filing_year > 0 else None
    max_filing_date = _parse_date(args.max_filing_date) if args.max_filing_date else None
    include_case_prefixes = {
        token.strip().upper()
        for token in str(args.include_case_prefixes).split(",")
        if token.strip()
    }
    modeled_rows, stats = _build_survival_rows(
        case_details_rows,
        censor_date,
        min_filing_year=min_filing_year,
        min_filing_date=min_filing_date,
        max_filing_year=max_filing_year,
        max_filing_date=max_filing_date,
        include_case_prefixes=include_case_prefixes or None,
    )
    _write_rows_csv(out_cases_csv, modeled_rows)

    by_district: dict[str, list[CaseSurvivalRow]] = defaultdict(list)
    for row in modeled_rows:
        by_district[row.district].append(row)

    curves: list[dict[str, Any]] = []
    medians: list[dict[str, Any]] = []

    overall_rows = modeled_rows[:]
    if overall_rows:
        ox, oy, omed = _km_curve(overall_rows)
        curves.append({"district": "Kerala (overall)", "x_days": ox, "y_survival": oy})
        oevents = sum(r.event for r in overall_rows)
        medians.append(
            {
                "district": "Kerala (overall)",
                "modeled_cases": len(overall_rows),
                "events": oevents,
                "censored": len(overall_rows) - oevents,
                "km_median_days": omed if omed is not None else "",
                "km_median_months": round((omed / 30.4375), 2) if omed is not None else "NR",
            }
        )

    for district in sorted(by_district):
        rows = by_district[district]
        if len(rows) < args.min_cases_per_district:
            continue
        x, y, median_days = _km_curve(rows)
        curves.append({"district": district, "x_days": x, "y_survival": y})
        events = sum(r.event for r in rows)
        medians.append(
            {
                "district": district,
                "modeled_cases": len(rows),
                "events": events,
                "censored": len(rows) - events,
                "km_median_days": median_days if median_days is not None else "",
                "km_median_months": round((median_days / 30.4375), 2) if median_days is not None else "NR",
            }
        )

    medians.sort(key=lambda r: (-int(r["modeled_cases"]), str(r["district"])))
    _write_medians_csv(out_medians_csv, medians)
    _write_stats_sidecar(
        out_cases_csv.with_name(out_cases_csv.name + ".stats.json"),
        run_root=run_root,
        censor_date=censor_date,
        stats=stats,
        min_filing_year=min_filing_year,
        min_filing_date=min_filing_date,
        max_filing_year=max_filing_year,
        max_filing_date=max_filing_date,
        include_case_prefixes=include_case_prefixes,
    )
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(
        _build_html(
            run_id=run_root.name,
            censor_date=censor_date,
            curves=curves,
            medians=medians,
            stats=stats,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "censor_date": censor_date.isoformat(),
                "modeled_cases": len(modeled_rows),
                "districts": len(by_district),
                "out_html": str(out_html),
                "out_medians_csv": str(out_medians_csv),
                "out_cases_csv": str(out_cases_csv),
                "stats": stats,
                "min_filing_year": min_filing_year or "",
                "min_filing_date": min_filing_date.isoformat() if min_filing_date else "",
                "max_filing_year": max_filing_year or "",
                "max_filing_date": max_filing_date.isoformat() if max_filing_date else "",
                "include_case_prefixes": sorted(include_case_prefixes),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
