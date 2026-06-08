from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from apply_toggle_data_to_pucar import (
    apply_external_toggle_data_reference_to_html_text,
    build_external_toggle_data_script,
)


FINAL_DATA_FILES = {
    "km_case_rows.csv": "km_case_rows.csv",
    "km_medians.csv": "km_medians.csv",
    "km_toggle_data.json": "toggle_data.json",
    "km_toggle_summary.csv": "toggle_summary.csv",
    "km_toggle_rows.csv": "toggle_rows.csv",
    "km_oncourts_restkollam_other_kerala_summary.csv": "oncourts_restkollam_other_kerala_summary.csv",
    "km_oncourts_restkollam_other_kerala_rows.csv": "oncourts_restkollam_other_kerala_rows.csv",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote the final Kollam vs Kerala analysis into stable data/ and analysis/ paths with externalized data."
    )
    parser.add_argument(
        "--run-root",
        default="output/runs/kerala_ni138_phase6_combined_20260309",
        help="Source run root containing refreshed analysis artifacts.",
    )
    parser.add_argument(
        "--source-analysis-dir",
        default="",
        help="Optional analysis directory override containing km_*.csv/json artifacts.",
    )
    parser.add_argument(
        "--source-template-html",
        default="",
        help="Optional source HTML template override. Defaults to <run-root>/analysis/kollam_vs_kerala_v2.html.",
    )
    parser.add_argument(
        "--data-dir",
        default="data/kollam_vs_kerala_v3",
        help="Stable data directory for promoted final artifacts.",
    )
    parser.add_argument(
        "--analysis-dir",
        default="analysis",
        help="Stable analysis directory for generated final HTML.",
    )
    parser.add_argument(
        "--template-name",
        default="kollam_vs_kerala_v3_template.html",
        help="Filename to use for the stable HTML template copy.",
    )
    parser.add_argument(
        "--output-name",
        default="kollam_vs_kerala_v3.html",
        help="Filename to use for the stable generated HTML.",
    )
    parser.add_argument(
        "--data-script-name",
        default="kollam_vs_kerala_v3.data.js",
        help="Filename to use for the external data bundle.",
    )
    parser.add_argument(
        "--payload-variable",
        default="KOLLAM_VS_KERALA_V3_DATA",
        help="Window variable exposed by the external data bundle.",
    )
    return parser.parse_args()


def _copy_required_files(*, source_analysis_dir: Path, data_dir: Path) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for source_name, target_name in FINAL_DATA_FILES.items():
        src = source_analysis_dir / source_name
        if not src.exists():
            raise FileNotFoundError(f"Missing required analysis artifact: {src}")
        dst = data_dir / target_name
        shutil.copy2(src, dst)
        copied.append({"source": str(src), "target": str(dst)})
    return copied


def _write_external_data_bundle(*, payload_json_path: Path, out_path: Path, variable_name: str) -> dict[str, str]:
    payload = json.loads(payload_json_path.read_text(encoding="utf-8"))
    out_path.write_text(
        build_external_toggle_data_script(payload=payload, variable_name=variable_name),
        encoding="utf-8",
    )
    return {
        "payload_json": str(payload_json_path),
        "data_script": str(out_path),
        "variable_name": variable_name,
    }


def _build_html(*, template_html: Path, data_script_src: str, variable_name: str, out_html: Path) -> dict[str, str]:
    source_html_text = template_html.read_text(encoding="utf-8")
    new_html = apply_external_toggle_data_reference_to_html_text(
        source_html_text,
        data_script_src=data_script_src,
        variable_name=variable_name,
    )
    new_html = new_html.replace(
        "Cases filed since <strong>January 2025</strong>. Data as of <strong id=\"hero-date\">10 March 2026</strong>.",
        "Cases filed since <strong id=\"hero-start-date\">January 2025</strong>. Data as of <strong id=\"hero-date\">10 March 2026</strong>.",
    )
    new_html = new_html.replace(
        "NI 138 cases filed since January 2025.",
        'NI 138 cases filed since <span class="start-date-text">January 2025</span>.',
    )
    new_html = new_html.replace(
        "filed since January 2025 across Kerala's district courts.",
        'filed since <span class="start-date-text">January 2025</span> across Kerala\'s district courts.',
    )
    new_html = new_html.replace(
        '  if (payload.meta) document.getElementById("hero-date").textContent = payload.meta.censor_date;\n',
        '  if (payload.meta) {\n'
        '    document.getElementById("hero-date").textContent = payload.meta.censor_date;\n'
        '    const startDateText = payload.meta.min_filing_label || payload.meta.min_filing_date || `January ${payload.meta.min_filing_year}`;\n'
        '    const startDateEl = document.getElementById("hero-start-date");\n'
        '    if (startDateEl) startDateEl.textContent = startDateText;\n'
        '    document.querySelectorAll(".start-date-text").forEach((el) => { el.textContent = startDateText; });\n'
        '  }\n',
    )
    new_html = new_html.replace(
        "<p>We tracked every NI 138 (cheque-bounce) case filed since January 2025 across Kerala's district courts.</p>",
        "<p>We tracked every NI 138 (cheque-bounce) case filed since January 2025 across Kerala's district courts. On the scraped Kerala side, transfer and made-over disposals are excluded before KM modeling because they are administrative exits, not substantive resolutions.</p>",
    )
    out_html.write_text(new_html, encoding="utf-8")
    return {
        "template_html": str(template_html),
        "data_script_src": data_script_src,
        "variable_name": variable_name,
        "output_html": str(out_html),
    }


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    source_analysis_dir = Path(args.source_analysis_dir).resolve() if args.source_analysis_dir else run_root / "analysis"
    data_dir = Path(args.data_dir).resolve()
    analysis_dir = Path(args.analysis_dir).resolve()
    templates_dir = analysis_dir / "templates"
    template_out = templates_dir / args.template_name
    html_out = analysis_dir / args.output_name
    data_script_out = data_dir / args.data_script_name

    data_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    copied_data = _copy_required_files(source_analysis_dir=source_analysis_dir, data_dir=data_dir)

    source_html = (
        Path(args.source_template_html).resolve()
        if args.source_template_html
        else run_root / "analysis" / "kollam_vs_kerala_v2.html"
    )
    if not source_html.exists():
        raise FileNotFoundError(f"Missing source template HTML: {source_html}")
    shutil.copy2(source_html, template_out)

    data_bundle_result = _write_external_data_bundle(
        payload_json_path=data_dir / "toggle_data.json",
        out_path=data_script_out,
        variable_name=args.payload_variable,
    )
    data_script_src = os.path.relpath(data_script_out, start=analysis_dir)
    html_result = _build_html(
        template_html=template_out,
        data_script_src=data_script_src,
        variable_name=args.payload_variable,
        out_html=html_out,
    )

    manifest = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_root": str(run_root),
        "source_analysis_dir": str(source_analysis_dir),
        "data_dir": str(data_dir),
        "analysis_dir": str(analysis_dir),
        "template_html": str(template_out),
        "output_html": str(html_out),
        "copied_data": copied_data,
        "data_bundle": data_bundle_result,
        "html_generation": html_result,
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"manifest": str(manifest_path), "output_html": str(html_out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
