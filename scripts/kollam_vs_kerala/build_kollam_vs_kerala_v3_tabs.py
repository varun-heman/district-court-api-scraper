from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apply_toggle_data_to_pucar import build_external_toggle_data_script


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a single 4-tab HTML for the Kollam vs Kerala start-date variants."
    )
    parser.add_argument(
        "--variants-json",
        required=True,
        help="Path to kollam_vs_kerala_v3_variants.json",
    )
    parser.add_argument(
        "--analysis-dir",
        default="analysis",
        help="Analysis directory for the output HTML.",
    )
    parser.add_argument(
        "--data-dir",
        default="data/kollam_vs_kerala_v3_tabs",
        help="Consolidated data directory for the tabbed HTML payload and exports.",
    )
    parser.add_argument(
        "--output-name",
        default="kollam_vs_kerala_v3_tabs.html",
        help="Output HTML filename.",
    )
    parser.add_argument(
        "--data-script-name",
        default="kollam_vs_kerala_v3_tabs.data.js",
        help="External data bundle filename.",
    )
    parser.add_argument(
        "--payload-json-name",
        default="kollam_vs_kerala_v3_tabs.json",
        help="Combined JSON payload filename.",
    )
    parser.add_argument(
        "--payload-variable",
        default="KOLLAM_VS_KERALA_V3_TABS_DATA",
        help="Window variable exposed by the external data bundle.",
    )
    parser.add_argument(
        "--integrated-output-name",
        default="",
        help="Optional single-file HTML output with embedded data.",
    )
    return parser.parse_args()


def _load_variants(variants_json: Path) -> list[dict[str, Any]]:
    payload = json.loads(variants_json.read_text(encoding="utf-8"))
    variants = payload.get("variants") or []
    loaded: list[dict[str, Any]] = []
    for variant in variants:
        data_dir = Path(str(variant["data_dir"])).resolve()
        toggle_data = json.loads((data_dir / "toggle_data.json").read_text(encoding="utf-8"))
        loaded.append(
            {
                "slug": variant["slug"],
                "label": variant["label"],
                "start_date": variant["start_date"],
                "data_dir": str(data_dir),
                "payload": toggle_data,
                "km_case_rows_csv": str(data_dir / "km_case_rows.csv"),
                "toggle_summary_csv": str(data_dir / "toggle_summary.csv"),
            }
        )
    return loaded


def _write_combined_csv(
    *,
    variants: list[dict[str, Any]],
    source_key: str,
    out_path: Path,
    extra_fields: list[str],
) -> None:
    rows_out: list[dict[str, str]] = []
    fieldnames: list[str] = []
    for variant in variants:
        source_path = Path(str(variant[source_key]))
        with source_path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                for name in [*extra_fields, *reader.fieldnames]:
                    if name not in fieldnames:
                        fieldnames.append(name)
            for row in reader:
                row_out = {
                    "variant_slug": variant["slug"],
                    "variant_label": variant["label"],
                    **row,
                }
                rows_out.append(row_out)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows_out:
            writer.writerow(row)


