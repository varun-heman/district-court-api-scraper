from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class VariantSpec:
    slug: str
    start_date: str
    label: str


VARIANTS = [
    VariantSpec(slug="jan2025", start_date="2025-01-01", label="January 2025"),
    VariantSpec(slug="apr2025", start_date="2025-04-01", label="April 2025"),
    VariantSpec(slug="jul2025", start_date="2025-07-01", label="July 2025"),
    VariantSpec(slug="oct2025", start_date="2025-10-01", label="October 2025"),
]


def _parse_args() -> tuple[Path, Path, Path]:
    project_root = Path("/Users/siddarth/Documents/Work/xkdr/repository/db-courts/SRC/district-court-api-scraper")
    run_root = project_root / "output" / "runs" / "kerala_ni138_phase6_combined_20260309"
    return project_root, run_root, run_root / "analysis" / "variants"


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    project_root, run_root, variant_root = _parse_args()
    variant_root.mkdir(parents=True, exist_ok=True)

    outputs: list[dict[str, str]] = []
    template_html = run_root / "analysis" / "kollam_vs_kerala_v2.html"

    for variant in VARIANTS:
        variant_analysis_dir = variant_root / variant.slug
        variant_analysis_dir.mkdir(parents=True, exist_ok=True)

        km_case_rows = variant_analysis_dir / "km_case_rows.csv"
        km_medians = variant_analysis_dir / "km_medians.csv"
        km_html = variant_analysis_dir / "km_districts.html"
        toggle_json = variant_analysis_dir / "km_toggle_data.json"
        toggle_summary = variant_analysis_dir / "km_toggle_summary.csv"
        toggle_rows = variant_analysis_dir / "km_toggle_rows.csv"
        compare_html = variant_analysis_dir / "km_oncourts_restkollam_other_kerala.html"
        compare_summary = variant_analysis_dir / "km_oncourts_restkollam_other_kerala_summary.csv"
        compare_rows = variant_analysis_dir / "km_oncourts_restkollam_other_kerala_rows.csv"

        _run(
            [
                sys.executable,
                "scripts/kaplan_meier/km_by_district.py",
                "--run-root",
                str(run_root),
                "--censor-date",
                "2026-03-10",
                "--min-filing-year",
                "2025",
                "--min-filing-date",
                variant.start_date,
                "--include-case-prefixes",
                "CC,ST",
                "--out-html",
                str(km_html),
                "--out-medians-csv",
                str(km_medians),
                "--out-cases-csv",
                str(km_case_rows),
            ],
            cwd=project_root,
        )
        _run(
            [
                sys.executable,
                "scripts/kaplan_meier/km_compute_toggle_data.py",
                "--oncourts-csv",
                "data/kollam-lifecycle-kaplanmeier.csv",
                "--scraped-km-csv",
                str(km_case_rows),
                "--kollam-name",
                "Kollam",
                "--min-filing-year",
                "2025",
                "--min-filing-date",
                variant.start_date,
                "--censor-date",
                "2026-03-10",
                "--out-json",
                str(toggle_json),
                "--out-summary-csv",
                str(toggle_summary),
                "--out-rows-csv",
                str(toggle_rows),
            ],
            cwd=project_root,
        )
        _run(
            [
                sys.executable,
                "scripts/kaplan_meier/km_superimpose_oncourts_kollam.py",
                "--oncourts-csv",
                "data/kollam-lifecycle-kaplanmeier.csv",
                "--scraped-km-csv",
                str(km_case_rows),
                "--district",
                "Kollam",
                "--min-filing-year",
                "2025",
                "--min-filing-date",
                variant.start_date,
                "--censor-date",
                "2026-03-10",
                "--out-html",
                str(compare_html),
                "--out-summary-csv",
                str(compare_summary),
                "--out-rows-csv",
                str(compare_rows),
            ],
            cwd=project_root,
        )

        data_dir = project_root / "data" / f"kollam_vs_kerala_v3_{variant.slug}"
        output_name = f"kollam_vs_kerala_v3_{variant.slug}.html"
        template_name = f"kollam_vs_kerala_v3_{variant.slug}_template.html"
        data_script_name = f"kollam_vs_kerala_v3_{variant.slug}.data.js"
        _run(
            [
                sys.executable,
                "scripts/kollam_vs_kerala/build_kollam_vs_kerala_v3.py",
                "--run-root",
                str(run_root),
                "--source-analysis-dir",
                str(variant_analysis_dir),
                "--source-template-html",
                str(template_html),
                "--data-dir",
                str(data_dir),
                "--analysis-dir",
                str(project_root / "analysis"),
                "--template-name",
                template_name,
                "--output-name",
                output_name,
                "--data-script-name",
                data_script_name,
                "--payload-variable",
                f"KOLLAM_VS_KERALA_V3_{variant.slug.upper()}_DATA",
            ],
            cwd=project_root,
        )

        outputs.append(
            {
                "slug": variant.slug,
                "label": variant.label,
                "start_date": variant.start_date,
                "variant_analysis_dir": str(variant_analysis_dir),
                "data_dir": str(data_dir),
                "html": str(project_root / "analysis" / output_name),
            }
        )

    summary_path = project_root / "analysis" / "kollam_vs_kerala_v3_variants.json"
    summary_payload = {
        "run_root": str(run_root),
        "variants": [dict(item) for item in outputs],
        "variant_specs": [asdict(v) for v in VARIANTS],
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")

    _run(
        [
            sys.executable,
            "scripts/kollam_vs_kerala/build_kollam_vs_kerala_v3_tabs.py",
            "--variants-json",
            str(summary_path),
            "--analysis-dir",
            str(project_root / "analysis"),
            "--data-dir",
            str(project_root / "data" / "kollam_vs_kerala_v3_tabs"),
        ],
        cwd=project_root,
    )

    summary_payload["combined_tabs"] = {
        "data_dir": str(project_root / "data" / "kollam_vs_kerala_v3_tabs"),
        "html": str(project_root / "analysis" / "kollam_vs_kerala_v3_tabs.html"),
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(summary_path), "variants": outputs, "combined_tabs": summary_payload["combined_tabs"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
