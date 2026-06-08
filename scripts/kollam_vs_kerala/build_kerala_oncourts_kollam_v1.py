from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


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
        description="Promote the final Kerala ONCourts/Kollam analysis into stable data/ and analysis/ paths."
    )
    parser.add_argument(
        "--run-root",
        default="output/runs/kerala_ni138_phase6_combined_20260309",
        help="Source run root containing refreshed analysis artifacts.",
    )
    parser.add_argument(
        "--data-dir",
        default="data/kerala_oncourts_kollam_v1",
        help="Stable data directory for promoted final artifacts.",
    )
    parser.add_argument(
        "--analysis-dir",
        default="analysis",
        help="Stable analysis directory for generated final HTML.",
    )
    parser.add_argument(
        "--template-name",
        default="kerala_oncourts_kollam_template.html",
        help="Filename to use for the stable HTML template copy.",
    )
    parser.add_argument(
        "--output-name",
        default="kerala_oncourts_kollam_v1.html",
        help="Filename to use for the stable generated HTML.",
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


def _replace_payload_only(*, source_html: Path, toggle_json: Path, out_html: Path) -> dict[str, str]:
    html = source_html.read_text(encoding="utf-8")
    payload = json.loads(toggle_json.read_text(encoding="utf-8"))
    payload_json = json.dumps(payload, ensure_ascii=False)
    pattern = re.compile(
        r"(// ══════════════════════════════════════════\s*"
        r"//  DATA PAYLOAD \(from v1\)\s*"
        r"// ══════════════════════════════════════════\s*"
        r"const payload = )\{[\s\S]*?\};",
        re.MULTILINE,
    )
    new_html, n = pattern.subn(rf"\1{payload_json};", html, count=1)
    if n != 1:
        raise RuntimeError("Could not find the template payload block to replace.")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(new_html, encoding="utf-8")
    return {
        "source_html": str(source_html),
        "toggle_json": str(toggle_json),
        "out_html": str(out_html),
        "mode": "payload_only",
    }


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    source_analysis_dir = run_root / "analysis"
    data_dir = Path(args.data_dir).resolve()
    analysis_dir = Path(args.analysis_dir).resolve()
    templates_dir = analysis_dir / "templates"
    template_out = templates_dir / args.template_name
    html_out = analysis_dir / args.output_name

    data_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    copied_data = _copy_required_files(source_analysis_dir=source_analysis_dir, data_dir=data_dir)

    template_src = source_analysis_dir / "kollam_vs_kerala_v2.html"
    if not template_src.exists():
        raise FileNotFoundError(f"Missing source template HTML: {template_src}")
    shutil.copy2(template_src, template_out)

    apply_result = _replace_payload_only(
        source_html=template_out,
        toggle_json=data_dir / "toggle_data.json",
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
        "html_generation": apply_result,
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"manifest": str(manifest_path), "output_html": str(html_out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
