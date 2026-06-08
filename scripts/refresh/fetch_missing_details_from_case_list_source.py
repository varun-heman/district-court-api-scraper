from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from district_court_api_scraper.config import AppConfig  # noqa: E402
from district_court_api_scraper.configurable_batch import (  # noqa: E402
    QuerySpec,
    _dedupe_case_details,
    _dedupe_case_lists,
    _dedupe_hearings,
    _dedupe_rows,
    _fetch_case_and_hearings,
    _normalize_status,
    _safe_name,
    _to_caselists_rows,
)
from district_court_api_scraper.ecourts_client import ECourtsClient  # noqa: E402
from district_court_api_scraper.parsers import parse_case_detail_summary  # noqa: E402
from district_court_api_scraper.structured_exports import (  # noqa: E402
    TARGET_CASE_COLUMNS,
    TARGET_CASELIST_COLUMNS,
    TARGET_HEARING_COLUMNS,
)
from district_court_api_scraper.transport import ECourtsTransport  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch only missing case-details from an existing case-list JSONL source.",
    )
    parser.add_argument("--run-id", required=True, help="Target run id under output/runs")
    parser.add_argument("--source-jsonl", required=True, help="Path to normalized case_list_items.jsonl source")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--case-years", default="2023,2024", help="Comma-separated case-number years")
    parser.add_argument("--cnr-years", default="2023,2024", help="Comma-separated CNR suffix years")
    parser.add_argument("--prefixes", default="CC,ST", help="Comma-separated case-number prefixes")
    parser.add_argument("--fetch-hearings", action="store_true", help="Also fetch hearings for new case-details")
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})


def _parse_years(raw: str) -> set[str]:
    return {token.strip() for token in str(raw).split(",") if token.strip()}


def _parse_prefixes(raw: str) -> tuple[str, ...]:
    return tuple(token.strip().upper() for token in str(raw).split(",") if token.strip())


def _case_key(*, case_no: str, cino: str) -> str:
    return f"{case_no.strip()}|{cino.strip()}"


def _case_number_year(case_number: str) -> str:
    match = re.search(r"/(\d{4})$", str(case_number).strip())
    return match.group(1) if match else ""


