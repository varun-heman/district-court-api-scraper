from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


@dataclass(slots=True)
class SurvivalRow:
    dataset: str
    case_id: str
    filing_date: str
    end_date: str
    event: int
    duration_days: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a self-contained Kollam vs Kerala DiD attempt HTML."
    )
    parser.add_argument(
        "--pre-km-csv",
        default="data/kollam_vs_kerala_did_attempt/pre_2023_2024_km_case_rows.csv",
        help="Pre-period scraped KM case rows CSV (district-level output from km_by_district.py).",
    )
    parser.add_argument(
        "--post-compare-rows-csv",
        default="data/kollam_vs_kerala_did_attempt/post_2025_oncourts_compare_rows.csv",
        help="Post-period ONCourts comparison rows CSV (output from km_superimpose_oncourts_kollam.py).",
    )
    parser.add_argument(
        "--analysis-dir",
        default="analysis",
        help="Directory for final HTML output.",
    )
    parser.add_argument(
        "--data-dir",
        default="data/kollam_vs_kerala_did_attempt",
        help="Directory for payload and manifest outputs.",
    )
    parser.add_argument(
        "--output-name",
        default="kollam_v_kerala_DiD_attempt.html",
        help="Final HTML filename.",
    )
    parser.add_argument(
        "--payload-json-name",
        default="kollam_v_kerala_did_attempt.payload.json",
        help="Payload JSON filename.",
    )
    parser.add_argument(
        "--manifest-name",
        default="manifest.json",
        help="Manifest JSON filename.",
    )
    parser.add_argument(
        "--censor-date",
        default="2026-03-20",
        help="Shared censor date used for both cohorts.",
    )
    parser.add_argument(
        "--kollam-name",
        default="Kollam",
        help="Kollam district label in the scraped KM case rows.",
    )
    return parser.parse_args()


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


def _interp_survival(x_days: list[int], y_survival: list[float], day: int) -> float:
    if not x_days or not y_survival:
        return 1.0
    if day <= x_days[0]:
        return y_survival[0]
    if day >= x_days[-1]:
        return y_survival[-1]
    for index in range(1, len(x_days)):
        if x_days[index] >= day:
            return y_survival[index - 1]
    return y_survival[-1]


def _fmt_number(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "NR"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and digits == 0):
        return f"{int(value):,}"
    return f"{value:,.{digits}f}"


def _series_summary(rows: list[SurvivalRow]) -> dict[str, Any]:
    x_days, y_survival, km_median_days = _km_curve(rows)
    events = sum(row.event for row in rows)
    censored = len(rows) - events
    completed_durations = sorted(row.duration_days for row in rows if row.event == 1)
    completed_event_median_days = median(completed_durations) if completed_durations else None
    total_person_time = sum(max(row.duration_days, 0) for row in rows)
    extrapolated_median_days = None
    if events > 0 and total_person_time > 0:
        lambda_hat = events / total_person_time
        if lambda_hat > 0:
            extrapolated_median_days = math.log(2.0) / lambda_hat
    milestone_days = [90, 180, 365, 540, 730]
    milestone_resolved = {
        str(day): round((1.0 - _interp_survival(x_days, y_survival, day)) * 100.0, 1) for day in milestone_days
    }
    return {
        "modeled_cases": len(rows),
        "events": events,
        "censored": censored,
        "km_median_days": km_median_days if km_median_days is not None else "",
        "km_median_months": round(km_median_days / 30.4375, 2) if km_median_days is not None else "NR",
        "completed_event_median_days": round(completed_event_median_days, 1)
        if completed_event_median_days is not None
        else "",
        "completed_event_median_months": round(completed_event_median_days / 30.4375, 2)
        if completed_event_median_days is not None
        else "NR",
        "extrapolated_median_days": round(extrapolated_median_days, 1) if extrapolated_median_days is not None else "",
        "extrapolated_median_months": round(extrapolated_median_days / 30.4375, 2)
        if extrapolated_median_days is not None
        else "NR",
        "completed_durations": completed_durations,
        "x_days": x_days,
        "y_survival": y_survival,
        "milestone_resolved_pct": milestone_resolved,
    }


def _load_pre_groups(path: Path, *, kollam_name: str) -> dict[str, list[SurvivalRow]]:
    kollam_key = kollam_name.strip().lower()
    kollam_rows: list[SurvivalRow] = []
    other_rows: list[SurvivalRow] = []
    all_rows: list[SurvivalRow] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for item in reader:
            district = str(item.get("district", "")).strip()
            row = SurvivalRow(
                dataset="Pre",
                case_id=str(item.get("cino", "")).strip(),
                filing_date=str(item.get("filing_date", "")).strip(),
                end_date=str(item.get("end_date", "")).strip(),
                event=int(str(item.get("event", "0")).strip() or "0"),
                duration_days=int(str(item.get("duration_days", "0")).strip() or "0"),
            )
            all_rows.append(row)
            if district.lower() == kollam_key:
                kollam_rows.append(row)
            else:
                other_rows.append(row)
    return {
        "All Kollam": kollam_rows,
        "Other Districts": other_rows,
        "Kerala (Combined)": all_rows,
    }


