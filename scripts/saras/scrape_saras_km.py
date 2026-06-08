#!/usr/bin/env python3
"""Scrape minimal Saras (Ahmedabad) case data for a Kaplan-Meier analysis.

Reads two caselist HTML snapshots (pending + disposed), extracts the per-case
``viewHistory`` payloads, and fetches each case's detail page via
``home/viewHistory`` through a sticky-proxy thread pool. We only need the
lifecycle dates (filing / first-hearing / decision) and the hearing count, so
per-hearing business text and PDFs are deliberately skipped.

One worker thread owns one proxy for its whole lifetime: the eCourts app_token
is session-bound, so the egress IP must stay sticky for the session. With N
working proxies the natural optimum is N threads (one session each).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from district_court_api_scraper.config import AppConfig  # noqa: E402
from district_court_api_scraper.ecourts_client import ECourtsClient  # noqa: E402
from district_court_api_scraper.parsers import (  # noqa: E402
    parse_case_detail_summary,
    parse_case_list_items,
)
from district_court_api_scraper.transport import ECourtsTransport  # noqa: E402


def _is_not_found(payload: dict | None) -> bool:
    if not payload:
        return True
    blob = json.dumps(payload)[:500].lower()
    return "search page not found" in blob or "not found" in blob


def load_tasks(pending_html: Path, disposed_html: Path) -> list[dict]:
    tasks: list[dict] = []
    for status, fn in [("pending", pending_html), ("disposed", disposed_html)]:
        rows = parse_case_list_items(fn.read_text(encoding="utf-8", errors="ignore"))
        for row in rows:
            vh = row.get("view_history") or {}
            cino = str(vh.get("cino", "")).strip()
            if not cino:
                continue
            tasks.append(
                {
                    "status_bucket": status,
                    "cino": cino,
                    "case_number": row.get("case_number", ""),
                    "petitioner": row.get("petitioner", ""),
                    "respondent": row.get("respondent", ""),
                    "vh": vh,
                }
            )
    return tasks


def extract_fields(task: dict, detail_html: str) -> dict:
    summary = parse_case_detail_summary(detail_html)
    cd = summary.get("case_details") or {}
    cs = summary.get("case_status") or {}
    history = summary.get("history_rows") or []
    hearing_dates = [
        str(r.get("business_on_date", "")).strip()
        for r in history
        if isinstance(r, dict) and str(r.get("business_on_date", "")).strip()
    ]
    return {
        "cino": task["cino"],
        "status_bucket": task["status_bucket"],
        "case_number": task["case_number"],
        "case_type": str(cd.get("Case Type", "")),
        "filing_date": str(cd.get("Filing Date", "")),
        "reg_date": str(cd.get("Registration Date", "")),
        "first_hearing_date": str(cs.get("First Hearing Date", "")),
        "next_hearing_date": str(cs.get("Next Hearing Date", "")),
        "decision_date": str(cs.get("Decision Date", "")),
        "case_status": str(cs.get("Case Status", cs.get("Case Stage", ""))),
        "disposal_type": str(cs.get("Nature of Disposal", "")),
        "n_hearings": len(history),
        "hearing_dates": hearing_dates,
        "petitioner": task["petitioner"],
        "respondent": task["respondent"],
    }


class Writer:
    def __init__(self, out_jsonl: Path, fail_jsonl: Path):
        self._lock = threading.Lock()
        self._out = out_jsonl.open("a", encoding="utf-8")
        self._fail = fail_jsonl.open("a", encoding="utf-8")
        self.ok = 0
        self.failed = 0

    def write_ok(self, rec: dict) -> None:
        with self._lock:
            self._out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._out.flush()
            self.ok += 1

    def write_fail(self, rec: dict) -> None:
        with self._lock:
            self._fail.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fail.flush()
            self.failed += 1

    def close(self) -> None:
        self._out.close()
        self._fail.close()


def worker(
    *,
    name: str,
    proxy: str,
    cfg: AppConfig,
    q: "Queue[dict]",
    writer: Writer,
    min_delay: float,
    raw_dir: Path | None,
) -> None:
    transport = ECourtsTransport(
        base_url=cfg.base_url,
        user_agent=cfg.user_agent,
        min_delay_seconds=min_delay,
        max_retries=cfg.max_retries,
        timeout_seconds=cfg.request_timeout_seconds,
        proxy_url=proxy,
    )
    client = ECourtsClient(transport)
    if not client.bootstrap():
        # Couldn't establish a session on this proxy; push everything back.
        return
    while True:
        try:
            task = q.get_nowait()
        except Empty:
            return
        vh = task["vh"]
        try:
            resp = client.view_history(
                case_no=str(vh.get("case_no", "")),
                cino=str(vh.get("cino", "")),
                court_code=str(vh.get("court_code", "")),
                hideparty=str(vh.get("hideparty", "")),
                search_flag=str(vh.get("search_flag", "")),
                state_code=str(vh.get("state_code", "")),
                dist_code=str(vh.get("dist_code", "")),
                court_complex_code=str(vh.get("court_complex_code", "")),
                search_by=str(vh.get("search_by", "CSact")),
            )
            if _is_not_found(resp):
                writer.write_fail({**{k: task[k] for k in ("cino", "status_bucket", "case_number")}, "error": "viewHistory not-found/empty", "worker": name})
                continue
            detail_html = str(resp.get("data_list", ""))
            if not detail_html.strip():
                writer.write_fail({**{k: task[k] for k in ("cino", "status_bucket", "case_number")}, "error": "empty detail html", "worker": name})
                continue
            rec = extract_fields(task, detail_html)
            rec["worker"] = name
            if raw_dir is not None:
                (raw_dir / f"{task['status_bucket']}_{task['cino']}.html").write_text(detail_html, encoding="utf-8")
            writer.write_ok(rec)
        except Exception as exc:  # noqa: BLE001 - record and continue
            writer.write_fail({**{k: task[k] for k in ("cino", "status_bucket", "case_number")}, "error": f"{type(exc).__name__}: {exc}", "worker": name})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Scrape Saras case minimal data for KM analysis")
    p.add_argument("--pending-html", default="tmp/20260528_saras_pending.html")
    p.add_argument("--disposed-html", default="tmp/20260528_saras_disposed.html")
    p.add_argument("--proxy-file", default="configs/runtime/proxy_ips.txt")
    p.add_argument("--run-id", default=None)
    p.add_argument("--workers", type=int, default=0, help="0 = one per proxy")
    p.add_argument("--min-delay", type=float, default=1.2)
    p.add_argument("--limit", type=int, default=0, help="cap tasks for smoke testing")
    p.add_argument("--save-raw", action="store_true", help="persist each detail HTML")
    args = p.parse_args(argv)

    cfg = AppConfig.default(project_root=ROOT)
    proxies = [ln.strip() for ln in Path(args.proxy_file).read_text().splitlines() if ln.strip()]
    if not proxies:
        print("No proxies in proxy file", file=sys.stderr)
        return 2
    n_workers = args.workers or len(proxies)

    run_id = args.run_id or f"saras_km_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_root = cfg.output_runs_dir / run_id
    norm_dir = run_root / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = None
    if args.save_raw:
        raw_dir = run_root / "raw" / "case_details"
        raw_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = norm_dir / "cases_min.jsonl"
    fail_jsonl = norm_dir / "incomplete.jsonl"

    tasks = load_tasks(Path(args.pending_html), Path(args.disposed_html))
    if args.limit:
        # interleave pending/disposed for a representative smoke sample
        pend = [t for t in tasks if t["status_bucket"] == "pending"][: args.limit // 2]
        disp = [t for t in tasks if t["status_bucket"] == "disposed"][: args.limit - len(pend)]
        tasks = pend + disp

    # Resume: skip cinos already present in cases_min.jsonl
    done: set[str] = set()
    if out_jsonl.exists():
        for ln in out_jsonl.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(ln)["cino"])
            except Exception:  # noqa: BLE001
                pass
    tasks = [t for t in tasks if t["cino"] not in done]

    print(f"run_id={run_id}")
    print(f"tasks={len(tasks)} (skipped {len(done)} already done) workers={n_workers} proxies={len(proxies)}")
    if not tasks:
        print("nothing to do")
        return 0

    q: "Queue[dict]" = Queue()
    for t in tasks:
        q.put(t)
    writer = Writer(out_jsonl, fail_jsonl)

    started = time.time()
    threads: list[threading.Thread] = []
    for i in range(n_workers):
        th = threading.Thread(
            target=worker,
            kwargs=dict(
                name=f"w{i:02d}",
                proxy=proxies[i % len(proxies)],
                cfg=cfg,
                q=q,
                writer=writer,
                min_delay=args.min_delay,
                raw_dir=raw_dir,
            ),
            daemon=True,
        )
        th.start()
        threads.append(th)

    # progress loop
    total = len(tasks)
    while any(th.is_alive() for th in threads):
        time.sleep(10)
        remaining = q.qsize()
        elapsed = time.time() - started
        rate = (writer.ok + writer.failed) / elapsed if elapsed else 0
        eta = remaining / rate if rate else 0
        print(f"  [{elapsed:6.0f}s] ok={writer.ok} fail={writer.failed} remaining~{remaining} rate={rate:.1f}/s eta~{eta/60:.1f}m", flush=True)

    for th in threads:
        th.join()
    writer.close()
    elapsed = time.time() - started
    print(f"DONE ok={writer.ok} fail={writer.failed} elapsed={elapsed/60:.1f}m")
    print(f"output={out_jsonl}")
    print(f"incompletes={fail_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
