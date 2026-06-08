# Kerala S138 assessment approach

## Purpose

This document is the operating manual for the Kerala NI Act Section 138 assessment build in this repo.

It describes:

- what was built
- which source datasets feed it
- the filtering rules that define the analytical cohort
- the scripts and outputs that recreate the assessment from code
- what must change to rerun the same approach for a different filing window or another state

The current implementation is Kerala-specific and Kollam-benchmark-specific, but the workflow is general enough to reuse once the state-specific inputs are replaced.

## What the assessment produces

The current pipeline produces three layers of output:

1. Analytical intermediate files
   - row-level Kaplan-Meier cohorts
   - per-district median tables
   - comparison payloads and summary CSVs

2. Promoted variant pages
   - one HTML per filing-window start date
   - one promoted `data/` directory per variant

3. Combined start-date views
   - one multi-toggle HTML with external local data files
   - one single-file integrated HTML for sharing

Current promoted outputs:

- `analysis/kollam_vs_kerala_v3_jan2025.html`
- `analysis/kollam_vs_kerala_v3_apr2025.html`
- `analysis/kollam_vs_kerala_v3_jul2025.html`
- `analysis/kollam_vs_kerala_v3_oct2025.html`
- `analysis/kollam_vs_kerala_v3_tabs.html`
- `analysis/kollam_vs_kerala_v4_tabs_integrated.html`

Current promoted data directories:

- `data/kollam_vs_kerala_v3_jan2025`
- `data/kollam_vs_kerala_v3_apr2025`
- `data/kollam_vs_kerala_v3_jul2025`
- `data/kollam_vs_kerala_v3_oct2025`
- `data/kollam_vs_kerala_v3_tabs`

## Current Kerala implementation

Current canonical run root:

- `output/runs/kerala_ni138_phase6_combined_20260309`

Current canonical detail corpus:

- `output/runs/kerala_ni138_phase6_combined_20260309/normalized/case_details.jsonl`

Current benchmark source for Kollam digital-court comparison:

- `kollam-lifecycle-kaplanmeier.csv`

This benchmark CSV is not derived from `case_details.jsonl`. It is a separate lifecycle export used only for the ONCourts Kollam benchmark series.

Current start-date variants:

- January 1, 2025
- April 1, 2025
- July 1, 2025
- October 1, 2025

Current benchmark district label:

- `Kollam`

Current case prefix scope:

- `CC`
- `ST`

## Data sources

The assessment is built from two different source families.

### 1. Canonical scraped case-detail corpus

Source file:

- `output/runs/kerala_ni138_phase6_combined_20260309/normalized/case_details.jsonl`

This is the main analytical source of truth for Kerala.

Each row represents a normalized case-detail record derived from public district-court data. The relevant fields for this assessment are:

- `source_district`
- `cino`
- `case_number`
- `summary.case_details`
- `summary.case_status`
- `summary.history_rows`
- `raw_file`

This source is used for:

- district-wise KM cohorts
- row-level modeled case exports
- disposal diagnostics and crosstabs
- Kerala-side comparisons against the benchmark district

### 2. Benchmark lifecycle CSV

Source file:

- `kollam-lifecycle-kaplanmeier.csv`

This is a separate benchmark dataset for ONCourts Kollam.

Current fields used by the scripts:

- `filingnumber`
- `filing_date`
- `case_status`
- `relevant_date`

This source is used for:

- ONCourts benchmark KM series
- benchmark-vs-scraped comparison payloads
- combined toggle payloads and HTML

Important limitation:

- this CSV does not carry `Nature of Disposal`
- because of that, the benchmark side remains status-based
- the transfer / made-over exclusion currently applies only to the scraped Kerala cohort

## Canonical case model

The assessment does not trust case-list counts by themselves. The meaningful analytical unit is a deduped case-detail row from the canonical corpus.

Deduplication rule:

- dedupe by `district + cino`

Why this matters:

- different scrape runs capture different parts of the universe
- different backfill passes recover different missing detail cohorts
- the assessment is only trustworthy after all relevant runs have been merged into one canonical `case_details.jsonl`

## Filtering rules

The current analytical cohort is defined by the following rules.

### Base filters

- keep one row per `district + cino`
- require a parsable `Filing Date`
- require `filing_date >= chosen start date`
- require case-number prefix in `CC,ST`

### Disposal filter

The script `scripts/disposal_filters.py` normalizes `Nature of Disposal` into broader categories.

Current normalized exclusion bucket:

- `transferred / made over`

Any scraped Kerala case whose disposal falls into that bucket is dropped before survival modeling.

That means:

- it is not counted as a resolution event
- it is not kept as a censored pending row
- it disappears from the KM cohort entirely

This rule exists because transfer / made-over outcomes are administrative exits, not substantive end states for the case-resolution question being measured.

### Event-date inference

For the scraped Kerala cohort, event timing is inferred from:

1. `Decision Date` in `summary.case_status`, if usable
2. otherwise, the latest hearing-history row whose purpose matches disposal-like patterns

