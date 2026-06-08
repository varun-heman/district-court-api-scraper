from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from district_court_api_scraper.captcha import CaptchaAttemptPolicy, CaptchaSolver  # noqa: E402
from district_court_api_scraper.config import AppConfig  # noqa: E402
from district_court_api_scraper.configurable_batch import (  # noqa: E402
    QuerySpec,
    _build_queries,
    _submit_act_html,
    load_batch_spec,
)
from district_court_api_scraper.ecourts_client import ECourtsClient  # noqa: E402
from district_court_api_scraper.parsers import parse_case_list_items  # noqa: E402
from district_court_api_scraper.transport import ECourtsTransport  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rescrape case lists separately for Pending/Disposed and backfill case status."
    )
    parser.add_argument("--config", required=True, help="Batch config JSON used for the run (e.g., phase4 both config)")
    parser.add_argument("--run-root", required=True, help="Run root path (output/runs/<run_id>)")
    parser.add_argument("--workers", type=int, default=6, help="Thread workers for list scraping")
    parser.add_argument(
        "--write-updated-csvs",
        action="store_true",
        help="If set, overwrite exports/cases.csv and exports/caselists.csv Status using inferred mapping.",
    )
    parser.add_argument(
        "--target-cnrs-csv",
        default="",
        help="Optional CSV containing subset CNRs to target (column: cino or cnr).",
    )
    return parser.parse_args()


def _query_key(query: QuerySpec) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        query.state_name,
        query.district_name,
        query.state_code,
        query.district_code,
        query.court_complex_name,
        query.court_complex_code,
        query.est_code,
        query.act_id,
    )


def _signature_from_query(query: QuerySpec) -> tuple[str, str, str, str]:
    return (
        query.district_name.strip().lower(),
        query.court_complex_name.strip().lower(),
        str(query.act_id).strip(),
        str(query.est_code).strip(),
    )


def _status_from_flags(*, seen_pending: bool, seen_disposed: bool) -> str:
    if seen_pending and seen_disposed:
        return "Both"
    if seen_pending:
        return "Pending"
    if seen_disposed:
        return "Disposed"
    return ""


def _scrape_status_query(query: QuerySpec, app_config: AppConfig) -> dict[str, Any]:
    transport = ECourtsTransport(
        base_url=app_config.base_url,
        user_agent=app_config.user_agent,
        min_delay_seconds=app_config.min_delay_seconds,
        max_retries=app_config.max_retries,
        timeout_seconds=app_config.request_timeout_seconds,
    )
    client = ECourtsClient(transport)
    solver = CaptchaSolver()
    policy = CaptchaAttemptPolicy()

    html_text, error = _submit_act_html(
        client=client,
        solver=solver,
        policy=policy,
        query=query,
    )
    if error:
        return {"ok": False, "query": query, "error": error, "cnrs": set()}

    rows = parse_case_list_items(html_text)
    cnrs: set[str] = set()
    for row in rows:
        vh = row.get("view_history") or {}
        cino = str(vh.get("cino", "")).strip()
        if cino:
            cnrs.add(cino)
    return {"ok": True, "query": query, "error": "", "cnrs": cnrs, "count": len(cnrs)}


def _update_csv_status(path: Path, status_map: dict[str, str]) -> dict[str, int]:
    rows: list[dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        for row in reader:
            cnr = str(row.get("cnr", "") or row.get("CNR", "")).strip()
            inferred = status_map.get(cnr, "")
            if inferred:
                row["Status"] = inferred
            rows.append(row)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})

    return {
        "rows": len(rows),
        "status_pending": sum(1 for r in rows if str(r.get("Status", "")).strip().lower() == "pending"),
        "status_disposed": sum(1 for r in rows if str(r.get("Status", "")).strip().lower() == "disposed"),
        "status_both": sum(1 for r in rows if str(r.get("Status", "")).strip().lower() == "both"),
    }


def _load_target_cnrs(path: Path) -> set[str]:
    out: set[str] = set()
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = {c.lower() for c in (reader.fieldnames or [])}
        key = "cino" if "cino" in columns else "cnr" if "cnr" in columns else ""
        if not key:
            return out
        for row in reader:
            cnr = str(row.get(key, "")).strip()
            if cnr:
                out.add(cnr)
    return out


def _extract_est_code_from_raw(raw_file: str) -> str:
    text = str(raw_file or "")
    m = re.search(r"_est_([A-Za-z0-9]+)_", text)
    return m.group(1) if m else ""


