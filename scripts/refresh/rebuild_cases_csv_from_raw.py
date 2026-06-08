from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from district_court_api_scraper.parsers import parse_case_detail_summary  # noqa: E402


DISPOSED_RE = re.compile(
    r"dispos|convict|acquit|abated|dismiss|withdraw|settled|closed|compoun|uncontested|contested",
    re.IGNORECASE,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild exports/cases.csv fields from raw case-detail HTML.")
    parser.add_argument("--run-root", required=True, help="Run root path (output/runs/<run_id>)")
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional output CSV path (default: overwrite exports/cases.csv)",
    )
    return parser.parse_args()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _parse_date_ddmmyyyy(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d-%m-%Y")
    except ValueError:
        return None


def _extract_disposal_date_from_history(summary: dict[str, Any]) -> str:
    latest: tuple[datetime, str] | None = None
    for row in (summary.get("history_rows") or []):
        if not isinstance(row, dict):
            continue
        purpose = str(row.get("purpose_of_hearing", "")).strip()
        if not DISPOSED_RE.search(purpose):
            continue
        dt_text = str(row.get("business_on_date", "")).strip() or str(row.get("hearing_date", "")).strip()
        dt = _parse_date_ddmmyyyy(dt_text)
        if dt is None:
            continue
        if latest is None or dt > latest[0]:
            latest = (dt, dt.strftime("%d-%m-%Y"))
    return latest[1] if latest else ""


def main() -> int:
    args = _parse_args()
    run_root = Path(args.run_root).resolve()
    cases_csv = run_root / "exports" / "cases.csv"
    out_csv = Path(args.output_csv).resolve() if args.output_csv else cases_csv
    case_details_jsonl = run_root / "normalized" / "case_details.jsonl"
    if not cases_csv.exists():
        raise SystemExit(f"cases.csv not found: {cases_csv}")
    if not case_details_jsonl.exists():
        raise SystemExit(f"case_details.jsonl not found: {case_details_jsonl}")

    entries = _load_jsonl(case_details_jsonl)
    by_cino: dict[str, dict[str, Any]] = {}
    reparsed = 0
    for entry in entries:
        cino = str(entry.get("cino", "")).strip()
        if not cino:
            continue
        raw_file = Path(str(entry.get("raw_file", "")))
        summary = entry.get("summary") or {}
        needs_reparse = not (summary.get("case_details") or {}).get("Filing Date")
        if raw_file.exists() and needs_reparse:
            summary = parse_case_detail_summary(raw_file.read_text(encoding="utf-8", errors="ignore"))
            reparsed += 1
        by_cino[cino] = summary

    rows: list[dict[str, str]] = []
    with cases_csv.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        for row in reader:
            cino = str(row.get("cnr", "")).strip()
            summary = by_cino.get(cino) or {}
            case_details = summary.get("case_details") or {}
            case_status = summary.get("case_status") or {}

            filing_date = str(case_details.get("Filing Date", "")).strip()
            reg_date = str(case_details.get("Registration Date", "")).strip()
            decision_date = str(case_status.get("Decision Date", "")).strip()
            if not decision_date:
                decision_date = _extract_disposal_date_from_history(summary)

            if filing_date:
                row["filing_date"] = filing_date
            if reg_date:
                row["reg_date"] = reg_date
            if decision_date:
                row["decision_date"] = decision_date
            rows.append(row)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})

    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "cases_csv": str(out_csv),
                "rows": len(rows),
                "reparsed_case_details": reparsed,
                "filled_filing_date": sum(1 for r in rows if (r.get("filing_date") or "").strip()),
                "filled_reg_date": sum(1 for r in rows if (r.get("reg_date") or "").strip()),
                "filled_decision_date": sum(1 for r in rows if (r.get("decision_date") or "").strip()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
