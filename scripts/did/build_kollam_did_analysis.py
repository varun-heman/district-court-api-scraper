from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COHORT_ORDER = [
    "Kollam 2023-2024",
    "Kerala 2023-2024",
    "ONCourts 2025+",
    "Rest of Kollam 2025+",
    "Kerala 2025+",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a narrative Kollam ONCourts transition analysis HTML from the existing payload.",
    )
    parser.add_argument(
        "--source-payload-json",
        default="data/kollam_vs_kerala_did_attempt/kollam_v_kerala_did_attempt.payload.json",
        help="Existing payload JSON from the exact-window Kollam/Kerala build.",
    )
    parser.add_argument(
        "--analysis-dir",
        default="analysis",
        help="Directory for final HTML output.",
    )
    parser.add_argument(
        "--data-dir",
        default="data/kollam_did_analysis",
        help="Directory for transformed payload and manifest outputs.",
    )
    parser.add_argument(
        "--output-name",
        default="kollam_did_analysis.html",
        help="Final HTML filename.",
    )
    parser.add_argument(
        "--payload-json-name",
        default="kollam_did_analysis.payload.json",
        help="Payload JSON filename.",
    )
    parser.add_argument(
        "--manifest-name",
        default="manifest.json",
        help="Manifest JSON filename.",
    )
    parser.add_argument(
        "--did-plan-path",
        default="analysis/kollam_did_plan.md",
        help="Path to the companion DiD plan document to reference from the page.",
    )
    return parser.parse_args()


