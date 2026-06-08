# District court benchmarker approach

## Purpose

This document is the operating manual for a config-driven district court benchmark build in this repo.

The goal is to:

- choose the districts to scrape in one JSON config
- download case lists and case details for those districts
- rebuild normalized outputs from raw files if needed
- consolidate the result into a canonical benchmark corpus
- feed that canonical corpus into downstream analysis such as KM curves, district comparisons, or benchmark HTMLs

The Kerala NI Act Section 138 build is the working example for this approach.

## What the benchmarker uses

The benchmarker depends on two config layers.

### 1. District-courts snapshot

This is the state and court lookup file consumed by `configurable_batch.py`.

Example:

- `configs/runtime/kerala_live_district_courts_20260309.json`

This file maps:

- state name to state code
- district name to district code
- court complex name to `crtvalue`
- optional establishment codes

Freeze this file per run family. Do not point long-running benchmark work at a moving upstream config if you want reproducibility.

### 2. Batch target config

This is the high-level scrape spec consumed by `district_court_api_scraper.cli batch-scrape`.

Example seed file added for this workflow:

- `configs/district_benchmarker_template.json`

This file decides:

- which state and districts to scrape
- whether to scrape all court complexes or a named subset
- whether to expand establishments under each court complex
- which act ids, section, and status buckets to query
- which CNR years to keep for detail fetch
- whether hearings should be fetched

## Config contract

The batch config schema is defined by `src/district_court_api_scraper/configurable_batch.py`.

Top-level fields:

- `run_id`: output run id under `output/runs/`
- `workers`: concurrent query workers
- `target_cnr_years`: exact CNR years to fetch when `min_cnr_year` is not used
- `min_cnr_year`: lower bound for detail fetch by CNR year
- `sample_size`: optional sample CSV export size
- `district_courts_config_path`: path to the frozen district-courts snapshot
- `allowed_case_keys_path`: optional selector-key allowlist for targeted refreshes
- `fetch_hearings`: whether to fetch hearing history along with case details
- `progress_log_interval_seconds`: progress write frequency
- `targets`: district target blocks

Each target block contains:

- `state`
- `district`
- `court_complexes`
- `expand_establishments`
- `case_types`

Each `case_types` block contains:

- `act_ids`
- `section`
- `statuses`

## How query expansion works

The benchmarker expands one target block into many actual eCourts queries.

Expansion dimensions:

- district
- court complex
- establishment code
- act id
- status

Important rules from the code:

- `court_complexes: "all"` means every court complex under the district snapshot entry
- `court_complexes: ["..."]` means only the named complexes
- `expand_establishments: false` means use only the first establishment code attached to that court complex
- `expand_establishments: true` means expand every establishment code available for that court complex
- `statuses` can include `Pending`, `Disposed`, or `Both`

The Kerala scrape used both broad district-wide configs and narrower court-level refresh configs. The benchmarker should do the same.

## Kerala pattern to copy

The Kerala work effectively used three config modes.

### Mode 1. Broad district sweep

Use this when you want one benchmark corpus across many districts.

Pattern:

- one target block per district
- `court_complexes: "all"`
- `expand_establishments: false` for an initial pass
- Section 138 act ids `18`, `1744`, `2190`
- `statuses: ["Pending", "Disposed"]` or `["Both"]` depending on the operational phase

Example source configs:

- `configs/runtime/kerala_phase1_lists_config.json`
- `configs/runtime/kerala_phase4_all_both_config.json`

### Mode 2. Expanded refresh

Use this when the first pass shows coverage gaps at the establishment level.

Pattern:

- name the exact court complexes to refresh
- set `expand_establishments: true`
- optionally constrain detail fetch using `allowed_case_keys_path`

Example source config:

- `configs/runtime/unblock_refresh_20260310/kerala_refresh_all_caselists_pending_disposed_20260311.json`

### Mode 3. Missing-detail backfill

Use this when you already have case lists and want to fetch only missing details.

Pattern:

- keep the same district and court target structure
- generate a selector-key allowlist from missing rows
- set `allowed_case_keys_path` to that file
- rerun `batch-scrape` against the same run or a new refresh run

Relevant scripts:

- `scripts/attribute_status_from_case_lists.py`
- `scripts/retry_status_queries.py`
- `scripts/rebuild_batch_run_from_raw.py`

## Recommended workflow from scratch

### Step 1. Freeze the district-courts snapshot

Create a runtime snapshot for the target state.

Naming convention:

- `configs/runtime/<state>_live_district_courts_<YYYYMMDD>.json`

For Kerala, the current frozen snapshot is:

- `configs/runtime/kerala_live_district_courts_20260309.json`

### Step 2. Create the benchmarker config

Start from:

- `configs/district_benchmarker_template.json`

Then edit:

- `run_id`
- `district_courts_config_path`
- district list under `targets`
- case-family filters under `case_types`
- year filter via `min_cnr_year` or `target_cnr_years`

For a district benchmark, the config is the source of truth for which districts are in scope.

### Step 3. Run the configurable batch scrape

From `district-court-api-scraper/`:

```bash
.venv/bin/python -m district_court_api_scraper.cli \
  --project-root "$PWD" \
  batch-scrape \
  --config configs/district_benchmarker_template.json
```

