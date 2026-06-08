from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .config import RunPaths


def export_run(run_paths: RunPaths) -> tuple[Path, Path]:
    cases = _read_jsonl(run_paths.case_details_jsonl)
    hearings = _read_jsonl(run_paths.hearings_jsonl)
    _write_cases_csv(run_paths.cases_csv, cases)
    _write_hearings_csv(run_paths.hearings_csv, hearings)
    return run_paths.cases_csv, run_paths.hearings_csv


def _write_cases_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_id",
        "source",
        "case_no",
        "cino",
        "case_number",
        "court_name",
        "filing_number",
        "registration_number",
        "case_status",
        "next_hearing_date",
        "stage_of_case",
        "petitioners",
        "respondents",
        "pdf_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            summary = row.get("summary") or {}
            case_details = summary.get("case_details") or {}
            case_status = summary.get("case_status") or {}
            writer.writerow(
                {
                    "run_id": row.get("run_id", ""),
                    "source": row.get("source", ""),
                    "case_no": row.get("case_no", ""),
                    "cino": row.get("cino", ""),
                    "case_number": row.get("case_number", ""),
                    "court_name": summary.get("court_name", ""),
                    "filing_number": case_details.get("Filing Number", ""),
                    "registration_number": case_details.get("Registration Number", ""),
                    "case_status": case_status.get("Case Status", ""),
                    "next_hearing_date": case_status.get("Next Hearing Date", ""),
                    "stage_of_case": case_status.get("Stage of Case", ""),
                    "petitioners": " | ".join(summary.get("petitioners", [])),
                    "respondents": " | ".join(summary.get("respondents", [])),
                    "pdf_count": len(row.get("pdf_downloads", [])),
                }
            )


def _write_hearings_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_id",
        "cino",
        "case_number1",
        "businessDate",
        "hearing_date_count",
        "pdf_count",
        "raw_file",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            history_rows = (row.get("summary") or {}).get("history_rows", [])
            writer.writerow(
                {
                    "run_id": row.get("run_id", ""),
                    "cino": row.get("cino", ""),
                    "case_number1": row.get("payload", {}).get("case_number1", ""),
                    "businessDate": row.get("payload", {}).get("businessDate", ""),
                    "hearing_date_count": len(history_rows),
                    "pdf_count": len(row.get("pdf_downloads", [])),
                    "raw_file": row.get("raw_file", ""),
                }
            )


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