If neither produces a valid end date:

- the case is kept as censored at the censor date

For the ONCourts benchmark CSV:

- `case_status == disposed` and a valid `relevant_date` produce an event
- otherwise the row is censored at the censor date

## Diagnostic outputs

The assessment includes a disposal diagnostic layer separate from the KM build.

Scripts:

- `scripts/crosstab_case_type_nature_of_disposal.py`
- `scripts/disposal_filters.py`

Current outputs:

- `analysis/crosstab_case_type_nature_of_disposal_2025plus_cc_st.csv`
- `analysis/cases_for_crosstab_case_type_nature_of_disposal_2025plus_cc_st.csv`

Important distinction:

- the crosstab export still includes transfer / made-over rows
- the KM cohort does not
- do not assume crosstab totals match KM modeled-row totals

Use the crosstab layer to understand disposal composition and to decide whether disposal normalization rules should change.

## Build pipeline

This is the actual pipeline used in code.

### Step 1. Consolidate to a canonical detail corpus

Goal:

- merge all relevant scrape and backfill runs into one canonical `case_details.jsonl`

Manual principle:

- do not build KM outputs from partial runs
- merge first, analyze second

Current canonical output:

- `output/runs/kerala_ni138_phase6_combined_20260309/normalized/case_details.jsonl`

### Step 2. Build district-wise KM cohorts from the canonical corpus

Script:

- `scripts/km_by_district.py`

Inputs:

- `--run-root`
- `--censor-date`
- `--min-filing-year`
- `--min-filing-date`
- `--include-case-prefixes`

Key outputs:

- `km_case_rows.csv`
- `km_medians.csv`
- `km_districts.html`
- `km_case_rows.csv.stats.json`

What this script does:

- loads canonical `case_details.jsonl`
- dedupes by `district + cino`
- applies filing-date and case-prefix filters
- applies disposal exclusion using `scripts/disposal_filters.py`
- infers event dates
- writes the row-level survival cohort

### Step 3. Build ONCourts + scraped comparison payloads

Script:

- `scripts/km_compute_toggle_data.py`

Inputs:

- `--oncourts-csv`
- `--scraped-km-csv`
- `--kollam-name`
- `--min-filing-year`
- `--min-filing-date`
- `--censor-date`

Key outputs:

- `km_toggle_data.json`
- `km_toggle_summary.csv`
- `km_toggle_rows.csv`

What this script does:

- loads the benchmark ONCourts CSV
- loads the filtered scraped KM cohort
- splits scraped rows into:
  - `Rest of Kollam`
  - all other districts
  - Kerala combined
- computes survival series and summary metrics
- carries forward the scraped-side exclusion count from `km_case_rows.csv.stats.json`

### Step 4. Build the comparison diagnostic page

Script:

- `scripts/km_superimpose_oncourts_kollam.py`

Outputs:

- `km_oncourts_restkollam_other_kerala.html`
- `km_oncourts_restkollam_other_kerala_summary.csv`
- `km_oncourts_restkollam_other_kerala_rows.csv`

This is a diagnostic comparison layer, not the promoted user-facing page.

### Step 5. Promote each filing-window variant into stable `analysis/` and `data/` paths

Script:

- `scripts/build_kollam_vs_kerala_v3.py`

Inputs:

- a source analysis directory containing:
  - `km_case_rows.csv`
  - `km_medians.csv`
  - `km_toggle_data.json`
  - `km_toggle_summary.csv`
  - `km_toggle_rows.csv`
  - `km_oncourts_restkollam_other_kerala_summary.csv`
  - `km_oncourts_restkollam_other_kerala_rows.csv`
- a base HTML template

Outputs per variant:

- promoted HTML in `analysis/`
- promoted data bundle in `data/`
- manifest file

### Step 6. Build all filing-window variants

Script:

- `scripts/build_kollam_vs_kerala_start_date_variants.py`

What it currently does:

- iterates over the four hard-coded start dates
- runs:
  - `km_by_district.py`
  - `km_compute_toggle_data.py`
  - `km_superimpose_oncourts_kollam.py`
  - `build_kollam_vs_kerala_v3.py`
- writes:
  - `analysis/kollam_vs_kerala_v3_variants.json`

Current limitation:

- this orchestrator is hard-coded to the current Kerala run root and current four dates

### Step 7. Build the combined start-date view

Script:

- `scripts/build_kollam_vs_kerala_v3_tabs.py`

Inputs:

- `analysis/kollam_vs_kerala_v3_variants.json`

Outputs:

- `analysis/kollam_vs_kerala_v3_tabs.html`
- `data/kollam_vs_kerala_v3_tabs/kollam_vs_kerala_v3_tabs.json`
- `data/kollam_vs_kerala_v3_tabs/kollam_vs_kerala_v3_tabs.data.js`
- `data/kollam_vs_kerala_v3_tabs/modeled_rows_all_variants.csv`
- `data/kollam_vs_kerala_v3_tabs/series_summary_all_variants.csv`

