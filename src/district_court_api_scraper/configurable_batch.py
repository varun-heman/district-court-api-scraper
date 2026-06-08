from __future__ import annotations

import collections
import csv
import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .captcha import CaptchaAttemptPolicy, CaptchaSolver
from .config import AppConfig, RunPaths, new_run_id
from .ecourts_client import ECourtsClient
from .proxy_pool import RoundRobinProxyPool, load_pool_from_env
from .parsers import (
    parse_case_detail_summary,
    parse_case_list_items,
    parse_display_pdf_calls,
    parse_view_business_calls,
)
from .structured_exports import TARGET_CASE_COLUMNS, TARGET_CASELIST_COLUMNS, TARGET_HEARING_COLUMNS
from .transport import ECourtsTransport

try:
    from tqdm import tqdm as _tqdm
    _TQDM_AVAILABLE = True
except ImportError:
    _TQDM_AVAILABLE = False


SEARCH_PAGE_NOT_FOUND_RE = re.compile(r"search\s+page\s+not\s+found", re.IGNORECASE)
NO_RECORD_RE = re.compile(r"record\s+not\s+found|no\s+record", re.IGNORECASE)
DEFAULT_TARGET_YEARS = {"2023", "2024", "2025", "2026"}
DISPOSED_PURPOSE_RE = re.compile(
    r"dispos|convict|acquit|abated|dismiss|withdraw|settled|closed|compoun|uncontested|contested",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CaseTypeSpec:
    act_ids: list[str]
    section: str
    statuses: list[str]


@dataclass(slots=True)
class DistrictTargetSpec:
    state: str
    district: str
    court_complexes: list[str] | str
    case_types: list[CaseTypeSpec]
    expand_establishments: bool = False


@dataclass(slots=True)
class BatchScrapeSpec:
    run_id: str | None
    workers: int
    target_years: set[str]
    min_cnr_year: int | None
    sample_size: int | None
    district_courts_config_path: Path
    targets: list[DistrictTargetSpec]
    allowed_case_keys: set[str] | None
    fetch_hearings: bool
    fetch_pdfs: bool
    progress_log_interval_seconds: float


@dataclass(slots=True)
class QuerySpec:
    state_name: str
    district_name: str
    state_code: str
    district_code: str
    court_complex_name: str
    court_complex_code: str
    est_code: str
    act_id: str
    section: str
    status: str
    slug: str


@dataclass(slots=True)
class QueryResult:
    case_list_items: list[dict[str, Any]]
    case_details_items: list[dict[str, Any]]
    hearings_items: list[dict[str, Any]]
    cases_rows: list[dict[str, Any]]
    hearings_rows: list[dict[str, Any]]
    incomplete_items: list[dict[str, Any]]
    pdf_download_count: int


@dataclass(slots=True)
class BatchControlPaths:
    control_dir: Path
    pause_path: Path
    progress_path: Path


def _build_batch_control(run_paths: RunPaths) -> BatchControlPaths:
    control_dir = run_paths.run_root / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    return BatchControlPaths(
        control_dir=control_dir,
        pause_path=control_dir / "pause",
        progress_path=run_paths.run_root / "progress_batch_scrape.json",
    )


def _wait_for_pause_release(control: BatchControlPaths | None) -> None:
    if control is None:
        return
    while control.pause_path.exists():
        time.sleep(2.0)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_eta_seconds(value: float | None) -> str:
    if value is None or value < 0:
        return ""
    total_seconds = int(round(value))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes}m {seconds:02d}s"


def _build_progress_payload(
    *,
    run_id: str,
    status: str,
    workers: int,
    total_queries: int,
    started_queries: int,
    completed_queries: int,
    active_queries: int,
    queued_queries: int,
    case_list_items: int,
    case_details_items: int,
    hearing_items: int,
    cases_csv_rows: int,
    hearings_csv_rows: int,
    incomplete_count: int,
    pdf_download_count: int,
    started_at_monotonic: float,
    pause_requested: bool,
    last_query_slug: str,
    progress_path: Path,
    pause_path: Path,
) -> dict[str, Any]:
    elapsed_seconds = max(time.monotonic() - started_at_monotonic, 0.0)
    queries_per_second = (completed_queries / elapsed_seconds) if elapsed_seconds > 0 and completed_queries > 0 else 0.0
    remaining_queries = max(total_queries - completed_queries, 0)
    eta_seconds = (remaining_queries / queries_per_second) if queries_per_second > 0 else None
    eta_text = _format_eta_seconds(eta_seconds)
    return {
        "run_id": run_id,
        "status": status,
        "timestamp_utc": _utc_now_iso(),
        "workers": workers,
        "total_queries": total_queries,
        "started_queries": started_queries,
        "completed_queries": completed_queries,
        "remaining_queries": remaining_queries,
        "active_queries": active_queries,
        "queued_queries": queued_queries,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "queries_per_second": round(queries_per_second, 4),
        "eta_seconds": round(eta_seconds, 2) if eta_seconds is not None else None,
        "eta_text": eta_text,
        "case_list_items": case_list_items,
        "case_details_items": case_details_items,
        "hearing_items": hearing_items,
        "cases_csv_rows": cases_csv_rows,
        "hearings_csv_rows": hearings_csv_rows,
        "incomplete_count": incomplete_count,
        "pdf_download_count": pdf_download_count,
        "pause_requested": pause_requested,
        "last_query_slug": last_query_slug,
        "progress_path": str(progress_path),
        "pause_path": str(pause_path),
    }