def _load_post_groups(path: Path) -> dict[str, list[SurvivalRow]]:
    grouped: dict[str, list[SurvivalRow]] = defaultdict(list)
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for item in reader:
            row = SurvivalRow(
                dataset=str(item.get("dataset", "")).strip(),
                case_id=str(item.get("case_id", "")).strip(),
                filing_date=str(item.get("filing_date", "")).strip(),
                end_date=str(item.get("end_date", "")).strip(),
                event=int(str(item.get("event", "0")).strip() or "0"),
                duration_days=int(str(item.get("duration_days", "0")).strip() or "0"),
            )
            grouped[row.dataset].append(row)
    grouped["All Kollam"] = list(grouped.get("ONCourts", [])) + list(grouped.get("Rest of Kollam", []))
    return dict(grouped)


def _series_payload(
    *,
    dataset: str,
    label: str,
    color: str,
    default_visible: bool,
    rows: list[SurvivalRow],
    family: str,
    overlay_match: str,
) -> dict[str, Any]:
    metrics = _series_summary(rows)
    km_median_days = metrics.get("km_median_days")
    extrapolated_median_days = metrics.get("extrapolated_median_days")
    if km_median_days not in ("", None):
        display_median_days = km_median_days
        display_median_kind = "Actual KM"
    elif extrapolated_median_days not in ("", None):
        display_median_days = extrapolated_median_days
        display_median_kind = "Projected"
    else:
        display_median_days = ""
        display_median_kind = "NR"
    return {
        "dataset": dataset,
        "label": label,
        "color": color,
        "default_visible": default_visible,
        "family": family,
        "overlay_match": overlay_match,
        "display_median_days": display_median_days,
        "display_median_kind": display_median_kind,
        **metrics,
    }


