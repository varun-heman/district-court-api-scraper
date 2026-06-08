from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


TARGET_CASE_COLUMNS = [
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
    "acts_under",
    "sections_under",
    "disposal_nature",
    "disposal_date",
    "processes_count",
    "final_orders_count",
]

TARGET_HEARING_COLUMNS = [
    "District",
    "cnr_order_id",
    "Status",
    "cnr",
    "order_no",
    "orders_date",
    "business_text",
    "next_purpose",
    "row_source",
    "nature_of_disposal",
    "pdf_path",
]

TARGET_CASELIST_COLUMNS = [
    "State",
    "District",
    "Court Complex",
    "Status",
    "Court Name",
    "File Number",
    "Petitioner",
    "Respondent",
    "CNR",
]


@dataclass(slots=True)
class StructuredExportResult:
    run_id: str
    years: list[str]
    cases_csv: Path
    hearings_csv: Path
    caselists_csv: Path
    sample_cases_csv: Path | None
    sample_hearings_csv: Path | None
    cases_rows: int
    hearings_rows: int
    caselists_rows: int
    sample_size: int | None
    skipped_case_details_without_list_match: int


def export_structured_outputs(
    *,
    run_root: Path,
    years: set[str],
    state_name: str,
    district_name: str,
    sample_size: int | None = None,
) -> StructuredExportResult:
    normalized = run_root / "normalized"
    exports = run_root / "exports"
    exports.mkdir(parents=True, exist_ok=True)

    case_list_items = _read_jsonl(normalized / "case_list_items.jsonl")
    case_details = _read_jsonl(normalized / "case_details.jsonl")
    hearings = _read_jsonl(normalized / "hearings.jsonl")
    purpose_map = _build_purpose_map(case_details)

    list_rows, list_lookup = _build_caselists(
        case_list_items=case_list_items,
        years=years,
        state_name=state_name,
        district_name=district_name,
    )

    case_rows, skipped = _build_cases(
        case_details=case_details,
        list_lookup=list_lookup,
        years=years,
        district_name=district_name,
    )
    hearing_rows = _build_hearings(
        hearings=hearings,
        list_lookup=list_lookup,
        years=years,
        district_name=district_name,
        purpose_map=purpose_map,
        case_details=case_details,
    )

    cases_csv = exports / "cases.csv"
    hearings_csv = exports / "hearings.csv"
    caselists_csv = exports / "caselists.csv"
    _write_csv(cases_csv, TARGET_CASE_COLUMNS, case_rows)
    _write_csv(hearings_csv, TARGET_HEARING_COLUMNS, hearing_rows)
    _write_csv(caselists_csv, TARGET_CASELIST_COLUMNS, list_rows)

    sample_cases_csv: Path | None = None
    sample_hearings_csv: Path | None = None
    if sample_size and sample_size > 0:
        sample_cases_csv = exports / "sample_cases.csv"
        sample_hearings_csv = exports / "sample_hearings.csv"
        _write_csv(sample_cases_csv, TARGET_CASE_COLUMNS, case_rows[:sample_size])
        _write_csv(sample_hearings_csv, TARGET_HEARING_COLUMNS, hearing_rows[:sample_size])

    run_id = run_root.name
    result = StructuredExportResult(
        run_id=run_id,
        years=sorted(years),
        cases_csv=cases_csv,
        hearings_csv=hearings_csv,
        caselists_csv=caselists_csv,
        sample_cases_csv=sample_cases_csv,
        sample_hearings_csv=sample_hearings_csv,
        cases_rows=len(case_rows),
        hearings_rows=len(hearing_rows),
        caselists_rows=len(list_rows),
        sample_size=sample_size if sample_size and sample_size > 0 else None,
        skipped_case_details_without_list_match=skipped,
    )
    (run_root / "summary_structured_exports.json").write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "years": result.years,
                "cases_rows": result.cases_rows,
                "hearings_rows": result.hearings_rows,
                "caselists_rows": result.caselists_rows,
                "sample_size": result.sample_size,
                "skipped_case_details_without_list_match": result.skipped_case_details_without_list_match,
                "cases_csv": str(result.cases_csv),
                "hearings_csv": str(result.hearings_csv),
                "caselists_csv": str(result.caselists_csv),
                "sample_cases_csv": str(result.sample_cases_csv) if result.sample_cases_csv else "",
                "sample_hearings_csv": str(result.sample_hearings_csv) if result.sample_hearings_csv else "",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return result


