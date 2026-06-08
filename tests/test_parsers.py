from __future__ import annotations

from pathlib import Path

from district_court_api_scraper.parsers import (
    parse_case_detail_summary,
    parse_case_list_items,
    parse_view_business_calls,
    parse_view_history_calls,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_act_listing_rows_from_submit_act_html() -> None:
    html = _read("submit_act_case_list.html")
    rows = parse_case_list_items(html)
    assert len(rows) == 1
    row = rows[0]
    assert row["case_number"] == "ST/123/2025"
    assert row["petitioner"] == "A Petitioner"
    assert row["respondent"] == "B Respondent"
    assert row["view_history"]["case_no"] == "203100042542025"
    assert row["view_history"]["cino"] == "KLTV350016432025"


def test_parse_view_history_args_from_listing_html() -> None:
    html = _read("submit_act_case_list.html")
    calls = parse_view_history_calls(html)
    assert len(calls) == 1
    call = calls[0]
    assert call.case_no == "203100042542025"
    assert call.search_by == "CSact"
    assert call.state_code == "4"
    assert call.dist_code == "9"
    assert call.court_complex_code == "1040097"


def test_parse_view_business_args_from_case_detail_html() -> None:
    html = _read("case_detail.html")
    calls = parse_view_business_calls(html)
    assert len(calls) == 1
    call = calls[0]
    assert call.case_number1 == "KLTV350016432025"
    assert call.businessDate == "15-03-2026"
    assert call.search_by == "cnr"
    assert call.srno == "1"


def test_parse_case_detail_summary_captures_multi_pair_rows_and_subordinate_decision_date() -> None:
    html = """
    <table class="case_details_table">
      <tr><th>Case Type</th><td colspan="3">ST - SUMMONS CASE</td></tr>
      <tr><th> Filing Number </th><td>123/2025</td><th>Filing Date</th><td>28-11-2025</td></tr>
      <tr><th>Registration Number</th><td>45/2025</td><th>Registration Date</th><td>01-12-2025</td></tr>
    </table>
    <table class="case_status_table">
      <tr><th>Case Stage</th><td>Call on</td></tr>
    </table>
    <table class="Lower_court_table">
      <tr><th>Case Number and Year</th><td>ST - 001 - 2020</td></tr>
      <tr><th>Case Decision Date</th><td>23-04-2025</td></tr>
    </table>
    """
    summary = parse_case_detail_summary(html)
    assert summary["case_details"]["Filing Date"] == "28-11-2025"
    assert summary["case_details"]["Registration Date"] == "01-12-2025"
    assert summary["subordinate_info"]["Case Decision Date"] == "23-04-2025"
    assert summary["case_status"].get("Decision Date", "") == ""
