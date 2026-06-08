from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .captcha import CaptchaAttemptPolicy, CaptchaSolver
from .config import (
    AppConfig,
    STATUS_INCOMPLETE,
    TASK_ACT_LIST,
    TASK_CASE_DETAIL,
    TASK_CNR_CASE,
    TASK_DISCOVER,
    TASK_HEARING_DETAIL,
    new_run_id,
)
from .ecourts_client import ECourtsClient
from .exporters import export_run
from .parsers import (
    parse_case_detail_summary,
    parse_case_list_items,
    parse_display_pdf_calls,
    parse_hearing_summary,
    parse_options,
    parse_view_business_calls,
)
from .proxy_pool import RoundRobinProxyPool
from .queue import Task, TaskQueue
from .transport import ECourtsTransport


NO_RECORD_RE = re.compile(r"record\s+not\s+found|no\s+record", re.IGNORECASE)


class PipelineRunner:
    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        run_id: str | None = None,
        client: ECourtsClient | None = None,
        queue: TaskQueue | None = None,
        captcha_solver: CaptchaSolver | None = None,
        captcha_policy: CaptchaAttemptPolicy | None = None,
    ) -> None:
        self.config = config or AppConfig.default()
        self.run_id = run_id or new_run_id("district")
        self.run_paths = self.config.ensure_run_paths(self.run_id)
        self.queue = queue or TaskQueue(self.config.queue_db_path)
        self.captcha_solver = captcha_solver or CaptchaSolver()
        self.captcha_policy = captcha_policy or CaptchaAttemptPolicy()
        if client is None:
            proxy_url: str | None = None
            if self.config.proxy_file:
                pool = RoundRobinProxyPool(self.config.proxy_file)
                # Single-worker shape: pin one proxy for the whole session.
                proxy_url = pool.next()
            transport = ECourtsTransport(
                base_url=self.config.base_url,
                user_agent=self.config.user_agent,
                min_delay_seconds=self.config.min_delay_seconds,
                max_retries=self.config.max_retries,
                timeout_seconds=self.config.request_timeout_seconds,
                proxy_url=proxy_url,
            )
            client = ECourtsClient(transport=transport)
        self.client = client

    def run_discover(self, *, state_code: str) -> None:
        self.queue.enqueue(
            run_id=self.run_id,
            task_type=TASK_DISCOVER,
            payload={"state_code": str(state_code)},
            dedupe_key=f"state:{state_code}",
            priority=10,
        )
        self.process_pending(task_types=[TASK_DISCOVER])

    def run_list_acts(
        self,
        *,
        state_code: str,
        dist_code: str,
        court_complex_code: str,
        est_code: str,
        act_code: str,
        section: str,
        case_status: str,
    ) -> None:
        payload = {
            "state_code": str(state_code),
            "dist_code": str(dist_code),
            "court_complex_code": str(court_complex_code),
            "est_code": str(est_code),
            "act_code": str(act_code),
            "section": str(section),
            "case_status": str(case_status),
        }
        self.queue.enqueue(
            run_id=self.run_id,
            task_type=TASK_ACT_LIST,
            payload=payload,
            dedupe_key="|".join(
                [
                    payload["state_code"],
                    payload["dist_code"],
                    payload["court_complex_code"],
                    payload["est_code"],
                    payload["act_code"],
                    payload["section"],
                    payload["case_status"],
                ]
            ),
            priority=20,
        )
        self.process_pending(task_types=[TASK_ACT_LIST])

    def run_fetch_details(self) -> None:
        self.process_pending(task_types=[TASK_CASE_DETAIL, TASK_HEARING_DETAIL])

    def run_fetch_cnr(self, *, input_path: Path) -> None:
        cnrs = _read_cnr_input(input_path)
        for cino in cnrs:
            self.queue.enqueue(
                run_id=self.run_id,
                task_type=TASK_CNR_CASE,
                payload={"cino": cino},
                dedupe_key=cino,
                priority=30,
            )
        self.process_pending(task_types=[TASK_CNR_CASE, TASK_HEARING_DETAIL])

    def retry_incomplete(self) -> int:
        count = self.queue.requeue_incomplete(self.run_id)
        if count > 0:
            self.process_pending()
        return count

    def export(self) -> tuple[Path, Path]:
        return export_run(self.run_paths)

    def process_pending(self, task_types: list[str] | None = None) -> None:
        while True:
            task = self.queue.claim_next(self.run_id, task_types=task_types)
            if task is None:
                break
            try:
                self._dispatch(task)
            except Exception as exc:  # pragma: no cover - hard safety net
                self.queue.log_attempt(task.id, error_type="exception", message=str(exc))
                self.queue.mark_failed(task.id, f"Unhandled exception: {exc}")
            self._write_incomplete_snapshot()

    def _dispatch(self, task: Task) -> None:
        if task.task_type == TASK_DISCOVER:
            self._handle_discover(task)
            return
        if task.task_type == TASK_ACT_LIST:
            self._handle_act_list(task)
            return
        if task.task_type == TASK_CASE_DETAIL:
            self._handle_case_detail(task)
            return
        if task.task_type == TASK_HEARING_DETAIL:
            self._handle_hearing_detail(task)
            return
        if task.task_type == TASK_CNR_CASE:
            self._handle_cnr_case(task)
            return
        self.queue.mark_failed(task.id, f"Unknown task type: {task.task_type}")

    def _handle_discover(self, task: Task) -> None:
        state_code = str(task.payload["state_code"])
        districts_resp = self.client.fill_district(state_code)
        if districts_resp is None:
            self.queue.mark_incomplete(task.id, "fillDistrict: empty response")
            return
        districts = parse_options(str(districts_resp.get("dist_list", "")))
        discovery: dict[str, Any] = {"state_code": state_code, "districts": []}
        for district in districts:
            dist_code = district["value"]
            complex_resp = self.client.fill_complex(state_code, dist_code)
            complexes = parse_options(str((complex_resp or {}).get("complex_list", "")))
            district_row: dict[str, Any] = {
                "district": district,
                "court_complexes": [],
            }
            for complex_item in complexes:
                complex_code = complex_item["value"].split("@", 1)[0]
                est_resp = self.client.fill_court_establishment(state_code, dist_code, complex_code)
                establishments = parse_options(str((est_resp or {}).get("establishment_list", "")))
                act_resp = self.client.fill_act_type(
                    state_code=state_code,
                    dist_code=dist_code,
                    court_complex_code=complex_code,
                    est_code=establishments[0]["value"] if establishments else "",
                    search_act="",
                )
                acts = parse_options(str((act_resp or {}).get("act_list", "")))
                district_row["court_complexes"].append(
                    {
                        "complex": complex_item,
                        "establishments": establishments,
                        "acts": acts,
                    }
                )
            discovery["districts"].append(district_row)

        output_file = self.run_paths.normalized_dir / "discovery.json"
        with output_file.open("w", encoding="utf-8") as fh:
            json.dump(discovery, fh, indent=2, ensure_ascii=False)
        self.queue.mark_success(task.id, {"district_count": len(discovery["districts"])})

    def _handle_act_list(self, task: Task) -> None:
        payload = task.payload
        max_attempts = self.captcha_policy.max_attempts
        for attempt in range(1, max_attempts + 1):
            stage = self.captcha_policy.stage_for_attempt(attempt)
            image = self.client.get_captcha_image()
            if not image:
                self.queue.log_attempt(
                    task.id,
                    error_type="captcha_image",
                    message="Failed to fetch captcha image",
                    captcha_stage=stage,
                )
                continue
            token = self.captcha_solver.solve_captcha(
                image,
                stage=stage,
                variant_index=attempt - 1,
            )
            if not token:
                self.queue.log_attempt(
                    task.id,
                    error_type="captcha_solve",
                    message="Solver returned no token",
                    captcha_stage=stage,
                )
                continue

            response = self.client.submit_act(
                state_code=payload["state_code"],
                dist_code=payload["dist_code"],
                court_complex_code=payload["court_complex_code"],
                est_code=payload["est_code"],
                actcode=payload["act_code"],
                section=payload["section"],
                case_status=payload["case_status"],
                captcha_code=token,
            )
            if response is None:
                self.queue.log_attempt(
                    task.id,
                    error_type="submit_act",
                    message="No response from submitAct",
                    captcha_stage=stage,
                )
                continue

            if self.client.is_invalid_captcha(response):
                self.queue.log_attempt(
                    task.id,
                    error_type="invalid_captcha",
                    message="submitAct returned invalid captcha",
                    captcha_stage=stage,
                    response_snippet=json.dumps(response)[:900],
                )
                continue

            if _status_ok(response):
                act_html = str(response.get("act_data") or "")
                raw_file = self.run_paths.raw_case_lists / f"act_list_task_{task.id}.html"
                raw_file.write_text(act_html, encoding="utf-8")
                items = parse_case_list_items(act_html)
                for item in items:
                    item_payload = dict(item["view_history"])
                    item_payload["case_number"] = item.get("case_number", "")
                    item_payload["petitioner"] = item.get("petitioner", "")
                    item_payload["respondent"] = item.get("respondent", "")
                    self.queue.enqueue(
                        run_id=self.run_id,
                        task_type=TASK_CASE_DETAIL,
                        payload=item_payload,
                        dedupe_key=f"{item_payload.get('case_no','')}|{item_payload.get('cino','')}",
                        priority=40,
                    )
                    _append_jsonl(
                        self.run_paths.case_list_items_jsonl,
                        {"run_id": self.run_id, **item},
                    )
                self.queue.mark_success(task.id, {"item_count": len(items), "raw_file": str(raw_file)})
                return

            if NO_RECORD_RE.search(json.dumps(response)):
                self.queue.mark_success(task.id, {"item_count": 0, "note": "no_records"})
                return

            self.queue.log_attempt(
                task.id,
                error_type="submit_act_unknown",
                message="submitAct returned non-success payload",
                response_snippet=json.dumps(response)[:1000],
            )

        self.queue.mark_incomplete(task.id, "captcha attempts exhausted for ACT_LIST")

    def _handle_case_detail(self, task: Task) -> None:
        p = task.payload
        response = self.client.view_history(
            case_no=str(p.get("case_no", "")),
            cino=str(p.get("cino", "")),
            court_code=str(p.get("court_code", "")),
            hideparty=str(p.get("hideparty", "")),
            search_flag=str(p.get("search_flag", "")),
            state_code=str(p.get("state_code", "")),
            dist_code=str(p.get("dist_code", "")),
            court_complex_code=str(p.get("court_complex_code", "")),
            search_by=str(p.get("search_by", "CSact")),
        )
        if response is None:
            self.queue.mark_incomplete(task.id, "viewHistory empty response")
            return
        if not _status_ok(response):
            self.queue.mark_incomplete(task.id, "viewHistory non-success response")
            self.queue.log_attempt(
                task.id,
                error_type="view_history",
                message="Non-success response",
                response_snippet=json.dumps(response)[:1000],
            )
            return
        detail_html = str(response.get("data_list") or "")
        if not detail_html.strip():
            self.queue.mark_failed(task.id, "viewHistory returned empty data_list")
            return

        basename = _safe_name(f"{p.get('cino','')}_{p.get('case_no','')}_detail_{task.id}")
        raw_file = self.run_paths.raw_case_details / f"{basename}.html"
        raw_file.write_text(detail_html, encoding="utf-8")

        summary = parse_case_detail_summary(detail_html)
        final_order_downloads, final_order_failures = self._download_final_order_pdfs(
            task,
            cino=str(p.get("cino", "")),
            final_orders=summary.get("final_orders", []) or [],
        )
        # Generic PDF flow only picks up calls that the final-orders flow didn't
        # already cover — avoids downloading the same PDF twice for every case.
        pdf_calls = _pdf_calls_minus_final_orders(
            parse_display_pdf_calls(detail_html),
            summary.get("final_orders", []) or [],
        )
        pdf_downloads, pdf_failures = self._download_pdfs(task, pdf_calls, base_name=basename)
        business_calls = parse_view_business_calls(
            detail_html,
            default_court_complex_code=str(p.get("court_complex_code", "")) or None,
        )
        for call in business_calls:
            hearing_payload = call.to_payload()
            hearing_payload["cino"] = str(p.get("cino", ""))
            self.queue.enqueue(
                run_id=self.run_id,
                task_type=TASK_HEARING_DETAIL,
                payload=hearing_payload,
                dedupe_key="|".join(
                    [
                        hearing_payload.get("cino", ""),
                        hearing_payload.get("case_number1", ""),
                        hearing_payload.get("businessDate", ""),
                        hearing_payload.get("srno", ""),
                    ]
                ),
                priority=50,
            )

        _append_jsonl(
            self.run_paths.case_details_jsonl,
            {
                "run_id": self.run_id,
                "source": "list",
                "case_no": p.get("case_no", ""),
                "cino": p.get("cino", ""),
                "case_number": p.get("case_number", ""),
                "raw_file": str(raw_file),
                "summary": summary,
                "pdf_links": [call.params for call in pdf_calls],
                "pdf_downloads": pdf_downloads,
                "final_order_downloads": final_order_downloads,
            },
        )
        if final_order_failures > 0:
            self.queue.mark_incomplete(
                task.id,
                f"{final_order_failures} final-order PDF(s) failed to download",
            )
            return
        if pdf_calls and not pdf_downloads and pdf_failures > 0:
            self.queue.mark_incomplete(task.id, "all case-detail PDFs failed to download")
            return
        self.queue.mark_success(
            task.id,
            {
                "hearing_task_count": len(business_calls),
                "pdf_count": len(pdf_downloads),
                "final_order_pdf_count": len(final_order_downloads),
            },
        )

    def _handle_hearing_detail(self, task: Task) -> None:
        payload = task.payload
        response = self.client.view_business({k: str(v) for k, v in payload.items()})
        if response is None:
            self.queue.mark_incomplete(task.id, "viewBusiness empty response")
            return
        if not _status_ok(response):
            self.queue.mark_incomplete(task.id, "viewBusiness non-success response")
            self.queue.log_attempt(
                task.id,
                error_type="view_business",
                message="Non-success response",
                response_snippet=json.dumps(response)[:900],
            )
            return
        hearing_html = str(response.get("data_list") or "")
        if not hearing_html.strip():
            self.queue.mark_failed(task.id, "viewBusiness returned empty data_list")
            return

        basename = _safe_name(
            f"{payload.get('cino','')}_{payload.get('case_number1','')}_{payload.get('businessDate','')}_{task.id}"
        )
        raw_file = self.run_paths.raw_hearings / f"{basename}.html"
        raw_file.write_text(hearing_html, encoding="utf-8")
        summary = parse_hearing_summary(hearing_html)
        pdf_calls = parse_display_pdf_calls(hearing_html)
        pdf_downloads, pdf_failures = self._download_pdfs(task, pdf_calls, base_name=basename)

        _append_jsonl(
            self.run_paths.hearings_jsonl,
            {
                "run_id": self.run_id,
                "cino": payload.get("cino", ""),
                "payload": payload,
                "raw_file": str(raw_file),
                "summary": summary,
                "pdf_downloads": pdf_downloads,
            },
        )
        if pdf_calls and not pdf_downloads and pdf_failures > 0:
            self.queue.mark_incomplete(task.id, "all hearing PDFs failed to download")
            return
        self.queue.mark_success(task.id, {"pdf_count": len(pdf_downloads)})

    def _handle_cnr_case(self, task: Task) -> None:
        cino = str(task.payload["cino"]).upper()
        for attempt in range(1, self.captcha_policy.max_attempts + 1):
            stage = self.captcha_policy.stage_for_attempt(attempt)
            image = self.client.get_captcha_image()
            if not image:
                self.queue.log_attempt(
                    task.id,
                    error_type="captcha_image",
                    message="Failed to fetch captcha image",
                    captcha_stage=stage,
                )
                continue
            token = self.captcha_solver.solve_captcha(
                image,
                stage=stage,
                variant_index=attempt - 1,
            )
            if not token:
                self.queue.log_attempt(
                    task.id,
                    error_type="captcha_solve",
                    message="Solver returned no token",
                    captcha_stage=stage,
                )
                continue
            response = self.client.search_by_cnr(cino=cino, captcha_code=token)
            if response is None:
                self.queue.log_attempt(
                    task.id,
                    error_type="search_by_cnr",
                    message="No response",
                    captcha_stage=stage,
                )
                continue
            if self.client.is_invalid_captcha(response):
                self.queue.log_attempt(
                    task.id,
                    error_type="invalid_captcha",
                    message="searchByCNR invalid captcha",
                    captcha_stage=stage,
                    response_snippet=json.dumps(response)[:900],
                )
                continue
            if not _status_ok(response):
                if NO_RECORD_RE.search(json.dumps(response)):
                    self.queue.mark_failed(task.id, "CNR not found")
                    return
                self.queue.log_attempt(
                    task.id,
                    error_type="cnr_non_success",
                    message="searchByCNR non-success payload",
                    response_snippet=json.dumps(response)[:900],
                )
                continue

            detail_html = str(response.get("casetype_list") or response.get("data_list") or "")
            if not detail_html.strip():
                self.queue.log_attempt(
                    task.id,
                    error_type="cnr_empty_html",
                    message="searchByCNR success without HTML",
                )
                continue
            basename = _safe_name(f"{cino}_cnr_{task.id}")
            raw_file = self.run_paths.raw_case_details / f"{basename}.html"
            raw_file.write_text(detail_html, encoding="utf-8")
            summary = parse_case_detail_summary(detail_html)
            final_order_downloads, final_order_failures = self._download_final_order_pdfs(
                task,
                cino=cino,
                final_orders=summary.get("final_orders", []) or [],
            )
            pdf_calls = _pdf_calls_minus_final_orders(
                parse_display_pdf_calls(detail_html),
                summary.get("final_orders", []) or [],
            )
            pdf_downloads, pdf_failures = self._download_pdfs(task, pdf_calls, base_name=basename)
            business_calls = parse_view_business_calls(detail_html)
            for call in business_calls:
                hearing_payload = call.to_payload()
                hearing_payload["cino"] = cino
                self.queue.enqueue(
                    run_id=self.run_id,
                    task_type=TASK_HEARING_DETAIL,
                    payload=hearing_payload,
                    dedupe_key="|".join(
                        [
                            cino,
                            hearing_payload.get("case_number1", ""),
                            hearing_payload.get("businessDate", ""),
                            hearing_payload.get("srno", ""),
                        ]
                    ),
                    priority=50,
                )
            _append_jsonl(
                self.run_paths.case_details_jsonl,
                {
                    "run_id": self.run_id,
                    "source": "cnr",
                    "case_no": "",
                    "cino": cino,
                    "case_number": "",
                    "raw_file": str(raw_file),
                    "summary": summary,
                    "pdf_links": [call.params for call in pdf_calls],
                    "pdf_downloads": pdf_downloads,
                    "final_order_downloads": final_order_downloads,
                },
            )
            if final_order_failures > 0:
                self.queue.mark_incomplete(
                    task.id,
                    f"{final_order_failures} final-order PDF(s) failed to download",
                )
                return
            if pdf_calls and not pdf_downloads and pdf_failures > 0:
                self.queue.mark_incomplete(task.id, "all CNR PDFs failed to download")
                return
            self.queue.mark_success(
                task.id,
                {
                    "hearing_task_count": len(business_calls),
                    "pdf_count": len(pdf_downloads),
                    "final_order_pdf_count": len(final_order_downloads),
                },
            )
            return
        self.queue.mark_incomplete(task.id, "captcha attempts exhausted for CNR_CASE")

    def _download_pdfs(self, task: Task, calls: list[Any], *, base_name: str) -> tuple[list[dict[str, Any]], int]:
        downloads: list[dict[str, Any]] = []
        failures = 0
        for idx, call in enumerate(calls, start=1):
            result = self.client.download_pdf(call.params)
            if not result.ok or result.content is None:
                failures += 1
                self.queue.log_attempt(
                    task.id,
                    error_type="pdf_download",
                    message=result.error or "pdf download failed",
                    response_snippet=json.dumps(call.params)[:600],
                )
                continue
            filename = f"{base_name}_pdf_{idx}.pdf"
            pdf_path = self.run_paths.raw_pdfs / filename
            pdf_path.write_bytes(result.content)
            downloads.append(
                {
                    "file": str(pdf_path),
                    "size_bytes": len(result.content),
                    "source_url": result.source_url,
                    "params": call.params,
                }
            )
        return downloads, failures

    def _download_final_order_pdfs(
        self,
        task: Task,
        *,
        cino: str,
        final_orders: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Download each Final Orders / Judgements PDF into pdfs/<cino>/<order_no>.pdf.
        Strict policy: any pdf_params present but unable to download counts as a failure.
        """
        cino_dir = self.run_paths.raw_pdfs / _safe_name(cino or "unknown")
        downloads: list[dict[str, Any]] = []
        failures = 0
        for order in final_orders or []:
            params = order.get("pdf_params")
            if not params:
                continue
            order_no = _safe_name(str(order.get("order_no") or "0000"))
            result = self.client.download_pdf(dict(params))
            if not result.ok or result.content is None:
                failures += 1
                self.queue.log_attempt(
                    task.id,
                    error_type="final_order_pdf",
                    message=result.error or "final-order pdf download failed",
                    response_snippet=json.dumps({"order_no": order_no, "params": params})[:600],
                )
                continue
            cino_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = cino_dir / f"{order_no}.pdf"
            pdf_path.write_bytes(result.content)
            downloads.append(
                {
                    "file": str(pdf_path),
                    "size_bytes": len(result.content),
                    "source_url": result.source_url,
                    "order_no": order_no,
                    "order_date": order.get("order_date", ""),
                    "params": params,
                }
            )
        return downloads, failures

    def _write_incomplete_snapshot(self) -> None:
        tasks = self.queue.get_tasks(self.run_id, status=STATUS_INCOMPLETE)
        self.run_paths.incomplete_tasks_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with self.run_paths.incomplete_tasks_jsonl.open("w", encoding="utf-8") as fh:
            for task in tasks:
                line = {
                    "run_id": self.run_id,
                    "task_id": task.id,
                    "task_type": task.task_type,
                    "payload": task.payload,
                    "attempts": task.attempts,
                    "last_error": task.last_error,
                }
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _status_ok(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    if status is None:
        return True
    return str(status).strip() in {"1", "true", "True"}


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return cleaned or "item"


CNR_TOKEN_RE = re.compile(r"^[A-Z]{4}[0-9]{12}$")


def _pdf_calls_minus_final_orders(pdf_calls: list[Any], final_orders: list[dict[str, Any]]) -> list[Any]:
    """Drop PdfCall entries whose filename matches one already targeted by a final order."""
    final_filenames = {
        (o.get("pdf_params") or {}).get("filename")
        for o in final_orders or []
        if o.get("pdf_params") and (o.get("pdf_params") or {}).get("filename")
    }
    if not final_filenames:
        return list(pdf_calls)
    return [c for c in pdf_calls if c.params.get("filename") not in final_filenames]


def _read_cnr_input(path: Path) -> list[str]:
    """Reads CNRs from a plain-text or single-column CSV file.

    - Strips quotes/whitespace, uppercases tokens.
    - Skips header rows and any token that doesn't match the CNR shape
      (4 letters + 12 digits), so a `CNR_Number` header is dropped cleanly.
    - Dedupes while preserving order.
    """
    seen: dict[str, None] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            token = (row[0] or "").strip().strip('"').strip("'").upper()
            if not token or token.startswith("#"):
                continue
            if not CNR_TOKEN_RE.match(token):
                continue
            seen.setdefault(token, None)
    return list(seen.keys())