def _allowed_signatures_from_run(run_root: Path, target_cnrs: set[str]) -> set[tuple[str, str, str, str]]:
    path = run_root / "normalized" / "case_details.jsonl"
    allowed: set[tuple[str, str, str, str]] = set()
    if not path.exists() or not target_cnrs:
        return allowed
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            cnr = str(item.get("cino", "")).strip()
            if cnr not in target_cnrs:
                continue
            district = str(item.get("source_district", "")).strip().lower()
            court = str(item.get("source_court_complex_name", "")).strip().lower()
            act_id = str(item.get("source_act_id", "")).strip()
            est_code = _extract_est_code_from_raw(str(item.get("raw_file", "")))
            allowed.add((district, court, act_id, est_code))
    return allowed


def main() -> int:
    args = _parse_args()
    config_path = Path(args.config).resolve()
    run_root = Path(args.run_root).resolve()
    app_config = AppConfig.default(project_root=PROJECT_ROOT)

    spec = load_batch_spec(config_path)
    base_queries = _build_queries(spec=spec)

    target_cnrs: set[str] = set()
    allowed_signatures: set[tuple[str, str, str, str]] = set()
    if args.target_cnrs_csv:
        target_cnrs = _load_target_cnrs(Path(args.target_cnrs_csv).resolve())
        allowed_signatures = _allowed_signatures_from_run(run_root, target_cnrs)

    unique_by_key: dict[tuple[str, str, str, str, str, str, str, str], QuerySpec] = {}
    for q in base_queries:
        if allowed_signatures:
            if _signature_from_query(q) not in allowed_signatures:
                continue
        unique_by_key.setdefault(_query_key(q), q)
    unique_queries = list(unique_by_key.values())

    status_queries: list[QuerySpec] = []
    for q in unique_queries:
        status_queries.append(replace(q, status="Pending", slug=f"{q.slug}_pending"))
        status_queries.append(replace(q, status="Disposed", slug=f"{q.slug}_disposed"))

    cnr_flags: dict[str, dict[str, bool]] = {}
    errors: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = [executor.submit(_scrape_status_query, q, app_config) for q in status_queries]
        done = 0
        total = len(futures)
        for future in as_completed(futures):
            result = future.result()
            q = result["query"]
            done += 1
            if not result["ok"]:
                errors.append(
                    {
                        "district": q.district_name,
                        "court_complex": q.court_complex_name,
                        "act_id": q.act_id,
                        "status": q.status,
                        "error": str(result.get("error", "")),
                    }
                )
                continue
            for cnr in result["cnrs"]:
                cnr_flags.setdefault(cnr, {"Pending": False, "Disposed": False})
                cnr_flags[cnr][q.status] = True
            if done % 20 == 0:
                print(f"progress {done}/{total}")

    status_map: dict[str, str] = {}
    for cnr, flags in cnr_flags.items():
        status = _status_from_flags(seen_pending=flags.get("Pending", False), seen_disposed=flags.get("Disposed", False))
        if status:
            status_map[cnr] = status

    analysis_dir = run_root / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    status_csv = analysis_dir / "status_map_from_lists.csv"
    with status_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["cnr", "seen_pending", "seen_disposed", "inferred_status"],
        )
        writer.writeheader()
        for cnr in sorted(cnr_flags):
            flags = cnr_flags[cnr]
            writer.writerow(
                {
                    "cnr": cnr,
                    "seen_pending": "1" if flags.get("Pending", False) else "0",
                    "seen_disposed": "1" if flags.get("Disposed", False) else "0",
                    "inferred_status": _status_from_flags(
                        seen_pending=flags.get("Pending", False),
                        seen_disposed=flags.get("Disposed", False),
                    ),
                }
            )

    errors_json = analysis_dir / "status_map_errors.json"
    errors_json.write_text(json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8")

    updates: dict[str, Any] = {}
    if args.write_updated_csvs:
        cases_csv = run_root / "exports" / "cases.csv"
        caselists_csv = run_root / "exports" / "caselists.csv"
        if cases_csv.exists():
            updates["cases_csv"] = _update_csv_status(cases_csv, status_map)
        if caselists_csv.exists():
            updates["caselists_csv"] = _update_csv_status(caselists_csv, status_map)

    summary = {
        "run_root": str(run_root),
        "config": str(config_path),
        "base_query_count": len(unique_queries),
        "status_query_count": len(status_queries),
        "workers": int(args.workers),
        "cnrs_with_any_status": len(cnr_flags),
        "cnrs_inferred_pending": sum(1 for s in status_map.values() if s == "Pending"),
        "cnrs_inferred_disposed": sum(1 for s in status_map.values() if s == "Disposed"),
        "cnrs_inferred_both": sum(1 for s in status_map.values() if s == "Both"),
        "target_cnrs": len(target_cnrs),
        "allowed_signatures": len(allowed_signatures),
        "error_count": len(errors),
        "status_csv": str(status_csv),
        "errors_json": str(errors_json),
        "updates": updates,
    }
    (analysis_dir / "status_map_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
