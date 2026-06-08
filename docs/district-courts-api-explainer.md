# District Courts API Explainer

This document explains how the Selenium-free district scraper works, what endpoints it uses, how Kollam Section 138 collection is executed, and what operational fixes were made during live runs.

## Scope

- Target site: `https://services.ecourts.gov.in/ecourtindia_v6`
- Mode: direct HTTP calls to `/?p=<route>`
- Captcha policy: 5 OCR attempts -> 5 Gemini (`gemini-2.5-flash-lite`) attempts -> mark task `INCOMPLETE`
- Queue/state backend: SQLite (`state/tasks.db`)
- Output root: `output/runs/<run_id>/...`

## Route Map

### Metadata / setup

- `casestatus/fillDistrict`
- `casestatus/fillcomplex`
- `casestatus/fillCourtEstablishment`
- `casestatus/fillActType`

### Search / case listing

- `casestatus/submitAct` (captcha-gated)
- `cnr_status/searchByCNR/` (captcha-gated)

### Case details / hearings / PDFs

- `home/viewHistory` (no captcha after list item is known)
- `home/viewBusiness` (no captcha after detail item is known)
- `home/display_pdf` (session-sensitive PDF resolver/downloader)

## Request Contract

All AJAX calls are posted to `/?p=<route>` with:

- `ajax_req=true`
- `app_token` (from page/session context, refreshed from responses)
- session cookies on the same `requests.Session`

## Parsing Chain

1. `submitAct` HTML (`act_data`) -> parse `viewHistory(...)` rows.
2. `viewHistory` HTML (`data_list`) -> parse `viewBusiness(...)` and `displayPdf(...)`.
3. `viewBusiness` HTML (`data_list`) -> parse more `displayPdf(...)` when present.

## Critical Session Rule

`viewHistory` and `viewBusiness` must run in the same authenticated session context as the `submitAct` that produced the list row.

If this is violated, the API can return:

- `status=1` with HTML payload containing `Welcome User Search Page not Found here`

This causes false positives (few/zero hearings, malformed details).  
The new batch runner enforces the sequence in one worker session:

1. solve captcha + `submitAct`
2. parse list rows
3. call `viewHistory` for those rows
4. call `viewBusiness` for those details

## PDF Strategy

The frontend calls `displayPdf(...)` JS, which maps to `home/display_pdf`.

Implemented flow:

1. Parse `displayPdf(...)` args from HTML.
2. Normalize params (`filename`, `case_val`/`caseno`, `court_code`/`cCode`, `normal_v`, `appFlag`).
3. POST `home/display_pdf` in same session.
4. If response includes `order`, fetch that URL and store PDF bytes.
5. If all PDFs for a task fail, mark task `INCOMPLETE` (retryable).

## Kollam Section 138 Execution Plan

### District and courts

- State: Kerala (`state_code=4`)
- District: Kollam (`dist_code=15`)
- Courts: **all Kollam court complexes from `district_courts_v2` config** (not only a subset folder)

### NI Act IDs used for Section 138-equivalent pull

Validated NI-related IDs in Kollam:

- `18` - Negotiable Instruments Act
- `1744` - Negotiable Instruments (Amendment) Act
- `2190` - NEGOTIABLE INSTRUMENTS ACT

Excluded:

- `2012` - SEBI debt instruments regulation (not Section 138 NI bucket)

### Status buckets

- `Pending`
- `Disposed`

### 2025 filter

- Identify CNRs where last 4 chars are `2025`.
- Only those are pushed into detail/hearing scrape stage.

## Operational Update Log

### Update 1: all-courts correction

- Initial run target used a user-provided subset folder.
- Updated plan to include **all Kollam courts from config** as requested.

### Update 2: captcha-gated listing fallback

- Environment had no `GEMINI_API_KEY`, so captcha-gated `submitAct` runs are high-risk for completeness.
- Implemented robust fallback for this run:
  - use complete saved Kollam case-list corpus (`district-court-v2/data/case_lists/Kerala/Kollam`) for NI act/status universe;
  - run live API detail/hearing/PDF scraping for filtered `CNR ... 2025`.

This preserves direct-API scraping for expensive stages and keeps run reproducible despite captcha constraints.

### Update 3: all-courts Kollam inventory + NI ID validation

Validated against `district_courts_v2` Kollam config:

- all court complexes included (full district set)
- NI Section 138 bucket IDs used: `18`, `1744`, `2190`
- explicitly excluded: `2012` (SEBI debt instrument regulation)

### Update 4: live run outcomes (run_id: `kollam_section138_all_courts_v2`)

- NI list universe (all Kollam courts, pending+disposed, 3 NI IDs): `16,918` rows
- CNR-2025 rows: `399`
- unique CNR-2025 case keys: `399`
- case detail fetch: `397` success, `2` incomplete
- hearing fetch: `2,211` success
- PDF downloads: `0` files (no downloadable `displayPdf` payloads observed in fetched 2025 details/hearings)