def run_batch_scrape(
    *,
    app_config: AppConfig,
    config_path: Path,
    run_id_override: str | None = None,
) -> dict[str, Any]:
    spec = load_batch_spec(config_path)
    run_id = run_id_override or spec.run_id or new_run_id("district_batch")
    run_paths = app_config.ensure_run_paths(run_id)
    control = _build_batch_control(run_paths)
    queries = _build_queries(spec=spec)
    proxy_pool: RoundRobinProxyPool | None = None
    if app_config.proxy_file:
        proxy_pool = RoundRobinProxyPool(app_config.proxy_file)

    all_case_lists: list[dict[str, Any]] = []
    all_case_details: list[dict[str, Any]] = []
    all_hearings: list[dict[str, Any]] = []
    all_cases_rows: list[dict[str, Any]] = []
    all_hearings_rows: list[dict[str, Any]] = []
    all_incomplete: list[dict[str, Any]] = []
    total_pdf_downloads = 0
    started_at = time.monotonic()
    started_queries = 0
    completed_queries = 0
    next_progress_write_at = started_at
    last_status = "running"
    last_query_slug = ""
    # Rolling window for ETA (last 20 query completion times)
    _completion_times: collections.deque[float] = collections.deque(maxlen=20)
    _bar = _tqdm(
        total=len(queries),
        unit="query",
        desc=f"{run_id}",
        dynamic_ncols=True,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{rate_fmt}{postfix}]",
    ) if _TQDM_AVAILABLE else None

    def write_progress(*, status: str, force: bool = False) -> None:
        nonlocal next_progress_write_at, last_status
        now = time.monotonic()
        if not force and status == last_status and now < next_progress_write_at:
            return
        payload = _build_progress_payload(
            run_id=run_id,
            status=status,
            workers=spec.workers,
            total_queries=len(queries),
            started_queries=started_queries,
            completed_queries=completed_queries,
            active_queries=len(futures),
            queued_queries=max(len(queries) - started_queries, 0),
            case_list_items=len(all_case_lists),
            case_details_items=len(all_case_details),
            hearing_items=len(all_hearings),
            cases_csv_rows=len(all_cases_rows),
            hearings_csv_rows=len(all_hearings_rows),
            incomplete_count=len(all_incomplete),
            pdf_download_count=total_pdf_downloads,
            started_at_monotonic=started_at,
            pause_requested=control.pause_path.exists(),
            last_query_slug=last_query_slug,
            progress_path=control.progress_path,
            pause_path=control.pause_path,
        )
        control.progress_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        last_status = status
        next_progress_write_at = now + spec.progress_log_interval_seconds
        # Rolling ETA based on last 20 completed queries
        rolling_eta = "unknown"
        if len(_completion_times) >= 2:
            intervals = [_completion_times[i] - _completion_times[i-1] for i in range(1, len(_completion_times))]
            avg_interval = sum(intervals) / len(intervals)
            remaining = max(payload['total_queries'] - payload['completed_queries'], 0)
            rolling_eta = _format_eta_seconds(avg_interval * remaining)
        elapsed_str = _format_eta_seconds(payload['elapsed_seconds'])
        status_line = " ".join([
            f"status={payload['status']}",
            f"queries={payload['completed_queries']}/{payload['total_queries']}",
            f"active={payload['active_queries']}",
            f"elapsed={elapsed_str}",
            f"case_lists={payload['case_list_items']}",
            f"case_details={payload['case_details_items']}",
            f"incomplete={payload['incomplete_count']}",
            f"eta={rolling_eta}",
        ])
        if _bar is not None:
            _bar.set_postfix_str(f"cases={payload['case_details_items']} incomplete={payload['incomplete_count']} elapsed={elapsed_str} eta={rolling_eta}", refresh=False)
        else:
            print(status_line, flush=True)

    def wait_if_paused() -> None:
        paused_logged = False
        while control.pause_path.exists():
            if not paused_logged:
                write_progress(status="paused", force=True)
                paused_logged = True
            time.sleep(2.0)
        if paused_logged:
            write_progress(status="running", force=True)

    futures: dict[Future[QueryResult], QuerySpec] = {}
    next_query_index = 0

    with ThreadPoolExecutor(max_workers=spec.workers) as executor:
        def submit_available() -> None:
            nonlocal next_query_index, started_queries
            while len(futures) < spec.workers and next_query_index < len(queries):
                wait_if_paused()
                query = queries[next_query_index]
                next_query_index += 1
                future = executor.submit(
                    _run_query,
                    query=query,
                    app_config=app_config,
                    run_paths=run_paths,
                    target_years=spec.target_years,
                    min_cnr_year=spec.min_cnr_year,
                    allowed_case_keys=spec.allowed_case_keys,
                    fetch_hearings=spec.fetch_hearings,
                    fetch_pdfs=spec.fetch_pdfs,
                    control=control,
                    log_fn=_bar.write if _bar is not None else None,
                    proxy_pool=proxy_pool,
                )
                futures[future] = query
                started_queries += 1

        submit_available()
        write_progress(status="running", force=True)

        while futures:
            wait_if_paused()
            done, _ = wait(set(futures), return_when=FIRST_COMPLETED, timeout=1.0)
            if not done:
                write_progress(status="running")
                continue
            for future in done:
                query = futures.pop(future)
                last_query_slug = query.slug
                try:
                    result = future.result()
                except Exception as exc:
                    all_incomplete.append(
                        {
                            "task_type": "QUERY",
                            "query": _as_query_dict(query),
                            "error": f"Unhandled query exception: {exc}",
                        }
                    )
                    completed_queries += 1
                    write_progress(status="running", force=True)
                    continue
                all_case_lists.extend(result.case_list_items)
                all_case_details.extend(result.case_details_items)
                all_hearings.extend(result.hearings_items)
                all_cases_rows.extend(result.cases_rows)
                all_hearings_rows.extend(result.hearings_rows)
                all_incomplete.extend(result.incomplete_items)
                total_pdf_downloads += result.pdf_download_count
                completed_queries += 1
                _completion_times.append(time.monotonic())
                if _bar is not None:
                    _bar.update(1)
                    _bar.write(f"✓ [{query.district_name}] {query.court_complex_name} → {query.status} | {len(result.case_list_items)} listed, {len(result.case_details_items)} details")
                write_progress(status="running")
            submit_available()

    if _bar is not None:
        _bar.close()

    all_case_lists = _dedupe_case_lists(all_case_lists)
    all_case_details = _dedupe_case_details(all_case_details)
    all_hearings = _dedupe_hearings(all_hearings)
    all_cases_rows = _dedupe_rows(all_cases_rows, key_fields=["cnr", "filing_no", "reg_no"])
    all_hearings_rows = _dedupe_rows(
        all_hearings_rows,
        key_fields=["cnr", "order_no", "orders_date", "business_text"],
    )
    all_case_lists.sort(key=lambda x: (x.get("view_history", {}).get("cino", ""), x.get("case_number", "")))
    all_cases_rows.sort(key=lambda x: x.get("cnr", ""))
    all_hearings_rows.sort(key=lambda x: (x.get("cnr", ""), x.get("order_no", ""), x.get("orders_date", "")))

    _write_jsonl(run_paths.case_list_items_jsonl, all_case_lists)
    _write_jsonl(run_paths.case_details_jsonl, all_case_details)
    _write_jsonl(run_paths.hearings_jsonl, all_hearings)
    _write_jsonl(run_paths.incomplete_tasks_jsonl, all_incomplete)

    _write_csv(run_paths.exports_dir / "cases.csv", TARGET_CASE_COLUMNS, all_cases_rows)
    _write_csv(run_paths.exports_dir / "hearings.csv", TARGET_HEARING_COLUMNS, all_hearings_rows)
    _write_csv(
        run_paths.exports_dir / "caselists.csv",
        TARGET_CASELIST_COLUMNS,
        _to_caselists_rows(all_case_lists),
    )

    sample_cases_csv: str | None = None
    sample_hearings_csv: str | None = None
    if spec.sample_size and spec.sample_size > 0:
        sample_cases_path = run_paths.exports_dir / "sample_cases.csv"
        sample_hearings_path = run_paths.exports_dir / "sample_hearings.csv"
        _write_csv(sample_cases_path, TARGET_CASE_COLUMNS, all_cases_rows[: spec.sample_size])
        _write_csv(sample_hearings_path, TARGET_HEARING_COLUMNS, all_hearings_rows[: spec.sample_size])
        sample_cases_csv = str(sample_cases_path)
        sample_hearings_csv = str(sample_hearings_path)

    summary = {
        "run_id": run_id,
        "queries": len(queries),
        "workers": spec.workers,
        "target_years": sorted(spec.target_years),
        "min_cnr_year": spec.min_cnr_year,
        "case_list_items": len(all_case_lists),
        "case_details_items": len(all_case_details),
        "hearing_items": len(all_hearings),
        "cases_csv_rows": len(all_cases_rows),
        "hearings_csv_rows": len(all_hearings_rows),
        "caselists_csv_rows": len(_to_caselists_rows(all_case_lists)),
        "incomplete_count": len(all_incomplete),
        "pdf_download_count": total_pdf_downloads,
        "cases_csv": str(run_paths.exports_dir / "cases.csv"),
        "hearings_csv": str(run_paths.exports_dir / "hearings.csv"),
        "caselists_csv": str(run_paths.exports_dir / "caselists.csv"),
        "sample_cases_csv": sample_cases_csv or "",
        "sample_hearings_csv": sample_hearings_csv or "",
        "progress_path": str(control.progress_path),
        "pause_path": str(control.pause_path),
    }
    (run_paths.run_root / "summary_batch_scrape.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_progress(status="completed", force=True)
    return summary


def load_batch_spec(config_path: Path) -> BatchScrapeSpec:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    district_cfg_path = Path(str(payload["district_courts_config_path"]))
    if not district_cfg_path.is_absolute():
        district_cfg_path = (config_path.parent / district_cfg_path).resolve()
    targets: list[DistrictTargetSpec] = []
    for target in payload.get("targets", []):
        case_types: list[CaseTypeSpec] = []
        for case_type in target.get("case_types", []):
            case_types.append(
                CaseTypeSpec(
                    act_ids=[str(x) for x in case_type.get("act_ids", [])],
                    section=str(case_type.get("section", "")),
                    statuses=[str(x) for x in case_type.get("statuses", ["Pending", "Disposed"])],
                )
            )
        targets.append(
            DistrictTargetSpec(
                state=str(target.get("state", "")),
                district=str(target.get("district", "")),
                court_complexes=target.get("court_complexes", "all"),
                case_types=case_types,
                expand_establishments=bool(target.get("expand_establishments", False)),
            )
        )
    years = {str(x) for x in payload.get("target_cnr_years", sorted(DEFAULT_TARGET_YEARS))}
    min_cnr_year = int(payload["min_cnr_year"]) if payload.get("min_cnr_year") is not None else None
    allowed_case_keys: set[str] | None = None
    if payload.get("allowed_case_keys_path"):
        allowed_path = Path(str(payload["allowed_case_keys_path"]))
        if not allowed_path.is_absolute():
            allowed_path = (config_path.parent / allowed_path).resolve()
        if allowed_path.exists():
            allowed_case_keys = {
                line.strip()
                for line in allowed_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
    return BatchScrapeSpec(
        run_id=str(payload.get("run_id")) if payload.get("run_id") else None,
        workers=max(1, int(payload.get("workers", 16))),
        target_years=years,
        min_cnr_year=min_cnr_year,
        sample_size=int(payload["sample_size"]) if payload.get("sample_size") is not None else None,
        district_courts_config_path=district_cfg_path,
        targets=targets,
        allowed_case_keys=allowed_case_keys,
        fetch_hearings=bool(payload.get("fetch_hearings", True)),
        fetch_pdfs=bool(payload.get("fetch_pdfs", True)),
        progress_log_interval_seconds=max(1.0, float(payload.get("progress_log_interval_seconds", 15.0))),
    )


def _build_queries(*, spec: BatchScrapeSpec) -> list[QuerySpec]:
    district_config = json.loads(spec.district_courts_config_path.read_text(encoding="utf-8"))
    queries: list[QuerySpec] = []
    for target in spec.targets:
        state_block = district_config.get(target.state) or {}
        state_code = str(state_block.get("stvalue", ""))
        district_block = (state_block.get("districts") or {}).get(target.district) or {}
        district_code = str(district_block.get("dstvalue", ""))
        courts_map = district_block.get("court_complexes") or {}
        if isinstance(target.court_complexes, str) and target.court_complexes.lower() == "all":
            court_names = list(courts_map.keys())
        else:
            court_names = [str(name) for name in target.court_complexes]

        for court_name in court_names:
            court_cfg = courts_map.get(court_name) or {}
            crtvalue = str(court_cfg.get("crtvalue", ""))
            court_complex_code, est_codes_from_crt = _parse_crtvalue(crtvalue)
            if not court_complex_code:
                continue
            est_codes = _choose_est_codes(
                court_cfg=court_cfg,
                est_codes_from_crt=est_codes_from_crt,
                expand=target.expand_establishments,
            )
            for case_type in target.case_types:
                for act_id in case_type.act_ids:
                    for status in case_type.statuses:
                        normalized_status = _normalize_status(status)
                        for est_code in est_codes:
                            slug = _safe_name(
                                f"{target.state}_{target.district}_{court_name}_{act_id}_{normalized_status}_est_{est_code or 'none'}"
                            )
                            queries.append(
                                QuerySpec(
                                    state_name=target.state,
                                    district_name=target.district,
                                    state_code=state_code,
                                    district_code=district_code,
                                    court_complex_name=court_name,
                                    court_complex_code=court_complex_code,
                                    est_code=est_code,
                                    act_id=str(act_id),
                                    section=case_type.section,
                                    status=normalized_status,
                                    slug=slug,
                                )
                            )
    return queries


def _choose_est_codes(*, court_cfg: dict[str, Any], est_codes_from_crt: list[str], expand: bool) -> list[str]:
    explicit_ests: list[str] = []
    establishments = court_cfg.get("establishments") or {}
    if isinstance(establishments, dict):
        for item in establishments.values():
            if isinstance(item, dict) and item.get("estvalue"):
                explicit_ests.append(str(item["estvalue"]))
    merged = explicit_ests + est_codes_from_crt
    merged = [x for i, x in enumerate(merged) if x and x not in merged[:i]]
    if expand:
        return merged or [""]
    return [merged[0]] if merged else [""]


def _run_query(
    *,
    query: QuerySpec,
    app_config: AppConfig,
    run_paths: RunPaths,
    target_years: set[str],
    min_cnr_year: int | None,
    allowed_case_keys: set[str] | None,
    fetch_hearings: bool,
    fetch_pdfs: bool = False,
    control: BatchControlPaths | None = None,
    log_fn: Any = None,
    proxy_pool: RoundRobinProxyPool | None = None,
) -> QueryResult:
    transport = ECourtsTransport(
        base_url=app_config.base_url,
        user_agent=app_config.user_agent,
        min_delay_seconds=app_config.min_delay_seconds,
        max_retries=app_config.max_retries,
        timeout_seconds=app_config.request_timeout_seconds,
        proxy_url=proxy_pool.next() if proxy_pool else None,
        proxy_pool=proxy_pool,
    )
    client = ECourtsClient(transport)
    solver = CaptchaSolver()
    policy = CaptchaAttemptPolicy()

    case_list_items: list[dict[str, Any]] = []
    case_details_items: list[dict[str, Any]] = []
    hearings_items: list[dict[str, Any]] = []
    cases_rows: list[dict[str, Any]] = []
    hearings_rows: list[dict[str, Any]] = []
    incomplete_items: list[dict[str, Any]] = []
    pdf_download_count = 0

    raw_case_list_path = run_paths.raw_case_lists / f"{query.slug}.html"
    if raw_case_list_path.exists():
        act_html = raw_case_list_path.read_text(encoding="utf-8", errors="ignore")
    else:
        _wait_for_pause_release(control)
        act_html, submit_error = _submit_act_html(
            client=client,
            solver=solver,
            policy=policy,
            query=query,
        )
        if submit_error:
            incomplete_items.append(
                {
                    "task_type": "ACT_LIST",
                    "query": _as_query_dict(query),
                    "error": submit_error,
                }
            )
            return QueryResult(
                case_list_items=case_list_items,
                case_details_items=case_details_items,
                hearings_items=hearings_items,
                cases_rows=cases_rows,
                hearings_rows=hearings_rows,
                incomplete_items=incomplete_items,
                pdf_download_count=pdf_download_count,
            )
        raw_case_list_path.write_text(act_html, encoding="utf-8")

    parsed_rows = parse_case_list_items(act_html)

    for row in parsed_rows:
        vh = row.get("view_history") or {}
        cino = str(vh.get("cino", ""))
        if not cino:
            continue
        list_item = {
            "source_state": query.state_name,
            "source_district": query.district_name,
            "source_court_complex_name": query.court_complex_name,
            "source_act_id": query.act_id,
            "status_bucket": query.status.lower(),
            **row,
        }
        selector_key = _build_selector_key(list_item)
        list_item["selector_key"] = selector_key
        case_list_items.append(list_item)
        if allowed_case_keys is not None and selector_key not in allowed_case_keys:
            continue
        if allowed_case_keys is None and not _cnr_in_targets(cino=cino, target_years=target_years, min_cnr_year=min_cnr_year):
            continue

        _wait_for_pause_release(control)
        case_row, hearing_rows, case_detail_item, case_hearing_items, case_incomplete, pdf_count = _fetch_case_and_hearings(
            client=client,
            run_paths=run_paths,
            query=query,
            list_item=list_item,
            fetch_hearings=fetch_hearings,
            fetch_pdfs=fetch_pdfs,
        )
        pdf_download_count += pdf_count
        incomplete_items.extend(case_incomplete)
        if log_fn is not None:
            district = f"[{query.district_name}]".ljust(18)
            complex_name = query.court_complex_name[:26].ljust(26)
            cnr = cino.ljust(18)
            log_fn(f"\n\033[32m✓\033[0m {district} {complex_name} | {cnr} | h={len(case_hearing_items)}")
        if case_row is not None:
            cases_rows.append(case_row)
        if hearing_rows:
            hearings_rows.extend(hearing_rows)
        if case_detail_item is not None:
            case_details_items.append(case_detail_item)
        if case_hearing_items:
            hearings_items.extend(case_hearing_items)

    return QueryResult(
        case_list_items=case_list_items,
        case_details_items=case_details_items,
        hearings_items=hearings_items,
        cases_rows=cases_rows,
        hearings_rows=hearings_rows,
        incomplete_items=incomplete_items,
        pdf_download_count=pdf_download_count,
    )


def _submit_act_html(
    *,
    client: ECourtsClient,
    solver: CaptchaSolver,
    policy: CaptchaAttemptPolicy,
    query: QuerySpec,
) -> tuple[str, str | None]:
    for attempt in range(1, policy.max_attempts + 1):
        stage = policy.stage_for_attempt(attempt)
        captcha_image = client.get_captcha_image()
        if not captcha_image or _looks_like_html(captcha_image):
            continue
        token = solver.solve_captcha(captcha_image, stage=stage, variant_index=attempt - 1)
        if not token:
            continue
        response = client.submit_act(
            state_code=query.state_code,
            dist_code=query.district_code,
            court_complex_code=query.court_complex_code,
            est_code=query.est_code,
            actcode=query.act_id,
            section=query.section,
            case_status=query.status,
            captcha_code=token,
        )
        if response is None:
            continue
        if client.is_invalid_captcha(response):
            continue
        if _is_not_found_payload(response):
            return "", "submitAct returned Search Page not Found (session invalid)"
        if NO_RECORD_RE.search(json.dumps(response)):
            return "", None
        if str(response.get("status", "")).strip() not in {"1", "true", "True"}:
            continue
        act_html = str(response.get("act_data", ""))
        if act_html:
            return act_html, None
    return "", "captcha attempts exhausted or submitAct unavailable"


def _fetch_case_and_hearings(
    *,
    client: ECourtsClient,
    run_paths: RunPaths,
    query: QuerySpec,
    list_item: dict[str, Any],
    fetch_hearings: bool,
    fetch_pdfs: bool = False,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
]:
    vh = list_item.get("view_history") or {}
    cino = str(vh.get("cino", ""))
    case_no = str(vh.get("case_no", ""))
    incomplete: list[dict[str, Any]] = []
    pdf_count = 0
    detail_name = _safe_name(f"{query.slug}_{cino}_{case_no}")
    detail_file = run_paths.raw_case_details / f"{detail_name}.html"
    detail_html = ""
    detail_from_cache = False

    if detail_file.exists():
        detail_html = detail_file.read_text(encoding="utf-8", errors="ignore")
        detail_from_cache = True
    else:
        detail_response = client.view_history(
            case_no=case_no,
            cino=cino,
            court_code=str(vh.get("court_code", "")),
            hideparty=str(vh.get("hideparty", "")),
            search_flag=str(vh.get("search_flag", "")),
            state_code=str(vh.get("state_code", query.state_code)),
            dist_code=str(vh.get("dist_code", query.district_code)),
            court_complex_code=str(vh.get("court_complex_code", query.court_complex_code)),
            search_by=str(vh.get("search_by", "CSact")),
        )
        if detail_response is None or _is_not_found_payload(detail_response):
            incomplete.append(
                {
                    "task_type": "CASE_DETAIL",
                    "query": _as_query_dict(query),
                    "cino": cino,
                    "case_no": case_no,
                    "error": "viewHistory non-success/empty",
                }
            )
            return None, [], None, [], incomplete, pdf_count
        detail_html = str(detail_response.get("data_list", ""))
        if not detail_html.strip() or _is_not_found_html(detail_html):
            incomplete.append(
                {
                    "task_type": "CASE_DETAIL",
                    "query": _as_query_dict(query),
                    "cino": cino,
                    "case_no": case_no,
                    "error": "viewHistory empty/invalid detail HTML",
                }
            )
            return None, [], None, [], incomplete, pdf_count
        detail_file.write_text(detail_html, encoding="utf-8")
    detail_summary = parse_case_detail_summary(detail_html)
    history_rows = detail_summary.get("history_rows") or []
    judge = str(history_rows[0].get("judge", "")) if history_rows else ""
    case_details = detail_summary.get("case_details") or {}
    case_status = detail_summary.get("case_status") or {}

    detail_pdf_downloads = []
    if fetch_pdfs and not detail_from_cache:
        detail_pdf_downloads = _download_pdfs(
            client=client,
            run_paths=run_paths,
            html_text=detail_html,
            prefix=f"{detail_name}_detail",
        )
    pdf_count += len(detail_pdf_downloads)

    case_row = {
        "District": query.district_name,
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
        "Status": query.status,
    }

    case_detail_item = {
        "source_state": query.state_name,
        "source_district": query.district_name,
        "source_court_complex_name": query.court_complex_name,
        "source_act_id": query.act_id,
        "source_status": query.status.lower(),
        "case_no": case_no,
        "cino": cino,
        "case_number": list_item.get("case_number", ""),
        "raw_file": str(detail_file),
        "summary": detail_summary,
        "pdf_downloads": detail_pdf_downloads,
    }

    if not fetch_hearings:
        return case_row, [], case_detail_item, [], incomplete, pdf_count

    hearing_rows: list[dict[str, Any]] = []
    hearing_items: list[dict[str, Any]] = []
    purpose_by_date: dict[str, str] = {}
    for row in detail_summary.get("history_rows") or []:
        if isinstance(row, dict):
            business_on = str(row.get("business_on_date", "")).strip()
            purpose = str(row.get("purpose_of_hearing", "")).strip()
            if business_on and purpose:
                purpose_by_date.setdefault(business_on, purpose)
    calls = parse_view_business_calls(detail_html, default_court_complex_code=str(vh.get("court_complex_code", "")))
    for idx, call in enumerate(calls, start=1):
        payload = call.to_payload()
        payload["cino"] = cino
        hearing_name = _safe_name(f"{detail_name}_hearing_{idx}")
        hearing_file = run_paths.raw_hearings / f"{hearing_name}.html"
        hearing_html = ""
        hearing_from_cache = False
        if hearing_file.exists():
            hearing_html = hearing_file.read_text(encoding="utf-8", errors="ignore")
            hearing_from_cache = True
        else:
            hearing_resp = client.view_business(payload)
            if hearing_resp is None or _is_not_found_payload(hearing_resp):
                incomplete.append(
                    {
                        "task_type": "HEARING_DETAIL",
                        "query": _as_query_dict(query),
                        "cino": cino,
                        "case_no": case_no,
                        "error": f"viewBusiness non-success for call {idx}",
                    }
                )
                continue
            hearing_html = str(hearing_resp.get("data_list", ""))
            if not hearing_html.strip() or _is_not_found_html(hearing_html):
                continue
            hearing_file.write_text(hearing_html, encoding="utf-8")
        hearing_summary = parse_case_detail_summary(hearing_html)
        next_purpose = ""
        hrows = hearing_summary.get("history_rows") or []
        if hrows and isinstance(hrows[0], dict):
            next_purpose = str(hrows[0].get("purpose_of_hearing", ""))
        if not next_purpose:
            next_purpose = purpose_by_date.get(str(payload.get("businessDate", "")), "")
        order_rows = _parse_order_rows(hearing_html)
        hearing_pdf_downloads = []
        if fetch_pdfs and not hearing_from_cache:
            hearing_pdf_downloads = _download_pdfs(
                client=client,
                run_paths=run_paths,
                html_text=hearing_html,
                prefix=f"{hearing_name}_hearing",
            )
        pdf_count += len(hearing_pdf_downloads)
        hearing_items.append(
            {
                "cino": cino,
                "payload": payload,
                "raw_file": str(hearing_file),
                "summary": hearing_summary,
                "pdf_downloads": hearing_pdf_downloads,
            }
        )
        if not order_rows:
            hearing_rows.append(
                {
                    "District": query.district_name,
                    "cnr_order_id": f"{cino}_{idx:04d}",
                    "Status": query.status,
                    "cnr": cino,
                    "order_no": "",
                    "orders_date": str(payload.get("businessDate", "")),
                    "business_text": "",
                    "next_purpose": next_purpose,
                }
            )
            continue
        for order in order_rows:
            order_no = str(order.get("order_no", ""))
            suffix = order_no if order_no else f"{idx:04d}"
            hearing_rows.append(
                {
                    "District": query.district_name,
                    "cnr_order_id": f"{cino}_{suffix}",
                    "Status": query.status,
                    "cnr": cino,
                    "order_no": order_no,
                    "orders_date": str(order.get("orders_date", "")),
                    "business_text": str(order.get("business_text", "")),
                    "next_purpose": next_purpose,
                }
            )

    return case_row, hearing_rows, case_detail_item, hearing_items, incomplete, pdf_count


def _download_pdfs(*, client: ECourtsClient, run_paths: RunPaths, html_text: str, prefix: str) -> list[dict[str, Any]]:
    downloads: list[dict[str, Any]] = []
    calls = parse_display_pdf_calls(html_text)
    for idx, call in enumerate(calls, start=1):
        result = client.download_pdf(call.params)
        if not result.ok or result.content is None:
            continue
        filename = _safe_name(f"{prefix}_{idx}.pdf")
        output = run_paths.raw_pdfs / filename
        output.write_bytes(result.content)
        downloads.append(
            {
                "file": str(output),
                "size_bytes": len(result.content),
                "source_url": result.source_url,
                "params": call.params,
            }
        )
    return downloads


def _parse_order_rows(html_text: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    rows: list[dict[str, str]] = []
    for table in soup.find_all("table", class_="order_table"):
        for tr in table.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            order_no = re.sub(r"[^0-9]", "", cols[0].get_text(" ", strip=True))
            if not order_no:
                continue
            rows.append(
                {
                    "order_no": order_no,
                    "orders_date": cols[1].get_text(" ", strip=True),
                    "business_text": cols[2].get_text(" ", strip=True),
                }
            )
    if rows:
        return rows

    business_text = _extract_daily_status_business_text(soup)
    orders_date = _extract_daily_status_date(soup)
    if business_text or orders_date:
        rows.append(
            {
                "order_no": "",
                "orders_date": orders_date,
                "business_text": business_text,
            }
        )
    return rows


def _extract_daily_status_business_text(soup: BeautifulSoup) -> str:
    for b in soup.find_all("b"):
        if "Business" not in b.get_text(" ", strip=True):
            continue
        tr = b.find_parent("tr")
        if tr is None:
            continue
        tds = tr.find_all("td")
        if not tds:
            continue
        value = tds[-1].get_text(" ", strip=True)
        return re.sub(r"\s+", " ", value).strip()
    return ""


def _extract_daily_status_date(soup: BeautifulSoup) -> str:
    text = soup.get_text("\n", strip=True)
    match = re.search(r"Date\s*[:]\s*([0-9]{2}-[0-9]{2}-[0-9]{4})", text)
    if match:
        return match.group(1)
    return ""


def _derive_decision_date(*, case_status: dict[str, Any], history_rows: list[dict[str, Any]]) -> str:
    decision_date = str(case_status.get("Decision Date", "")).strip()
    if decision_date:
        return decision_date
    # Fallback: if history contains explicit disposal-like purpose, use its date.
    for row in reversed(history_rows):
        if not isinstance(row, dict):
            continue
        purpose = str(row.get("purpose_of_hearing", "")).strip()
        if not DISPOSED_PURPOSE_RE.search(purpose):
            continue
        candidate = str(row.get("business_on_date", "")).strip() or str(row.get("hearing_date", "")).strip()
        if candidate:
            return candidate
    return ""


def _to_caselists_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    dedupe: set[str] = set()
    for item in items:
        vh = item.get("view_history") or {}
        cino = str(vh.get("cino", ""))
        case_no = str(vh.get("case_no", ""))
        key = f"{case_no}|{cino}"
        if key in dedupe:
            continue
        dedupe.add(key)
        rows.append(
            {
                "State": str(item.get("source_state", "")),
                "District": str(item.get("source_district", "")),
                "Court Complex": str(item.get("source_court_complex_name", "")),
                "Status": _normalize_status(item.get("status_bucket", "")),
                "Court Name": str(item.get("source_court_complex_name", "")),
                "File Number": str(item.get("case_number", "")),
                "Petitioner": str(item.get("petitioner", "")),
                "Respondent": str(item.get("respondent", "")),
                "CNR": cino,
            }
        )
    rows.sort(key=lambda x: x.get("CNR", ""))
    return rows


def _dedupe_case_lists(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedupe: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        vh = item.get("view_history") or {}
        key = f"{vh.get('case_no','')}|{vh.get('cino','')}"
        if key in dedupe:
            continue
        dedupe.add(key)
        output.append(item)
    return output


def _dedupe_case_details(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedupe: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        key = f"{item.get('case_no','')}|{item.get('cino','')}"
        if key in dedupe:
            continue
        dedupe.add(key)
        output.append(item)
    return output


def _dedupe_hearings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedupe: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        payload = item.get("payload") or {}
        key = "|".join(
            [
                str(item.get("cino", "")),
                str(payload.get("case_number1", "")),
                str(payload.get("businessDate", "")),
                str(payload.get("srno", "")),
            ]
        )
        if key in dedupe:
            continue
        dedupe.add(key)
        output.append(item)
    return output


def _dedupe_rows(rows: list[dict[str, Any]], *, key_fields: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = "|".join(str(row.get(field, "")) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


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


def _parse_crtvalue(crtvalue: str) -> tuple[str, list[str]]:
    parts = (crtvalue or "").split("@")
    court_complex_code = parts[0].strip() if parts else ""
    est_codes: list[str] = []
    if len(parts) >= 2:
        est_codes = [x.strip() for x in parts[1].split(",") if x.strip()]
    return court_complex_code, est_codes


def _cnr_in_years(cino: str, years: set[str]) -> bool:
    return len(cino) >= 4 and cino[-4:] in years


def _cnr_in_targets(*, cino: str, target_years: set[str], min_cnr_year: int | None) -> bool:
    if len(cino) < 4:
        return False
    tail = cino[-4:]
    if not tail.isdigit():
        return False
    year = int(tail)
    if min_cnr_year is not None:
        return year >= min_cnr_year
    return tail in target_years


def _build_selector_key(item: dict[str, Any]) -> str:
    vh = item.get("view_history") or {}
    district = str(item.get("source_district", "")).strip()
    court = str(item.get("source_court_complex_name", "")).strip()
    case_no = str(vh.get("case_no", "")).strip()
    cino = str(vh.get("cino", "")).strip()
    return "|".join([district, court, case_no, cino])


def _as_query_dict(query: QuerySpec) -> dict[str, str]:
    return {
        "state_name": query.state_name,
        "district_name": query.district_name,
        "state_code": query.state_code,
        "district_code": query.district_code,
        "court_complex_name": query.court_complex_name,
        "court_complex_code": query.court_complex_code,
        "est_code": query.est_code,
        "act_id": query.act_id,
        "section": query.section,
        "status": query.status,
    }


def _is_not_found_payload(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    text = json.dumps(payload, ensure_ascii=False)
    return bool(SEARCH_PAGE_NOT_FOUND_RE.search(text))


def _is_not_found_html(html_text: str) -> bool:
    return bool(SEARCH_PAGE_NOT_FOUND_RE.search(html_text or ""))


def _looks_like_html(content: bytes) -> bool:
    head = content[:512].decode("utf-8", errors="ignore").lower()
    return "<html" in head or "<!doctype html" in head


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "item"


def _normalize_status(status: Any) -> str:
    value = str(status or "").strip().lower()
    if value == "pending":
        return "Pending"
    if value == "disposed":
        return "Disposed"
    return str(status or "")
