#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from district_court_api_scraper.config import AppConfig, TASK_CASE_DETAIL, TASK_HEARING_DETAIL
from district_court_api_scraper.parsers import parse_case_list_items
from district_court_api_scraper.pipelines import PipelineRunner


TARGET_ACT_IDS = {"18", "1744", "2190"}
TARGET_STATUSES = {"pending", "disposed"}


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "item"


def _extract_meta_from_filename(path: Path) -> dict[str, str]:
    # Example: Kollam_Court Complex Punalur_Negotiable_18_pending.html
    stem = path.stem
    match = re.match(r"^Kollam_(.+)_Negotiable_(\d+)_(pending|disposed)$", stem, flags=re.IGNORECASE)
    if not match:
        return {"court_complex": "", "act_id": "", "status": ""}
    return {
        "court_complex": match.group(1),
        "act_id": match.group(2),
        "status": match.group(3).lower(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Kollam NI Act Section 138 scrape using saved case lists + live detail APIs.")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="district-court-api-scraper project root",
    )
    parser.add_argument(
        "--case-lists-root",
        default="/Users/siddarth/Documents/Work/xkdr/repository/db-courts/SRC/district-court-v2/data/case_lists/Kerala/Kollam",
        help="Path containing Kollam case-list HTML files",
    )
    parser.add_argument("--run-id", default="kollam_section138")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    case_lists_root = Path(args.case_lists_root).resolve()
    config = AppConfig.default(project_root=project_root)
    runner = PipelineRunner(config=config, run_id=args.run_id)

    html_files = sorted(case_lists_root.glob("Kollam_*_Negotiable_*_*.html"))
    all_rows: list[dict[str, Any]] = []
    year_2025_rows: list[dict[str, Any]] = []
    by_key_2025: dict[str, dict[str, Any]] = {}

    for html_file in html_files:
        meta = _extract_meta_from_filename(html_file)
        if meta["act_id"] not in TARGET_ACT_IDS or meta["status"] not in TARGET_STATUSES:
            continue
        html_text = html_file.read_text(encoding="utf-8", errors="ignore")
        rows = parse_case_list_items(html_text)
        raw_copy = runner.run_paths.raw_case_lists / f"{_safe_name(html_file.name)}"
        if not raw_copy.exists():
            raw_copy.write_text(html_text, encoding="utf-8")
        for row in rows:
            vh = row.get("view_history") or {}
            cino = str(vh.get("cino", ""))
            record = {
                "run_id": runner.run_id,
                "source_file": str(html_file),
                "court_complex": meta["court_complex"],
                "act_id": meta["act_id"],
                "status_bucket": meta["status"],
                **row,
            }
            all_rows.append(record)
            if cino.endswith("2025"):
                year_2025_rows.append(record)
                key = f"{vh.get('case_no','')}|{cino}"
                if key not in by_key_2025:
                    by_key_2025[key] = record

    # Persist complete list universe (pending + disposed, 3 NI acts).
    with runner.run_paths.case_list_items_jsonl.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Queue only 2025 CNRs for detail/hearing scrape.
    for key, row in by_key_2025.items():
        vh = row["view_history"]
        payload = dict(vh)
        payload["case_number"] = row.get("case_number", "")
        payload["petitioner"] = row.get("petitioner", "")
        payload["respondent"] = row.get("respondent", "")
        payload["source_file"] = row.get("source_file", "")
        payload["source_status"] = row.get("status_bucket", "")
        payload["source_act_id"] = row.get("act_id", "")
        payload["source_court_complex_name"] = row.get("court_complex", "")
        runner.queue.enqueue(
            run_id=runner.run_id,
            task_type=TASK_CASE_DETAIL,
            payload=payload,
            dedupe_key=key,
            priority=40,
        )

    runner.process_pending(task_types=[TASK_CASE_DETAIL, TASK_HEARING_DETAIL])
    cases_csv, hearings_csv = runner.export()

    summary = {
        "run_id": runner.run_id,
        "total_case_list_rows": len(all_rows),
        "total_case_list_rows_2025": len(year_2025_rows),
        "unique_cases_2025": len(by_key_2025),
        "output_case_list_jsonl": str(runner.run_paths.case_list_items_jsonl),
        "output_case_details_jsonl": str(runner.run_paths.case_details_jsonl),
        "output_hearings_jsonl": str(runner.run_paths.hearings_jsonl),
        "output_incomplete_jsonl": str(runner.run_paths.incomplete_tasks_jsonl),
        "cases_csv": str(cases_csv),
        "hearings_csv": str(hearings_csv),
    }
    summary_path = runner.run_paths.run_root / "summary_kollam_section138.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