def _build_html(*, variable_name: str, data_loader_html: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kollam vs Kerala NI 138 Resolution Tracker</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #f6f4ef;
      --card: #ffffff;
      --ink: #1f2937;
      --muted: #64748b;
      --border: #e7e5df;
      --teal: #0d7c66;
      --amber: #e8973a;
      --slate: #8b95a2;
      --rose: #cb6f5d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at 12% 12%, rgba(13,124,102,0.14) 0, transparent 34%),
        radial-gradient(circle at 90% 0%, rgba(232,151,58,0.16) 0, transparent 30%),
        var(--bg);
      font-family: "Mulish", "Segoe UI", sans-serif;
      color: var(--ink);
    }}
    .hero {{
      padding: 40px 18px 24px;
    }}
    .hero-inner {{
      max-width: 1200px;
      margin: 0 auto;
      background: linear-gradient(135deg, rgba(13,124,102,0.96), rgba(23,44,68,0.96));
      color: #fff;
      border-radius: 24px;
      padding: 28px 28px 24px;
      box-shadow: 0 18px 40px rgba(15, 23, 42, 0.14);
    }}
    .eyebrow {{
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      opacity: 0.78;
      margin-bottom: 10px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(30px, 4vw, 48px);
      line-height: 1.02;
    }}
    .subtitle {{
      margin-top: 12px;
      max-width: 820px;
      font-size: 16px;
      line-height: 1.5;
      opacity: 0.92;
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto 36px;
      padding: 0 18px 36px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: end;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .section-title {{
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }}
    .section-subtitle {{
      color: var(--muted);
      font-size: 14px;
    }}
    .tab-bar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .hero-switch-label {{
      margin-top: 18px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      opacity: 0.78;
    }}
    .tab-btn, .toggle-btn {{
      appearance: none;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
      cursor: pointer;
      transition: 160ms ease;
    }}
    .tab-btn.active, .toggle-btn.active {{
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
    }}
    .tab-btn:hover, .toggle-btn:hover {{
      transform: translateY(-1px);
    }}
    .legend-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #fff;
      cursor: pointer;
    }}
    .pill input {{
      margin: 0;
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }}
    .toggle-bar {{
      display: inline-flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .scoreboard-panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 22px 24px 18px;
      margin-bottom: 18px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
    }}
    .sb-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 6px;
    }}
    .sb-day-label {{
      font-size: 32px;
      font-weight: 800;
      color: var(--teal);
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .sb-day-label .unit {{
      font-size: 14px;
      font-weight: 700;
      color: var(--muted);
      margin-left: 4px;
      letter-spacing: 0;
    }}
    .sb-play-btn {{
      width: 36px;
      height: 36px;
      border-radius: 50%;
      border: 2px solid var(--teal);
      background: none;
      color: var(--teal);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      transition: 0.15s ease;
    }}
    .sb-play-btn:hover {{
      background: var(--teal);
      color: #fff;
    }}
    .sb-timeline {{
      position: relative;
      margin: 16px 0 22px;
      height: 40px;
    }}
    .sb-track {{
      position: absolute;
      top: 16px;
      left: 0;
      right: 0;
      height: 6px;
      background: #ececec;
      border-radius: 3px;
    }}
    .sb-track-fill {{
      height: 100%;
      background: var(--teal);
      border-radius: 3px;
      transition: width 60ms linear;
    }}
    .sb-thumb {{
      position: absolute;
      top: 8px;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: var(--teal);
      border: 3px solid #fff;
      box-shadow: 0 1px 6px rgba(0,0,0,0.25);
      transform: translateX(-50%);
      cursor: grab;
      z-index: 2;
      transition: left 60ms linear;
    }}
    .sb-thumb:active {{
      cursor: grabbing;
      transform: translateX(-50%) scale(1.15);
    }}
    .sb-snap-dots {{
      position: absolute;
      top: 14px;
      left: 0;
      right: 0;
      height: 10px;
      display: flex;
      justify-content: space-between;
      pointer-events: none;
    }}
    .sb-snap-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #d9d9d9;
      border: 2px solid #fff;
      pointer-events: all;
      cursor: pointer;
      transition: background 0.2s;
    }}
    .sb-snap-dot.active {{
      background: var(--teal);
    }}
    .sb-snap-labels {{
      position: relative;
      height: 18px;
    }}
    .sb-snap-label {{
      font-size: 10px;
      font-weight: 700;
      color: #8c97a5;
      text-align: center;
      width: 60px;
      cursor: pointer;
      letter-spacing: 0.03em;
    }}
    .sb-snap-label.active {{
      color: var(--teal);
    }}
    .sb-rows {{
      margin-top: 4px;
    }}
    .score-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .score-row .rank {{
      font-size: 11px;
      font-weight: 800;
      color: #8c97a5;
      width: 18px;
      text-align: center;
      flex-shrink: 0;
    }}
    .score-row .rank.top {{
      color: var(--teal);
    }}
    .score-row .name {{
      font-size: 12.5px;
      font-weight: 700;
      color: var(--ink);
      width: 160px;
      flex-shrink: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .score-row .bar-bg {{
      flex: 1;
      height: 16px;
      background: #ececec;
      border-radius: 8px;
      overflow: hidden;
    }}
    .score-row .bar-fill {{
      height: 100%;
      border-radius: 8px;
      transition: width 0.3s ease;
    }}
    .score-row .pct {{
      font-size: 13px;
      font-weight: 800;
      width: 52px;
      text-align: right;
    }}
    .dataset-cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .ds-card {{
      border: 1px solid var(--border);
      border-left: 4px solid var(--teal);
      border-radius: 16px;
      padding: 14px;
      background: linear-gradient(180deg, #fff, #fbfaf7);
    }}
    .ds-name {{
      font-size: 16px;
      font-weight: 800;
      margin-bottom: 8px;
    }}
    .ds-stat {{
      font-size: 13px;
      line-height: 1.55;
      color: #334155;
    }}
    .ds-bar-wrap {{
      margin-top: 10px;
    }}
    .ds-bar-bg {{
      height: 10px;
      background: #ede9e1;
      border-radius: 999px;
      overflow: hidden;
    }}
    .ds-bar-fill {{
      height: 100%;
      border-radius: 999px;
    }}
    .ds-bar-label {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    td.num {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .nr-badge {{
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      background: #f1f5f9;
      color: #475569;
      font-size: 12px;
    }}
    .methodology {{
      margin: 0;
      color: #334155;
      line-height: 1.6;
    }}
    .methodology p {{
      margin: 0 0 8px;
    }}
    .page-footer {{
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      padding: 0 18px 28px;
    }}
    @media (max-width: 720px) {{
      .hero-inner {{
        padding: 22px 18px;
      }}
      .card {{
        padding: 16px;
      }}
    }}
  </style>
</head>
<body>
  <div class="hero">
    <div class="hero-inner">
      <div class="eyebrow">Case Resolution Tracker · NI 138 Cheque-Bounce Cases</div>
      <h1>Kollam vs Kerala: NI 138 case resolution by filing window.</h1>
      <div class="subtitle" id="hero-subtitle"></div>
      <div class="hero-switch-label">Start Date</div>
      <div class="tab-bar" id="tab-bar"></div>
    </div>
  </div>

  <div class="wrap">
    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Select Datasets</div>
          <div class="section-subtitle">Choose which series to display for the active filing window.</div>
        </div>
      </div>
      <div class="legend-pills" id="legend-pills"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title" id="km-title">How fast are cases being resolved?</div>
          <div class="section-subtitle" id="km-subtitle">Each line tracks the cumulative percentage of cases resolved.</div>
        </div>
      </div>
      <div class="toggle-bar" id="km-toggle">
        <button class="toggle-btn active" data-mode="resolved">Cases Resolved</button>
        <button class="toggle-btn" data-mode="pending">Cases Still Pending</button>
      </div>
      <div id="plot-km" style="height: 520px;"></div>
    </div>

    <div class="section-head">
      <div>
        <div class="section-title">The Scoreboard</div>
        <div class="section-subtitle">Drag the slider or press play to watch the ranking change over time.</div>
      </div>
    </div>
    <div class="scoreboard-panel" id="scoreboard">
      <div class="sb-header">
        <div class="sb-day-label"><span id="sb-day-num">90</span><span class="unit">days</span></div>
        <button class="sb-play-btn" id="sb-play" title="Play animation">&#9654;</button>
      </div>
      <div class="sb-timeline" id="sb-timeline">
        <div class="sb-track"><div class="sb-track-fill" id="sb-fill"></div></div>
        <div class="sb-thumb" id="sb-thumb"></div>
        <div class="sb-snap-dots" id="sb-dots"></div>
      </div>
      <div class="sb-snap-labels" id="sb-labels"></div>
      <div class="sb-rows" id="sb-rows"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Resolved Case Duration</div>
          <div class="section-subtitle">Switch between coarse buckets and the detailed histogram.</div>
        </div>
      </div>
      <div class="toggle-bar" id="hist-toggle">
        <button class="toggle-btn active" data-mode="grouped">By Time Period</button>
        <button class="toggle-btn" data-mode="detailed">Detailed Distribution</button>
      </div>
      <div id="plot-hist" style="height: 420px;"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Dataset Summaries</div>
          <div class="section-subtitle">Observed completions are after excluding transfer / made-over outcomes from the scraped Kerala side.</div>
        </div>
      </div>
      <div class="dataset-cards" id="dataset-cards"></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">Detailed Statistics</div>
          <div class="section-subtitle">Metrics for the active filing-window tab.</div>
        </div>
      </div>
      <div class="table-wrap"><table id="summary"></table></div>
    </div>

    <div class="card">
      <div class="section-head">
        <div>
          <div class="section-title">How We Measured This</div>
          <div class="section-subtitle">Filters and interpretation for the combined start-date view.</div>
        </div>
      </div>
      <div class="methodology" id="methodology"></div>
    </div>
  </div>

  <div class="page-footer">PUCAR · ONCourts · Kollam vs Kerala · Start-date variants</div>

  {data_loader_html}
  <script>
    const bundle = window.{variable_name};
    const FONT = "'Mulish', 'Calibri', sans-serif";
    const preferredOrder = ["ONCourts", "Rest of Kollam", "Kerala (Combined)"];
    const NAMES = {{
      "ONCourts": "ONCourts Kollam (Digital)",
      "Rest of Kollam": "Kollam (Traditional)",
      "Kerala (Combined)": "Kerala Overall"
    }};
    const baseColors = {{
      "ONCourts": "#0D7C66",
      "Rest of Kollam": "#E8973A",
      "Kerala (Combined)": "#8B95A2"
    }};
    const districtPalette = ["#4C78A8","#F58518","#54A24B","#B279A2","#EECA3B","#72B7B2","#FF9DA6","#9D755D","#BAB0AC","#2E8B57","#7B68EE","#CD5C5C"];

    const selectionByVariant = {{}};
    let currentSlug = bundle.variant_order[0];
    let currentVariant = null;
    let currentPayload = null;
    let allSeries = [];
    let selected = new Set();
    let chartMode = "resolved";
    let histMode = "grouped";
    const SB_MILESTONES = [
      {{ day: 90, label: "3 months" }},
      {{ day: 180, label: "6 months" }},
      {{ day: 270, label: "9 months" }},
      {{ day: 365, label: "12 months" }}
    ];
    const SB_MIN = 30;
    const SB_MAX = 400;
    let sbDay = SB_MILESTONES[0].day;
    let sbPlaying = false;
    let sbAnimFrame = null;
    let scoreboardInitialized = false;
    let scoreboardAutoplayDone = false;

    const friendlyName = (dataset) => NAMES[dataset] || dataset;
    function formatMetric(key, value) {{
      const digits = key.endsWith("_months") ? 1 : 0;
      return Number(value).toLocaleString(undefined, {{
        minimumFractionDigits: digits,
        maximumFractionDigits: digits
      }});
    }}

    function colorMap(series) {{
      const colors = {{}};
      let paletteIndex = 0;
      series.forEach((item) => {{
        colors[item.dataset] = baseColors[item.dataset] || districtPalette[paletteIndex++ % districtPalette.length];
      }});
      return colors;
    }}

    function hydrateVariant(slug) {{
      currentSlug = slug;
      currentVariant = bundle.variants.find((item) => item.slug === slug);
      currentPayload = currentVariant.payload;
      allSeries = currentPayload.series.slice().sort((a, b) => {{
        const ai = preferredOrder.indexOf(a.dataset);
        const bi = preferredOrder.indexOf(b.dataset);
        if (ai !== -1 && bi !== -1) return ai - bi;
        if (ai !== -1) return a.dataset === "Kerala (Combined)" ? 1 : -1;
        if (bi !== -1) return b.dataset === "Kerala (Combined)" ? -1 : 1;
        return a.dataset.localeCompare(b.dataset);
      }});
      const remembered = selectionByVariant[slug];
      if (remembered) {{
        selected = new Set(remembered);
      }} else {{
        selected = new Set(allSeries.filter((item) => item.default_visible).map((item) => item.dataset));
        selectionByVariant[slug] = Array.from(selected);
      }}
    }}

    function visibleSeries() {{
      return allSeries.filter((item) => selected.has(item.dataset));
    }}

    function rememberSelection() {{
      selectionByVariant[currentSlug] = Array.from(selected);
    }}

    function renderTabs() {{
      const root = document.getElementById("tab-bar");
      root.innerHTML = "";
      bundle.variants.forEach((variant) => {{
        const button = document.createElement("button");
        button.className = "tab-btn" + (variant.slug === currentSlug ? " active" : "");
        button.textContent = variant.label;
        button.addEventListener("click", () => {{
          if (variant.slug === currentSlug) return;
          hydrateVariant(variant.slug);
          renderAll();
        }});
        root.appendChild(button);
      }});
    }}

    function renderHero() {{
      const meta = currentPayload.meta || {{}};
      const subtitle = document.getElementById("hero-subtitle");
      subtitle.innerHTML =
        `Comparing Kollam ONCourts against the rest of Kerala for cases filed since <strong>${{currentVariant.label}}</strong>. ` +
        `Data as of <strong>${{meta.censor_date || ""}}</strong>. Use the start-date toggle to switch the filing window.`;
    }}

    function renderLegend() {{
      const colors = colorMap(allSeries);
      const root = document.getElementById("legend-pills");
      root.innerHTML = "";
      allSeries.forEach((series) => {{
        const label = document.createElement("label");
        label.className = "pill";
        label.style.opacity = selected.has(series.dataset) ? "1" : "0.45";
        label.innerHTML = `<input type="checkbox" ${{selected.has(series.dataset) ? "checked" : ""}}><span class="dot" style="background:${{colors[series.dataset]}}"></span>${{friendlyName(series.dataset)}}`;
        label.querySelector("input").addEventListener("change", (event) => {{
          if (event.target.checked) selected.add(series.dataset);
          else selected.delete(series.dataset);
          rememberSelection();
          renderLegend();
          renderKM();
          updateScoreboardDisplay();
          renderHistogram();
          renderDatasetCards();
        }});
        root.appendChild(label);
      }});
    }}

    function interpSurvival(series, day) {{
      const x = series.x_days || [];
      const y = series.y_survival || [];
      if (!x.length) return null;
      if (day <= x[0]) return y[0];
      if (day >= x[x.length - 1]) return y[y.length - 1];
      for (let index = 1; index < x.length; index += 1) {{
        if (x[index] >= day) return y[index - 1];
      }}
      return y[y.length - 1];
    }}

    function sbDayToFrac(day) {{
      return (day - SB_MIN) / (SB_MAX - SB_MIN);
    }}

    function sbFracToDay(frac) {{
      return Math.round(SB_MIN + frac * (SB_MAX - SB_MIN));
    }}

    function initScoreboard() {{
      if (scoreboardInitialized) return;
      scoreboardInitialized = true;

      const dotsEl = document.getElementById("sb-dots");
      const labelsEl = document.getElementById("sb-labels");
      dotsEl.innerHTML = "";
      labelsEl.innerHTML = "";
      SB_MILESTONES.forEach((milestone) => {{
        const dot = document.createElement("div");
        dot.className = "sb-snap-dot";
        dot.style.position = "absolute";
        dot.style.left = (sbDayToFrac(milestone.day) * 100) + "%";
        dot.style.transform = "translateX(-50%)";
        dot.addEventListener("click", () => sbSnapTo(milestone.day));
        dotsEl.appendChild(dot);

        const label = document.createElement("div");
        label.className = "sb-snap-label";
        label.textContent = milestone.label;
        label.style.position = "absolute";
        label.style.left = (sbDayToFrac(milestone.day) * 100) + "%";
        label.style.transform = "translateX(-50%)";
        label.addEventListener("click", () => sbSnapTo(milestone.day));
        labelsEl.appendChild(label);
      }});

      const timeline = document.getElementById("sb-timeline");
      const thumb = document.getElementById("sb-thumb");
      let dragging = false;

      function onDrag(clientX) {{
        const rect = timeline.getBoundingClientRect();
        let frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        sbDay = sbFracToDay(frac);
        updateScoreboardDisplay();
      }}

      thumb.addEventListener("mousedown", (event) => {{
        dragging = true;
        event.preventDefault();
      }});
      timeline.addEventListener("mousedown", (event) => {{
        dragging = true;
        onDrag(event.clientX);
      }});
      document.addEventListener("mousemove", (event) => {{
        if (dragging) onDrag(event.clientX);
      }});
      document.addEventListener("mouseup", () => {{
        if (!dragging) return;
        dragging = false;
        sbSnapToNearest();
      }});

      thumb.addEventListener("touchstart", (event) => {{
        dragging = true;
        event.preventDefault();
      }}, {{ passive: false }});
      timeline.addEventListener("touchstart", (event) => {{
        dragging = true;
        onDrag(event.touches[0].clientX);
      }}, {{ passive: true }});
      document.addEventListener("touchmove", (event) => {{
        if (dragging) onDrag(event.touches[0].clientX);
      }}, {{ passive: true }});
      document.addEventListener("touchend", () => {{
        if (!dragging) return;
        dragging = false;
        sbSnapToNearest();
      }});

      document.getElementById("sb-play").addEventListener("click", sbTogglePlay);
      updateScoreboardDisplay();
    }}

    function sbSnapToNearest() {{
      let closest = SB_MILESTONES[0].day;
      let minDistance = Infinity;
      SB_MILESTONES.forEach((milestone) => {{
        const distance = Math.abs(sbDay - milestone.day);
        if (distance < minDistance) {{
          minDistance = distance;
          closest = milestone.day;
        }}
      }});
      sbDay = closest;
      updateScoreboardDisplay();
    }}

    function sbSnapTo(day) {{
      sbStopPlay();
      sbDay = day;
      updateScoreboardDisplay();
    }}

    function sbTogglePlay() {{
      if (sbPlaying) {{
        sbStopPlay();
        return;
      }}
      sbPlaying = true;
      document.getElementById("sb-play").innerHTML = "&#9646;&#9646;";
      sbDay = SB_MIN;
      updateScoreboardDisplay();
      const startTime = performance.now();
      const totalDuration = 3500;

      function step(now) {{
        if (!sbPlaying) return;
        const elapsed = now - startTime;
        const progress = Math.min(1, elapsed / totalDuration);
        const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
        sbDay = sbFracToDay(eased);
        updateScoreboardDisplay();
        if (progress < 1) {{
          sbAnimFrame = requestAnimationFrame(step);
        }} else {{
          sbDay = SB_MAX;
          updateScoreboardDisplay();
          sbSnapToNearest();
          sbStopPlay();
        }}
      }}

      sbAnimFrame = requestAnimationFrame(step);
    }}

    function sbStopPlay() {{
      sbPlaying = false;
      if (sbAnimFrame) {{
        cancelAnimationFrame(sbAnimFrame);
        sbAnimFrame = null;
      }}
      document.getElementById("sb-play").innerHTML = "&#9654;";
    }}

    function updateScoreboardDisplay() {{
      if (!scoreboardInitialized) return;
      const colors = colorMap(allSeries);
      const frac = sbDayToFrac(sbDay);
      document.getElementById("sb-thumb").style.left = (frac * 100) + "%";
      document.getElementById("sb-fill").style.width = (frac * 100) + "%";
      document.getElementById("sb-day-num").textContent = sbDay;
      document.querySelectorAll(".sb-snap-dot").forEach((dot, index) => {{
        dot.classList.toggle("active", sbDay >= SB_MILESTONES[index].day);
      }});
      document.querySelectorAll(".sb-snap-label").forEach((label, index) => {{
        label.classList.toggle("active", Math.abs(sbDay - SB_MILESTONES[index].day) < 15);
      }});

      const rows = visibleSeries()
        .filter((series) => series.x_days && series.x_days.length > 2)
        .map((series) => {{
          const survival = interpSurvival(series, sbDay);
          const pct = survival !== null ? (1 - survival) : 0;
          return {{ name: friendlyName(series.dataset), pct, color: colors[series.dataset] }};
        }})
        .sort((a, b) => b.pct - a.pct);

      const rowsEl = document.getElementById("sb-rows");
      rowsEl.innerHTML = "";
      rows.forEach((row, index) => {{
        const width = Math.max(0, Math.min(100, row.pct * 100));
        const node = document.createElement("div");
        node.className = "score-row";
        node.innerHTML = `
          <div class="rank ${{index === 0 ? "top" : ""}}">${{index + 1}}</div>
          <div class="name">${{row.name}}</div>
          <div class="bar-bg"><div class="bar-fill" style="width:${{width}}%; background:${{row.color}}"></div></div>
          <div class="pct" style="color:${{row.color}}">${{(row.pct * 100).toFixed(1)}}%</div>
        `;
        rowsEl.appendChild(node);
      }});
    }}

    function renderKM() {{
      const series = visibleSeries();
      const colors = colorMap(allSeries);
      const resolvedMode = chartMode === "resolved";
      const traces = series.map((item) => ({{
        type: "scatter",
        mode: "lines",
        name: friendlyName(item.dataset),
        x: item.x_days,
        y: resolvedMode ? item.y_survival.map((value) => 1 - value) : item.y_survival,
        line: {{
          width: item.dataset === "ONCourts" ? 3.5 : (item.dataset === "Kerala (Combined)" ? 3 : 2.2),
          color: colors[item.dataset],
          shape: "spline",
          smoothing: 0.4,
        }},
        hovertemplate: `<b>%{{fullData.name}}</b><br>Day %{{x}}<br>${{resolvedMode ? "Resolved" : "Still pending"}}: %{{y:.1%}}<extra></extra>`
      }}));
      const maxX = Math.max(1, ...series.flatMap((item) => item.x_days || [0]));
      const oncourts = series.find((item) => item.dataset === "ONCourts");
      const annotations = [];
      if (oncourts && maxX >= 180) {{
        const survival = interpSurvival(oncourts, 180);
        const value = resolvedMode ? (1 - survival) : survival;
        annotations.push({{
          x: 180,
          y: value,
          text: `ONCourts at 6 mo: ${{(value * 100).toFixed(0)}}% ${{resolvedMode ? "resolved" : "pending"}}`,
          showarrow: true,
          arrowhead: 0,
          arrowcolor: colors.ONCourts,
          ax: 64,
          ay: -28,
          font: {{ size: 11, color: colors.ONCourts, family: FONT }},
          bgcolor: "rgba(255,255,255,0.88)",
          bordercolor: colors.ONCourts,
          borderwidth: 1,
          borderpad: 4
        }});
      }}

      document.getElementById("km-title").textContent = resolvedMode
        ? "How fast are cases being resolved?"
        : "How many cases are still pending?";
      document.getElementById("km-subtitle").textContent = resolvedMode
        ? "Each line tracks the cumulative percentage of cases resolved. Steeper means faster."
        : "Each line tracks the percentage of cases still awaiting disposition. Lower means faster.";

      Plotly.react("plot-km", traces, {{
        font: {{ family: FONT, color: "#2D2D2D" }},
        xaxis: {{
          title: {{ text: "Days since filing", font: {{ size: 13, color: "#5A5A5A" }} }},
          gridcolor: "#ECECEC",
          linecolor: "#D9D9D9",
          tickfont: {{ size: 11, color: "#5A5A5A" }},
          zeroline: false
        }},
        yaxis: {{
          title: {{ text: resolvedMode ? "% of cases resolved" : "% of cases still pending", font: {{ size: 13, color: "#5A5A5A" }} }},
          range: resolvedMode ? [-0.02, 0.55] : [0.45, 1.02],
          gridcolor: "#ECECEC",
          linecolor: "#D9D9D9",
          tickfont: {{ size: 11, color: "#5A5A5A" }},
          tickformat: ".0%",
          zeroline: false
        }},
        margin: {{ l: 70, r: 24, t: 12, b: 60 }},
        plot_bgcolor: "#FFFFFF",
        paper_bgcolor: "#FFFFFF",
        showlegend: false,
        shapes: [{{
          type: "line",
          x0: 0,
          x1: maxX,
          y0: 0.5,
          y1: 0.5,
          line: {{ dash: "dot", color: "#E0E0E0", width: 1 }}
        }}],
        annotations: [
          {{
            x: maxX - 10,
            y: 0.505,
            text: "Halfway point",
            showarrow: false,
            font: {{ size: 10, color: "#999" }},
            xanchor: "right"
          }},
          ...annotations
        ],
        hovermode: "x unified"
      }}, {{ responsive: true, displaylogo: false, displayModeBar: false }});
    }}

    function renderHistogram() {{
      const series = visibleSeries();
      const colors = colorMap(allSeries);
      if (histMode === "grouped") {{
        const buckets = [
          {{ label: "0-90 days", min: 0, max: 90 }},
          {{ label: "91-180 days", min: 91, max: 180 }},
          {{ label: "181-270 days", min: 181, max: 270 }},
          {{ label: "271-365 days", min: 271, max: 365 }},
          {{ label: "Over 365 days", min: 366, max: Infinity }}
        ];
        const traces = series.map((item) => ({{
          type: "bar",
          name: friendlyName(item.dataset),
          x: buckets.map((bucket) => bucket.label),
          y: buckets.map((bucket) => (item.completed_durations || []).filter((value) => value >= bucket.min && value <= bucket.max).length),
          marker: {{ color: colors[item.dataset] }},
          hovertemplate: `<b>%{{fullData.name}}</b><br>%{{x}}<br>Count: %{{y}}<extra></extra>`
        }}));
        Plotly.react("plot-hist", traces, {{
          barmode: "group",
          font: {{ family: FONT, color: "#2D2D2D" }},
          xaxis: {{ tickfont: {{ size: 12, color: "#5A5A5A" }}, gridcolor: "#ECECEC" }},
          yaxis: {{
            title: {{ text: "Number of resolved cases", font: {{ size: 13, color: "#5A5A5A" }} }},
            gridcolor: "#ECECEC",
            tickfont: {{ size: 11, color: "#5A5A5A" }},
            zeroline: false
          }},
          margin: {{ l: 60, r: 24, t: 12, b: 60 }},
          plot_bgcolor: "#FFFFFF",
          paper_bgcolor: "#FFFFFF",
          showlegend: false
        }}, {{ responsive: true, displaylogo: false, displayModeBar: false }});
      }} else {{
        const traces = series.map((item) => ({{
          type: "histogram",
          name: friendlyName(item.dataset),
          x: item.completed_durations || [],
          marker: {{ color: colors[item.dataset] }},
          opacity: item.dataset === "Kerala (Combined)" ? 0.28 : 0.62,
          hovertemplate: `<b>%{{fullData.name}}</b><br>%{{x}} days<br>Count: %{{y}}<extra></extra>`
        }}));
        Plotly.react("plot-hist", traces, {{
          barmode: "overlay",
          font: {{ family: FONT, color: "#2D2D2D" }},
          xaxis: {{
            title: {{ text: "Days to resolution", font: {{ size: 13, color: "#5A5A5A" }} }},
            gridcolor: "#ECECEC",
            tickfont: {{ size: 11, color: "#5A5A5A" }},
            zeroline: false
          }},
          yaxis: {{
            title: {{ text: "Number of cases", font: {{ size: 13, color: "#5A5A5A" }} }},
            gridcolor: "#ECECEC",
            tickfont: {{ size: 11, color: "#5A5A5A" }},
            zeroline: false
          }},
          margin: {{ l: 60, r: 24, t: 12, b: 60 }},
          plot_bgcolor: "#FFFFFF",
          paper_bgcolor: "#FFFFFF",
          showlegend: false
        }}, {{ responsive: true, displaylogo: false, displayModeBar: false }});
      }}
    }}

    function renderDatasetCards() {{
      const colors = colorMap(allSeries);
      const root = document.getElementById("dataset-cards");
      root.innerHTML = "";
      visibleSeries().forEach((series) => {{
        const pctResolved = series.modeled_cases > 0 ? (series.events / series.modeled_cases) : 0;
        const medianText = series.completed_event_median_months && series.completed_event_median_months !== "NR"
          ? `${{series.completed_event_median_months}} months (${{series.completed_event_median_days}} days)`
          : "Not yet reached";
        const card = document.createElement("div");
        card.className = "ds-card";
        card.style.borderLeftColor = colors[series.dataset];
        card.innerHTML = `
          <div class="ds-name" style="color:${{colors[series.dataset]}}">${{friendlyName(series.dataset)}}</div>
          <div class="ds-stat">
            <strong>${{series.modeled_cases.toLocaleString()}}</strong> cases tracked<br>
            <strong>${{series.events.toLocaleString()}}</strong> resolved · ${{series.censored.toLocaleString()}} still pending<br>
            Median duration among resolved cases: <strong>${{medianText}}</strong>
          </div>
          <div class="ds-bar-wrap">
            <div class="ds-bar-bg"><div class="ds-bar-fill" style="width:${{(pctResolved * 100).toFixed(1)}}%; background:${{colors[series.dataset]}}"></div></div>
            <div class="ds-bar-label">${{(pctResolved * 100).toFixed(1)}}% resolved</div>
          </div>
        `;
        root.appendChild(card);
      }});
    }}

    function renderTable() {{
      const table = document.getElementById("summary");
      table.innerHTML = "";
      const headers = {{
        dataset: "Dataset",
        modeled_cases: "Cases Tracked",
        events: "Resolved",
        censored: "Still Pending",
        completed_event_median_days: "Median (Resolved, days)",
        completed_event_median_months: "Median (Resolved, months)",
        extrapolated_median_days: "Projected Median (days)",
        extrapolated_median_months: "Projected Median (months)"
      }};
      const keys = Object.keys(headers);
      const thead = document.createElement("thead");
      const hr = document.createElement("tr");
      keys.forEach((key) => {{
        const th = document.createElement("th");
        th.textContent = headers[key];
        hr.appendChild(th);
      }});
      thead.appendChild(hr);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      allSeries.forEach((row) => {{
        const tr = document.createElement("tr");
        keys.forEach((key) => {{
          const td = document.createElement("td");
          if (key === "dataset") {{
            td.textContent = friendlyName(row[key]);
          }} else if (typeof row[key] === "number") {{
            td.className = "num";
            td.textContent = formatMetric(key, row[key]);
          }} else if (row[key] === "" || row[key] === "NR") {{
            td.innerHTML = '<span class="nr-badge">Not yet reached</span>';
          }} else {{
            td.textContent = row[key] ?? "";
          }}
          tr.appendChild(td);
        }});
        tbody.appendChild(tr);
      }});
      table.appendChild(tbody);
    }}

    function renderMethodology() {{
      const root = document.getElementById("methodology");
      const meta = currentPayload.meta || {{}};
      root.innerHTML = `
        <p>We tracked every NI 138 (cheque-bounce) case filed since <strong>${{currentVariant.label}}</strong> across Kerala's district courts.</p>
        <p>On the scraped Kerala side, disposal bucket(s) <strong>${{(meta.excluded_scraped_disposal_groups || []).join(", ") || "none"}}</strong> are excluded before KM modeling. Those rows are administrative exits, not substantive resolutions, so they are neither counted as resolved nor kept as pending.</p>
        <p>ONCourts Kollam remains status-based because the ONCourts lifecycle export does not include disposal-type labels.</p>
        <p>Cases that have not concluded by the as-of date are treated as still pending and are censored in the Kaplan-Meier analysis.</p>
        <p>The scoreboard ranks datasets by cumulative share resolved at the selected day. The projected median uses a constant-hazard extrapolation; the observed median uses completed cases only.</p>
      `;
    }}

    function renderAll() {{
      renderTabs();
      renderHero();
      renderLegend();
      renderKM();
      initScoreboard();
      updateScoreboardDisplay();
      renderHistogram();
      renderDatasetCards();
      renderTable();
      renderMethodology();
      if (!scoreboardAutoplayDone) {{
        scoreboardAutoplayDone = true;
        setTimeout(() => sbTogglePlay(), 1600);
      }}
    }}

    document.getElementById("km-toggle").addEventListener("click", (event) => {{
      const button = event.target.closest(".toggle-btn");
      if (!button) return;
      chartMode = button.dataset.mode;
      document.querySelectorAll("#km-toggle .toggle-btn").forEach((node) => node.classList.toggle("active", node === button));
      renderKM();
    }});

    document.getElementById("hist-toggle").addEventListener("click", (event) => {{
      const button = event.target.closest(".toggle-btn");
      if (!button) return;
      histMode = button.dataset.mode;
      document.querySelectorAll("#hist-toggle .toggle-btn").forEach((node) => node.classList.toggle("active", node === button));
      renderHistogram();
    }});

    hydrateVariant(currentSlug);
    renderAll();
  </script>
</body>
</html>
"""


def main() -> int:
    args = _parse_args()
    variants_json = Path(args.variants_json).resolve()
    analysis_dir = Path(args.analysis_dir).resolve()
    data_dir = Path(args.data_dir).resolve()
    output_html = analysis_dir / args.output_name
    integrated_output_html = analysis_dir / args.integrated_output_name if args.integrated_output_name else None
    payload_json_path = data_dir / args.payload_json_name
    data_script_path = data_dir / args.data_script_name

    variants = _load_variants(variants_json)
    if not variants:
        raise ValueError(f"No variants found in {variants_json}")

    analysis_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    combined_payload = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant_order": [variant["slug"] for variant in variants],
        "filters": {"excluded_scraped_disposal_groups": ["transferred / made over"]},
        "variants": variants,
    }
    payload_json_path.write_text(json.dumps(combined_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    data_script_path.write_text(
        f"window.{args.payload_variable} = {json.dumps(combined_payload, ensure_ascii=False)};\n",
        encoding="utf-8",
    )

    _write_combined_csv(
        variants=variants,
        source_key="km_case_rows_csv",
        out_path=data_dir / "modeled_rows_all_variants.csv",
        extra_fields=["variant_slug", "variant_label"],
    )
    _write_combined_csv(
        variants=variants,
        source_key="toggle_summary_csv",
        out_path=data_dir / "series_summary_all_variants.csv",
        extra_fields=["variant_slug", "variant_label"],
    )

    data_script_src = os.path.relpath(data_script_path, start=analysis_dir)
    output_html.write_text(
        _build_html(
            variable_name=args.payload_variable,
            data_loader_html=f'<script src="{data_script_src}"></script>',
        ),
        encoding="utf-8",
    )
    if integrated_output_html is not None:
        integrated_output_html.write_text(
            _build_html(
                variable_name=args.payload_variable,
                data_loader_html="<script>\n"
                + build_external_toggle_data_script(payload=combined_payload, variable_name=args.payload_variable)
                + "</script>",
            ),
            encoding="utf-8",
        )

    manifest = {
        "built_at_utc": combined_payload["built_at_utc"],
        "variants_json": str(variants_json),
        "output_html": str(output_html),
        "integrated_output_html": str(integrated_output_html) if integrated_output_html is not None else "",
        "payload_json": str(payload_json_path),
        "data_script": str(data_script_path),
        "combined_modeled_rows_csv": str(data_dir / "modeled_rows_all_variants.csv"),
        "combined_series_summary_csv": str(data_dir / "series_summary_all_variants.csv"),
        "variant_order": combined_payload["variant_order"],
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
