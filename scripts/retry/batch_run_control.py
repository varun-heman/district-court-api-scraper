from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or control a configurable batch scrape run.")
    parser.add_argument("--run-root", required=True, help="Path to output/runs/<run_id>")
    parser.add_argument("command", choices=["status", "pause", "resume"])
    return parser.parse_args()


def _progress_path(run_root: Path) -> Path:
    return run_root / "progress_batch_scrape.json"


def _summary_path(run_root: Path) -> Path:
    return run_root / "summary_batch_scrape.json"


def _pause_path(run_root: Path) -> Path:
    return run_root / "control" / "pause"


def _print_status(run_root: Path) -> int:
    progress_path = _progress_path(run_root)
    summary_path = _summary_path(run_root)
    if progress_path.exists():
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    elif summary_path.exists():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        print(f"No progress or summary file found under {run_root}")
        return 1

    lines = [
        f"run_id: {payload.get('run_id', '')}",
        f"status: {payload.get('status', 'completed' if summary_path.exists() else 'unknown')}",
        f"queries: {payload.get('completed_queries', payload.get('queries', ''))}/{payload.get('total_queries', payload.get('queries', ''))}",
        f"workers: {payload.get('workers', '')}",
        f"active: {payload.get('active_queries', '')}",
        f"queued: {payload.get('queued_queries', '')}",
        f"case_lists: {payload.get('case_list_items', '')}",
        f"case_details: {payload.get('case_details_items', '')}",
        f"incomplete: {payload.get('incomplete_count', '')}",
        f"elapsed_seconds: {payload.get('elapsed_seconds', '')}",
        f"eta: {payload.get('eta_text', '')}",
        f"pause_requested: {payload.get('pause_requested', _pause_path(run_root).exists())}",
        f"last_query_slug: {payload.get('last_query_slug', '')}",
    ]
    print("\n".join(lines))
    return 0


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    pause_path = _pause_path(run_root)
    pause_path.parent.mkdir(parents=True, exist_ok=True)

    if args.command == "status":
        return _print_status(run_root)
    if args.command == "pause":
        pause_path.write_text("pause\n", encoding="utf-8")
        print(f"Pause requested: {pause_path}")
        return 0
    if args.command == "resume":
        if pause_path.exists():
            pause_path.unlink()
        print(f"Pause cleared: {pause_path}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