def _series_lookup(source_payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    periods = {period["slug"]: period for period in source_payload["periods"]}
    pre_lookup = {item["dataset"]: item for item in periods["pre_2023_2024"]["series"]}
    post_lookup = {item["dataset"]: item for item in periods["post_2025"]["series"]}
    return pre_lookup, post_lookup


def _metric_card(label: str, value: str, detail: str, tone: str = "default") -> dict[str, str]:
    return {
        "label": label,
        "value": value,
        "detail": detail,
        "tone": tone,
    }


def _fmt_pp(value: float) -> str:
    return f"{value:+.1f} pp"


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_days(value: Any) -> str:
    if value in ("", None, "NR"):
        return "NR"
    return f"{float(value):,.1f} d"


def _build_payload(source_payload: dict[str, Any], *, source_payload_path: Path, did_plan_path: str) -> dict[str, Any]:
    pre_lookup, post_lookup = _series_lookup(source_payload)

    selected_series = {
        "Kollam 2023-2024": pre_lookup["Kollam 2023-2024"],
        "Kerala 2023-2024": pre_lookup["Kerala 2023-2024"],
        "ONCourts 2025+": post_lookup["ONCourts 2025+"],
        "Rest of Kollam 2025+": post_lookup["Rest of Kollam 2025+"],
        "Kerala 2025+": post_lookup["Kerala 2025+"],
    }

    row365 = {
        name: float(selected_series[name]["milestone_resolved_pct"]["365"])
        for name in COHORT_ORDER
    }
    row180 = {
        name: float(selected_series[name]["milestone_resolved_pct"]["180"])
        for name in COHORT_ORDER
    }
    row730 = {
        name: float(selected_series[name]["milestone_resolved_pct"]["730"])
        for name in COHORT_ORDER
    }

    kerala_shift_365 = row365["Kerala 2025+"] - row365["Kerala 2023-2024"]
    oncourts_vs_old_kollam_365 = row365["ONCourts 2025+"] - row365["Kollam 2023-2024"]
    rest_vs_old_kollam_365 = row365["Rest of Kollam 2025+"] - row365["Kollam 2023-2024"]
    oncourts_vs_kerala_2025_365 = row365["ONCourts 2025+"] - row365["Kerala 2025+"]
    rest_vs_kerala_2025_365 = row365["Rest of Kollam 2025+"] - row365["Kerala 2025+"]

    benchmark_cards = [
        _metric_card(
            "ONCourts 2025+",
            _fmt_pct(row365["ONCourts 2025+"]),
            f"Resolved within 365 days. This is { _fmt_pp(oncourts_vs_kerala_2025_365) } above Kerala 2025+ and { _fmt_pp(oncourts_vs_old_kollam_365) } above old Kollam.",
            "teal",
        ),
        _metric_card(
            "Rest of Kollam 2025+",
            _fmt_pct(row365["Rest of Kollam 2025+"]),
            f"Resolved within 365 days. This is { _fmt_pp(rest_vs_kerala_2025_365) } above Kerala 2025+ and { _fmt_pp(rest_vs_old_kollam_365) } above old Kollam.",
            "amber",
        ),
        _metric_card(
            "Kerala 2025+",
            _fmt_pct(row365["Kerala 2025+"]),
            f"Statewide benchmark at 365 days. Kerala moved by { _fmt_pp(kerala_shift_365) } versus Kerala 2023-2024, which is why this page treats the comparison as descriptive rather than causal.",
            "blue",
        ),
        _metric_card(
            "ONCourts KM Median",
            _fmt_days(selected_series["ONCourts 2025+"]["display_median_days"]),
            f"{selected_series['ONCourts 2025+']['display_median_kind']} median. Rest of Kollam is { _fmt_days(selected_series['Rest of Kollam 2025+']['display_median_days']) } and Kerala 2025+ is { _fmt_days(selected_series['Kerala 2025+']['display_median_days']) }.",
            "navy",
        ),
    ]

    cohort_rows = []
    for dataset in COHORT_ORDER:
        item = selected_series[dataset]
        cohort_rows.append(
            {
                "dataset": dataset,
                "period": "2023-2024" if "2023-2024" in dataset else "2025+",
                "cases": item["modeled_cases"],
                "events": item["events"],
                "censored": item["censored"],
                "resolved_180": item["milestone_resolved_pct"]["180"],
                "resolved_365": item["milestone_resolved_pct"]["365"],
                "resolved_730": item["milestone_resolved_pct"]["730"],
                "median_kind": item["display_median_kind"],
                "median_days": item["display_median_days"],
                "color": item["color"],
            }
        )

    milestone_rows = []
    for day in [180, 365, 730]:
        milestone_rows.append(
            {
                "day": day,
                "Kollam 2023-2024": selected_series["Kollam 2023-2024"]["milestone_resolved_pct"][str(day)],
                "Kerala 2023-2024": selected_series["Kerala 2023-2024"]["milestone_resolved_pct"][str(day)],
                "ONCourts 2025+": selected_series["ONCourts 2025+"]["milestone_resolved_pct"][str(day)],
                "Rest of Kollam 2025+": selected_series["Rest of Kollam 2025+"]["milestone_resolved_pct"][str(day)],
                "Kerala 2025+": selected_series["Kerala 2025+"]["milestone_resolved_pct"][str(day)],
            }
        )

    return {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_payload_json": str(source_payload_path),
        "censor_date": source_payload["censor_date"],
        "did_plan_path": did_plan_path,
        "hero": {
            "title": "Kollam ONCourts transition analysis",
            "subtitle": "The question here is practical, not purely econometric: what changed after ONCourts went live in Kollam, and how should we benchmark it against the older Kollam system and the statewide Kerala backdrop?",
            "pills": [
                "Before 2025, Kollam cases sat in the legacy court world.",
                "From 2025 onward, Kollam splits into ONCourts and the rest of Kollam.",
                "Kerala itself slowed down in 2025+, so this page reads as transition evidence, not clean causal DiD.",
            ],
        },
        "chart": {
            "title": "Post-2025 performance with old-world overlays",
            "subtitle": "Solid lines are the current-world comparison. Dashed lines are the old-world Kollam and Kerala baselines from 2023-2024.",
            "primary_series": [
                selected_series["ONCourts 2025+"],
                selected_series["Rest of Kollam 2025+"],
                selected_series["Kerala 2025+"],
            ],
            "overlay_series": [
                selected_series["Kollam 2023-2024"],
                selected_series["Kerala 2023-2024"],
            ],
        },
        "benchmark_cards": benchmark_cards,
        "milestone_rows": milestone_rows,
        "cohort_rows": cohort_rows,
        "notes": [
            "This page intentionally avoids presenting a strong causal DiD claim. Kerala itself moves downward between the pre and post periods.",
            "The clean descriptive comparison is therefore: old Kollam versus ONCourts 2025+, old Kollam versus Rest of Kollam 2025+, and each of those against the Kerala benchmark in the same period.",
            "KM median is labeled as Actual KM only when the cohort reaches the 50% resolution threshold. Otherwise the page shows the projected median from the constant-hazard extrapolation.",
            "A separate implementation plan for a more defensible DiD is written at the path shown below.",
        ],
    }


def _build_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kollam ONCourts Transition Analysis</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {
      --bg: #f5f1ea;
      --card: #fffdf9;
      --ink: #1f2937;
      --muted: #66758d;
      --border: #e7dfd4;
      --teal: #0d7c66;
      --amber: #e8973a;
      --rose: #cb6f5d;
      --blue: #5b7c99;
      --navy: #15304d;
      --cream: #fbf7ef;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 12% 8%, rgba(13,124,102,0.12) 0, transparent 28%),
        radial-gradient(circle at 90% 4%, rgba(232,151,58,0.12) 0, transparent 24%),
        var(--bg);
      color: var(--ink);
      font-family: "Mulish", "Segoe UI", sans-serif;
    }
    .hero {
      padding: 36px 18px 18px;
    }
    .hero-inner {
      max-width: 1280px;
      margin: 0 auto;
      border-radius: 28px;
      padding: 30px 30px 24px;
      background:
        linear-gradient(145deg, rgba(21,48,77,0.98), rgba(13,124,102,0.95)),
        #17324f;
      color: #fff;
      box-shadow: 0 24px 52px rgba(15, 23, 42, 0.15);
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
      font-size: clamp(30px, 4vw, 52px);
      line-height: 1.02;
      letter-spacing: -0.04em;
      max-width: 840px;
    }
    .hero-subtitle {
      margin-top: 14px;
      max-width: 920px;
      font-size: 17px;
      line-height: 1.55;
      opacity: 0.93;
    }
    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 20px;
    }
    .hero-pill {
      display: inline-flex;
      align-items: center;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,0.11);
      border: 1px solid rgba(255,255,255,0.18);
      font-size: 13px;
      line-height: 1.35;
    }
    .wrap {
      max-width: 1280px;
      margin: 0 auto;
      padding: 0 18px 36px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 20px;
      margin-bottom: 16px;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-end;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }
    .section-title {
      font-size: 22px;
      font-weight: 800;
      letter-spacing: -0.03em;
    }
    .section-subtitle {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
      max-width: 760px;
    }
    .toggle-bar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .toggle-btn {
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
    .toggle-btn.active {
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
    }
    .toggle-btn:hover {
      transform: translateY(-1px);
    }
    .grid-main {
      display: grid;
      grid-template-columns: 1.25fr 0.75fr;
      gap: 16px;
      align-items: start;
    }
    .benchmark-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .metric-card {
      border-radius: 18px;
      padding: 16px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, #fff, var(--cream));
    }
    .metric-card.teal { border-top: 4px solid var(--teal); }
    .metric-card.amber { border-top: 4px solid var(--amber); }
    .metric-card.blue { border-top: 4px solid var(--blue); }
    .metric-card.navy { border-top: 4px solid var(--navy); }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }
    .metric-value {
      font-size: 42px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: -0.03em;
      margin-bottom: 10px;
    }
    .metric-detail {
      font-size: 14px;
      color: #334155;
      line-height: 1.55;
    }
    .callout {
      border-radius: 18px;
      padding: 16px;
      background: #fff8e8;
      border: 1px solid #efdcb1;
      color: #5d4622;
      margin-top: 14px;
    }
    .callout strong {
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .cohort-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .cohort-card {
      border-radius: 18px;
      padding: 16px;
      background: linear-gradient(180deg, #fff, #fbfaf7);
      border: 1px solid var(--border);
    }
    .cohort-card .name {
      font-size: 16px;
      font-weight: 800;
      margin-bottom: 8px;
    }
    .cohort-card .stat {
      color: #334155;
      font-size: 13px;
      line-height: 1.6;
    }
    .cohort-card .meter {
      margin-top: 10px;
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: #ede7de;
    }
    .cohort-card .fill {
      height: 100%;
      border-radius: 999px;
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
    .path-box {
      margin-top: 14px;
      padding: 14px 16px;
      border-radius: 16px;
      background: #f6f7fb;
      border: 1px solid #dbe1ea;
      font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
      font-size: 12px;
      color: #334155;
      overflow-wrap: anywhere;
    }
    .footer {
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      padding: 4px 18px 26px;
    }
    @media (max-width: 980px) {
      .grid-main {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 640px) {
      .hero-inner {
        padding: 24px 18px 20px;
      }
      .benchmark-grid {
        grid-template-columns: 1fr;
      }
      .metric-value {
        font-size: 34px;
      }
    }
  </style>
</head>
<body>
  <div class="hero">
    <div class="hero-inner">
      <div class="eyebrow">Kollam ONCourts · Transition Analysis</div>
      <h1 id="hero-title"></h1>
      <div class="hero-subtitle" id="hero-subtitle"></div>
      <div class="pill-row" id="hero-pills"></div>
    </div>
  </div>

  <div class="wrap">
    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Transition Chart</div>
          <div class="section-subtitle" id="chart-subtitle"></div>
        </div>
        <div class="toggle-bar">
          <button class="toggle-btn active" data-mode="resolved">Cases Resolved</button>
          <button class="toggle-btn" data-mode="pending">Cases Still Pending</button>
        </div>
      </div>
      <div class="grid-main">
        <div>
          <div id="plot-km" style="height: 560px;"></div>
        </div>
        <div>
          <div class="benchmark-grid" id="benchmark-grid"></div>
          <div class="callout">
            <strong>Read This Carefully</strong>
            Kerala 2025+ is slower than Kerala 2023-2024, so the page is showing benchmark context and transition evidence, not a clean causal treatment effect.
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Five Cohorts, One Frame</div>
          <div class="section-subtitle">The point is to keep the story simple: old Kollam, old Kerala, ONCourts, rest of Kollam, and current Kerala.</div>
        </div>
      </div>
      <div class="cohort-grid" id="cohort-grid"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Resolution Milestones</div>
          <div class="section-subtitle">These are descriptive timing checkpoints, not regression-adjusted effects.</div>
        </div>
      </div>
      <div class="table-wrap"><table id="milestone-table"></table></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">KM Medians</div>
          <div class="section-subtitle">Where a cohort has not yet crossed 50% resolved, the page shows the projected median rather than pretending the actual KM median is observed.</div>
        </div>
      </div>
      <div class="table-wrap"><table id="median-table"></table></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Why This Is Not Yet DiD</div>
          <div class="section-subtitle">This is the core identification warning behind the redesign.</div>
        </div>
      </div>
      <ul class="notes" id="notes"></ul>
      <div class="path-box" id="plan-path"></div>
    </div>
  </div>

  <div class="footer">PUCAR · Kollam ONCourts · Transition evidence before formal DiD</div>

  <script>
    const payload = __PAYLOAD_JSON__;
    const FONT = "'Mulish', 'Calibri', sans-serif";
    let chartMode = "resolved";

    function fmtPct(value) {
      return `${Number(value).toFixed(1)}%`;
    }

    function fmtMetric(value, digits = 1) {
      if (value === "" || value === null || value === undefined || value === "NR") return "NR";
      return Number(value).toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits
      });
    }

    function renderHero() {
      document.getElementById("hero-title").textContent = payload.hero.title;
      document.getElementById("hero-subtitle").textContent = payload.hero.subtitle;
      const pills = document.getElementById("hero-pills");
      pills.innerHTML = "";
      payload.hero.pills.forEach((text) => {
        const node = document.createElement("div");
        node.className = "hero-pill";
        node.textContent = text;
        pills.appendChild(node);
      });
      document.getElementById("chart-subtitle").textContent = payload.chart.subtitle;
    }

    function renderBenchmarks() {
      const root = document.getElementById("benchmark-grid");
      root.innerHTML = "";
      payload.benchmark_cards.forEach((item) => {
        const node = document.createElement("div");
        node.className = `metric-card ${item.tone}`;
        node.innerHTML = `<div class="metric-label">${item.label}</div><div class="metric-value">${item.value}</div><div class="metric-detail">${item.detail}</div>`;
        root.appendChild(node);
      });
    }

    function renderChart() {
      const resolvedMode = chartMode === "resolved";
      const traces = [];
      payload.chart.primary_series.forEach((item) => {
        traces.push({
          type: "scatter",
          mode: "lines",
          name: item.label,
          x: item.x_days,
          y: resolvedMode ? item.y_survival.map((value) => 1 - value) : item.y_survival,
          line: {
            width: item.dataset === "Kerala 2025+" ? 3.4 : 4,
            color: item.color,
            shape: "spline",
            smoothing: 0.42
          },
          hovertemplate: `<b>${item.label}</b><br>Day %{x}<br>${resolvedMode ? "Resolved" : "Still pending"}: %{y:.1%}<extra></extra>`
        });
      });
      payload.chart.overlay_series.forEach((item) => {
        traces.push({
          type: "scatter",
          mode: "lines",
          name: `${item.label} overlay`,
          x: item.x_days,
          y: resolvedMode ? item.y_survival.map((value) => 1 - value) : item.y_survival,
          line: {
            width: 2.4,
            color: item.color,
            dash: "dash",
            shape: "spline",
            smoothing: 0.42
          },
          opacity: 0.78,
          hovertemplate: `<b>${item.label} overlay</b><br>Day %{x}<br>${resolvedMode ? "Resolved" : "Still pending"}: %{y:.1%}<extra></extra>`
        });
      });
      const maxX = Math.max(1, ...traces.flatMap((trace) => trace.x || [0]));
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
          range: [-0.02, 1.02],
          gridcolor: "#ECECEC",
          linecolor: "#D9D9D9",
          tickfont: { size: 11, color: "#5A5A5A" },
          tickformat: ".0%",
          zeroline: false
        },
        margin: { l: 72, r: 18, t: 10, b: 64 },
        plot_bgcolor: "#FFFFFF",
        paper_bgcolor: "#FFFFFF",
        legend: { orientation: "h", y: -0.2, x: 0, font: { size: 11 } },
        shapes: [{
          type: "line",
          x0: 0,
          x1: maxX,
          y0: 0.5,
          y1: 0.5,
          line: { dash: "dot", color: "#E0E0E0", width: 1 }
        }],
        annotations: [{
          x: maxX - 8,
          y: 0.505,
          text: "Halfway point",
          showarrow: false,
          font: { size: 10, color: "#999" },
          xanchor: "right"
        }],
        hovermode: "x unified"
      }, { responsive: true, displaylogo: false, displayModeBar: false });
    }

    function renderCohorts() {
      const root = document.getElementById("cohort-grid");
      root.innerHTML = "";
      payload.cohort_rows.forEach((item) => {
        const pctResolved = item.cases > 0 ? (item.events / item.cases) * 100 : 0;
        const node = document.createElement("div");
        node.className = "cohort-card";
        node.innerHTML = `
          <div class="name" style="color:${item.color}">${item.dataset}</div>
          <div class="stat">
            <strong>${Number(item.cases).toLocaleString()}</strong> modeled cases<br>
            <strong>${Number(item.events).toLocaleString()}</strong> resolved · ${Number(item.censored).toLocaleString()} pending<br>
            365-day resolved: <strong>${fmtPct(item.resolved_365)}</strong><br>
            Median: <strong>${item.median_days === "" ? "NR" : `${fmtMetric(item.median_days, 1)} days`}</strong> (${item.median_kind})
          </div>
          <div class="meter"><div class="fill" style="width:${pctResolved.toFixed(1)}%; background:${item.color}"></div></div>
        `;
        root.appendChild(node);
      });
    }

    function renderMilestoneTable() {
      const table = document.getElementById("milestone-table");
      table.innerHTML = "";
      const headers = ["Day", "Kollam 2023-2024", "Kerala 2023-2024", "ONCourts 2025+", "Rest of Kollam 2025+", "Kerala 2025+"];
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      headers.forEach((label) => {
        const th = document.createElement("th");
        th.textContent = label;
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      payload.milestone_rows.forEach((row) => {
        const tr = document.createElement("tr");
        const values = [
          row.day,
          row["Kollam 2023-2024"],
          row["Kerala 2023-2024"],
          row["ONCourts 2025+"],
          row["Rest of Kollam 2025+"],
          row["Kerala 2025+"],
        ];
        values.forEach((value, index) => {
          const td = document.createElement("td");
          if (index > 0) td.className = "num";
          td.textContent = index === 0 ? String(value) : fmtPct(value);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
    }

    function renderMedianTable() {
      const table = document.getElementById("median-table");
      table.innerHTML = "";
      const headers = ["Dataset", "Period", "Cases", "Resolved", "365-day resolved", "Median type", "Median days"];
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      headers.forEach((label) => {
        const th = document.createElement("th");
        th.textContent = label;
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      payload.cohort_rows.forEach((row) => {
        const tr = document.createElement("tr");
        const values = [
          row.dataset,
          row.period,
          row.cases,
          row.events,
          row.resolved_365,
          row.median_kind,
          row.median_days,
        ];
        values.forEach((value, index) => {
          const td = document.createElement("td");
          if (index >= 2 && index !== 5) td.className = "num";
          if (index === 4) td.textContent = fmtPct(value);
          else if (index === 6) td.textContent = value === "" ? "NR" : `${fmtMetric(value, 1)} d`;
          else if (typeof value === "number") td.textContent = fmtMetric(value, 0);
          else td.textContent = value;
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
      document.getElementById("plan-path").textContent = payload.did_plan_path;
    }

    function renderAll() {
      renderHero();
      renderBenchmarks();
      renderChart();
      renderCohorts();
      renderMilestoneTable();
      renderMedianTable();
      renderNotes();
      document.querySelectorAll(".toggle-btn").forEach((button) => {
        button.classList.toggle("active", button.dataset.mode === chartMode);
      });
    }

    document.querySelectorAll(".toggle-btn").forEach((button) => {
      button.addEventListener("click", () => {
        chartMode = button.dataset.mode;
        renderAll();
      });
    });

    renderAll();
  </script>
</body>
</html>
"""
    return html.replace("__PAYLOAD_JSON__", payload_json)


def main() -> int:
    args = _parse_args()
    source_payload_json = Path(args.source_payload_json).resolve()
    analysis_dir = Path(args.analysis_dir).resolve()
    data_dir = Path(args.data_dir).resolve()
    output_html = analysis_dir / args.output_name
    payload_json = data_dir / args.payload_json_name
    manifest_json = data_dir / args.manifest_name

    analysis_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    source_payload = json.loads(source_payload_json.read_text(encoding="utf-8"))
    payload = _build_payload(
        source_payload,
        source_payload_path=source_payload_json,
        did_plan_path=str(Path(args.did_plan_path).resolve()),
    )

    payload_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_html.write_text(_build_html(payload), encoding="utf-8")

    manifest = {
        "built_at_utc": payload["built_at_utc"],
        "source_payload_json": str(source_payload_json),
        "payload_json": str(payload_json),
        "output_html": str(output_html),
        "did_plan_path": str(Path(args.did_plan_path).resolve()),
    }
    manifest_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