def _build_caselists(
    *,
    case_list_items: list[dict[str, Any]],
    years: set[str],
    state_name: str,
    district_name: str,
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    dedupe: dict[str, dict[str, str]] = {}
    for row in case_list_items:
        vh = row.get("view_history") or {}
        cino = str(vh.get("cino", ""))
        if not _cnr_in_years(cino, years):
            continue
        case_no = str(vh.get("case_no", ""))
        key = _case_key(case_no=case_no, cino=cino)
        status = _normalize_status(row.get("status_bucket", ""))
        court_complex = str(row.get("court_complex", "") or row.get("source_court_complex_name", ""))
        file_number = str(row.get("case_number", ""))
        petitioner = str(row.get("petitioner", ""))
        respondent = str(row.get("respondent", ""))
        dedupe.setdefault(
            key,
            {
                "State": state_name,
                "District": district_name,
                "Court Complex": court_complex,
                "Status": status,
                "Court Name": court_complex,
                "File Number": file_number,
                "Petitioner": petitioner,
                "Respondent": respondent,
                "CNR": cino,
                "__case_no": case_no,
            },
        )

    rows = list(dedupe.values())
    rows.sort(key=lambda x: x.get("CNR", ""))
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        cino = row.get("CNR", "")
        lookup[_case_key(case_no=row.get("__case_no", ""), cino=cino)] = row
        lookup.setdefault(_case_key(case_no="", cino=cino), row)
    for row in rows:
        row.pop("__case_no", None)
    return rows, lookup


def _build_cases(
    *,
    case_details: list[dict[str, Any]],
    list_lookup: dict[str, dict[str, str]],
    years: set[str],
    district_name: str,
) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    skipped = 0
    for entry in case_details:
        cino = str(entry.get("cino", ""))
        if not _cnr_in_years(cino, years):
            continue
        case_no = str(entry.get("case_no", ""))
        source = list_lookup.get(_case_key(case_no=case_no, cino=cino)) or list_lookup.get(
            _case_key(case_no="", cino=cino)
        )
        if source is None:
            # CNR-direct runs have no case-list source; synthesise from case detail
            # so cases.csv still emits a row per fetched CNR.
            summary_for_fallback = entry.get("summary") or {}
            pet = (summary_for_fallback.get("petitioners") or [""])[0]
            res = (summary_for_fallback.get("respondents") or [""])[0]
            source = {
                "State": "",
                "District": district_name,
                "Court Complex": "",
                "Status": "",
                "Court Name": str(summary_for_fallback.get("court_name", "")),
                "File Number": case_no,
                "Petitioner": str(pet or ""),
                "Respondent": str(res or ""),
                "CNR": cino,
            }
        summary = entry.get("summary") or {}
        case_details_map = summary.get("case_details") or {}
        case_status = summary.get("case_status") or {}
        history_rows = summary.get("history_rows") or []
        acts_rows = summary.get("acts") or []
        processes_rows = summary.get("processes") or []
        final_orders_rows = summary.get("final_orders") or []
        judge = ""
        if history_rows and isinstance(history_rows[0], dict):
            judge = str(history_rows[0].get("judge", ""))
        if not judge:
            judge = str(case_status.get("Court Number and Judge", ""))
        acts_under = " | ".join(
            sorted({str(a.get("act", "")).strip() for a in acts_rows if a.get("act")})
        )
        sections_under = " | ".join(
            sorted({str(a.get("section", "")).strip() for a in acts_rows if a.get("section")})
        )
        disposal_nature = str(case_status.get("Nature of Disposal", ""))
        disposal_date = str(case_status.get("Decision Date", ""))
        rows.append(
            {
                "District": district_name,
                "cnr": cino,
                "judge": judge,
                "case_type": str(case_details_map.get("Case Type", "")),
                "petitioner": source.get("Petitioner", ""),
                "petitioner_adv": "",
                "respondent": source.get("Respondent", ""),
                "respondent_adv": "",
                "other_resp": "",
                "filing_no": str(case_details_map.get("Filing Number", "")),
                "reg_no": str(case_details_map.get("Registration Number", "")),
                "filing_date": str(case_details_map.get("Filing Date", "")),
                "reg_date": str(case_details_map.get("Registration Date", "")),
                "decision_date": str(case_status.get("Decision Date", "")),
                "case_status": str(case_status.get("Case Status", case_status.get("Case Stage", ""))),
                "disposal_type": str(case_status.get("Nature of Disposal", disposal_nature)),
                "Status": source.get("Status", ""),
                "acts_under": acts_under,
                "sections_under": sections_under,
                "disposal_nature": disposal_nature,
                "disposal_date": disposal_date,
                "processes_count": str(len(processes_rows)),
                "final_orders_count": str(len(final_orders_rows)),
            }
        )
    rows.sort(key=lambda x: x.get("cnr", ""))
    return rows, skipped


def _build_hearings(
    *,
    hearings: list[dict[str, Any]],
    list_lookup: dict[str, dict[str, str]],
    years: set[str],
    district_name: str,
    purpose_map: dict[str, dict[str, str]],
    case_details: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for entry in hearings:
        cino = str(entry.get("cino", ""))
        if not _cnr_in_years(cino, years):
            continue
        payload = entry.get("payload") or {}
        source = list_lookup.get(_case_key(case_no=str(payload.get("case_no", "")), cino=cino)) or list_lookup.get(
            _case_key(case_no="", cino=cino)
        )
        status = source.get("Status", "") if source else ""
        next_purpose = ""
        history_rows = (entry.get("summary") or {}).get("history_rows") or []
        if history_rows and isinstance(history_rows[0], dict):
            next_purpose = str(history_rows[0].get("purpose_of_hearing", ""))
        raw_path = Path(str(entry.get("raw_file", "")))
        order_rows = _parse_order_rows(raw_path)
        daily_disposal = _read_daily_status_disposal(raw_path)
        business_date = str(payload.get("businessDate", ""))
        if not next_purpose:
            next_purpose = purpose_map.get(cino, {}).get(business_date, "")
        if not order_rows:
            rows.append(
                {
                    "District": district_name,
                    "cnr_order_id": f"{cino}_0000",
                    "Status": status,
                    "cnr": cino,
                    "order_no": "",
                    "orders_date": business_date,
                    "business_text": "",
                    "next_purpose": next_purpose,
                    "row_source": "hearing",
                    "nature_of_disposal": daily_disposal.get("nature_of_disposal", ""),
                    "pdf_path": "",
                }
            )
            continue
        for order in order_rows:
            order_no = str(order.get("order_no", ""))
            rows.append(
                {
                    "District": district_name,
                    "cnr_order_id": f"{cino}_{order_no or '0000'}",
                    "Status": status,
                    "cnr": cino,
                    "order_no": order_no,
                    "orders_date": str(order.get("orders_date", "")),
                    "business_text": str(order.get("business_text", "")),
                    "next_purpose": next_purpose,
                    "row_source": "hearing",
                    "nature_of_disposal": daily_disposal.get("nature_of_disposal", ""),
                    "pdf_path": "",
                }
            )

    # Emit one row per Final Orders / Judgements row from the case-detail page.
    # These reflect the canonical orders list (with PDFs) — the daily-status
    # parsing above can miss them when a hearing's daily-status fetch is absent.
    for entry in case_details or []:
        cino = str(entry.get("cino", ""))
        if not _cnr_in_years(cino, years):
            continue
        summary = entry.get("summary") or {}
        final_orders = summary.get("final_orders") or []
        pdf_downloads = {
            str(d.get("order_no", "")): str(d.get("file", ""))
            for d in (entry.get("final_order_downloads") or [])
        }
        source = list_lookup.get(_case_key(case_no=str(entry.get("case_no", "")), cino=cino)) or list_lookup.get(
            _case_key(case_no="", cino=cino)
        )
        status = source.get("Status", "") if source else ""
        for order in final_orders:
            order_no = str(order.get("order_no", ""))
            rows.append(
                {
                    "District": district_name,
                    "cnr_order_id": f"{cino}_{order_no or '0000'}",
                    "Status": status,
                    "cnr": cino,
                    "order_no": order_no,
                    "orders_date": str(order.get("order_date", "")),
                    "business_text": "",
                    "next_purpose": "",
                    "row_source": "final_order",
                    "nature_of_disposal": "",
                    "pdf_path": pdf_downloads.get(order_no, ""),
                }
            )

    rows.sort(key=lambda x: (x.get("cnr", ""), x.get("order_no", ""), x.get("orders_date", "")))
    return rows


def _read_daily_status_disposal(path: Path) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    return {
        "nature_of_disposal": _extract_daily_status_nature_of_disposal(soup),
        "disposal_date": _extract_daily_status_disposal_date(soup),
    }


def _parse_order_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    html_text = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html_text, "html.parser")
    out: list[dict[str, str]] = []
    for table in soup.find_all("table", class_="order_table"):
        for tr in table.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            order_no = re.sub(r"[^0-9]", "", cols[0].get_text(" ", strip=True))
            if not order_no:
                continue
            out.append(
                {
                    "order_no": order_no,
                    "orders_date": cols[1].get_text(" ", strip=True),
                    "business_text": cols[2].get_text(" ", strip=True),
                }
            )
    if out:
        return out

    business_text = _extract_daily_status_business_text(soup)
    orders_date = _extract_daily_status_date(soup)
    if business_text or orders_date:
        out.append(
            {
                "order_no": "",
                "orders_date": orders_date,
                "business_text": business_text,
            }
        )
    return out


def _extract_daily_status_business_text(soup: BeautifulSoup) -> str:
    return _extract_label_value(soup, label_prefix="Business")


def _extract_daily_status_date(soup: BeautifulSoup) -> str:
    text = soup.get_text("\n", strip=True)
    match = re.search(r"Date\s*[:]\s*([0-9]{2}-[0-9]{2}-[0-9]{4})", text)
    if match:
        return match.group(1)
    return ""


def _extract_daily_status_nature_of_disposal(soup: BeautifulSoup) -> str:
    return _extract_label_value(soup, label_prefix="Nature of Disposal")


def _extract_daily_status_disposal_date(soup: BeautifulSoup) -> str:
    return _extract_label_value(soup, label_prefix="Disposal Date")


def _extract_label_value(soup: BeautifulSoup, *, label_prefix: str) -> str:
    """Walks `<b>Label</b>` cells (daily-status layout) and returns the trailing cell text."""
    needle = label_prefix.strip().lower()
    for b in soup.find_all("b"):
        if not b.get_text(" ", strip=True).lower().startswith(needle):
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


def _build_purpose_map(case_details: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for item in case_details:
        cino = str(item.get("cino", ""))
        if not cino:
            continue
        summary = item.get("summary") or {}
        for row in summary.get("history_rows") or []:
            if not isinstance(row, dict):
                continue
            business_on = str(row.get("business_on_date", "")).strip()
            purpose = str(row.get("purpose_of_hearing", "")).strip()
            if not business_on or not purpose:
                continue
            out.setdefault(cino, {})
            out[cino].setdefault(business_on, purpose)
    return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})


def _case_key(*, case_no: str, cino: str) -> str:
    return f"{case_no}|{cino}"


def _cnr_in_years(cino: str, years: set[str]) -> bool:
    return len(cino) >= 4 and cino[-4:] in years


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "pending":
        return "Pending"
    if text == "disposed":
        return "Disposed"
    return str(value or "")
