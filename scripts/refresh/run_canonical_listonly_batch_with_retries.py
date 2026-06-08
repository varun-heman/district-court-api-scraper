from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from district_court_api_scraper.config import AppConfig  # noqa: E402
from district_court_api_scraper.configurable_batch import _build_queries, load_batch_spec, run_batch_scrape  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a list-only batch scrape and replay incomplete tasks into the same run root.",
    )
    parser.add_argument("--config", required=True, help="Path to the batch runtime config.")
    parser.add_argument("--max-retry-rounds", type=int, default=5)
    parser.add_argument("--retry-workers", type=int, default=None)
    parser.add_argument("--allow-existing-run-root", action="store_true")
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _append_round_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _audit_run(*, run_root: Path, run_id: str, expected_queries: int) -> dict[str, Any]:
    summary = _read_json(run_root / "summary_batch_scrape.json")
    progress = _read_json(run_root / "progress_batch_scrape.json")
    incomplete_path = run_root / "normalized" / "incomplete_tasks.jsonl"
    raw_case_lists_dir = run_root / "raw" / "case_lists"
    unresolved_incomplete_count = _count_jsonl(incomplete_path)
    raw_case_list_html_count = sum(1 for _ in raw_case_lists_dir.glob("*.html")) if raw_case_lists_dir.exists() else 0
    return {
        "timestamp_utc": _utc_now_iso(),
        "run_id": run_id,
        "expected_queries": expected_queries,
        "raw_case_list_html_count": raw_case_list_html_count,
        "unresolved_incomplete_count": unresolved_incomplete_count,
        "coverage_complete": raw_case_list_html_count == expected_queries and unresolved_incomplete_count == 0,
        "summary_incomplete_count": summary.get("incomplete_count"),
        "progress_status": progress.get("status"),
        "progress_completed_queries": progress.get("completed_queries"),
        "progress_total_queries": progress.get("total_queries"),
    }


def _run_retry(*, run_id: str, config_path: Path, workers: int) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "retry" / "retry_failed_batch_queries.py"),
        "--run-id",
        run_id,
        "--config",
        str(config_path),
        "--workers",
        str(workers),
    ]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def main() -> int:
    args = _parse_args()
    config_path = Path(args.config).resolve()
    app_config = AppConfig.default(project_root=PROJECT_ROOT)
    spec = load_batch_spec(config_path)
    if not spec.run_id:
        raise SystemExit("Config must include a run_id for canonical execution.")

    queries = _build_queries(spec=spec)
    expected_queries = len(queries)
    unique_slugs = len({query.slug for query in queries})
    if unique_slugs != expected_queries:
        raise SystemExit(f"Expected unique slug count mismatch: queries={expected_queries} unique_slugs={unique_slugs}")

    run_root = app_config.output_runs_dir / spec.run_id
    if run_root.exists() and not args.allow_existing_run_root:
        has_existing_output = any(
            [
                (run_root / "summary_batch_scrape.json").exists(),
                (run_root / "progress_batch_scrape.json").exists(),
                any((run_root / "raw" / "case_lists").glob("*.html")) if (run_root / "raw" / "case_lists").exists() else False,
            ]
        )
        if has_existing_output:
            raise SystemExit(f"Refusing to reuse existing run root: {run_root}")

    if not os.getenv("GEMINI_API_KEY"):
        print("warning: GEMINI_API_KEY is not set; captcha retries will run OCR-only", flush=True)

    retry_workers = args.retry_workers or spec.workers
    round_log_path = run_root / "logs" / "canonical_case_list_retry_rounds.jsonl"

    print(
        json.dumps(
            {
                "run_id": spec.run_id,
                "config": str(config_path),
                "expected_queries": expected_queries,
                "workers": spec.workers,
                "retry_workers": retry_workers,
                "max_retry_rounds": args.max_retry_rounds,
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )

    run_batch_scrape(app_config=app_config, config_path=config_path, run_id_override=None)
    previous_audit = _audit_run(run_root=run_root, run_id=spec.run_id, expected_queries=expected_queries)
    _append_round_log(round_log_path, {"round": 0, **previous_audit})

    for round_number in range(1, args.max_retry_rounds + 1):
        if previous_audit["coverage_complete"]:
            break
        print(
            f"retry_round={round_number} raw_case_lists={previous_audit['raw_case_list_html_count']}/{expected_queries} "
            f"unresolved_incomplete={previous_audit['unresolved_incomplete_count']}",
            flush=True,
        )
        _run_retry(run_id=spec.run_id, config_path=config_path, workers=retry_workers)
        current_audit = _audit_run(run_root=run_root, run_id=spec.run_id, expected_queries=expected_queries)
        _append_round_log(round_log_path, {"round": round_number, **current_audit})
        progressed = (
            current_audit["raw_case_list_html_count"] > previous_audit["raw_case_list_html_count"]
            or current_audit["unresolved_incomplete_count"] < previous_audit["unresolved_incomplete_count"]
        )
        previous_audit = current_audit
        if current_audit["coverage_complete"]:
            break
        if not progressed:
            print("retry coverage stalled; stopping early", flush=True)
            break

    final_audit = _audit_run(run_root=run_root, run_id=spec.run_id, expected_queries=expected_queries)
    print(json.dumps(final_audit, indent=2, ensure_ascii=False), flush=True)
    return 0 if final_audit["coverage_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
