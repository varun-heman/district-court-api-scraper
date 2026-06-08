from __future__ import annotations

import argparse
import csv
import json
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
    _run_query,
    _safe_name,
    _to_caselists_rows,
    load_batch_spec,
)
from district_court_api_scraper.ecourts_client import ECourtsClient  # noqa: E402
from district_court_api_scraper.transport import ECourtsTransport  # noqa: E402
from district_court_api_scraper.structured_exports import (  # noqa: E402
    TARGET_CASE_COLUMNS,
    TARGET_CASELIST_COLUMNS,
    TARGET_HEARING_COLUMNS,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry only failed batch scrape tasks from incomplete_tasks.jsonl.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", required=True, help="Batch runtime config used for the run.")
    parser.add_argument("--workers", type=int, default=16)
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})


def _query_from_dict(payload: dict[str, Any]) -> QuerySpec:
    return QuerySpec(
        state_name=str(payload["state_name"]),
        district_name=str(payload["district_name"]),
        state_code=str(payload["state_code"]),
        district_code=str(payload["district_code"]),
        court_complex_name=str(payload["court_complex_name"]),
        court_complex_code=str(payload["court_complex_code"]),
        est_code=str(payload["est_code"]),
        act_id=str(payload["act_id"]),
        section=str(payload["section"]),
        status=str(payload["status"]),
        slug=_safe_name(
            f"{payload['state_name']}_{payload['district_name']}_{payload['court_complex_name']}_{payload['act_id']}_{str(payload['status']).lower()}_est_{payload['est_code']}"
        ),
    )


def _build_case_payload(query: QuerySpec, cino: str, case_no: str) -> dict[str, Any]:
    return {
        "view_history": {
            "cino": cino,
            "case_no": case_no,
            "state_code": query.state_code,
            "dist_code": query.district_code,
            "court_complex_code": query.court_complex_code,
            "hideparty": "",
            "search_flag": "",
            "search_by": "CSact",
        },
        "case_number": "",
    }


def _load_failed_targets(path: Path) -> tuple[dict[str, QuerySpec], list[tuple[QuerySpec, str, str]]]:
    act_queries: dict[str, QuerySpec] = {}
    case_details: list[tuple[QuerySpec, str, str]] = []
    for rec in _read_jsonl(path):
        task_type = str(rec.get("task_type", "")).upper()
        if task_type not in {"ACT_LIST", "CASE_DETAIL"}:
            continue
        q = _query_from_dict(rec["query"])
        if task_type == "ACT_LIST":
            act_queries[q.slug] = q
            continue
        case_details.append((q, str(rec.get("cino", "")).strip(), str(rec.get("case_no", "")).strip()))
    return act_queries, case_details


def _run_case_retry(task: tuple[QuerySpec, str, str], app_config: AppConfig, run_paths) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    query, cino, case_no = task
    transport = ECourtsTransport(
        base_url=app_config.base_url,
        user_agent=app_config.user_agent,
        min_delay_seconds=app_config.min_delay_seconds,
        max_retries=app_config.max_retries,
        timeout_seconds=app_config.request_timeout_seconds,
    )
    client = ECourtsClient(transport)
    list_item = _build_case_payload(query, cino, case_no)
    case_row, hearing_rows, case_detail_item, hearing_items, case_incomplete, _pdf_count = _fetch_case_and_hearings(
        client=client,
        run_paths=run_paths,
        query=query,
        list_item=list_item,
        fetch_hearings=True,
    )
    return (
        {"case_row": case_row, "case_detail": case_detail_item, "hearing_rows": hearing_rows, "incomplete": case_incomplete},
        hearing_items,
        {"query": _query_from_dict(query.__dict__)},
    )


