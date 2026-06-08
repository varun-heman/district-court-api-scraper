#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_DIR = ROOT / "output/runs/saras_ahmedabad_100cnr_20260528"
DEFAULT_PROMPT = ROOT / "prompts/20260528_ahd_saras_prompt_combined.md"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
MAX_OCR_CHARS_PER_PDF = 80000


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OCR, Gemini-extract, and report on Ahmedabad SARAS 100 CNR sample."
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--analysis-name", default="ahd_saras_20260528")
    parser.add_argument("--ocr-workers", type=int, default=4)
    parser.add_argument("--gemini-workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--force-gemini", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-gemini", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    analysis_dir = run_dir / "analysis" / args.analysis_name
    dirs = make_dirs(analysis_dir)

    cases = dedupe_cases(read_csv(run_dir / "exports/cases.csv"))
    hearings = read_csv(run_dir / "exports/hearings.csv")
    if args.limit:
        keep = set(sorted(cases)[: args.limit])
        cases = {cnr: row for cnr, row in cases.items() if cnr in keep}
        hearings = [row for row in hearings if row.get("cnr") in keep]

    if args.report_only:
        ocr_records = read_jsonl(dirs["ocr_manifest"])
    elif args.skip_ocr:
        ocr_records = read_jsonl(dirs["ocr_manifest"])
    else:
        pdfs = find_final_order_pdfs(run_dir, hearings)
        print(f"OCR source PDFs: {len(pdfs)}", flush=True)
        ocr_records = run_ocr(
            pdfs,
            dirs["ocr_text"],
            dirs["ocr_manifest"],
            workers=args.ocr_workers,
            force=args.force_ocr,
        )

    packages = build_case_packages(cases, hearings, ocr_records, dirs["gemini_inputs"])

    if not args.report_only and not args.skip_gemini:
        ran = run_gemini_batch(
            packages,
            prompt_path=args.prompt,
            output_dir=dirs["gemini_outputs"],
            error_path=dirs["gemini_errors"],
            model=args.model,
            workers=args.gemini_workers,
            force=args.force_gemini,
        )
        if not ran:
            print("Gemini extraction skipped; report will use heuristic fallback.", flush=True)

    extraction_records = load_or_build_extractions(packages, dirs["gemini_outputs"])
    report = build_report(cases, hearings, ocr_records, extraction_records, run_dir, args.model)
    report_path = analysis_dir / "ahd_saras_100_case_report.md"
    report_path.write_text(report, encoding="utf-8")

    details_path = analysis_dir / "case_extractions.jsonl"
    write_jsonl(details_path, extraction_records)
    hearing_rows = flatten_hearing_extractions(extraction_records)
    write_csv(analysis_dir / "hearing_extractions.csv", hearing_rows)

    print(f"Report: {report_path}", flush=True)
    print(f"Case extractions: {details_path}", flush=True)
    print(f"Hearing extractions: {analysis_dir / 'hearing_extractions.csv'}", flush=True)
    return 0


def make_dirs(analysis_dir: Path) -> dict[str, Path]:
    dirs = {
        "analysis": analysis_dir,
        "ocr_text": analysis_dir / "ocr_text",
        "ocr_manifest": analysis_dir / "ocr_manifest.jsonl",
        "gemini_inputs": analysis_dir / "gemini_inputs",
        "gemini_outputs": analysis_dir / "gemini_outputs",
        "gemini_errors": analysis_dir / "gemini_errors.jsonl",
    }
    for key, path in dirs.items():
        if key.endswith("manifest") or key.endswith("errors"):
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    return dirs


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def dedupe_cases(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    cases: dict[str, dict[str, str]] = {}
    for row in rows:
        cnr = row.get("cnr", "").strip()
        if cnr and cnr not in cases:
            cases[cnr] = row
    return cases


def find_final_order_pdfs(run_dir: Path, hearings: list[dict[str, str]]) -> list[Path]:
    paths: dict[str, Path] = {}
    for row in hearings:
        pdf_path = (row.get("pdf_path") or "").strip()
        if pdf_path:
            path = Path(pdf_path)
            if path.exists() and path.suffix.lower() == ".pdf":
                paths[str(path.resolve())] = path.resolve()
    raw_pdf_dir = run_dir / "raw/pdfs"
    if raw_pdf_dir.exists():
        for path in raw_pdf_dir.rglob("*.pdf"):
            paths[str(path.resolve())] = path.resolve()
    return [paths[key] for key in sorted(paths)]


def run_ocr(
    pdfs: list[Path],
    ocr_text_dir: Path,
    manifest_path: Path,
    *,
    workers: int,
    force: bool,
) -> list[dict[str, Any]]:
    langs = detect_tesseract_langs()
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(extract_pdf_text, pdf, ocr_text_dir, langs, force)
            for pdf in pdfs
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            if index % 10 == 0 or index == len(futures):
                print(f"OCR progress: {index}/{len(futures)}", flush=True)
    records.sort(key=lambda row: (row.get("cnr") or "", row.get("pdf_path") or ""))
    write_jsonl(manifest_path, records)
    return records


def detect_tesseract_langs() -> str:
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        available = set(result.stdout.split())
    except Exception:
        return "eng"
    langs = ["eng"]
    if "guj" in available:
        langs.append("guj")
    return "+".join(langs)


def extract_pdf_text(pdf_path: Path, ocr_text_dir: Path, langs: str, force: bool) -> dict[str, Any]:
    cnr = cnr_from_pdf_path(pdf_path)
    out_dir = ocr_text_dir / cnr
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir / f"{safe_name(pdf_path.parent.name + '_' + pdf_path.name)}.txt"
    if text_path.exists() and not force:
        text = text_path.read_text(encoding="utf-8", errors="replace")
        return {
            "cnr": cnr,
            "pdf_path": str(pdf_path),
            "text_path": str(text_path),
            "status": "cached",
            "best_engine": "cached",
            "best_chars": len(text),
            "tesseract_chars": None,
            "pdftotext_chars": None,
            "ocr_status": ocr_status_for_text(text, pdf_path.exists()),
        }

    tess_text, tess_error = tesseract_pdf(pdf_path, langs)
    pdf_text, pdf_error = pdftotext_pdf(pdf_path)
    best_engine = "tesseract"
    best_text = tess_text
    if len(pdf_text.strip()) > max(200, len(tess_text.strip()) * 1.2):
        best_engine = "pdftotext_fallback"
        best_text = pdf_text
    if not best_text.strip() and pdf_text.strip():
        best_engine = "pdftotext_fallback"
        best_text = pdf_text

    text_path.write_text(best_text, encoding="utf-8")
    return {
        "cnr": cnr,
        "pdf_path": str(pdf_path),
        "text_path": str(text_path),
        "status": "ok" if best_text.strip() else "no_text",
        "best_engine": best_engine,
        "best_chars": len(best_text),
        "tesseract_chars": len(tess_text),
        "pdftotext_chars": len(pdf_text),
        "tesseract_error": tess_error,
        "pdftotext_error": pdf_error,
        "ocr_status": ocr_status_for_text(best_text, pdf_path.exists()),
    }


def tesseract_pdf(pdf_path: Path, langs: str) -> tuple[str, str | None]:
    if shutil.which("pdftoppm") is None or shutil.which("tesseract") is None:
        return "", "pdftoppm or tesseract not found"
    chunks: list[str] = []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ahd_saras_ocr_") as tmp:
        prefix = str(Path(tmp) / "page")
        result = subprocess.run(
            ["pdftoppm", "-r", "200", "-png", str(pdf_path), prefix],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            return "", result.stderr.strip() or "pdftoppm failed"
        pages = sorted(Path(tmp).glob("page-*.png"), key=page_sort_key)
        for page in pages:
            result = subprocess.run(
                ["tesseract", str(page), "stdout", "-l", langs, "--psm", "6"],
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if result.returncode != 0:
                errors.append(result.stderr.strip())
            chunks.append(result.stdout)
    error = " | ".join(error for error in errors if error) or None
    return "\n\n".join(chunks), error


def pdftotext_pdf(pdf_path: Path) -> tuple[str, str | None]:
    if shutil.which("pdftotext") is None:
        return "", "pdftotext not found"
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return "", result.stderr.strip() or "pdftotext failed"
    return result.stdout, None


def page_sort_key(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name)
    return int(match.group(1)) if match else 0


def cnr_from_pdf_path(path: Path) -> str:
    for part in reversed(path.parts):
        match = re.search(r"(GJ[A-Z0-9]{10,})", part)
        if match:
            return match.group(1)
    return "UNKNOWN"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def ocr_status_for_text(text: str, pdf_exists: bool) -> str:
    if not pdf_exists:
        return "missing_pdf"
    stripped = text.strip()
    if not stripped:
        return "no_text"
    if len(stripped) < 120:
        return "too_short"
    return "ok"


def build_case_packages(
    cases: dict[str, dict[str, str]],
    hearings: list[dict[str, str]],
    ocr_records: list[dict[str, Any]],
    input_dir: Path,
) -> list[dict[str, Any]]:
    hearings_by_cnr: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in hearings:
        cnr = row.get("cnr", "").strip()
        if cnr in cases:
            hearings_by_cnr[cnr].append(row)

    ocr_by_cnr: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ocr_records:
        cnr = str(row.get("cnr") or "").strip()
        if cnr in cases:
            ocr_by_cnr[cnr].append(row)

    packages: list[dict[str, Any]] = []
    for cnr in sorted(cases):
        case = cases[cnr]
        hearing_source_rows = [
            row for row in sort_hearings(hearings_by_cnr[cnr])
            if (row.get("row_source") or "").strip() != "final_order"
        ]
        normalized_hearings = normalize_hearings_with_stable_ids(cnr, hearing_source_rows)
        final_texts = []
        for record in sorted(ocr_by_cnr.get(cnr, []), key=lambda item: item.get("pdf_path") or ""):
            text_path = Path(str(record.get("text_path") or ""))
            text = ""
            if text_path.exists():
                text = text_path.read_text(encoding="utf-8", errors="replace")
            final_texts.append(
                {
                    "pdfPath": record.get("pdf_path"),
                    "textPath": record.get("text_path"),
                    "ocrStatus": record.get("ocr_status"),
                    "engine": record.get("best_engine"),
                    "text": text[:MAX_OCR_CHARS_PER_PDF],
                }
            )
        package = {
            "cnr": cnr,
            "case_metadata": {
                "district": case.get("District"),
                "judge": case.get("judge"),
                "caseType": case.get("case_type"),
                "filingNo": case.get("filing_no"),
                "registrationNo": case.get("reg_no"),
                "filingDate": iso_date(case.get("filing_date")),
                "registrationDate": iso_date(case.get("reg_date")),
                "decisionDate": iso_date(case.get("decision_date")),
                "caseStatus": case.get("case_status"),
                "sourceDisposalLabel": case.get("disposal_type") or case.get("disposal_nature"),
                "actsUnder": case.get("acts_under"),
                "sectionsUnder": case.get("sections_under"),
            },
            "hearings": normalized_hearings,
            "final_order_texts": final_texts,
        }
        input_path = input_dir / f"{cnr}.json"
        input_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        packages.append(package)
    return packages


def sort_hearings(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        rows,
        key=lambda row: (
            parse_date(row.get("orders_date")) or date.max,
            row.get("row_source") == "final_order",
            row.get("cnr_order_id") or "",
            row.get("pdf_path") or "",
        ),
    )


def normalize_hearings_with_stable_ids(cnr: str, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        source_id = row.get("cnr_order_id") or f"{cnr}_row"
        counts[source_id] += 1
        stable_id = f"{source_id}__{counts[source_id]:04d}"
        normalized.append(normalize_hearing(row, stable_id, source_id))
    return normalized


def normalize_hearing(row: dict[str, str], stable_order_id: str, source_order_id: str) -> dict[str, Any]:
    return {
        "orderID": stable_order_id,
        "sourceOrderID": source_order_id,
        "orderNo": row.get("order_no") or None,
        "hearingDate": iso_date(row.get("orders_date")),
        "businessText": row.get("business_text") or "",
        "nextPurpose": row.get("next_purpose") or "",
        "rowSource": row.get("row_source") or "",
        "natureOfDisposal": row.get("nature_of_disposal") or "",
        "pdfPath": row.get("pdf_path") or "",
    }


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def run_gemini_batch(
    packages: list[dict[str, Any]],
    *,
    prompt_path: Path,
    output_dir: Path,
    error_path: Path,
    model: str,
    workers: int,
    force: bool,
) -> bool:
    load_env()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not set.", flush=True)
        return False
    if not prompt_path.exists():
        print(f"Prompt file not found: {prompt_path}", flush=True)
        return False
    if not preflight_gemini(api_key, model):
        return False

    prompt = prompt_path.read_text(encoding="utf-8")
    pending = []
    for package in packages:
        out_path = output_dir / f"{package['cnr']}.json"
        if out_path.exists() and not force:
            continue
        pending.append((package, out_path))
    if not pending:
        print("Gemini outputs already exist; skipping calls.", flush=True)
        return True

    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(call_gemini, api_key, model, prompt, package, out_path)
            for package, out_path in pending
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            error = future.result()
            if error:
                errors.append(error)
            if index % 10 == 0 or index == len(futures):
                print(f"Gemini progress: {index}/{len(futures)}", flush=True)
    if errors:
        write_jsonl(error_path, errors)
        print(f"Gemini errors: {len(errors)}", flush=True)
    return True


def preflight_gemini(api_key: str, model: str) -> bool:
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        client = genai.Client(api_key=api_key)
        client.models.generate_content(
            model=model,
            contents='Return {"ok": true}.',
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
    except Exception as exc:
        print(f"Gemini preflight failed for {model}: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        return False
    return True


def call_gemini(
    api_key: str,
    model: str,
    prompt: str,
    package: dict[str, Any],
    out_path: Path,
) -> dict[str, Any] | None:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(api_key=api_key)
    request = (
        prompt
        + "\n\n## Case Package JSON\n\n"
        + json.dumps(package, ensure_ascii=False)
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=model,
                contents=request,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
            parsed = parse_json_response(response.text or "")
            out_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            return None
        except Exception as exc:
            last_error = exc
            time.sleep(2**attempt)
    return {
        "cnr": package.get("cnr"),
        "error": type(last_error).__name__ if last_error else "UnknownError",
        "message": str(last_error)[:1000] if last_error else "",
    }


def parse_json_response(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            decoder = json.JSONDecoder()
            parsed, _ = decoder.raw_decode(cleaned)
            return parsed
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                decoder = json.JSONDecoder()
                parsed, _ = decoder.raw_decode(candidate)
                return parsed
        raise


def load_or_build_extractions(
    packages: list[dict[str, Any]],
    gemini_outputs: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    used_gemini = 0
    for package in packages:
        path = gemini_outputs / f"{package['cnr']}.json"
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                record["_extraction_source"] = "gemini"
                records.append(record)
                used_gemini += 1
                continue
            except Exception:
                pass
        record = heuristic_extract_case(package)
        record["_extraction_source"] = "heuristic_fallback"
        records.append(record)
    print(f"Extraction sources: gemini={used_gemini}, heuristic={len(records) - used_gemini}", flush=True)
    return records


def heuristic_extract_case(package: dict[str, Any]) -> dict[str, Any]:
    source_label = package.get("case_metadata", {}).get("sourceDisposalLabel") or ""
    final_text = "\n".join(item.get("text") or "" for item in package.get("final_order_texts", []))
    disposal_type, disposal_confidence, disposal_quote = heuristic_disposal(source_label, final_text)
    ocr_status = combined_ocr_status(package.get("final_order_texts", []))
    hearings = heuristic_hearings(package.get("hearings", []))
    return {
        "cnr": package["cnr"],
        "case_summary": {
            "source_disposal_label": source_label or None,
            "disposal_type": disposal_type,
            "disposal_confidence": disposal_confidence,
            "disposal_evidence": [
                {
                    "source": "final_order_pdf" if final_text.strip() else "case_export",
                    "date": package.get("case_metadata", {}).get("decisionDate"),
                    "quote": disposal_quote,
                    "page": 1,
                }
            ],
            "ocr_status": ocr_status,
            "needs_review": disposal_type == "unknown" or disposal_confidence < 0.75,
        },
        "hearings": hearings,
        "case_level_notes": ["Heuristic fallback used because Gemini output was unavailable."],
    }


def heuristic_disposal(label: str, text: str) -> tuple[str, float, str]:
    haystack = f"{text}\n{label}".lower()
    quote = decisive_phrase(text, [
        "lok adalat", "convict", "guilty", "dismiss", "withdraw", "acquit",
        "compromise", "settlement", "transferred", "death", "abat",
    ])
    if "lok adalat" in haystack:
        return "lok_adalat_settlement_withdrawal", 0.86 if text.strip() else 0.72, quote or label
    if "judgment by conviction" in haystack or "convict" in haystack or "guilty" in haystack:
        return "conviction_on_merits", 0.86 if text.strip() else 0.72, quote or label
    if "dismissed for default" in haystack or "default" in haystack or "non prosecution" in haystack:
        return "dismissed_for_default", 0.86 if text.strip() else 0.72, quote or label
    if "withdrawal after recording evidence" in haystack:
        return "withdrawn", 0.78 if text.strip() else 0.65, quote or label
    if "withdraw" in haystack:
        return "withdrawn", 0.84 if text.strip() else 0.72, quote or label
    if "acquit" in haystack:
        return "acquittal_on_merits", 0.70, quote or label
    if "compromise" in haystack or "settlement" in haystack or "settled" in haystack:
        return "compounded_settled_outside_lok_adalat", 0.72, quote or label
    if "transfer" in haystack:
        return "transferred", 0.75, quote or label
    if "death" in haystack or "abat" in haystack:
        return "abated_death", 0.75, quote or label
    return "unknown", 0.30, quote or label or "No decisive disposal evidence."


def decisive_phrase(text: str, needles: list[str]) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    lower = clean.lower()
    for needle in needles:
        pos = lower.find(needle)
        if pos >= 0:
            start = max(0, pos - 80)
            end = min(len(clean), pos + 180)
            return clean[start:end]
    return ""


def combined_ocr_status(final_texts: list[dict[str, Any]]) -> str:
    if not final_texts:
        return "missing_pdf"
    statuses = [item.get("ocrStatus") or "unknown" for item in final_texts]
    if "ok" in statuses:
        return "ok"
    if "too_short" in statuses:
        return "too_short"
    if "no_text" in statuses:
        return "no_text"
    return str(statuses[0])


def heuristic_hearings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current_stage = "admission"
    seen_actions: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        text = f"{row.get('businessText') or ''} {row.get('nextPurpose') or ''}"
        business = str(row.get("businessText") or "")
        lower = text.lower()
        business_lower = business.lower()
        prev_stage = current_stage
        stage, modifier, action = infer_stage_action(prev_stage, lower, business_lower, row)
        stage_changed = stage != prev_stage
        current_stage = stage if stage != "unknown" else current_stage
        order_type = "NA"
        if action:
            order_type = "Repeat" if action in seen_actions else "New"
            seen_actions.add(action)
        substantive = infer_substantive(row, lower, business_lower, stage_changed, action, order_type)
        reason, attribution = infer_reason_attribution(lower, business_lower, substantive)
        out.append(
            {
                "orderID": row.get("orderID") or "",
                "orderNo": row.get("orderNo"),
                "hearingDate": row.get("hearingDate"),
                "stage": stage,
                "stageModifier": modifier,
                "stageChanged": stage_changed,
                "stageChangeReason": f"Changed from {prev_stage} based on {action or 'text cue'}." if stage_changed else None,
                "whetherSubstantive": substantive,
                "substantiveRationale": substantive_rationale(substantive, action, stage_changed, row),
                "actions": [action] if action else [],
                "orderType": order_type,
                "nonSubstantiveReason": "NA" if substantive == "Yes" else reason,
                "attribution": "NA" if substantive == "Yes" else attribution,
                "reasonRationale": "NA" if substantive == "Yes" else f"Heuristic cue from text: {short_text(text)}",
                "attributionRationale": "NA" if substantive == "Yes" else f"Attribution follows cue hierarchy for: {short_text(text)}",
                "evidencePhrases": [short_text(text)] if text.strip() else [],
                "confidence": "Medium" if substantive != "Unclear" else "Low",
            }
        )
    return out


def infer_stage_action(
    prev_stage: str,
    lower: str,
    business_lower: str,
    row: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    row_source = str(row.get("rowSource") or "").lower()
    next_purpose = str(row.get("nextPurpose") or "").lower()
    if row_source == "final_order" or "disposed" in next_purpose or disposal_cue(lower):
        return "judgment", disposal_modifier(lower), "final_disposal"
    if "lok adalat" in lower:
        return prev_stage, "lok_adalat", "lok_adalat_reference"
    if warrant_cue(lower):
        return "warrant", None, warrant_action(lower)
    if "plea" in lower or "bail" in lower or "copies to accused" in lower:
        return "bail_plea", None, "bail_or_plea"
    if evidence_cue(business_lower) or (prev_stage in {"evidence", "arguments"} and "evidence" in next_purpose):
        return "evidence", None, evidence_action(lower)
    if "argument" in lower:
        return "arguments", None, "arguments"
    if summons_cue(lower) or (prev_stage in {"admission", "summons"} and process_next_purpose(next_purpose)):
        return "summons", None, summons_action(lower)
    return prev_stage, None, None


def disposal_cue(text: str) -> bool:
    return any(
        cue in text
        for cue in [
            "convict", "acquit", "dismiss", "withdraw", "disposed",
            "compromise", "settlement", "final order", "judgement", "judgment",
        ]
    )


def disposal_modifier(text: str) -> str | None:
    if "lok adalat" in text:
        return "lok_adalat"
    if "settlement" in text or "compromise" in text:
        return "settlement"
    if "transfer" in text:
        return "transfer"
    return None


def warrant_cue(text: str) -> bool:
    return "warrant" in text or "nbw" in text or " b.w" in text or " bw " in text or "82" in text and "83" in text


def warrant_action(text: str) -> str:
    if "nbw" in text or "non bailable" in text:
        return "issue_nbw"
    if "82" in text and "83" in text:
        return "process_82_83"
    return "issue_warrant"


def summons_cue(text: str) -> bool:
    return "summons" in text or "process to accused" in text or "issue process" in text


def summons_action(text: str) -> str:
    if "repeat" in text or "rpt" in text:
        return "repeat_summons"
    return "issue_summons"


def process_next_purpose(next_purpose: str) -> bool:
    return "process to accused" in next_purpose or "summons" in next_purpose or "notice" in next_purpose


def evidence_cue(text: str) -> bool:
    return any(cue in text for cue in ["evidence", "proof affidavit", "cross", "exhibit", "deposition"])


def evidence_action(text: str) -> str:
    if "proof affidavit" in text:
        return "proof_affidavit"
    if "cross" in text:
        return "cross_examination"
    if "exhibit" in text or "exh" in text:
        return "document_exhibited"
    return "evidence"


def infer_substantive(
    row: dict[str, Any],
    lower: str,
    business_lower: str,
    stage_changed: bool,
    action: str | None,
    order_type: str,
) -> str:
    if str(row.get("rowSource") or "").lower() == "final_order" or disposal_cue(lower):
        return "Yes"
    if action in {"issue_summons", "issue_warrant", "issue_nbw", "process_82_83", "bail_or_plea", "arguments"} and order_type == "New":
        return "Yes"
    if action in {"proof_affidavit", "cross_examination", "document_exhibited"}:
        return "Yes"
    if "withdrawal pursis" in business_lower or "stands for withdrawal" in business_lower:
        return "Yes"
    if stage_changed and action and order_type == "New":
        return "Yes"
    if not lower.strip() or lower.strip() in {"--", "-"}:
        return "Unclear"
    return "No"


def substantive_rationale(
    substantive: str,
    action: str | None,
    stage_changed: bool,
    row: dict[str, Any],
) -> str:
    if substantive == "Yes":
        if str(row.get("rowSource") or "").lower() == "final_order":
            return "Final order or judgment row records disposal."
        if action:
            return f"Concrete action detected: {action}."
        if stage_changed:
            return "Stage changed based on current hearing text."
        return "Dispositive or progress cue detected."
    if substantive == "No":
        return "No new concrete progress detected; treated as adjournment or waiting."
    return "Sparse or unclear text."


def infer_reason_attribution(lower: str, business_lower: str, substantive: str) -> tuple[str, str]:
    if substantive == "Yes":
        return "NA", "NA"
    if any(cue in lower for cue in ["notified", "no sitting", "holiday", "leave", "nsn"]):
        return "Court Holiday / No Sitting", "Court"
    if "lok adalat" in lower:
        return "External Dependency", "Others-Lok Adalat"
    if "police" in lower or "sho" in lower:
        return "External Dependency", "Others-Police"
    if warrant_cue(lower) or summons_cue(lower) or "accused absent" in lower or "accd absent" in lower:
        return "Awaiting Process / Summons / Warrant Return", "Respondent"
    if "complainant absent" in lower or "compt absent" in lower or "proof affidavit" in lower:
        return "Petitioner Absence / Non-Compliance", "Petitioner"
    if "both absent" in lower or ("both" in lower and ("time" in lower or "applied" in lower)):
        return "Both Parties Unready / Absent", "Both Parties"
    if "time" in lower or "adjourn" in lower or "applied" in lower:
        return "Party Sought Time / Adjournment", "Petitioner" if "complainant" in lower or "compt" in lower else "Both Parties"
    if "evidence" in lower:
        return "Evidence / Filing Not Ready", "Petitioner"
    if not business_lower.strip() or business_lower.strip() in {"--", "-"}:
        return "Unclear Non-Substantive Reason", "Respondent" if process_next_purpose(lower) else "Court"
    return "Unclear Non-Substantive Reason", "Both Parties"


def short_text(text: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    return clean[:limit]


def build_report(
    cases: dict[str, dict[str, str]],
    hearings: list[dict[str, str]],
    ocr_records: list[dict[str, Any]],
    extractions: list[dict[str, Any]],
    run_dir: Path,
    model: str,
) -> str:
    source_counts = Counter(row.get("_extraction_source", "unknown") for row in extractions)
    disposal_counts = Counter(
        row.get("case_summary", {}).get("disposal_type") or "unknown"
        for row in extractions
    )
    registry_counts = Counter(
        normalize_registry_disposal(row.get("disposal_type") or row.get("disposal_nature") or "")
        for row in cases.values()
    )
    ocr_status_counts = Counter(row.get("ocr_status") or "unknown" for row in ocr_records)
    hearing_rows = flatten_hearing_extractions(extractions)
    non_sub = [row for row in hearing_rows if row["whetherSubstantive"] == "No"]
    stage_time = compute_stage_time(hearing_rows)

    lines: list[str] = []
    lines.append("# Ahmedabad SARAS 100 Case Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Run: `{run_dir}`")
    lines.append(f"Prompt: `{DEFAULT_PROMPT}`")
    lines.append(f"Requested model: `{model}`")
    lines.append("")
    lines.append("## Method And Coverage")
    lines.append("")
    lines.append(f"- Unique CNRs: {len(cases)}")
    lines.append(f"- Exported hearing/final-order rows: {len(hearings)}")
    lines.append(f"- Hearing rows extracted: {len(hearing_rows)}")
    lines.append(f"- Final-order PDFs discovered/OCRed: {len(ocr_records)}")
    lines.append(f"- OCR status: {format_counter(ocr_status_counts)}")
    lines.append(f"- Extraction source: {format_counter(source_counts)}")
    if source_counts.get("heuristic_fallback"):
        lines.append("- Note: Gemini outputs were unavailable for some or all cases, so those rows use the deterministic fallback in `scripts/analyze_ahd_saras_sample.py`.")
    lines.append("")
    lines.append("## 1. What Happened To The 100 Cases")
    lines.append("")
    lines.extend(counter_table(disposal_counts, "Disposal type", "Cases"))
    lines.append("")
    lines.append("Registry labels, deduped by CNR:")
    lines.append("")
    lines.extend(counter_table(registry_counts, "Registry disposal", "Cases"))
    lines.append("")
    lines.append("## 2. Stages Taking The Most Time")
    lines.append("")
    lines.extend(stage_time_table(stage_time))
    lines.append("")
    lines.append("Interpretation: total days sums the interval from a hearing to the next listed hearing in the same CNR, assigned to the current classified stage.")
    lines.append("")
    lines.append("## 3. Adjournment Hotspots")
    lines.append("")
    lines.append(f"Non-substantive hearing rows: {len(non_sub)}")
    lines.append("")
    lines.append("By stage:")
    lines.append("")
    lines.extend(counter_table(Counter(row["stage"] for row in non_sub), "Stage", "Adjournments"))
    lines.append("")
    lines.append("By reason:")
    lines.append("")
    lines.extend(counter_table(Counter(row["nonSubstantiveReason"] for row in non_sub), "Reason", "Adjournments"))
    lines.append("")
    lines.append("Top stage/reason combinations:")
    lines.append("")
    combo_counts = Counter((row["stage"], row["nonSubstantiveReason"]) for row in non_sub)
    lines.extend(two_key_counter_table(combo_counts, "Stage", "Reason", "Adjournments", limit=15))
    lines.append("")
    lines.append("## 4. Who Is Responsible")
    lines.append("")
    lines.extend(counter_table(Counter(row["attribution"] for row in non_sub), "Attribution", "Adjournments"))
    lines.append("")
    lines.append("Responsibility by stage:")
    lines.append("")
    attr_stage = Counter((row["stage"], row["attribution"]) for row in non_sub)
    lines.extend(two_key_counter_table(attr_stage, "Stage", "Attribution", "Adjournments", limit=15))
    lines.append("")
    lines.append("## Files Written")
    lines.append("")
    analysis_dir = run_dir / "analysis/ahd_saras_20260528"
    lines.append(f"- OCR text and manifest: `{analysis_dir / 'ocr_text'}` and `{analysis_dir / 'ocr_manifest.jsonl'}`")
    lines.append(f"- Gemini inputs: `{analysis_dir / 'gemini_inputs'}`")
    lines.append(f"- Gemini outputs: `{analysis_dir / 'gemini_outputs'}`")
    lines.append(f"- Case extractions: `{analysis_dir / 'case_extractions.jsonl'}`")
    lines.append(f"- Hearing extractions CSV: `{analysis_dir / 'hearing_extractions.csv'}`")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- The combined prompt is designed for Gemini JSON extraction. If the extraction source is `heuristic_fallback`, use the report as a preliminary run until a valid Gemini key is supplied.")
    lines.append("- Court responsibility is only assigned to administrative/no-sitting style delays; coercive process delays are assigned to the party whose compliance is awaited.")
    lines.append("- Stage-time estimates depend on the exported hearing dates and do not include time before the first listed hearing or after final disposal.")
    lines.append("")
    return "\n".join(lines)


def flatten_hearing_extractions(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in extractions:
        cnr = record.get("cnr") or ""
        source = record.get("_extraction_source", "unknown")
        for hearing in record.get("hearings", []):
            rows.append(
                {
                    "cnr": cnr,
                    "extractionSource": source,
                    "orderID": hearing.get("orderID") or "",
                    "orderNo": hearing.get("orderNo") or "",
                    "hearingDate": hearing.get("hearingDate") or "",
                    "stage": hearing.get("stage") or "unknown",
                    "stageModifier": hearing.get("stageModifier") or "",
                    "stageChanged": hearing.get("stageChanged"),
                    "whetherSubstantive": hearing.get("whetherSubstantive") or "Unclear",
                    "orderType": hearing.get("orderType") or "",
                    "nonSubstantiveReason": hearing.get("nonSubstantiveReason") or "",
                    "attribution": hearing.get("attribution") or "",
                    "confidence": hearing.get("confidence") or "",
                    "substantiveRationale": hearing.get("substantiveRationale") or "",
                    "reasonRationale": hearing.get("reasonRationale") or "",
                    "attributionRationale": hearing.get("attributionRationale") or "",
                }
            )
    return rows


def compute_stage_time(hearing_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_cnr: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in hearing_rows:
        by_cnr[row["cnr"]].append(row)
    intervals: dict[str, list[int]] = defaultdict(list)
    non_sub_counts: Counter[str] = Counter()
    for rows in by_cnr.values():
        rows = sorted(rows, key=lambda row: parse_date(row.get("hearingDate")) or date.max)
        for index, row in enumerate(rows):
            if row.get("whetherSubstantive") == "No":
                non_sub_counts[row.get("stage") or "unknown"] += 1
            if index + 1 >= len(rows):
                continue
            start = parse_date(row.get("hearingDate"))
            end = parse_date(rows[index + 1].get("hearingDate"))
            if not start or not end or end < start:
                continue
            intervals[row.get("stage") or "unknown"].append((end - start).days)
    out: dict[str, dict[str, Any]] = {}
    for stage, values in intervals.items():
        out[stage] = {
            "intervals": len(values),
            "total_days": sum(values),
            "avg_days": mean(values) if values else 0,
            "median_days": median(values) if values else 0,
            "non_substantive_rows": non_sub_counts[stage],
        }
    return out


def normalize_registry_disposal(label: str) -> str:
    lower = label.lower()
    if "lok adalat" in lower:
        return "lok_adalat_settlement_withdrawal"
    if "conviction" in lower:
        return "conviction_on_merits"
    if "dismissed for default" in lower:
        return "dismissed_for_default"
    if "withdraw" in lower:
        return "withdrawn"
    return label or "unknown"


def format_counter(counter: Counter[Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in counter.most_common()) or "none"


def counter_table(counter: Counter[Any], key_header: str, value_header: str, limit: int | None = None) -> list[str]:
    lines = [f"| {key_header} | {value_header} |", "|---|---:|"]
    items = counter.most_common(limit)
    for key, value in items:
        lines.append(f"| {key} | {value} |")
    return lines


def two_key_counter_table(
    counter: Counter[tuple[Any, Any]],
    left: str,
    right: str,
    value_header: str,
    *,
    limit: int,
) -> list[str]:
    lines = [f"| {left} | {right} | {value_header} |", "|---|---|---:|"]
    for (a, b), value in counter.most_common(limit):
        lines.append(f"| {a} | {b} | {value} |")
    return lines


def stage_time_table(stage_time: dict[str, dict[str, Any]]) -> list[str]:
    lines = ["| Stage | Total days | Intervals | Avg days | Median days | Non-substantive rows |", "|---|---:|---:|---:|---:|---:|"]
    for stage, data in sorted(stage_time.items(), key=lambda item: item[1]["total_days"], reverse=True):
        lines.append(
            f"| {stage} | {data['total_days']} | {data['intervals']} | "
            f"{data['avg_days']:.1f} | {data['median_days']:.1f} | {data['non_substantive_rows']} |"
        )
    return lines


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def iso_date(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


if __name__ == "__main__":
    sys.exit(main())