def _build_payload(
    *,
    pre_groups: dict[str, list[SurvivalRow]],
    post_groups: dict[str, list[SurvivalRow]],
    censor_date: str,
) -> dict[str, Any]:
    colors = {
        "Kollam 2023-2024": "#CB6F5D",
        "Kerala 2023-2024": "#8B95A2",
        "ONCourts 2025+": "#0D7C66",
        "Rest of Kollam 2025+": "#E8973A",
        "Kerala 2025+": "#5B7C99",
    }

    pre_series = [
        _series_payload(
            dataset="Kollam 2023-2024",
            label="Kollam 2023-2024",
            color=colors["Kollam 2023-2024"],
            default_visible=True,
            rows=pre_groups["All Kollam"],
            family="matched",
            overlay_match="Kollam 2023-2024",
        ),
        _series_payload(
            dataset="Kerala 2023-2024",
            label="Kerala 2023-2024",
            color=colors["Kerala 2023-2024"],
            default_visible=True,
            rows=pre_groups["Kerala (Combined)"],
            family="matched",
            overlay_match="Kerala 2023-2024",
        ),
    ]

    post_series = [
        _series_payload(
            dataset="ONCourts 2025+",
            label="ONCourts 2025+",
            color=colors["ONCourts 2025+"],
            default_visible=True,
            rows=post_groups["ONCourts"],
            family="component",
            overlay_match="Kollam 2023-2024",
        ),
        _series_payload(
            dataset="Rest of Kollam 2025+",
            label="Rest of Kollam 2025+",
            color=colors["Rest of Kollam 2025+"],
            default_visible=True,
            rows=post_groups["Rest of Kollam"],
            family="component",
            overlay_match="Kollam 2023-2024",
        ),
        _series_payload(
            dataset="Kerala 2025+",
            label="Kerala 2025+",
            color=colors["Kerala 2025+"],
            default_visible=True,
            rows=post_groups["Kerala (Combined)"],
            family="matched",
            overlay_match="Kerala 2023-2024",
        ),
    ]

    def series_by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {item["dataset"]: item for item in items}

    pre_lookup = series_by_name(pre_series)
    post_lookup = series_by_name(post_series)

    def build_did_rows(post_treatment_name: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in [90, 180, 365, 540, 730]:
            treatment_pre = float(pre_lookup["Kollam 2023-2024"]["milestone_resolved_pct"][str(day)])
            treatment_post = float(post_lookup[post_treatment_name]["milestone_resolved_pct"][str(day)])
            kerala_pre = float(pre_lookup["Kerala 2023-2024"]["milestone_resolved_pct"][str(day)])
            kerala_post = float(post_lookup["Kerala 2025+"]["milestone_resolved_pct"][str(day)])
            rows.append(
                {
                    "day": day,
                    "treatment_pre": treatment_pre,
                    "treatment_post": treatment_post,
                    "kerala_pre": kerala_pre,
                    "kerala_post": kerala_post,
                    "gap_pre_vs_kerala": round(treatment_pre - kerala_pre, 1),
                    "gap_post_vs_kerala": round(treatment_post - kerala_post, 1),
                    "did_vs_kerala": round((treatment_post - kerala_post) - (treatment_pre - kerala_pre), 1),
                }
            )
        return rows

    oncourts_did_rows = build_did_rows("ONCourts 2025+")
    rest_did_rows = build_did_rows("Rest of Kollam 2025+")

    headline_day = 365
    oncourts_headline_row = next(row for row in oncourts_did_rows if row["day"] == headline_day)
    rest_headline_row = next(row for row in rest_did_rows if row["day"] == headline_day)

    cohort_medians = [
        pre_lookup["Kollam 2023-2024"],
        pre_lookup["Kerala 2023-2024"],
        post_lookup["ONCourts 2025+"],
        post_lookup["Rest of Kollam 2025+"],
        post_lookup["Kerala 2025+"],
    ]

    return {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "censor_date": censor_date,
        "headline": {
            "day": headline_day,
            "oncourts_did_vs_kerala": oncourts_headline_row["did_vs_kerala"],
            "rest_did_vs_kerala": rest_headline_row["did_vs_kerala"],
            "oncourts_post": oncourts_headline_row["treatment_post"],
            "rest_post": rest_headline_row["treatment_post"],
            "kollam_pre": oncourts_headline_row["treatment_pre"],
            "kerala_post": oncourts_headline_row["kerala_post"],
            "kerala_pre": oncourts_headline_row["kerala_pre"],
        },
        "periods": [
            {
                "slug": "pre_2023_2024",
                "label": "2023-24",
                "title": "2023-2024 legacy cohort",
                "subtitle": "Only Kollam 2023-2024 and Kerala 2023-2024 are shown in the pre period.",
                "overlay_allowed": False,
                "series": pre_series,
            },
            {
                "slug": "post_2025",
                "label": "2025+",
                "title": "2025 onward post-shift cohort",
                "subtitle": "Only ONCourts 2025+, Rest of Kollam 2025+, and Kerala 2025+ are shown. Legacy curves remain available as dashed overlays.",
                "overlay_allowed": True,
                "series": post_series,
            },
        ],
        "did_summary": {
            "comparisons": [
                {
                    "slug": "oncourts_vs_kerala",
                    "label": "ONCourts vs Kerala",
                    "treatment_pre_label": "Kollam 2023-2024",
                    "treatment_post_label": "ONCourts 2025+",
                    "control_pre_label": "Kerala 2023-2024",
                    "control_post_label": "Kerala 2025+",
                    "rows": oncourts_did_rows,
                },
                {
                    "slug": "rest_vs_kerala",
                    "label": "Rest of Kollam vs Kerala",
                    "treatment_pre_label": "Kollam 2023-2024",
                    "treatment_post_label": "Rest of Kollam 2025+",
                    "control_pre_label": "Kerala 2023-2024",
                    "control_post_label": "Kerala 2025+",
                    "rows": rest_did_rows,
                },
            ],
            "cohort_medians": cohort_medians,
        },
        "notes": [
            "The page now shows only five cohorts: ONCourts 2025+, Kollam 2023-2024, Rest of Kollam 2025+, Kerala 2023-2024, and Kerala 2025+.",
            "The DiD block compares ONCourts 2025+ and Rest of Kollam 2025+ separately against Kerala, using Kollam 2023-2024 as the shared pre-period treatment baseline.",
            "KM median is shown as Actual KM when the 50% disposal threshold is reached; otherwise the page shows the projected median from the constant-hazard extrapolation.",
            "Transfer / made-over disposals remain excluded on the scraped side before KM modeling, matching the existing Kerala comparison workflow.",
        ],
    }


def _build_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kollam vs Kerala DiD Attempt</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {
      --bg: #f7f4ee;
      --card: #ffffff;
      --ink: #1f2937;
      --muted: #64748b;
      --border: #e7e5df;
      --teal: #0d7c66;
      --amber: #e8973a;
      --slate: #8b95a2;
      --rose: #cb6f5d;
      --blue: #5b7c99;
      --navy: #15304d;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 10% 10%, rgba(13,124,102,0.12) 0, transparent 32%),
        radial-gradient(circle at 88% 2%, rgba(232,151,58,0.14) 0, transparent 26%),
        var(--bg);
      color: var(--ink);
      font-family: "Mulish", "Segoe UI", sans-serif;
    }
    .hero {
      padding: 40px 18px 24px;
    }
    .hero-inner {
      max-width: 1220px;
      margin: 0 auto;
      background: linear-gradient(135deg, rgba(21,48,77,0.97), rgba(13,124,102,0.95));
      color: #fff;
      border-radius: 26px;
      padding: 30px 28px 24px;
      box-shadow: 0 20px 44px rgba(15, 23, 42, 0.16);
    }
    .eyebrow {
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      opacity: 0.78;
      margin-bottom: 10px;
    }
    h1 {
      margin: 0;
      font-size: clamp(30px, 4vw, 48px);
      line-height: 1.02;
      letter-spacing: -0.03em;
    }
    .subtitle {
      margin-top: 12px;
      max-width: 900px;
      font-size: 16px;
      line-height: 1.55;
      opacity: 0.92;
    }
    .hero-metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 22px;
    }
    .hero-metric {
      background: rgba(255,255,255,0.12);
      border: 1px solid rgba(255,255,255,0.16);
      border-radius: 16px;
      padding: 14px;
    }
    .hero-metric .label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      opacity: 0.76;
      margin-bottom: 8px;
    }
    .hero-metric .value {
      font-size: 30px;
      font-weight: 800;
      line-height: 1;
    }
    .hero-metric .detail {
      margin-top: 8px;
      font-size: 12px;
      line-height: 1.45;
      opacity: 0.86;
    }
    .wrap {
      max-width: 1220px;
      margin: 0 auto 36px;
      padding: 0 18px 36px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: end;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .section-title {
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .section-subtitle {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }
    .tab-bar, .toggle-bar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .tab-btn, .toggle-btn {
      appearance: none;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
      cursor: pointer;
      transition: 160ms ease;
    }
    .tab-btn.active, .toggle-btn.active {
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
    }
    .tab-btn:hover, .toggle-btn:hover {
      transform: translateY(-1px);
    }
    .toggle-btn[disabled] {
      opacity: 0.45;
      cursor: default;
      transform: none;
    }
    .legend-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #fff;
      cursor: pointer;
    }
    .pill input {
      margin: 0;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 16px;
    }
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
    }
    .summary-card {
      border: 1px solid var(--border);
      border-left: 4px solid var(--rose);
      border-radius: 16px;
      padding: 14px;
      background: linear-gradient(180deg, #fff, #fbfaf7);
    }
    .summary-card .name {
      font-size: 16px;
      font-weight: 800;
      margin-bottom: 8px;
    }
    .summary-card .stat {
      font-size: 13px;
      line-height: 1.55;
      color: #334155;
    }
    .bar-bg {
      margin-top: 10px;
      height: 10px;
      background: #ede9e1;
      border-radius: 999px;
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      border-radius: 999px;
    }
    .bar-label {
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .did-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .did-card {
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      background: linear-gradient(180deg, #ffffff, #fbfaf7);
    }
    .did-card .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }
    .did-card .value {
      font-size: 32px;
      font-weight: 800;
      line-height: 1;
    }
    .did-card .detail {
      margin-top: 8px;
      color: #334155;
      font-size: 13px;
      line-height: 1.5;
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    td.num {
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    .notes {
      margin: 0;
      padding-left: 18px;
      color: #334155;
      line-height: 1.65;
    }
    .notes li {
      margin-bottom: 6px;
    }
    .footer {
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      padding: 0 18px 28px;
    }
    @media (max-width: 880px) {
      .grid-2 {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 720px) {
      .hero-inner {
        padding: 22px 18px;
      }
      .card {
        padding: 16px;
      }
    }
  </style>
</head>
<body>
  <div class="hero">
    <div class="hero-inner">
      <div class="eyebrow">Difference-in-Difference Attempt · NI 138 Resolution Curves</div>
      <h1>Kollam legacy courts, ONCourts, and Kerala across the shift.</h1>
      <div class="subtitle" id="hero-subtitle"></div>
      <div class="hero-metrics" id="hero-metrics"></div>
    </div>
  </div>

  <div class="wrap">
    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Period</div>
          <div class="section-subtitle">Switch between the pre-period legacy cohort and the later post-shift cohort.</div>
        </div>
      </div>
      <div class="tab-bar" id="period-tabs"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Series</div>
          <div class="section-subtitle" id="series-subtitle"></div>
        </div>
        <div class="toggle-bar">
          <button class="toggle-btn active" data-mode="resolved">Cases Resolved</button>
          <button class="toggle-btn" data-mode="pending">Cases Still Pending</button>
          <button class="toggle-btn" id="overlay-toggle">Overlay 2023-24 Curves</button>
        </div>
      </div>
      <div class="legend-pills" id="legend-pills"></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="section-head">
          <div>
            <div class="section-title" id="km-title">Kaplan-Meier curves</div>
            <div class="section-subtitle" id="km-subtitle"></div>
          </div>
        </div>
        <div id="plot-km" style="height: 520px;"></div>
      </div>

      <div class="card">
        <div class="section-head">
          <div>
            <div class="section-title">Matched DiD Readout</div>
            <div class="section-subtitle">Two separate DiD comparisons are shown: ONCourts versus Kerala, and Rest of Kollam versus Kerala.</div>
          </div>
        </div>
        <div class="did-grid" id="did-cards"></div>
        <div class="table-wrap"><table id="did-table"></table></div>
      </div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Active Period Summary</div>
          <div class="section-subtitle">Only the requested cohorts are shown: Kollam 2023-2024 and Kerala 2023-2024 in pre, then ONCourts 2025+, Rest of Kollam 2025+, and Kerala 2025+ in post.</div>
        </div>
      </div>
      <div class="cards-grid" id="summary-cards"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Active Period Statistics</div>
          <div class="section-subtitle">The table follows the currently selected period tab.</div>
        </div>
      </div>
      <div class="table-wrap"><table id="summary-table"></table></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Notes</div>
          <div class="section-subtitle">Build assumptions and cohort caveats.</div>
        </div>
      </div>
      <ul class="notes" id="notes"></ul>
    </div>
  </div>

  <div class="footer">PUCAR · ONCourts · Kerala NI 138 · DiD attempt</div>

  <script>
    const payload = __PAYLOAD_JSON__;
    const FONT = "'Mulish', 'Calibri', sans-serif";
    const periodLookup = Object.fromEntries(payload.periods.map((period) => [period.slug, period]));
    const orderByPeriod = {
      pre_2023_2024: ["Kollam 2023-2024", "Kerala 2023-2024"],
      post_2025: ["ONCourts 2025+", "Rest of Kollam 2025+", "Kerala 2025+"]
    };
    const selectionByPeriod = {};
    let currentPeriod = payload.periods[1].slug;
    let chartMode = "resolved";
    let overlayEnabled = true;

    function fmtPct(value) {
      return `${Number(value).toFixed(1)}%`;
    }

    function fmtMetric(value, digits = 1) {
      if (value === "" || value === "NR" || value === null || value === undefined) return "NR";
      return Number(value).toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits
      });
    }

    function activePeriod() {
      return periodLookup[currentPeriod];
    }

    function seriesLookup(period) {
      return Object.fromEntries(period.series.map((item) => [item.dataset, item]));
    }

    function activeSelection() {
      if (!selectionByPeriod[currentPeriod]) {
        selectionByPeriod[currentPeriod] = new Set(activePeriod().series.filter((item) => item.default_visible).map((item) => item.dataset));
      }
      return selectionByPeriod[currentPeriod];
    }

    function orderedSeries(period) {
      const order = orderByPeriod[period.slug] || [];
      return period.series.slice().sort((a, b) => order.indexOf(a.dataset) - order.indexOf(b.dataset));
    }

    function visibleSeries() {
      const selected = activeSelection();
      return orderedSeries(activePeriod()).filter((item) => selected.has(item.dataset));
    }

    function renderHero() {
      const headline = payload.headline;
      document.getElementById("hero-subtitle").innerHTML =
        `Matched cohorts are censored at <strong>${payload.censor_date}</strong>. ` +
        `The page now keeps only five cohorts in view and reports DiD separately for <strong>ONCourts 2025+</strong> and <strong>Rest of Kollam 2025+</strong> against <strong>Kerala</strong>.`;
      const metrics = [
        {
          label: `365-day ONCourts DiD`,
          value: `${headline.oncourts_did_vs_kerala > 0 ? "+" : ""}${headline.oncourts_did_vs_kerala.toFixed(1)} pp`,
          detail: `ONCourts 2025+ is ${headline.oncourts_post.toFixed(1)}% resolved at 365 days versus Kerala 2025+ at ${headline.kerala_post.toFixed(1)}%, compared with Kollam 2023-2024 at ${headline.kollam_pre.toFixed(1)}% and Kerala 2023-2024 at ${headline.kerala_pre.toFixed(1)}%.`
        },
        {
          label: `365-day Rest-of-Kollam DiD`,
          value: `${headline.rest_did_vs_kerala > 0 ? "+" : ""}${headline.rest_did_vs_kerala.toFixed(1)} pp`,
          detail: `Rest of Kollam 2025+ is ${headline.rest_post.toFixed(1)}% resolved at 365 days against the same Kerala pre/post benchmark.`
        },
        {
          label: `ONCourts 365-day`,
          value: fmtPct(headline.oncourts_post),
          detail: `Observed resolution within 365 days for ONCourts 2025+.`
        },
        {
          label: `Kollam Pre 365-day`,
          value: fmtPct(headline.kollam_pre),
          detail: `Observed resolution within 365 days for Kollam 2023-2024.`
        }
      ];
      const root = document.getElementById("hero-metrics");
      root.innerHTML = "";
      metrics.forEach((item) => {
        const node = document.createElement("div");
        node.className = "hero-metric";
        node.innerHTML = `<div class="label">${item.label}</div><div class="value">${item.value}</div><div class="detail">${item.detail}</div>`;
        root.appendChild(node);
      });
    }

    function renderPeriodTabs() {
      const root = document.getElementById("period-tabs");
      root.innerHTML = "";
      payload.periods.forEach((period) => {
        const button = document.createElement("button");
        button.className = "tab-btn" + (period.slug === currentPeriod ? " active" : "");
        button.textContent = period.label;
        button.addEventListener("click", () => {
          if (currentPeriod === period.slug) return;
          currentPeriod = period.slug;
          renderAll();
        });
        root.appendChild(button);
      });
    }

    function renderLegend() {
      const selected = activeSelection();
      const root = document.getElementById("legend-pills");
      root.innerHTML = "";
      orderedSeries(activePeriod()).forEach((series) => {
        const label = document.createElement("label");
        label.className = "pill";
        label.style.opacity = selected.has(series.dataset) ? "1" : "0.45";
        label.innerHTML = `<input type="checkbox" ${selected.has(series.dataset) ? "checked" : ""}><span class="dot" style="background:${series.color}"></span>${series.label}`;
        label.querySelector("input").addEventListener("change", (event) => {
          if (event.target.checked) selected.add(series.dataset);
          else selected.delete(series.dataset);
          renderLegend();
          renderKM();
          renderSummaryCards();
          renderSummaryTable();
        });
        root.appendChild(label);
      });
      document.getElementById("series-subtitle").textContent = activePeriod().subtitle;
      const overlayButton = document.getElementById("overlay-toggle");
      const overlayAllowed = !!activePeriod().overlay_allowed;
      overlayButton.disabled = !overlayAllowed;
      overlayButton.classList.toggle("active", overlayAllowed && overlayEnabled);
    }

    function overlaySeriesForPost() {
      if (currentPeriod !== "post_2025" || !overlayEnabled) return [];
      const prePeriod = periodLookup["pre_2023_2024"];
      const preLookup = seriesLookup(prePeriod);
      const visible = visibleSeries();
      const matches = new Set(
        visible
          .map((item) => item.overlay_match)
          .filter((value) => value)
      );
      return Array.from(matches)
        .map((match) => preLookup[match])
        .filter(Boolean);
    }

    function renderKM() {
      const period = activePeriod();
      const series = visibleSeries();
      const resolvedMode = chartMode === "resolved";
      const overlaySeries = overlaySeriesForPost();
      const traces = [];

      series.forEach((item) => {
        traces.push({
          type: "scatter",
          mode: "lines",
          name: item.label,
          x: item.x_days,
          y: resolvedMode ? item.y_survival.map((value) => 1 - value) : item.y_survival,
          line: {
            width: item.family === "matched" ? 3.4 : 2.3,
            color: item.color,
            shape: "spline",
            smoothing: 0.42,
          },
          hovertemplate: `<b>${item.label}</b><br>Day %{x}<br>${resolvedMode ? "Resolved" : "Still pending"}: %{y:.1%}<extra></extra>`
        });
      });

      overlaySeries.forEach((item) => {
        traces.push({
          type: "scatter",
          mode: "lines",
          name: `${item.label} (2023-24 overlay)`,
          x: item.x_days,
          y: resolvedMode ? item.y_survival.map((value) => 1 - value) : item.y_survival,
          line: {
            width: 2,
            color: item.color,
            dash: "dash",
            shape: "spline",
            smoothing: 0.42,
          },
          opacity: 0.72,
          hovertemplate: `<b>${item.label} · 2023-24 overlay</b><br>Day %{x}<br>${resolvedMode ? "Resolved" : "Still pending"}: %{y:.1%}<extra></extra>`
        });
      });

      const maxX = Math.max(1, ...traces.flatMap((item) => item.x || [0]));
      document.getElementById("km-title").textContent = resolvedMode
        ? `${period.title}: how fast are cases being resolved?`
        : `${period.title}: how many cases are still pending?`;
      document.getElementById("km-subtitle").textContent = resolvedMode
        ? "Steeper lines indicate faster resolution. Dashed lines on the post tab show the 2023-24 Kollam or Kerala baselines."
        : "Lower lines indicate faster resolution. Dashed lines on the post tab show the 2023-24 Kollam or Kerala baselines.";

      Plotly.react("plot-km", traces, {
        font: { family: FONT, color: "#2D2D2D" },
        xaxis: {
          title: { text: "Days since filing", font: { size: 13, color: "#5A5A5A" } },
          gridcolor: "#ECECEC",
          linecolor: "#D9D9D9",
          tickfont: { size: 11, color: "#5A5A5A" },
          zeroline: false
        },
        yaxis: {
          title: { text: resolvedMode ? "% of cases resolved" : "% of cases still pending", font: { size: 13, color: "#5A5A5A" } },
          range: resolvedMode ? [-0.02, 1.02] : [-0.02, 1.02],
          gridcolor: "#ECECEC",
          linecolor: "#D9D9D9",
          tickfont: { size: 11, color: "#5A5A5A" },
          tickformat: ".0%",
          zeroline: false
        },
        margin: { l: 70, r: 24, t: 12, b: 60 },
        plot_bgcolor: "#FFFFFF",
        paper_bgcolor: "#FFFFFF",
        legend: { orientation: "h", y: -0.18, x: 0, font: { size: 11 } },
        shapes: [{
          type: "line",
          x0: 0,
          x1: maxX,
          y0: 0.5,
          y1: 0.5,
          line: { dash: "dot", color: "#E0E0E0", width: 1 }
        }],
        annotations: [{
          x: maxX - 10,
          y: 0.505,
          text: "Halfway point",
          showarrow: false,
          font: { size: 10, color: "#999" },
          xanchor: "right"
        }],
        hovermode: "x unified"
      }, { responsive: true, displaylogo: false, displayModeBar: false });
    }

    function renderDidCards() {
      const comparisons = Object.fromEntries(payload.did_summary.comparisons.map((item) => [item.slug, item]));
      const on365 = comparisons.oncourts_vs_kerala.rows.find((row) => row.day === 365);
      const rest365 = comparisons.rest_vs_kerala.rows.find((row) => row.day === 365);
      const medianLookup = Object.fromEntries(payload.did_summary.cohort_medians.map((item) => [item.dataset, item]));
      const onMedian = medianLookup["ONCourts 2025+"];
      const restMedian = medianLookup["Rest of Kollam 2025+"];
      const cards = [
        {
          label: "365-day ONCourts DiD",
          value: `${on365.did_vs_kerala > 0 ? "+" : ""}${on365.did_vs_kerala.toFixed(1)} pp`,
          detail: `Gap versus Kerala moved from ${on365.gap_pre_vs_kerala.toFixed(1)} pp in 2023-24 to ${on365.gap_post_vs_kerala.toFixed(1)} pp in 2025+.`
        },
        {
          label: "365-day Rest-of-Kollam DiD",
          value: `${rest365.did_vs_kerala > 0 ? "+" : ""}${rest365.did_vs_kerala.toFixed(1)} pp`,
          detail: `Legacy courts inside Kollam can now be compared separately against the same Kerala benchmark.`
        },
        {
          label: "ONCourts Median",
          value: onMedian.display_median_days === "" ? "NR" : `${fmtMetric(onMedian.display_median_days, 1)} d`,
          detail: `${onMedian.display_median_kind} median for ONCourts 2025+.`
        },
        {
          label: "Rest-of-Kollam Median",
          value: restMedian.display_median_days === "" ? "NR" : `${fmtMetric(restMedian.display_median_days, 1)} d`,
          detail: `${restMedian.display_median_kind} median for Rest of Kollam 2025+.`
        }
      ];
      const root = document.getElementById("did-cards");
      root.innerHTML = "";
      cards.forEach((item) => {
        const node = document.createElement("div");
        node.className = "did-card";
        node.innerHTML = `<div class="label">${item.label}</div><div class="value">${item.value}</div><div class="detail">${item.detail}</div>`;
        root.appendChild(node);
      });
    }

    function renderDidTable() {
      const table = document.getElementById("did-table");
      table.innerHTML = "";
      const comparisons = Object.fromEntries(payload.did_summary.comparisons.map((item) => [item.slug, item]));
      const onRows = Object.fromEntries(comparisons.oncourts_vs_kerala.rows.map((row) => [row.day, row]));
      const restRows = Object.fromEntries(comparisons.rest_vs_kerala.rows.map((row) => [row.day, row]));
      const headers = [
        ["day", "Day"],
        ["kollam_pre", "Kollam 2023-24"],
        ["kerala_pre", "Kerala 2023-24"],
        ["oncourts_post", "ONCourts 2025+"],
        ["kerala_post", "Kerala 2025+"],
        ["oncourts_did", "ONCourts DiD"],
        ["rest_post", "Rest of Kollam 2025+"],
        ["rest_did", "Rest DiD"]
      ];
      const thead = document.createElement("thead");
      const hr = document.createElement("tr");
      headers.forEach(([key, label]) => {
        const th = document.createElement("th");
        th.textContent = label;
        hr.appendChild(th);
      });
      thead.appendChild(hr);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      [90, 180, 365, 540, 730].forEach((day) => {
        const onRow = onRows[day];
        const restRow = restRows[day];
        const row = {
          day,
          kollam_pre: onRow.treatment_pre,
          kerala_pre: onRow.kerala_pre,
          oncourts_post: onRow.treatment_post,
          kerala_post: onRow.kerala_post,
          oncourts_did: onRow.did_vs_kerala,
          rest_post: restRow.treatment_post,
          rest_did: restRow.did_vs_kerala
        };
        const tr = document.createElement("tr");
        headers.forEach(([key]) => {
          const td = document.createElement("td");
          td.className = key === "day" ? "" : "num";
          td.textContent = key === "day" ? String(row[key]) : fmtPct(row[key]);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
    }

    function renderSummaryCards() {
      const root = document.getElementById("summary-cards");
      root.innerHTML = "";
      visibleSeries().forEach((series) => {
        const pctResolved = series.modeled_cases > 0 ? (series.events / series.modeled_cases) * 100 : 0;
        const medianText = series.display_median_days === ""
          ? "NR"
          : `${fmtMetric(series.display_median_days, 1)} days (${series.display_median_kind})`;
        const node = document.createElement("div");
        node.className = "summary-card";
        node.style.borderLeftColor = series.color;
        node.innerHTML = `
          <div class="name" style="color:${series.color}">${series.label}</div>
          <div class="stat">
            <strong>${Number(series.modeled_cases).toLocaleString()}</strong> cases tracked<br>
            <strong>${Number(series.events).toLocaleString()}</strong> resolved · ${Number(series.censored).toLocaleString()} still pending<br>
            KM median: <strong>${medianText}</strong><br>
            365-day resolved: <strong>${fmtPct(series.milestone_resolved_pct["365"])}</strong>
          </div>
          <div class="bar-bg"><div class="bar-fill" style="width:${pctResolved.toFixed(1)}%; background:${series.color}"></div></div>
          <div class="bar-label">${pctResolved.toFixed(1)}% resolved by censor date</div>
        `;
        root.appendChild(node);
      });
    }

    function renderSummaryTable() {
      const table = document.getElementById("summary-table");
      table.innerHTML = "";
      const headers = {
        label: "Dataset",
        modeled_cases: "Cases",
        events: "Resolved",
        censored: "Pending",
        display_median_kind: "Median Type",
        display_median_days: "KM / Projected Median (days)",
        resolved_90: "Resolved by 90d",
        resolved_180: "Resolved by 180d",
        resolved_365: "Resolved by 365d"
      };
      const keys = Object.keys(headers);
      const thead = document.createElement("thead");
      const hr = document.createElement("tr");
      keys.forEach((key) => {
        const th = document.createElement("th");
        th.textContent = headers[key];
        hr.appendChild(th);
      });
      thead.appendChild(hr);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      orderedSeries(activePeriod()).forEach((row) => {
        const tr = document.createElement("tr");
        keys.forEach((key) => {
          const td = document.createElement("td");
          if (key === "label") {
            td.textContent = row.label;
          } else if (key === "resolved_90") {
            td.className = "num";
            td.textContent = fmtPct(row.milestone_resolved_pct["90"]);
          } else if (key === "resolved_180") {
            td.className = "num";
            td.textContent = fmtPct(row.milestone_resolved_pct["180"]);
          } else if (key === "resolved_365") {
            td.className = "num";
            td.textContent = fmtPct(row.milestone_resolved_pct["365"]);
          } else if (key === "display_median_kind") {
            td.textContent = row.display_median_kind;
          } else if (typeof row[key] === "number") {
            td.className = "num";
            td.textContent = fmtMetric(row[key], key.includes("median") ? 1 : 0);
          } else if (row[key] === "" || row[key] === "NR") {
            td.textContent = "NR";
          } else {
            td.textContent = row[key] ?? "";
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
    }

    function renderNotes() {
      const root = document.getElementById("notes");
      root.innerHTML = "";
      payload.notes.forEach((note) => {
        const li = document.createElement("li");
        li.textContent = note;
        root.appendChild(li);
      });
    }

    function renderAll() {
      renderHero();
      renderPeriodTabs();
      renderLegend();
      renderKM();
      renderDidCards();
      renderDidTable();
      renderSummaryCards();
      renderSummaryTable();
      renderNotes();
      document.querySelectorAll(".toggle-btn[data-mode]").forEach((button) => {
        button.classList.toggle("active", button.dataset.mode === chartMode);
      });
    }

    document.querySelectorAll(".toggle-btn[data-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        chartMode = button.dataset.mode;
        renderAll();
      });
    });

    document.getElementById("overlay-toggle").addEventListener("click", () => {
      if (!activePeriod().overlay_allowed) return;
      overlayEnabled = !overlayEnabled;
      renderAll();
    });

    renderAll();
  </script>
</body>
</html>
"""
    return html.replace("__PAYLOAD_JSON__", payload_json)


def main() -> int:
    args = _parse_args()
    pre_km_csv = Path(args.pre_km_csv).resolve()
    post_compare_rows_csv = Path(args.post_compare_rows_csv).resolve()
    analysis_dir = Path(args.analysis_dir).resolve()
    data_dir = Path(args.data_dir).resolve()
    output_html = analysis_dir / args.output_name
    payload_json = data_dir / args.payload_json_name
    manifest_json = data_dir / args.manifest_name

    analysis_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    pre_groups = _load_pre_groups(pre_km_csv, kollam_name=args.kollam_name)
    post_groups = _load_post_groups(post_compare_rows_csv)
    payload = _build_payload(pre_groups=pre_groups, post_groups=post_groups, censor_date=args.censor_date)

    payload_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_html.write_text(_build_html(payload), encoding="utf-8")

    manifest = {
        "built_at_utc": payload["built_at_utc"],
        "pre_km_csv": str(pre_km_csv),
        "post_compare_rows_csv": str(post_compare_rows_csv),
        "payload_json": str(payload_json),
        "output_html": str(output_html),
        "censor_date": args.censor_date,
        "kollam_name": args.kollam_name,
    }
    manifest_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
