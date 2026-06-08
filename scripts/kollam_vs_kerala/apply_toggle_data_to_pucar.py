from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply km_toggle_data.json to reformatted PUCAR HTML.")
    parser.add_argument("--source-html", required=True, help="Path to reformatted source HTML")
    parser.add_argument("--toggle-json", required=True, help="Path to km_toggle_data.json")
    parser.add_argument("--out-html", required=True, help="Output HTML path")
    return parser.parse_args()


def _build_script(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    return f"""<script>
    const payload = {payload_json};
    const allSeries = Array.isArray(payload.series) ? payload.series.slice() : [];

    // Keep requested cohorts ordered: ONCourts, Rest of Kollam, individual districts, Kerala Combined.
    const preferredOrder = ["ONCourts", "Rest of Kollam", "Kerala (Combined)"];
    allSeries.sort((a, b) => {{
      const ai = preferredOrder.indexOf(a.dataset);
      const bi = preferredOrder.indexOf(b.dataset);
      const aPref = ai !== -1;
      const bPref = bi !== -1;
      if (aPref && bPref) return ai - bi;
      if (aPref && !bPref) return a.dataset === "Kerala (Combined)" ? 1 : -1;
      if (!aPref && bPref) return b.dataset === "Kerala (Combined)" ? -1 : 1;
      return String(a.dataset).localeCompare(String(b.dataset));
    }});

    const baseColors = {{
      "ONCourts": "#0D7C66",
      "Rest of Kollam": "#D94444",
      "Kerala (Combined)": "#5A5A5A",
    }};
    const districtPalette = [
      "#E8973A", "#4C78A8", "#F58518", "#54A24B", "#B279A2",
      "#EECA3B", "#72B7B2", "#FF9DA6", "#9D755D", "#BAB0AC",
      "#2E8B57", "#7B68EE", "#CD5C5C", "#20B2AA", "#FF8C00",
    ];
    const colors = {{}};
    const dash = {{}};
    let p = 0;
    allSeries.forEach((s) => {{
      const name = s.dataset;
      if (baseColors[name]) {{
        colors[name] = baseColors[name];
      }} else {{
        colors[name] = districtPalette[p % districtPalette.length];
        p += 1;
      }}
      if (name === "Kerala (Combined)") {{
        dash[name] = "dot";
      }} else if (name === "ONCourts" || name === "Rest of Kollam") {{
        dash[name] = "solid";
      }} else {{
        dash[name] = "dash";
      }}
    }});

    const selected = new Set(
      allSeries.filter((s) => Boolean(s.default_visible)).map((s) => s.dataset)
    );

    const getVisible = () => allSeries.filter((s) => selected.has(s.dataset));

    function renderKpis() {{
      const kpiRoot = document.getElementById("kpis");
      kpiRoot.innerHTML = "";
      const visible = getVisible();
      const totals = visible.reduce((acc, row) => {{
        acc.modeled += Number(row.modeled_cases || 0);
        acc.events += Number(row.events || 0);
        acc.censored += Number(row.censored || 0);
        return acc;
      }}, {{ modeled: 0, events: 0, censored: 0 }});
      const kpiData = [
        {{ label: "Visible Datasets", value: visible.length, cls: "" }},
        {{ label: "Total Cases Modeled", value: totals.modeled.toLocaleString(), cls: "teal" }},
        {{ label: "Completed Events", value: totals.events.toLocaleString(), cls: "teal" }},
        {{ label: "Censored (Ongoing)", value: totals.censored.toLocaleString(), cls: "red" }},
      ];
      kpiData.forEach((d) => {{
        const el = document.createElement("div");
        el.className = "kpi";
        el.innerHTML = `<div class="label">${{d.label}}</div><div class="value ${{d.cls}}">${{d.value}}</div>`;
        kpiRoot.appendChild(el);
      }});
    }}

    function renderLegend() {{
      const pillRoot = document.getElementById("legend-pills");
      pillRoot.innerHTML = "";
      allSeries.forEach((s) => {{
        const name = s.dataset;
        const checked = selected.has(name);
        const p = document.createElement("label");
        p.className = "pill";
        p.style.cursor = "pointer";
        p.style.userSelect = "none";
        p.style.opacity = checked ? "1" : "0.55";
        p.innerHTML = `
          <input type="checkbox" ${{checked ? "checked" : ""}} style="margin-right:6px; accent-color:${{colors[name]}};">
          <span class="dot" style="background:${{colors[name]}}"></span>
          ${{name}}
        `;
        const cb = p.querySelector("input");
        cb.addEventListener("change", () => {{
          if (cb.checked) selected.add(name);
          else selected.delete(name);
          renderLegend();
          renderKpis();
          renderPlots();
          renderSummary();
        }});
        pillRoot.appendChild(p);
      }});
    }}

    function renderPlots() {{
      const visible = getVisible();
      const traces = visible.map((c) => ({{
        type: "scatter",
        mode: "lines",
        name: c.dataset,
        x: c.x_days,
        y: c.y_survival,
        line: {{
          width: c.dataset === "ONCourts" ? 3.5 : (c.dataset === "Kerala (Combined)" ? 3.0 : 2.3),
          color: colors[c.dataset],
          dash: dash[c.dataset],
          shape: "hv"
        }},
        hovertemplate: "<b>%{{fullData.name}}</b><br>Day %{{x}}<br>S(t) = %{{y:.3f}}<extra></extra>"
      }}));
      const maxX = Math.max(1, ...visible.flatMap((c) => c.x_days || [0]));
      Plotly.react("plot-km", traces, {{
        font: {{ family: "Calibri, Inter, sans-serif", color: "#2D2D2D" }},
        xaxis: {{
          title: {{ text: "Days since filing", font: {{ size: 13, color: "#5A5A5A" }} }},
          gridcolor: "#ECECEC", linecolor: "#D9D9D9",
          tickfont: {{ size: 11, color: "#5A5A5A" }}, zeroline: false
        }},
        yaxis: {{
          title: {{ text: "S(t): proportion not yet disposed", font: {{ size: 13, color: "#5A5A5A" }} }},
          range: [0.48, 1.02], gridcolor: "#ECECEC", linecolor: "#D9D9D9",
          tickfont: {{ size: 11, color: "#5A5A5A" }}, tickformat: ".0%", zeroline: false
        }},
        margin: {{ l: 70, r: 24, t: 12, b: 60 }},
        plot_bgcolor: "#FFFFFF", paper_bgcolor: "#FFFFFF", showlegend: false,
        shapes: [{{
          type: "line", x0: 0, x1: maxX, y0: 0.5, y1: 0.5,
          line: {{ dash: "dot", color: "#D9D9D9", width: 1.5 }}
        }}],
        annotations: [{{
          x: Math.max(maxX - 10, 0), y: 0.505, text: "50% median line",
          showarrow: false, font: {{ size: 10, color: "#999999" }}, xanchor: "right"
        }}],
        hovermode: "x unified"
      }}, {{ responsive: true, displaylogo: false, displayModeBar: false }});

      const histTraces = visible.map((c) => ({{
        type: "histogram",
        name: c.dataset,
        x: c.completed_durations || [],
        marker: {{ color: colors[c.dataset] }},
        opacity: c.dataset === "Kerala (Combined)" ? 0.35 : 0.72,
        hovertemplate: "<b>%{{fullData.name}}</b><br>%{{x}} days<br>Count: %{{y}}<extra></extra>"
      }}));
      Plotly.react("plot-hist", histTraces, {{
        barmode: "overlay",
        font: {{ family: "Calibri, Inter, sans-serif", color: "#2D2D2D" }},
        xaxis: {{
          title: {{ text: "Days to completion", font: {{ size: 13, color: "#5A5A5A" }} }},
          gridcolor: "#ECECEC", linecolor: "#D9D9D9",
          tickfont: {{ size: 11, color: "#5A5A5A" }}, zeroline: false
        }},
        yaxis: {{
          title: {{ text: "Completed case count", font: {{ size: 13, color: "#5A5A5A" }} }},
          gridcolor: "#ECECEC", linecolor: "#D9D9D9",
          tickfont: {{ size: 11, color: "#5A5A5A" }}, zeroline: false
        }},
        margin: {{ l: 60, r: 24, t: 12, b: 60 }},
        plot_bgcolor: "#FFFFFF", paper_bgcolor: "#FFFFFF", showlegend: false
      }}, {{ responsive: true, displaylogo: false, displayModeBar: false }});
    }}

    function renderSummary() {{
      const table = document.getElementById("summary");
      table.innerHTML = "";
      const prettyHeaders = {{
        "dataset": "Dataset",
        "shown": "Shown",
        "modeled_cases": "Cases",
        "events": "Events",
        "censored": "Censored",
        "km_median_months": "KM Median (mo)",
        "completed_event_median_days": "Completed Median (d)",
        "completed_event_median_months": "Completed Median (mo)",
        "extrapolated_median_days": "Extrap. Median (d)",
        "extrapolated_median_months": "Extrap. Median (mo)"
      }};
      const headerKeys = Object.keys(prettyHeaders);
      const thead = document.createElement("thead");
      const hr = document.createElement("tr");
      headerKeys.forEach((h) => {{
        const th = document.createElement("th");
        th.textContent = prettyHeaders[h];
        hr.appendChild(th);
      }});
      thead.appendChild(hr);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      allSeries.forEach((row) => {{
        const tr = document.createElement("tr");
        headerKeys.forEach((h) => {{
          const td = document.createElement("td");
          if (h === "shown") {{
            td.innerHTML = selected.has(row.dataset) ? "Yes" : "No";
            tr.appendChild(td);
            return;
          }}
          let val = row[h] ?? "";
          if (h === "km_median_months" && (val === "NR" || val === "")) {{
            td.innerHTML = '<span class="nr-badge">NR</span>';
          }} else if (h === "km_median_days" && val === "") {{
            td.innerHTML = '<span class="nr-badge">NR</span>';
          }} else if (typeof val === "number") {{
            td.textContent = val.toLocaleString();
          }} else {{
            td.textContent = val;
          }}
          tr.appendChild(td);
        }});
        tbody.appendChild(tr);
      }});
      table.appendChild(tbody);
    }}

    // Update subtitle date from payload meta.
    const subtitle = document.querySelector(".subtitle");
    if (subtitle && payload.meta && payload.meta.censor_date) {{
      const d = payload.meta.censor_date;
      subtitle.innerHTML = `NI 138 Cases &nbsp;·&nbsp; <strong>Filing year ≥ ${{payload.meta.min_filing_year}}</strong> &nbsp;·&nbsp; Censor date: ${{d}} &nbsp;·&nbsp; Toggle-enabled cohorts`;
    }}

    renderLegend();
    renderKpis();
    renderPlots();
    renderSummary();
  </script>"""


def apply_toggle_payload_to_html_text(source_html_text: str, payload: dict) -> str:
    replacement_script = _build_script(payload)
    pattern = re.compile(r"<script>\s*// ═════════[\s\S]*?</script>", re.MULTILINE)
    new_html, n = pattern.subn(replacement_script, source_html_text, count=1)
    if n != 1:
        raise RuntimeError("Could not find inline data/script block to replace.")
    return new_html


def build_external_toggle_data_script(*, payload: dict, variable_name: str) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    return "\n".join(
        [
            "// Auto-generated toggle payload for local-file HTML loading.",
            f"window.{variable_name} = {payload_json};",
            "",
        ]
    )


def apply_external_toggle_data_reference_to_html_text(
    source_html_text: str,
    *,
    data_script_src: str,
    variable_name: str,
) -> str:
    replacement_script = f"""<script src="{data_script_src}"></script>
<script>
// ══════════════════════════════════════════
//  DATA PAYLOAD (external bundle)
// ══════════════════════════════════════════
const payload = window.{variable_name};
if (!payload) {{
  throw new Error("Missing external payload: {variable_name}");
}}
"""
    pattern = re.compile(
        r"<script>\s*"
        r"// ══════════════════════════════════════════\s*"
        r"//  DATA PAYLOAD \(from v1\)\s*"
        r"// ══════════════════════════════════════════\s*"
        r"const payload = \{[\s\S]*?\};",
        re.MULTILINE,
    )
    new_html, n = pattern.subn(replacement_script, source_html_text, count=1)
    if n != 1:
        raise RuntimeError("Could not find inline payload block to replace.")
    return new_html


def apply_toggle_data_to_html(*, source_html: Path, toggle_json: Path, out_html: Path) -> dict[str, str]:
    html = source_html.read_text(encoding="utf-8")
    payload = json.loads(toggle_json.read_text(encoding="utf-8"))
    new_html = apply_toggle_payload_to_html_text(html, payload)

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(new_html, encoding="utf-8")
    return {
        "source_html": str(source_html),
        "toggle_json": str(toggle_json),
        "out_html": str(out_html),
    }


def main() -> int:
    args = _parse_args()
    source_html = Path(args.source_html).resolve()
    toggle_json = Path(args.toggle_json).resolve()
    out_html = Path(args.out_html).resolve()

    result = apply_toggle_data_to_html(source_html=source_html, toggle_json=toggle_json, out_html=out_html)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
