#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from district_court_api_scraper.ecourts_client import ECourtsClient
from district_court_api_scraper.parsers import parse_case_detail_summary, parse_case_list_items, parse_view_business_calls
from district_court_api_scraper.transport import ECourtsTransport


TARGET_ACT_IDS = {"18", "1744", "2190"}
TARGET_YEARS = {"2025", "2026"}


thread_local = threading.local()


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "item"


def _read_case_lists(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    filtered_rows: list[dict[str, Any]] = []
    for html_file in sorted(root.glob("Kollam_*_Negotiable_*_*.html")):
        meta = _extract_meta_from_filename(html_file)
        if meta["act_id"] not in TARGET_ACT_IDS:
            continue
        html_text = html_file.read_text(encoding="utf-8", errors="ignore")
        rows = parse_case_list_items(html_text)
        for row in rows:
            vh = row.get("view_history", {})
            cino = str(vh.get("cino", ""))
            record = {
                "state": "Kerala",
                "district": "Kollam",
                "source_file": str(html_file),
                "court_complex_name": meta["court_complex"],
                "status_bucket": meta["status"].capitalize(),
                "act_id": meta["act_id"],
                **row,
            }
            all_rows.append(record)
            if len(cino) >= 4 and cino[-4:] in TARGET_YEARS:
                filtered_rows.append(record)
    return all_rows, filtered_rows


def _extract_meta_from_filename(path: Path) -> dict[str, str]:
    match = re.match(r"^Kollam_(.+)_Negotiable_(\d+)_(pending|disposed)\.html$", path.name, flags=re.IGNORECASE)
    if not match:
        return {"court_complex": "", "act_id": "", "status": ""}
    return {
        "court_complex": match.group(1),
        "act_id": match.group(2),
        "status": match.group(3),
    }


def _dedupe_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        vh = row.get("view_history", {})
        key = f"{vh.get('case_no','')}|{vh.get('cino','')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _get_client(base_url: str, min_delay: float, max_retries: int) -> ECourtsClient:
    client: ECourtsClient | None = getattr(thread_local, "client", None)
    if client is None:
        transport = ECourtsTransport(
            base_url=base_url,
            user_agent="Mozilla/5.0",
            min_delay_seconds=min_delay,
            max_retries=max_retries,
            timeout_seconds=30,
        )
        client = ECourtsClient(transport)
        thread_local.client = client
    return client


def _parse_order_rows(hearing_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(hearing_html, "html.parser")
    rows: list[dict[str, str]] = []
    for table in soup.find_all("table", class_="order_table"):
        for tr in table.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            order_no = cols[0].get_text(" ", strip=True)
            order_no = re.sub(r"[^0-9]", "", order_no)
            order_date = cols[1].get_text(" ", strip=True)
            business_text = cols[2].get_text(" ", strip=True)
            if not order_no:
                continue
            rows.append(
                {
                    "order_no": order_no,
                    "orders_date": order_date,
                    "business_text": business_text,
                }
            )
    return rows


def _process_case(
    row: dict[str, Any],
    *,
    base_url: str,
    min_delay: float,
    max_retries: int,
    raw_case_dir: Path,
    raw_hearing_dir: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    vh = row["view_history"]
    cino = str(vh.get("cino", ""))
    case_no = str(vh.get("case_no", ""))
    client = _get_client(base_url, min_delay, max_retries)

    response = client.view_history(
        case_no=case_no,
        cino=cino,
        court_code=str(vh.get("court_code", "")),
        hideparty=str(vh.get("hideparty", "")),
        search_flag=str(vh.get("search_flag", "")),
        state_code=str(vh.get("state_code", "")),
        dist_code=str(vh.get("dist_code", "")),
        court_complex_code=str(vh.get("court_complex_code", "")),
        search_by=str(vh.get("search_by", "")),
    )
    if not response or str(response.get("status", "")) != "1":
        return None, [], {"cino": cino, "case_no": case_no, "error": "viewHistory non-success/empty"}

    detail_html = str(response.get("data_list", ""))
    if not detail_html.strip():
        return None, [], {"cino": cino, "case_no": case_no, "error": "viewHistory empty data_list"}

    raw_case_path = raw_case_dir / f"{_safe_name(cino)}_{_safe_name(case_no)}.html"
    raw_case_path.write_text(detail_html, encoding="utf-8")
    summary = parse_case_detail_summary(detail_html)
    history_rows = summary.get("history_rows", [])
    primary_judge = history_rows[0]["judge"] if history_rows else ""

    case_details = summary.get("case_details", {})
    case_status = summary.get("case_status", {})
    case_row = {
        "District": "Kollam",
        "cnr": cino,
        "judge": primary_judge,
        "case_type": case_details.get("Case Type", ""),
        "petitioner": row.get("petitioner", ""),
        "petitioner_adv": "",
        "respondent": row.get("respondent", ""),
        "respondent_adv": "",
        "other_resp": "",
        "filing_no": case_details.get("Filing Number", ""),
        "reg_no": case_details.get("Registration Number", ""),
        "filing_date": case_details.get("Filing Date", ""),
        "reg_date": case_details.get("Registration Date", ""),
        "decision_date": case_status.get("Decision Date", ""),
        "case_status": case_status.get("Case Status", case_status.get("Stage of Case", "")),
        "disposal_type": case_status.get("Nature of Disposal", ""),
        "Status": row.get("status_bucket", ""),
    }

    hearing_rows: list[dict[str, Any]] = []
    calls = parse_view_business_calls(
        detail_html,
        default_court_complex_code=str(vh.get("court_complex_code", "")),
    )
    for idx, call in enumerate(calls, start=1):
        hr = client.view_business({**call.to_payload(), "cino": cino})
        if not hr or str(hr.get("status", "")) != "1":
            continue
        hearing_html = str(hr.get("data_list", ""))
        if not hearing_html.strip():
            continue
        raw_hearing_path = raw_hearing_dir / f"{_safe_name(cino)}_{idx}.html"
        raw_hearing_path.write_text(hearing_html, encoding="utf-8")
        next_purpose = ""
        hs = parse_case_detail_summary(hearing_html)
        hrows = hs.get("history_rows", [])
        if hrows:
            next_purpose = hrows[0].get("purpose_of_hearing", "")
        order_rows = _parse_order_rows(hearing_html)
        if not order_rows:
            hearing_rows.append(
                {
                    "District": "Kollam",
                    "cnr_order_id": f"{cino}_{idx:04d}",
                    "Status": row.get("status_bucket", ""),
                    "cnr": cino,
                    "order_no": "",
                    "orders_date": call.businessDate,
                    "business_text": "",
                    "next_purpose": next_purpose,
                }
            )
        else:
            for orow in order_rows:
                hearing_rows.append(
                    {
                        "District": "Kollam",
                        "cnr_order_id": f"{cino}_{orow['order_no']}",
                        "Status": row.get("status_bucket", ""),
                        "cnr": cino,
                        "order_no": orow["order_no"],
                        "orders_date": orow["orders_date"],
                        "business_text": orow["business_text"],
                        "next_purpose": next_purpose,
                    }
                )

    return case_row, hearing_rows, None


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description="Kollam Section 138 (2025+2026) multithread scrape.")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[2]),
    )
    parser.add_argument(
        "--case-lists-root",
        default="/Users/siddarth/Documents/Work/xkdr/repository/db-courts/SRC/district-court-v2/data/case_lists/Kerala/Kollam",
    )
    parser.add_argument("--run-id", default="kollam_section138_2025_2026_mt16")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--min-delay", type=float, default=0.05)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--base-url", default="https://services.ecourts.gov.in/ecourtindia_v6")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    run_root = project_root / "output" / "runs" / args.run_id
    raw_case_dir = run_root / "raw" / "case_details"
    raw_hearing_dir = run_root / "raw" / "hearings"
    export_dir = run_root / "exports"
    normalized_dir = run_root / "normalized"
    for d in (raw_case_dir, raw_hearing_dir, export_dir, normalized_dir):
        d.mkdir(parents=True, exist_ok=True)

    all_rows, year_rows = _read_case_lists(Path(args.case_lists_root).resolve())
    unique_rows = _dedupe_cases(year_rows)

    # caselists.csv in requested format (year-filtered, de-duplicated).
    caselist_rows = []
    for row in unique_rows:
        caselist_rows.append(
            {
                "State": "Kerala",
                "District": "Kollam",
                "Court Complex": row.get("court_complex_name", ""),
                "Status": row.get("status_bucket", ""),
                "Court Name": row.get("court_complex_name", ""),
                "File Number": row.get("case_number", ""),
                "Petitioner": row.get("petitioner", ""),
                "Respondent": row.get("respondent", ""),
                "CNR": row.get("view_history", {}).get("cino", ""),
            }
        )

    cases: list[dict[str, Any]] = []
    hearings: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as exe:
        futures = [
            exe.submit(
                _process_case,
                row,
                base_url=args.base_url,
                min_delay=args.min_delay,
                max_retries=args.max_retries,
                raw_case_dir=raw_case_dir,
                raw_hearing_dir=raw_hearing_dir,
            )
            for row in unique_rows
        ]
        for fut in as_completed(futures):
            case_row, hearing_rows, err = fut.result()
            with lock:
                if case_row:
                    cases.append(case_row)
                if hearing_rows:
                    hearings.extend(hearing_rows)
                if err:
                    incomplete.append(err)

    # Stable output ordering.
    cases.sort(key=lambda x: x.get("cnr", ""))
    hearings.sort(key=lambda x: (x.get("cnr", ""), x.get("order_no", ""), x.get("orders_date", "")))
    caselist_rows.sort(key=lambda x: x.get("CNR", ""))

    _write_csv(
        export_dir / "cases.csv",
        [
            "District",
            "cnr",
            "judge",
            "case_type",
            "petitioner",
            "petitioner_adv",
            "respondent",
            "respondent_adv",
            "other_resp",
            "filing_no",
            "reg_no",
            "filing_date",
            "reg_date",
            "decision_date",
            "case_status",
            "disposal_type",
            "Status",
        ],
        cases,
    )
    _write_csv(
        export_dir / "hearings.csv",
        [
            "District",
            "cnr_order_id",
            "Status",
            "cnr",
            "order_no",
            "orders_date",
            "business_text",
            "next_purpose",
        ],
        hearings,
    )
    _write_csv(
        export_dir / "caselists.csv",
        [
            "State",
            "District",
            "Court Complex",
            "Status",
            "Court Name",
            "File Number",
            "Petitioner",
            "Respondent",
            "CNR",
        ],
        caselist_rows,
    )

    (normalized_dir / "incomplete_cases.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in incomplete) + ("\n" if incomplete else ""),
        encoding="utf-8",
    )
    summary = {
        "run_id": args.run_id,
        "workers": args.workers,
        "years": sorted(TARGET_YEARS),
        "total_case_list_rows": len(all_rows),
        "total_case_list_rows_target_years": len(year_rows),
        "unique_target_cases": len(unique_rows),
        "cases_csv_rows": len(cases),
        "hearings_csv_rows": len(hearings),
        "caselists_csv_rows": len(caselist_rows),
        "incomplete_case_count": len(incomplete),
        "cases_csv": str(export_dir / "cases.csv"),
        "hearings_csv": str(export_dir / "hearings.csv"),
        "caselists_csv": str(export_dir / "caselists.csv"),
    }
    (run_root / "summary_mt.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