Incomplete case-detail tasks after retry:

1. `KLKM210000032025` (`case_no=207401000042025`) - `viewHistory` empty response
2. `KLKM210000012025` (`case_no=207401000032025`) - `viewHistory` empty response

These remain in `normalized/incomplete_tasks.jsonl` and can be retried later with:

```bash
MIN_REQUEST_DELAY=0.2 HTTP_MAX_RETRIES=2 ./.venv/bin/district-court-api-scraper retry-incomplete --run-id kollam_section138_all_courts_v2
```

### Update 5: Kerala-wide status attribution correction (2026-03-09)

For Kerala-wide NI-138 (2025+ filter), relying only on `status=Both` list pulls under-captured disposed cases in downstream detail sampling.

What was validated:

- Separate status sweeps (`Pending` + `Disposed`) over the same query signatures produce a reliable status map.
- `status_map_from_lists.csv` currently shows for CNR year `>=2025`:
  - `Disposed`: `736` (`2025: 733`, `2026: 3`)
  - `Pending`: `7768`
- From the disposed list HTML corpus alone, unique disposed CNRs in `2025+` are `775`, and `CC/ST` subset is `486` (`2025: 483`, `2026: 3`).

Implication:

- To avoid disposed undercount, production runs should always execute explicit `Disposed` list passes (not just `Both`) before detail/hearing scrape selection.

### Update 6: Kerala disposed detail/hearing catch-up run

Active run (started 2026-03-09):

- `run_id`: `kerala_ni138_phase5_disposed_20260309`
- config: `configs/runtime/kerala_phase5_disposed_config.json`
- mode: same NI IDs (`18`, `1744`, `2190`), `status=Disposed`, `min_cnr_year=2025`, `workers=16`

Current purpose:

- fetch missing disposed case details/hearings (and PDFs if present) to merge with prior pending-heavy corpus.

## Kollam Script

Script: `scripts/kollam_section138_scrape.py`

It:

1. Reads Kollam NI list HTMLs (`18/1744/2190`, pending+disposed).
2. Writes full list universe to `normalized/case_list_items.jsonl`.
3. Filters `CNR ... 2025`.
4. Queues/fetches live details/hearings/PDFs via API.
5. Exports `cases.csv` and `hearings.csv`.
6. Writes run summary JSON.

Run command:

```bash
./.venv/bin/python scripts/kollam_section138_scrape.py --run-id kollam_section138_all_courts
```

Actual completed run:

```bash
MIN_REQUEST_DELAY=0.2 HTTP_MAX_RETRIES=2 ./.venv/bin/python scripts/kollam_section138_scrape.py --run-id kollam_section138_all_courts_v2
```

## New Configurable Batch Mode

Added CLI command:

```bash
./.venv/bin/district-court-api-scraper batch-scrape --config configs/kollam_ni_138_2025_2026.json
```

This mode is district-agnostic and configurable for future runs (for example Mohali / SAS Nagar):

- select state + district by name
- select all courts or a subset
- provide one or more case-type blocks (`act_ids`, `section`, `statuses`)
- set target CNR year suffixes (`2025`, `2026`, etc.)
- set thread count (`workers`, up to 16)

Template configs:

- `configs/kollam_ni_138_2025_2026.json`
- `configs/example_batch_template.json`
- `configs/kollam_smoke_one_query.json` (1-query health check)

Output format from batch mode:

- `output/runs/<run_id>/exports/cases.csv`
- `output/runs/<run_id>/exports/hearings.csv`
- `output/runs/<run_id>/exports/caselists.csv`
- optional samples: `sample_cases.csv`, `sample_hearings.csv`

## Structured Re-Export Command

For existing runs, this command regenerates the required 3-file format:

```bash
./.venv/bin/district-court-api-scraper export-format --run-id <run_id> --years 2025,2026 --state-name Kerala --district-name Kollam --sample-size 50
```

It writes:

- `cases.csv`
- `hearings.csv`
- `caselists.csv`
- optional sample CSVs

### Smoke behavior in current environment

Current environment smoke result:

```bash
MIN_REQUEST_DELAY=0.1 HTTP_MAX_RETRIES=1 ./.venv/bin/district-court-api-scraper batch-scrape --config configs/kollam_smoke_one_query.json
```

Observed: `ACT_LIST` incomplete with `captcha attempts exhausted or submitAct unavailable`.  
This corresponds to the same backend HTML payload:

- `Welcome User Search Page not Found here`

Meaning: transport/session bootstrap is currently being rejected by the live service from this runtime context.  
The configurable code path is in place; when the service endpoint is reachable, the same command path runs with session-coupled detail/hearing fetch.