Optional output:

- `analysis/kollam_vs_kerala_v4_tabs_integrated.html`

The integrated output embeds the combined payload directly into the HTML so it can be shared as one file.

## Current build command

For the current Kerala implementation, the all-in-one rebuild command is:

```bash
cd /Users/siddarth/Documents/Work/xkdr/repository/db-courts/SRC/district-court-api-scraper
./.venv/bin/python scripts/build_kollam_vs_kerala_start_date_variants.py
```

To rebuild only the combined start-date page from already-generated variant payloads:

```bash
cd /Users/siddarth/Documents/Work/xkdr/repository/db-courts/SRC/district-court-api-scraper
./.venv/bin/python scripts/build_kollam_vs_kerala_v3_tabs.py \
  --variants-json analysis/kollam_vs_kerala_v3_variants.json \
  --analysis-dir analysis \
  --data-dir data/kollam_vs_kerala_v3_tabs \
  --integrated-output-name kollam_vs_kerala_v4_tabs_integrated.html
```

## How to rerun for a different filing window

There are two ways to do this.

### Option A. One-off direct script runs

Use the lower-level scripts directly and set:

- `--min-filing-date`
- `--censor-date`
- `--include-case-prefixes`

This is the safest path if you only need one new window.

### Option B. Extend the variant orchestrator

Edit `VARIANTS` in:

- `scripts/build_kollam_vs_kerala_start_date_variants.py`

Then rerun the orchestrator.

Use this if you want a new family of filing-window variants and an updated combined start-date toggle page.

## How to adapt this approach for another state

The workflow is reusable, but the current scripts are not fully parameterized for state portability.

To rerun the same assessment for another state, you need to replace or adapt the following inputs.

### Required state-specific inputs

1. Canonical normalized case-detail corpus
   - equivalent to `normalized/case_details.jsonl`

2. Benchmark lifecycle dataset, if you want a benchmark-vs-state comparison
   - equivalent to `kollam-lifecycle-kaplanmeier.csv`

3. Benchmark district label
   - equivalent to `Kollam`

4. Case-prefix scope
   - currently `CC,ST`
   - may differ in another state depending on which case families answer the same legal question

5. Filing-window variants
   - update the start dates and labels

### Scripts that are already mostly reusable

- `scripts/km_by_district.py`
- `scripts/km_compute_toggle_data.py`
- `scripts/crosstab_case_type_nature_of_disposal.py`
- `scripts/disposal_filters.py`

These are driven mainly by arguments and file inputs.

### Scripts that are currently Kerala/Kollam-specific in naming or assumptions

- `scripts/build_kollam_vs_kerala_start_date_variants.py`
- `scripts/build_kollam_vs_kerala_v3.py`
- `scripts/build_kollam_vs_kerala_v3_tabs.py`
- `scripts/km_superimpose_oncourts_kollam.py`

For another state, these should either be:

- copied and renamed for that state, or
- generalized into reusable state-agnostic builders

### If another state does not have an ONCourts-style benchmark CSV

You can still run the scraped-case KM pipeline:

- build district-wise KM cohorts with `km_by_district.py`
- build disposal diagnostics with `crosstab_case_type_nature_of_disposal.py`

But the benchmark-vs-state toggle pages will need a different benchmark source or a different comparison design.

## Suggested rebuild checklist

Use this checklist when recreating the assessment from scratch.

1. Confirm the canonical `case_details.jsonl` is complete enough for the target cohort.
2. Confirm the benchmark CSV exists and matches the same time window and district benchmark concept.
3. Run `km_by_district.py` for the target start date.
4. Inspect `km_case_rows.csv.stats.json` for:
   - missing filing dates
   - bad decision dates
   - excluded transfer / made-over rows
5. Run `km_compute_toggle_data.py`.
6. Run `km_superimpose_oncourts_kollam.py` if you want the diagnostic comparison page.
7. Promote the outputs with `build_kollam_vs_kerala_v3.py`.
8. If building multiple windows, run `build_kollam_vs_kerala_start_date_variants.py`.
9. If sharing externally, build `kollam_vs_kerala_v4_tabs_integrated.html`.
10. Regenerate the crosstab diagnostics if disposal composition itself is part of the assessment write-up.

## Current known caveats

### Consolidation caveat

The most important failure mode is still incomplete consolidation.

If the relevant disposed-detail cohort exists in a later scrape or backfill run but was not merged into canonical `case_details.jsonl`, the assessment will understate resolutions.

### Benchmark asymmetry caveat

The ONCourts benchmark side does not carry `Nature of Disposal`, so the transfer / made-over exclusion is not symmetric across both data sources.

### Sharing caveat

`analysis/kollam_vs_kerala_v4_tabs_integrated.html` is a one-file payload-integrated output, but it still loads Plotly from CDN. It is single-file for local data distribution, not a fully offline artifact.