def _filter_source_rows(
    rows: list[dict[str, Any]],
    *,
    case_years: set[str],
    cnr_years: set[str],
    prefixes: tuple[str, ...],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        vh = row.get("view_history") or {}
        case_no = str(vh.get("case_no", "")).strip()
        cino = str(vh.get("cino", "")).strip()
        case_number = str(row.get("case_number", "")).strip().upper()
        if not case_no or not cino or not case_number:
            continue
        if prefixes and not case_number.startswith(prefixes):
            continue
        if case_years and _case_number_year(case_number) not in case_years:
            continue
        if cnr_years and cino[-4:] not in cnr_years:
            continue
        filtered.append(row)
    filtered = _dedupe_case_lists(filtered)
    filtered.sort(key=lambda item: ((item.get("view_history") or {}).get("cino", ""), item.get("case_number", "")))
    return filtered


def _query_for_row(row: dict[str, Any]) -> QuerySpec:
    vh = row.get("view_history") or {}
    state_name = str(row.get("source_state", "Kerala"))
    district_name = str(row.get("source_district", ""))
    court_name = str(row.get("source_court_complex_name", row.get("court_complex", "")))
    act_id = str(row.get("source_act_id", ""))
    status = _normalize_status(row.get("status_bucket", row.get("source_status", "Both")))
    slug = _safe_name(
        f"{state_name}_{district_name}_{court_name}_{act_id}_{status.lower()}_source"
    )
    return QuerySpec(
        state_name=state_name,
        district_name=district_name,
        state_code=str(vh.get("state_code", "")),
        district_code=str(vh.get("dist_code", "")),
        court_complex_name=court_name,
        court_complex_code=str(vh.get("court_complex_code", "")),
        est_code=str(row.get("est_code", "")),
        act_id=act_id,
        section="138",
        status=status,
        slug=slug,
    )


def _detail_item_from_html(
    *,
    row: dict[str, Any],
    html_path: Path,
    summary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    vh = row.get("view_history") or {}
    cino = str(vh.get("cino", ""))
    case_no = str(vh.get("case_no", ""))
    history_rows = summary.get("history_rows") or []
    judge = str(history_rows[0].get("judge", "")) if history_rows else ""
    case_details_map = summary.get("case_details") or {}
    case_status = summary.get("case_status") or {}
    case_row = {
        "District": str(row.get("source_district", "")),
        "cnr": cino,
        "judge": judge,
        "case_type": str(case_details_map.get("Case Type", "")),
        "petitioner": str(row.get("petitioner", "")),
        "petitioner_adv": "",
        "respondent": str(row.get("respondent", "")),
        "respondent_adv": "",
        "other_resp": "",
        "filing_no": str(case_details_map.get("Filing Number", "")),
        "reg_no": str(case_details_map.get("Registration Number", "")),
        "filing_date": str(case_details_map.get("Filing Date", "")),
        "reg_date": str(case_details_map.get("Registration Date", "")),
        "decision_date": str(case_status.get("Decision Date", "")),
        "case_status": str(case_status.get("Case Status", case_status.get("Case Stage", ""))),
        "disposal_type": str(case_status.get("Nature of Disposal", "")),
        "Status": _normalize_status(row.get("status_bucket", row.get("source_status", ""))),
    }
    case_detail_item = {
        "source_state": str(row.get("source_state", "Kerala")),
        "source_district": str(row.get("source_district", "")),
        "source_court_complex_name": str(row.get("source_court_complex_name", row.get("court_complex", ""))),
        "source_act_id": str(row.get("source_act_id", "")),
        "source_status": str(row.get("status_bucket", row.get("source_status", ""))).lower(),
        "case_no": case_no,
        "cino": cino,
        "case_number": str(row.get("case_number", "")),
        "raw_file": str(html_path),
        "summary": summary,
        "pdf_downloads": [],
    }
    return case_detail_item, case_row


def _load_existing_details(
    *,
    raw_case_details_dir: Path,
    source_lookup: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    case_details: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for html_path in sorted(raw_case_details_dir.glob("*.html")):
        try:
            _slug, cino, case_no = html_path.stem.rsplit("_", 2)
        except ValueError:
            continue
        key = _case_key(case_no=case_no, cino=cino)
        row = source_lookup.get(key)
        if row is None:
            continue
        summary = parse_case_detail_summary(html_path.read_text(encoding="utf-8", errors="ignore"))
        case_detail_item, case_row = _detail_item_from_html(row=row, html_path=html_path, summary=summary)
        case_details.append(case_detail_item)
        case_rows.append(case_row)
        seen_keys.add(key)
    return case_details, case_rows, seen_keys


def _fetch_one(
    *,
    app_config: AppConfig,
    run_paths,
    row: dict[str, Any],
    fetch_hearings: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    transport = ECourtsTransport(
        base_url=app_config.base_url,
        user_agent=app_config.user_agent,
        min_delay_seconds=app_config.min_delay_seconds,
        max_retries=app_config.max_retries,
        timeout_seconds=app_config.request_timeout_seconds,
    )
    client = ECourtsClient(transport)
    query = _query_for_row(row)
    case_row, hearing_rows, case_detail_item, hearing_items, incomplete, _pdf_count = _fetch_case_and_hearings(
        client=client,
        run_paths=run_paths,
        query=query,
        list_item=row,
        fetch_hearings=fetch_hearings,
    )
    return case_detail_item, case_row, hearing_items, incomplete + hearing_rows_to_incomplete(hearing_rows)


def hearing_rows_to_incomplete(_hearing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return []


def main() -> int:
    args = _parse_args()
    app_config = AppConfig.default(project_root=PROJECT_ROOT)
    run_paths = app_config.ensure_run_paths(args.run_id)

    source_path = Path(args.source_jsonl).resolve()
    source_rows = _read_jsonl(source_path)
    filtered_rows = _filter_source_rows(
        source_rows,
        case_years=_parse_years(args.case_years),
        cnr_years=_parse_years(args.cnr_years),
        prefixes=_parse_prefixes(args.prefixes),
    )
    source_lookup: dict[str, dict[str, Any]] = {}
    for row in filtered_rows:
        vh = row.get("view_history") or {}
        key = _case_key(case_no=str(vh.get("case_no", "")), cino=str(vh.get("cino", "")))
        source_lookup[key] = row

    existing_case_details, existing_case_rows, existing_keys = _load_existing_details(
        raw_case_details_dir=run_paths.raw_case_details,
        source_lookup=source_lookup,
    )
    missing_rows = []
    for row in filtered_rows:
        vh = row.get("view_history") or {}
        key = _case_key(case_no=str(vh.get("case_no", "")), cino=str(vh.get("cino", "")))
        if key in existing_keys:
            continue
        missing_rows.append(row)

    new_case_details: list[dict[str, Any]] = []
    new_case_rows: list[dict[str, Any]] = []
    new_hearing_items: list[dict[str, Any]] = []
    incomplete_items: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = {
            executor.submit(
                _fetch_one,
                app_config=app_config,
                run_paths=run_paths,
                row=row,
                fetch_hearings=bool(args.fetch_hearings),
            ): row
            for row in missing_rows
        }
        completed = 0
        total = len(futures)
        for future in as_completed(futures):
            row = futures[future]
            vh = row.get("view_history") or {}
            try:
                case_detail_item, case_row, hearing_items, incomplete = future.result()
            except Exception as exc:
                incomplete_items.append(
                    {
                        "task_type": "CASE_DETAIL",
                        "case_no": str(vh.get("case_no", "")),
                        "cino": str(vh.get("cino", "")),
                        "error": f"Unhandled fetch exception: {exc}",
                    }
                )
                completed += 1
                if completed % 100 == 0 or completed == total:
                    print(f"completed={completed}/{total}", flush=True)
                continue
            if case_detail_item is not None:
                new_case_details.append(case_detail_item)
            if case_row is not None:
                new_case_rows.append(case_row)
            if hearing_items:
                new_hearing_items.extend(hearing_items)
            if incomplete:
                incomplete_items.extend(incomplete)
            completed += 1
            if completed % 100 == 0 or completed == total:
                print(f"completed={completed}/{total}", flush=True)

    all_case_lists = _dedupe_case_lists(filtered_rows)
    all_case_details = _dedupe_case_details(existing_case_details + new_case_details)
    all_cases_rows = _dedupe_rows(existing_case_rows + new_case_rows, key_fields=["cnr", "filing_no", "reg_no"])
    all_hearing_items = _dedupe_hearings(new_hearing_items)
    all_case_lists.sort(key=lambda item: ((item.get("view_history") or {}).get("cino", ""), item.get("case_number", "")))
    all_case_details.sort(key=lambda item: str(item.get("cino", "")))
    all_cases_rows.sort(key=lambda row: str(row.get("cnr", "")))

    _write_jsonl(run_paths.case_list_items_jsonl, all_case_lists)
    _write_jsonl(run_paths.case_details_jsonl, all_case_details)
    _write_jsonl(run_paths.hearings_jsonl, all_hearing_items)
    _write_jsonl(run_paths.incomplete_tasks_jsonl, incomplete_items)
    _write_csv(run_paths.exports_dir / "cases.csv", TARGET_CASE_COLUMNS, all_cases_rows)
    _write_csv(run_paths.exports_dir / "hearings.csv", TARGET_HEARING_COLUMNS, [])
    _write_csv(run_paths.exports_dir / "caselists.csv", TARGET_CASELIST_COLUMNS, _to_caselists_rows(all_case_lists))

    summary = {
        "run_id": args.run_id,
        "source_jsonl": str(source_path),
        "workers": max(1, int(args.workers)),
        "fetch_hearings": bool(args.fetch_hearings),
        "source_rows": len(source_rows),
        "filtered_case_list_rows": len(all_case_lists),
        "existing_case_details": len(existing_case_details),
        "missing_before_fetch": len(missing_rows),
        "new_case_details_fetched": len(new_case_details),
        "final_case_details": len(all_case_details),
        "incomplete_count": len(incomplete_items),
        "case_details_dir": str(run_paths.raw_case_details),
        "cases_csv": str(run_paths.exports_dir / "cases.csv"),
        "caselists_csv": str(run_paths.exports_dir / "caselists.csv"),
    }
    (run_paths.run_root / "summary_details_only_from_source.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