Outputs land under:

- `output/runs/<run_id>/raw/`
- `output/runs/<run_id>/normalized/`
- `output/runs/<run_id>/exports/`
- `output/runs/<run_id>/summary_batch_scrape.json`

### Step 4. Monitor or pause the run

```bash
.venv/bin/python scripts/batch_run_control.py \
  --run-root output/runs/<run_id> \
  status
```

```bash
.venv/bin/python scripts/batch_run_control.py \
  --run-root output/runs/<run_id> \
  pause
```

```bash
.venv/bin/python scripts/batch_run_control.py \
  --run-root output/runs/<run_id> \
  resume
```

### Step 5. Rebuild from raw if normalized outputs need repair

If a run has usable raw HTML but incomplete normalized outputs, rebuild from raw rather than rescraping immediately.

```bash
.venv/bin/python scripts/rebuild_batch_run_from_raw.py \
  --run-root output/runs/<run_id>
```

This regenerates:

- `normalized/case_list_items.jsonl`
- `normalized/case_details.jsonl`
- `exports/cases.csv`
- `exports/caselists.csv`
- `exports/hearings.csv`

### Step 6. Run targeted refreshes when coverage is incomplete

If you need list-only discovery first:

- create an empty text file
- set `allowed_case_keys_path` to that file
- keep the district targets unchanged

That causes the scraper to retain case lists while skipping detail fetch.

If you need missing-detail refresh later:

- compare refreshed `case_list_items.jsonl` against the canonical `case_details.jsonl`
- write missing selector keys to a text file
- set `allowed_case_keys_path` to that file
- rerun the expanded config

This is the Kerala unblock-refresh pattern documented in:

- `output/runs/kerala_ni138_phase6_combined_20260309/analysis/KERALA_UNBLOCK_REFRESH_RUNBOOK_20260310.md`

### Step 7. Canonicalize into one benchmark corpus

The benchmark analysis should not run on a pile of partial scrape runs.

Create one canonical run root and merge case details into:

- `output/runs/<canonical_run_id>/normalized/case_details.jsonl`

Canonical merge rule used in Kerala:

- dedupe by `cino`
- keep a backup of the old canonical file before merge
- write a merge summary JSON recording source runs, before count, and after count

Kerala examples:

- `output/runs/kerala_ni138_phase6_combined_20260309/summary_case_details_merge.json`
- `output/runs/kerala_ni138_phase6_combined_20260309/summary_case_details_merge_disposed_backfill_20260313.json`

At the moment, this merge step is operational and scripted via one-off repo snippets rather than a dedicated checked-in CLI. Treat the merge summary JSON as part of the reproducibility record.

### Step 8. Run downstream benchmark analysis

Once the canonical corpus is ready, downstream scripts can compute district comparisons.

For the Kerala S138 benchmark, relevant analysis scripts include:

- `scripts/km_by_district.py`
- `scripts/km_compute_toggle_data.py`
- `scripts/build_kollam_vs_kerala_v3.py`
- `scripts/build_kollam_vs_kerala_start_date_variants.py`
- `scripts/crosstab_case_type_nature_of_disposal.py`

For another benchmark family, the same pattern holds:

- canonicalize first
- analyze second
- promote shareable outputs last

## How to adapt this to another time period

You do not usually need a new scrape config just to change the analytical filing window.

If the benchmark corpus already contains the relevant filing years:

- keep the same canonical scrape corpus
- change the analysis parameters such as `--min-filing-date`
- regenerate the comparison outputs

You need a new scrape only if:

- the new period includes filing years not already present in the canonical corpus
- the district scope changes
- the case-family scope changes

## How to adapt this to another state

Change these inputs:

1. Create a new frozen district-courts snapshot for that state.
2. Copy `configs/district_benchmarker_template.json` to a state-specific config.
3. Replace the `state`, `district`, and `district_courts_config_path` values.
4. Verify the case-family act ids and section values for that state.
5. Run an initial broad sweep.
6. Audit gaps and run expanded establishment refreshes if necessary.
7. Merge successful backfills into one canonical run root.
8. Point downstream analysis at the new canonical run.

## Output contract

A completed benchmark scrape run should have at least:

- `summary_batch_scrape.json`
- `normalized/case_list_items.jsonl`
- `normalized/case_details.jsonl`
- `exports/cases.csv`
- `exports/caselists.csv`

A completed benchmark corpus should additionally have:

- merge summary JSONs
- analysis outputs derived from the canonical run

## Validation checklist

Before treating a benchmark corpus as ready:

1. Confirm every intended district appears in the config and the run summary.
2. Confirm the district snapshot file matches the intended state and scrape date.
3. Confirm `case_details.jsonl` exists and is non-trivially populated.
4. Confirm the year filter is what you intended: `min_cnr_year` or `target_cnr_years`.
5. Confirm whether establishments were expanded or collapsed.
6. Confirm missing-detail refreshes were merged into the canonical corpus.
7. Confirm downstream analysis points at the canonical run, not an intermediate refresh run.

## Current seed files

Manual:

- `analysis/District court benchmarker.md`

Seed config:

- `configs/district_benchmarker_template.json`
