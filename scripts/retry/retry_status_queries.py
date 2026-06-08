from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any


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
    parser = argparse.ArgumentParser(description="Retry failed status queries and patch status map/csvs.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--target-cnrs-csv", required=True, help="CSV with cino/cnr column")
    parser.add_argument("--errors-json", required=True, help="status_map_errors.json")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--workers", type=int, default=6)
    return parser.parse_args()


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _load_target_cnrs(path: Path) -> set[str]:
    out: set[str] = set()
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = {c.lower() for c in (reader.fieldnames or [])}
        key = "cino" if "cino" in cols else "cnr" if "cnr" in cols else ""
        if not key:
            return out
        for row in reader:
            c = str(row.get(key, "")).strip().upper()
            if c:
                out.add(c)
    return out


def _query_sig(query: QuerySpec) -> tuple[str, str, str]:
    return (_norm(query.district_name), _norm(query.court_complex_name), str(query.act_id).strip())


def _scrape_query(query: QuerySpec, app_config: AppConfig) -> tuple[QuerySpec, bool, set[str], str]:
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
    html_text, error = _submit_act_html(client=client, solver=solver, policy=policy, query=query)
    if error:
        return query, False, set(), error
    cnrs: set[str] = set()
    for row in parse_case_list_items(html_text):
        vh = row.get("view_history") or {}
        cino = str(vh.get("cino", "")).strip().upper()
        if cino:
            cnrs.add(cino)
    return query, True, cnrs, ""


def _load_status_map(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not path.exists():
        return out
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cnr = str(row.get("cnr", "")).strip().upper()
            if not cnr:
                continue
            out[cnr] = {
                "seen_pending": str(row.get("seen_pending", "0")),
                "seen_disposed": str(row.get("seen_disposed", "0")),
                "inferred_status": str(row.get("inferred_status", "")),
            }
    return out


def _write_status_map(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["cnr", "seen_pending", "seen_disposed", "inferred_status"])
        writer.writeheader()
        for cnr in sorted(rows):
            row = rows[cnr]
            writer.writerow(
                {
                    "cnr": cnr,
                    "seen_pending": row.get("seen_pending", "0"),
                    "seen_disposed": row.get("seen_disposed", "0"),
                    "inferred_status": row.get("inferred_status", ""),
                }
            )


def _inferred(p: bool, d: bool) -> str:
    if p and d:
        return "Both"
    if p:
        return "Pending"
    if d:
        return "Disposed"
    return ""


def _rewrite_export_status(path: Path, status_map: dict[str, dict[str, str]]) -> dict[str, int]:
    rows: list[dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        for row in reader:
            cnr = str(row.get("cnr", "") or row.get("CNR", "")).strip().upper()
            inferred = (status_map.get(cnr) or {}).get("inferred_status", "")
            if inferred:
                row["Status"] = inferred
            rows.append(row)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in cols})
    return {
        "rows": len(rows),
        "pending": sum(1 for r in rows if str(r.get("Status", "")).strip().lower() == "pending"),
        "disposed": sum(1 for r in rows if str(r.get("Status", "")).strip().lower() == "disposed"),
        "both": sum(1 for r in rows if str(r.get("Status", "")).strip().lower() == "both"),
    }


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    app_config = AppConfig.default(project_root=PROJECT_ROOT)
    spec = load_batch_spec(Path(args.config).resolve())
    all_queries = _build_queries(spec=spec)
    errors = json.loads(Path(args.errors_json).resolve().read_text(encoding="utf-8"))
    target_cnrs = _load_target_cnrs(Path(args.target_cnrs_csv).resolve())

    failed_sigs = {
        (_norm(str(e.get("district", ""))), _norm(str(e.get("court_complex", ""))), str(e.get("act_id", "")).strip())
        for e in errors
    }
    selected: list[QuerySpec] = []
    seen_q: set[tuple[str, str, str, str]] = set()
    for q in all_queries:
        if _query_sig(q) not in failed_sigs:
            continue
        for status in ("Pending", "Disposed"):
            q2 = replace(q, status=status, slug=f"{q.slug}_{status.lower()}_retry")
            key = (_query_sig(q2)[0], _query_sig(q2)[1], _query_sig(q2)[2], q2.status)
            if key in seen_q:
                continue
            seen_q.add(key)
            selected.append(q2)

    pending_queries = selected[:]
    successes: dict[tuple[str, str, str, str], set[str]] = {}
    failures: list[dict[str, str]] = []

    for rnd in range(1, max(1, int(args.rounds)) + 1):
        if not pending_queries:
            break
        print(f"round {rnd} pending_queries={len(pending_queries)}")
        next_pending: list[QuerySpec] = []
        with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
            futs = [executor.submit(_scrape_query, q, app_config) for q in pending_queries]
            for fut in as_completed(futs):
                q, ok, cnrs, error = fut.result()
                key = (_query_sig(q)[0], _query_sig(q)[1], _query_sig(q)[2], q.status)
                if ok:
                    successes[key] = cnrs
                else:
                    next_pending.append(q)
        pending_queries = next_pending

    for q in pending_queries:
        failures.append(
            {
                "district": q.district_name,
                "court_complex": q.court_complex_name,
                "act_id": q.act_id,
                "status": q.status,
                "error": "captcha attempts exhausted or submitAct unavailable",
            }
        )

    status_map_path = run_root / "analysis" / "status_map_from_lists.csv"
    status_rows = _load_status_map(status_map_path)
    for cnr in target_cnrs:
        status_rows.setdefault(cnr, {"seen_pending": "0", "seen_disposed": "0", "inferred_status": ""})

    for key, cnrs in successes.items():
        status = key[3]
        for cnr in cnrs:
            if cnr not in target_cnrs:
                continue
            row = status_rows.setdefault(cnr, {"seen_pending": "0", "seen_disposed": "0", "inferred_status": ""})
            if status == "Pending":
                row["seen_pending"] = "1"
            if status == "Disposed":
                row["seen_disposed"] = "1"

    for cnr, row in status_rows.items():
        p = row.get("seen_pending", "0") == "1"
        d = row.get("seen_disposed", "0") == "1"
        row["inferred_status"] = _inferred(p, d)

    _write_status_map(status_map_path, status_rows)

    errors_path = run_root / "analysis" / "status_map_errors.json"
    errors_path.write_text(json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8")

    updates = {}
    for name in ("cases.csv", "caselists.csv"):
        p = run_root / "exports" / name
        if p.exists():
            updates[name] = _rewrite_export_status(p, status_rows)

    target_status = {"Pending": 0, "Disposed": 0, "Both": 0, "Missing": 0}
    for cnr in target_cnrs:
        s = (status_rows.get(cnr) or {}).get("inferred_status", "")
        if s in target_status:
            target_status[s] += 1
        else:
            target_status["Missing"] += 1

    summary = {
        "selected_queries": len(selected),
        "successful_queries": len(successes),
        "failed_queries": len(failures),
        "target_cnrs": len(target_cnrs),
        "target_status": target_status,
        "updates": updates,
        "status_map_path": str(status_map_path),
        "errors_path": str(errors_path),
    }
    (run_root / "analysis" / "status_map_retry_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
