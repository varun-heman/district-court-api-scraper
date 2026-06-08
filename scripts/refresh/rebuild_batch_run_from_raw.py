from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from district_court_api_scraper.configurable_batch import (  # noqa: E402
    _build_selector_key,
    _dedupe_case_details,
    _dedupe_case_lists,
    _dedupe_rows,
    _derive_decision_date,
    _to_caselists_rows,
)
from district_court_api_scraper.parsers import parse_case_detail_summary, parse_case_list_items  # noqa: E402
from district_court_api_scraper.structured_exports import (  # noqa: E402
    TARGET_CASE_COLUMNS,
    TARGET_CASELIST_COLUMNS,
    TARGET_HEARING_COLUMNS,
)


LIST_STEM_RE = re.compile(
    r"^(?P<state>.+?)_(?P<district>.+?)_(?P<court>.+?)_(?P<act_id>\d+)_(?P<status>Pending|Disposed|Both)_est_(?P<est>.+)$"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild normalized batch outputs from raw case lists/details.")
    parser.add_argument("--run-root", required=True, help="Path to output/runs/<run_id>")
    return parser.parse_args()


def _parse_list_stem(stem: str) -> dict[str, str] | None:
    match = LIST_STEM_RE.match(stem)
    if not match:
        return None
    payload = match.groupdict()
    return {
        "source_state": payload["state"].replace("_", " "),
        "source_district": payload["district"].replace("_", " "),
        "source_court_complex_name": payload["court"].replace("_", " "),
        "source_act_id": payload["act_id"],
        "source_status": payload["status"].lower(),
        "status_bucket": payload["status"].lower(),
        "est_code": payload["est"],
        "slug": stem,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    raw_case_lists = run_root / "raw" / "case_lists"
    raw_case_details = run_root / "raw" / "case_details"
    normalized_dir = run_root / "normalized"
    exports_dir = run_root / "exports"

    slug_meta: dict[str, dict[str, str]] = {}
    case_list_items: list[dict[str, Any]] = []
    list_lookup: dict[tuple[str, str], dict[str, Any]] = {}

    for html_path in sorted(raw_case_lists.glob("*.html")):
        meta = _parse_list_stem(html_path.stem)
        if meta is None:
            continue
        slug_meta[meta["slug"]] = meta
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
        for row in parse_case_list_items(html_text):
            vh = row.get("view_history") or {}
            cino = str(vh.get("cino", "")).strip()
            case_no = str(vh.get("case_no", "")).strip()
            if not cino or not case_no:
                continue
            list_item = {
                "source_state": meta["source_state"],
                "source_district": meta["source_district"],
                "source_court_complex_name": meta["source_court_complex_name"],
                "source_act_id": meta["source_act_id"],
                "status_bucket": meta["status_bucket"],
                **row,
            }
            list_item["selector_key"] = _build_selector_key(list_item)
            case_list_items.append(list_item)
            list_lookup[(cino, case_no)] = list_item

    case_detail_items: list[dict[str, Any]] = []
    cases_rows: list[dict[str, Any]] = []

    for html_path in sorted(raw_case_details.glob("*.html")):
        try:
            slug, cino, case_no = html_path.stem.rsplit("_", 2)
        except ValueError:
            continue
        meta = slug_meta.get(slug)
        list_item = list_lookup.get((cino, case_no))
        if meta is None or list_item is None:
            continue
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
        summary = parse_case_detail_summary(html_text)
        history_rows = summary.get("history_rows") or []
        judge = str(history_rows[0].get("judge", "")) if history_rows else ""
        case_details = summary.get("case_details") or {}
        case_status = summary.get("case_status") or {}

        cases_rows.append(
            {
                "District": meta["source_district"],
                "cnr": cino,
                "judge": judge,
                "case_type": str(case_details.get("Case Type", "")),
                "petitioner": str(list_item.get("petitioner", "")),
                "petitioner_adv": "",
                "respondent": str(list_item.get("respondent", "")),
                "respondent_adv": "",
                "other_resp": "",
                "filing_no": str(case_details.get("Filing Number", "")),
                "reg_no": str(case_details.get("Registration Number", "")),
                "filing_date": str(case_details.get("Filing Date", "")),
                "reg_date": str(case_details.get("Registration Date", "")),
                "decision_date": _derive_decision_date(case_status=case_status, history_rows=history_rows),
                "case_status": str(case_status.get("Case Status", case_status.get("Case Stage", ""))),
                "disposal_type": str(case_status.get("Nature of Disposal", "")),
                "Status": meta["source_status"].title(),
            }
        )
        case_detail_items.append(
            {
                "source_state": meta["source_state"],
                "source_district": meta["source_district"],
                "source_court_complex_name": meta["source_court_complex_name"],
                "source_act_id": meta["source_act_id"],
                "source_status": meta["source_status"],
                "case_no": case_no,
                "cino": cino,
                "case_number": list_item.get("case_number", ""),
                "raw_file": str(html_path),
                "summary": summary,
                "pdf_downloads": [],
            }
        )

    case_list_items = _dedupe_case_lists(case_list_items)
    case_detail_items = _dedupe_case_details(case_detail_items)
    cases_rows = _dedupe_rows(cases_rows, key_fields=["cnr", "filing_no", "reg_no"])
    case_list_items.sort(key=lambda x: (x.get("view_history", {}).get("cino", ""), x.get("case_number", "")))
    case_detail_items.sort(key=lambda x: x.get("cino", ""))
    cases_rows.sort(key=lambda x: x.get("cnr", ""))

    _write_jsonl(normalized_dir / "case_list_items.jsonl", case_list_items)
    _write_jsonl(normalized_dir / "case_details.jsonl", case_detail_items)
    _write_jsonl(normalized_dir / "hearings.jsonl", [])
    _write_jsonl(normalized_dir / "incomplete_tasks.jsonl", [])
    _write_csv(exports_dir / "cases.csv", TARGET_CASE_COLUMNS, cases_rows)
    _write_csv(exports_dir / "hearings.csv", TARGET_HEARING_COLUMNS, [])
    _write_csv(exports_dir / "caselists.csv", TARGET_CASELIST_COLUMNS, _to_caselists_rows(case_list_items))

    summary = {
        "run_id": run_root.name,
        "recovered_from_raw": True,
        "case_list_items": len(case_list_items),
        "case_details_items": len(case_detail_items),
        "cases_csv_rows": len(cases_rows),
        "caselists_csv_rows": len(_to_caselists_rows(case_list_items)),
        "hearing_items": 0,
        "hearings_csv_rows": 0,
        "incomplete_count": 0,
        "cases_csv": str((exports_dir / "cases.csv").resolve()),
        "hearings_csv": str((exports_dir / "hearings.csv").resolve()),
        "caselists_csv": str((exports_dir / "caselists.csv").resolve()),
    }
    (run_root / "summary_batch_scrape.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