def main() -> int:
    args = _parse_args()
    app_config = AppConfig.default(project_root=PROJECT_ROOT)
    spec = load_batch_spec(Path(args.config).resolve())
    run_paths = app_config.ensure_run_paths(args.run_id)

    incomplete_path = run_paths.normalized_dir / "incomplete_tasks.jsonl"
    if not incomplete_path.exists():
        print(f"incomplete file not found: {incomplete_path}")
        return 1

    act_queries, case_targets = _load_failed_targets(incomplete_path)
    print(f"loaded failed entries: act_queries={len(act_queries)} case_detail={len(case_targets)}")

    # remove cached files so we force re-download
    for q in act_queries.values():
        case_list_file = run_paths.raw_case_lists / f"{q.slug}.html"
        if case_list_file.exists():
            case_list_file.unlink()

    for q, cino, case_no in case_targets:
        safe = _safe_name(f"{q.slug}_{cino}_{case_no}")
        case_detail_file = run_paths.raw_case_details / f"{safe}.html"
        if case_detail_file.exists():
            case_detail_file.unlink()
        for hf in run_paths.raw_hearings.glob(f"{safe}_hearing*.html"):
            hf.unlink()

    # avoid duplicate case retries for queries already retried as ACT_LIST
    case_targets = [(q, c, n) for (q, c, n) in case_targets if q.slug not in act_queries]

    existing_case_lists = _read_jsonl(run_paths.case_list_items_jsonl)
    existing_case_details = _read_jsonl(run_paths.case_details_jsonl)
    existing_hearing_items = _read_jsonl(run_paths.hearings_jsonl)
    existing_cases_rows = _read_csv_rows(run_paths.exports_dir / "cases.csv")
    existing_hearing_rows = _read_csv_rows(run_paths.exports_dir / "hearings.csv")
    all_new_incomplete: list[dict[str, Any]] = []
    all_new_case_lists: list[dict[str, Any]] = []
    all_new_case_details: list[dict[str, Any]] = []
    all_new_cases_rows: list[dict[str, Any]] = []
    all_new_hearing_rows: list[dict[str, Any]] = []
    all_new_hearing_items: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        act_futures = {
            executor.submit(
                _run_query,
                query=query,
                app_config=app_config,
                run_paths=run_paths,
                target_years=spec.target_years,
                min_cnr_year=spec.min_cnr_year,
                allowed_case_keys=spec.allowed_case_keys,
                fetch_hearings=True,
                control=None,
            ): query
            for query in act_queries.values()
        }
        for fut in as_completed(act_futures):
            q = act_futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # pragma: no cover
                all_new_incomplete.append({"task_type": "ACT_LIST", "query": q.__dict__, "error": str(exc)})
                continue
            all_new_case_lists.extend(result.case_list_items)
            all_new_case_details.extend(result.case_details_items)
            all_new_cases_rows.extend(result.cases_rows)
            all_new_hearing_rows.extend(result.hearings_rows)
            all_new_hearing_items.extend(result.hearings_items)
            all_new_incomplete.extend(result.incomplete_items)

    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        case_futures = {
            executor.submit(_run_case_retry, task, app_config=app_config, run_paths=run_paths): task
            for task in case_targets
        }
        for fut in as_completed(case_futures):
            q, cino, case_no = case_futures[fut]
            try:
                payload, hearing_items, _ = fut.result()
            except Exception as exc:  # pragma: no cover
                all_new_incomplete.append(
                    {"task_type": "CASE_DETAIL", "query": q.__dict__, "cino": cino, "case_no": case_no, "error": str(exc)}
                )
                continue
            case_row = payload.get("case_row")
            if case_row is not None:
                all_new_cases_rows.append(case_row)
            if payload.get("case_detail") is not None:
                all_new_case_details.append(payload["case_detail"])
            hearing_rows = payload.get("hearing_rows", [])
            if hearing_rows:
                all_new_hearing_rows.extend(hearing_rows)
            if hearing_items:
                all_new_hearing_items.extend(hearing_items)
            all_new_incomplete.extend(payload.get("incomplete", []))

    merged_case_lists = _dedupe_case_lists(existing_case_lists + all_new_case_lists)
    merged_case_details = _dedupe_case_details(existing_case_details + all_new_case_details)
    merged_hearing_items = _dedupe_hearings(existing_hearing_items + all_new_hearing_items)
    merged_cases_rows = _dedupe_rows(existing_cases_rows + all_new_cases_rows, key_fields=["cnr", "filing_no", "reg_no"])
    merged_hearing_rows = _dedupe_rows(
        existing_hearing_rows + all_new_hearing_rows,
        key_fields=["cnr", "order_no", "orders_date", "business_text"],
    )

    _write_jsonl(run_paths.case_list_items_jsonl, merged_case_lists)
    _write_jsonl(run_paths.case_details_jsonl, merged_case_details)
    _write_jsonl(run_paths.hearings_jsonl, merged_hearing_items)
    _write_csv(run_paths.exports_dir / "cases.csv", TARGET_CASE_COLUMNS, merged_cases_rows)
    _write_csv(run_paths.exports_dir / "hearings.csv", TARGET_HEARING_COLUMNS, merged_hearing_rows)
    _write_csv(run_paths.exports_dir / "caselists.csv", TARGET_CASELIST_COLUMNS, _to_caselists_rows(merged_case_lists))

    _write_jsonl(incomplete_path, all_new_incomplete)

    summary = {
        "run_id": args.run_id,
        "queries_retried_act": len(act_queries),
        "case_detail_retries": len(case_targets),
        "case_list_items": len(merged_case_lists),
        "case_details_items": len(merged_case_details),
        "hearing_items": len(merged_hearing_items),
        "cases_csv_rows": len(merged_cases_rows),
        "hearings_csv_rows": len(merged_hearing_rows),
        "caselists_csv_rows": len(_to_caselists_rows(merged_case_lists)),
        "incomplete_count": len(all_new_incomplete),
        "added_case_list_items": len(all_new_case_lists),
        "added_case_detail_items": len(all_new_case_details),
        "raw_case_list_html_count": len(list(run_paths.raw_case_lists.glob("*.html"))),
    }
    (run_paths.run_root / "summary_batch_scrape.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
