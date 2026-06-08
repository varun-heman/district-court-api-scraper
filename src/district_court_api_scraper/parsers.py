from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl

from bs4 import BeautifulSoup


VIEW_HISTORY_RE = re.compile(r"viewHistory\((.*?)\)", re.IGNORECASE | re.DOTALL)
VIEW_BUSINESS_RE = re.compile(r"viewBusiness\((.*?)\)", re.IGNORECASE | re.DOTALL)
DISPLAY_PDF_RE = re.compile(r"displayPdf\((.*?)\)", re.IGNORECASE | re.DOTALL)


@dataclass(slots=True)
class ViewHistoryCall:
    case_no: str
    cino: str
    court_code: str
    hideparty: str
    search_flag: str
    state_code: str
    dist_code: str
    court_complex_code: str
    search_by: str

    def to_payload(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class ViewBusinessCall:
    court_code: str
    dist_code: str
    nextdate1: str
    case_number1: str
    state_code: str
    disposal_flag: str
    businessDate: str
    court_no: str
    national_court_code: str
    search_by: str
    srno: str
    court_complex_code: str | None = None

    def to_payload(self) -> dict[str, str]:
        data = asdict(self)
        if data.get("court_complex_code") is None:
            data.pop("court_complex_code", None)
        return {k: v for k, v in data.items() if v is not None}


@dataclass(slots=True)
class PdfCall:
    params: dict[str, str]
    raw_call: str


def parse_options(options_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(options_html or "", "html.parser")
    output: list[dict[str, str]] = []
    for option in soup.find_all("option"):
        value = (option.get("value") or "").strip()
        label = option.get_text(" ", strip=True)
        if not value or value in {"0", ""}:
            continue
        output.append({"value": value, "label": label})
    return output


def parse_view_history_calls(html_text: str) -> list[ViewHistoryCall]:
    output: list[ViewHistoryCall] = []
    for match in VIEW_HISTORY_RE.findall(html_text or ""):
        args = _split_js_args(match)
        if len(args) < 9:
            continue
        output.append(
            ViewHistoryCall(
                case_no=args[0],
                cino=args[1],
                court_code=args[2],
                hideparty=args[3],
                search_flag=args[4],
                state_code=args[5],
                dist_code=args[6],
                court_complex_code=args[7],
                search_by=args[8],
            )
        )
    return output


def parse_view_business_calls(html_text: str, *, default_court_complex_code: str | None = None) -> list[ViewBusinessCall]:
    output: list[ViewBusinessCall] = []
    for match in VIEW_BUSINESS_RE.findall(html_text or ""):
        args = _split_js_args(match)
        if len(args) < 11:
            continue
        output.append(
            ViewBusinessCall(
                court_code=args[0],
                dist_code=args[1],
                nextdate1=args[2],
                case_number1=args[3],
                state_code=args[4],
                disposal_flag=args[5],
                businessDate=args[6],
                court_no=args[7],
                national_court_code=args[8],
                search_by=args[9],
                srno=args[10],
                court_complex_code=default_court_complex_code,
            )
        )
    return output


def parse_display_pdf_calls(html_text: str) -> list[PdfCall]:
    output: list[PdfCall] = []
    for match in DISPLAY_PDF_RE.findall(html_text or ""):
        args = _split_js_args(match)
        if not args:
            continue
        raw = match.strip()
        params: dict[str, str]
        if len(args) == 1 and "home/display_pdf" in args[0]:
            params = parse_display_pdf_route_arg(args[0])
        elif len(args) >= 5:
            params = {
                "normal_v": args[0],
                "case_val": args[1],
                "court_code": args[2],
                "filename": args[3],
                "appFlag": args[4],
            }
        else:
            continue
        normalized = normalize_pdf_params(params)
        if normalized:
            output.append(PdfCall(params=normalized, raw_call=raw))
    return output


def parse_display_pdf_route_arg(arg: str) -> dict[str, str]:
    decoded = html.unescape((arg or "").strip().strip('"').strip("'"))
    if "home/display_pdf" in decoded:
        decoded = decoded.split("home/display_pdf", 1)[1]
    decoded = decoded.lstrip("?&")
    pairs = parse_qsl(decoded, keep_blank_values=True)
    return {k: v for k, v in pairs}


def normalize_pdf_params(params: dict[str, str]) -> dict[str, str]:
    out = {k: str(v) for k, v in params.items() if v is not None}
    if "cCode" in out and "court_code" not in out:
        out["court_code"] = out["cCode"]
    if "caseno" in out and "case_val" not in out:
        out["case_val"] = out["caseno"]
    if "appFlag" not in out:
        out["appFlag"] = ""
    if "normal_v" not in out:
        out["normal_v"] = "1"
    if "filename" not in out:
        return {}
    return out


def parse_case_list_items(act_data_html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(act_data_html or "", "html.parser")
    table = soup.find("table", id="dispTable")
    if table is None:
        # Some deployments emit duplicate id attributes on the table, causing the
        # parser to retain a different id value. Fallback to the first table that
        # contains viewHistory() links.
        for candidate in soup.find_all("table"):
            blob = str(candidate)
            if "viewHistory(" in blob or "viewhistory(" in blob:
                table = candidate
                break
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr"):
        cols = tr.find_all("td")
        if len(cols) < 4:
            continue
        view_link = cols[3].find("a")
        if view_link is None:
            continue
        onclick = view_link.get("onclick") or view_link.get("onClick") or ""
        parsed_calls = parse_view_history_calls(onclick)
        if not parsed_calls:
            parsed_calls = parse_view_history_calls(str(cols[3]))
        if not parsed_calls:
            continue
        parties_text = cols[2].get_text("\n", strip=True)
        parties = re.split(r"\bVs\b", parties_text, maxsplit=1, flags=re.IGNORECASE)
        petitioner = parties[0].strip() if parties else ""
        respondent = parties[1].strip() if len(parties) > 1 else ""
        rows.append(
            {
                "sr_no": cols[0].get_text(" ", strip=True),
                "case_number": cols[1].get_text(" ", strip=True),
                "petitioner": petitioner,
                "respondent": respondent,
                "parties": parties_text,
                "view_history": parsed_calls[0].to_payload(),
            }
        )
    return rows


def parse_case_detail_summary(case_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(case_html or "", "html.parser")
    case_details = _parse_key_value_table(soup.find("table", class_="case_details_table"))
    case_status = _parse_key_value_table(soup.find("table", class_="case_status_table"))
    subordinate_info = _parse_key_value_table(soup.find("table", class_="Lower_court_table"))

    data: dict[str, Any] = {
        "court_name": _safe_text(soup.find(id="chHeading")),
        "case_details": case_details,
        "case_status": case_status,
        "subordinate_info": subordinate_info,
        "petitioners": _parse_party_table(soup.find("table", class_="Petitioner_Advocate_table")),
        "respondents": _parse_party_table(soup.find("table", class_="Respondent_Advocate_table")),
    }
    data["cnr"] = (
        data["case_details"].get("CNR Number")
        or data["case_status"].get("CNR Number")
        or data["case_details"].get("CNR")
        or ""
    )
    data["history_rows"] = parse_history_rows(case_html)
    data["acts"] = parse_acts_rows(case_html)
    data["processes"] = parse_processes_rows(case_html)
    data["final_orders"] = parse_final_orders_rows(case_html)
    return data


def parse_acts_rows(html_text: str) -> list[dict[str, str]]:
    """Parse the `<table class="acts_table">` — Under Act(s) | Under Section(s)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    table = soup.find("table", class_="acts_table")
    if table is None:
        return []
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td"])
        if len(cells) < 2:
            continue
        act = cells[0].get_text(" ", strip=True)
        section = cells[1].get_text(" ", strip=True)
        if not act and not section:
            continue
        # Skip header row when rendered as <td><b>Under Act(s)</b></td>
        if act.lower().startswith("under act") and section.lower().startswith("under section"):
            continue
        rows.append({"act": act, "section": section})
    return rows


def parse_processes_rows(html_text: str) -> list[dict[str, str]]:
    """
    Parse the Processes table — Process ID | Process Title | Process Date.

    On Saras-style deployments the table is `class="...FIR_details_table"` (yes,
    the class name says FIR — it's reused for this section), and the data cells
    are floating `<td>`s inside `<thead>` without `<tr>` wrappers. We walk every
    `<td>` directly and group them in threes.
    """
    soup = BeautifulSoup(html_text or "", "html.parser")
    table = soup.find("table", class_="FIR_details_table")
    if table is None:
        table = _find_table_after_heading(soup, heading_text="Processes")
    if table is None:
        return []
    cells = table.find_all("td")
    rows: list[dict[str, str]] = []
    for i in range(0, len(cells) - 2, 3):
        process_id = cells[i].get_text(" ", strip=True)
        process_title = cells[i + 1].get_text(" ", strip=True)
        process_date = cells[i + 2].get_text(" ", strip=True)
        if not process_id and not process_title and not process_date:
            continue
        rows.append(
            {
                "process_id": process_id,
                "process_title": process_title,
                "process_date": process_date,
            }
        )
    return rows


def parse_final_orders_rows(html_text: str) -> list[dict[str, Any]]:
    """
    Parse the Final Orders / Judgements table — Order Number | Order Date | Order Details (link).

    Returns rows with optional `pdf_params` for each row's displayPdf onclick.
    """
    soup = BeautifulSoup(html_text or "", "html.parser")
    # Prefer the explicit class — some deployments wrap Final Orders / Judgements
    # in a 1-row container table that `_find_table_after_heading` would otherwise
    # pick up before the real `order_table`.
    table = soup.find("table", class_="order_table") or _find_table_after_heading(
        soup, heading_text="Final Orders"
    )
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue
        order_no = cells[0].get_text(" ", strip=True)
        if not order_no or order_no.lower().startswith("order number"):
            continue
        digits = re.sub(r"[^0-9]", "", order_no)
        order_date = cells[1].get_text(" ", strip=True)
        details_cell = cells[2]
        pdf_calls = parse_display_pdf_calls(str(details_cell))
        pdf_params = pdf_calls[0].params if pdf_calls else None
        rows.append(
            {
                "order_no": digits or order_no,
                "order_date": order_date,
                "pdf_params": pdf_params,
            }
        )
    return rows


def _find_table_after_heading(soup: Any, *, heading_text: str) -> Any:
    """Locate the first `<table>` element that follows a heading whose text starts with `heading_text`."""
    needle = heading_text.lower()
    for header in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        if header.get_text(" ", strip=True).lower().startswith(needle):
            sibling = header.find_next("table")
            if sibling is not None:
                return sibling
    return None


def parse_history_rows(html_text: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    table = soup.find("table", class_="history_table")
    if table is None:
        return []
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        cols = tr.find_all("td")
        if len(cols) < 4:
            continue
        rows.append(
            {
                "judge": cols[0].get_text(" ", strip=True),
                "business_on_date": cols[1].get_text(" ", strip=True),
                "hearing_date": cols[2].get_text(" ", strip=True),
                "purpose_of_hearing": cols[3].get_text(" ", strip=True),
            }
        )
    return rows


def parse_hearing_summary(hearing_html: str) -> dict[str, Any]:
    return {
        "history_rows": parse_history_rows(hearing_html),
        "pdf_calls": [call.params for call in parse_display_pdf_calls(hearing_html)],
    }


def _safe_text(node: Any) -> str:
    if node is None:
        return ""
    return node.get_text(" ", strip=True)


def _parse_key_value_table(table: Any) -> dict[str, str]:
    if table is None:
        return {}
    data: dict[str, str] = {}
    for tr in table.find_all("tr"):
        cols = tr.find_all(["td", "th"])
        if len(cols) < 2:
            continue
        # Many eCourts tables encode multiple key/value pairs in one row:
        # <th>Key1</th><td>Value1</td><th>Key2</th><td>Value2</td>
        for i in range(0, len(cols) - 1, 2):
            key = _clean_key(cols[i].get_text(" ", strip=True))
            value = cols[i + 1].get_text(" ", strip=True)
            if key:
                data[key] = value
    return data


def _clean_key(text: str) -> str:
    key = re.sub(r"\s+", " ", text or "").strip()
    return key.rstrip(":").strip()


def _parse_party_table(table: Any) -> list[str]:
    if table is None:
        return []
    lines: list[str] = []
    for tr in table.find_all("tr"):
        text = tr.get_text(" ", strip=True)
        if not text:
            continue
        lines.append(text)
    return lines


def _split_js_args(args_blob: str) -> list[str]:
    if not (args_blob or "").strip():
        return []
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    for ch in args_blob:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            current.append(ch)
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == ",":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    # Always append the trailing arg (even when empty) — e.g., a JS call ending
    # in `,''` carries a meaningful empty string we must preserve to keep the
    # arg count aligned with the displayPdf signature.
    parts.append("".join(current).strip())
    return [html.unescape(part.strip()) for part in parts]
