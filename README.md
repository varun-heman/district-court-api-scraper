# District Court API Scraper

A Selenium-free scraper for the Indian district eCourts portal
(`services.ecourts.gov.in/ecourtindia_v6`). It talks to the site's AJAX
endpoints directly over HTTP, solves the listing captcha (OCR → Gemini
fallback), and walks the case-list → case-detail → hearing-history → PDF chain
in a single authenticated session. On top of the scraper sits a set of
analysis scripts built around one research question: **how long do Negotiable
Instruments Act §138 cheque-bounce cases take to resolve in Kerala, and does
the ONCourts digital-court rollout in Kollam change that?**

---

## Attribution

This repository is forked from [xKDR/district-court-api-scraper](https://github.com/xKDR/district-court-api-scraper), originally built by the xKDR Forum. The original work focused on Kerala NI-138 analysis. Changes made in this fork are documented in [`docs/CHANGES.md`](docs/CHANGES.md) and summarised below.

### Changes from upstream

| Area | Change |
|------|--------|
| `transport.py` | Session rotation: fresh HTTP session + app_token reset every 150–250 requests (randomised) to reduce rate-limiting fingerprint |
| `configurable_batch.py` | `fetch_pdfs` config flag to disable PDF downloads entirely; tqdm progress bar with H:MM:SS elapsed and rolling ETA; per-case green-tick log lines; proxy pool wiring |
| `proxy_pool.py` | Support for commercial rotating residential proxy services (e.g. Webshare) in addition to GCP tinyproxy fleet |
| `scripts/sample_cnrs_by_district.py` | New script: samples N disposed cases per district from a `cases.csv` export, for targeted hearing scrapes |
| `configs/punjab_ni138.json` | Batch config for Punjab NI-138 (cheque-bounce) cases, 2023–2026, all 23 districts |
| `analysis/` | Punjab NI-138 analysis data and Flourish choropleth map CSVs |

### Guides

For setup and day-to-day usage, use this README. For the low-level protocol
details (routes, session rules, captcha policy, PDF strategy) see
[`docs/district-courts-api-explainer.md`](docs/district-courts-api-explainer.md).
For the most recent change log see [`docs/CHANGES.md`](docs/CHANGES.md).

---

## Repository layout

```
.
├── src/district_court_api_scraper/   # the installable package (the scraper)
├── scripts/                          # one-off pipelines & analysis (grouped, see below)
├── configs/                          # batch-run JSON configs + runtime inputs
│   └── runtime/                      # proxy list, sample CNRs, live run configs
├── data/                             # curated inputs only; generated datasets are ignored
├── analysis/                         # analysis notes; generated reports/templates are ignored
├── output/runs/<run_id>/             # generated per-run scrape outputs (ignored)
├── tests/                            # pytest suite + fixtures
├── prompts/                          # LLM prompts used by the Saras analysis
├── state/tasks.db                    # local SQLite scrape queue (ignored)
└── docs/                             # explainer + change log
```

Bulk scrape data (`output/`, `tmp/`, `state/*.db`), generated analysis datasets
(`data/*` except the small ONCourts benchmark), generated reports/templates
(`analysis/**/*.html`, `analysis/*.csv`, `analysis/*.json`, `docs/*.html`),
secrets (`.env`), and the real proxy list are **gitignored**. See
[Data archive](#data-archive) for how the historical raw HTML/PDF is stored
outside git history.

### The package (`src/district_court_api_scraper/`)

| Module | Purpose |
|--------|---------|
| `config.py` | Env-driven settings (`AppConfig`), per-run directory layout (`RunPaths`), `.env` loader. |
| `transport.py` | Low-level HTTP: session, `app_token` bootstrap/refresh, throttle, retry, sticky proxy. |
| `ecourts_client.py` | High-level wrapper — one method per eCourts endpoint. |
| `captcha.py` | Captcha solver: Tesseract OCR (5 variants) → Gemini `2.5-flash-lite` fallback. |
| `queue.py` | SQLite task queue with dedupe, attempt logging, requeue-incomplete. |
| `pipelines.py` | `PipelineRunner` — single-worker discover → list → details → retry → export loop. |
| `configurable_batch.py` | Config-driven multithreaded batch scraper (one sticky proxy per worker). |
| `parsers.py` | BeautifulSoup/regex parsers for the case-list / detail / hearing / PDF HTML. |
| `exporters.py` | Basic `cases.csv` / `hearings.csv` export. |
| `structured_exports.py` | Rich export with the full analytical column set (acts, sections, disposal, etc.). |
| `proxy_pool.py` | Thread-safe round-robin over a newline-delimited proxy file. |

### Scripts (`scripts/`, grouped by purpose)

- **`scraping/`** — collection entry points: `kollam_section138_scrape.py`,
  `kollam_section138_mt.py` (multithreaded), `prepare_sample_keys.py`,
  `preflight_proxies.py` (check proxies are reachable before a run).
- **`refresh/`** — coverage backfill & rebuild:
  `attribute_status_from_case_lists.py`,
  `fetch_missing_details_from_case_list_source.py`,
  `rebuild_batch_run_from_raw.py`, `rebuild_cases_csv_from_raw.py`,
  `run_canonical_listonly_batch_with_retries.py`.
- **`retry/`** — recovery: `retry_failed_batch_queries.py`,
  `retry_status_queries.py`, `batch_run_control.py` (inspect/pause/resume a run).
- **`kaplan_meier/`** — survival analysis: `km_by_district.py` (per-district KM
  cohorts + medians), `km_compute_toggle_data.py`,
  `km_superimpose_oncourts_kollam.py`, `compare_kerala_case_cohorts.py`, and the
  shared `disposal_filters.py` (normalizes "Nature of Disposal", drops
  transferred/made-over cases). *These four import each other — keep them in
  this folder.*
- **`kollam_vs_kerala/`** — report builders for the headline comparison:
  `build_kollam_vs_kerala_v3.py`, `build_kollam_vs_kerala_v3_tabs.py`,
  `build_kollam_vs_kerala_start_date_variants.py` (orchestrator over the 4
  filing windows), `build_kerala_oncourts_kollam_v1.py`,
  `apply_toggle_data_to_pucar.py`.
- **`did/`** — difference-in-differences attempt:
  `build_kollam_did_analysis.py`, `build_kollam_vs_kerala_did_attempt.py`.
- **`crosstab/`** — `crosstab_case_type_nature_of_disposal.py`.
- **`saras/`** — the Ahmedabad sample: `scrape_saras_km.py`, `km_saras.py`,
  `analyze_ahd_saras_sample.py` (OCR + Gemini field extraction + report),
  `report_assets_saras.py`.

> Scripts assume they are run from the repository root, e.g.
> `.venv/bin/python scripts/kaplan_meier/km_by_district.py --help`.

---

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .          # add ".[dev]" for pytest
cp .env.example .env                # then fill in GEMINI_API_KEY (optional)
```

Optional, for proxied scraping:

```bash
cp configs/runtime/proxy_ips.txt.example configs/runtime/proxy_ips.txt
# edit in real http://IP:PORT endpoints
```

### Configuration (environment)

All settings are read from the environment (and a project `.env`). Nothing is
hardcoded. See `.env.example`.

| Var | Default | Meaning |
|-----|---------|---------|
| `GEMINI_API_KEY` | — | Captcha fallback after OCR fails. Optional (OCR-only without it). |
| `ECOURTS_PROXY_FILE` | — | Path to the proxy list (`http://IP:PORT` per line). |
| `ECOURTS_BASE_URL` | `…/ecourtindia_v6` | eCourts base URL. |
| `MIN_REQUEST_DELAY` | `2.5` | Seconds between requests. |
| `HTTP_MAX_RETRIES` | `4` | Retry budget per request. |
| `HTTP_TIMEOUT_SECONDS` | `30` | Per-request timeout. |

---

## Running

The package installs a console script, `district-court-api-scraper`:

| Subcommand | What it does |
|------------|--------------|
| `discover` | Discover district/court/establishment/act metadata for a state. |
| `list-acts` | Run an act-based case listing (captcha-gated). |
| `fetch-details` | Fetch queued case details + hearings for a run. |
| `fetch-cnr` | Fetch details from a CNR list (`.txt` or `.csv`); supports `--proxy-file`. |
| `retry-incomplete` | Requeue and rerun INCOMPLETE tasks. |
| `export` | Basic cases/hearings CSV export. |
| `export-format` | Structured cases/hearings/caselists CSVs (`--years`, `--state-name`, …). |
| `batch-scrape` | Config-driven multithreaded scrape from a JSON config. |

Example (config-driven batch, with proxy rotation):

```bash
ECOURTS_PROXY_FILE=configs/runtime/proxy_ips.txt \
  .venv/bin/district-court-api-scraper batch-scrape \
    --config configs/kollam_ni_138_2025_2026.json
```

Example (CNR list, the Saras Ahmedabad sample):

```bash
.venv/bin/district-court-api-scraper fetch-cnr \
  --input configs/saras-ahmedabad-cnr-sample.csv \
  --proxy-file configs/runtime/proxy_ips.txt \
  --run-id saras_ahmedabad_100cnr
```

Config templates live in `configs/` (`example_batch_template.json`,
`kollam_ni_138_2025_2026.json`, `kollam_smoke_one_query.json`).

---

## Where results live

Each scrape produces `output/runs/<run_id>/`:

```
raw/         downloaded HTML + PDFs   (large; archived, not kept locally)
normalized/  parsed *.jsonl           (case_details, hearings, case_list_items)
exports/     cases.csv, hearings.csv, caselists.csv   ← the clean results
analysis/    per-run KM / report artifacts
summary_*.json
```

**The canonical Kerala NI-138 corpus** (the merged result of many collection
passes) is:

```
output/runs/kerala_ni138_phase6_combined_20260309/normalized/case_details.jsonl
```

Almost every analysis script defaults to this run root.

---

## Experiments

The work centres on Kerala NI-138 (cheque-bounce, §138) cases, case types
`CC`/`ST`, filed 2025+. Five threads of analysis were run:

1. **District benchmarker** (`analysis/District court benchmarker.md`) — the
   operating framework: a reproducible, config-driven pipeline (frozen
   district snapshot + batch target config) that can scrape any state's
   districts into one canonical `case_details.jsonl` and then run the analyses
   below. Kerala NI-138 is the worked example.

2. **Kaplan–Meier survival / case lifecycle** (`analysis/Kerala S138 comparison
   approach.md`) — the headline analysis. Per-district KM survival curves over
   the canonical corpus (event = disposal; transferred/made-over cases
   excluded), benchmarked against the separate **ONCourts** Kollam lifecycle
   dataset (`data/kollam-lifecycle-kaplanmeier.csv`). The scripts can regenerate
   the Jan/Apr/Jul/Oct 2025 variants and the combined tabbed report
   (`analysis/kollam_vs_kerala_v4_tabs_integrated.html`, ignored because it is
   generated output).

3. **Kollam-vs-Kerala difference-in-differences attempt**
   (`analysis/kollam_did_plan.md`; generated HTML reports are ignored) — an
   attempt to isolate the ONCourts effect from statewide trends (Kollam 2023-24
   vs ONCourts 2025+ vs Rest-of-Kollam vs Kerala). **Explicitly transition
   evidence, not a clean causal DiD yet** — it still needs matched controls and
   parallel-trends checks.

4. **ONCourts comparison v1** — the earlier superimposition of ONCourts Kollam
   vs Rest-of-Kollam vs other districts vs Kerala combined. Generated report and
   dataset outputs are ignored.

5. **Disposal crosstabs** — case type × nature-of-disposal composition for
   2025+ CC/ST cases. Note: the crosstab keeps transfer/made-over rows that the
   KM cohort drops, so totals differ. Generated CSV outputs are ignored.

A separate strand is the **Saras / Ahmedabad** sample (`scripts/saras/`,
`prompts/`, `configs/saras-ahmedabad-cnr-sample.csv`): a 100-CNR Ahmedabad pull
used to validate richer field extraction (Acts/Sections, Processes, Final
Orders + PDFs, per-hearing disposal) and an LLM-assisted analysis. Results:
`output/runs/saras_ahmedabad_100cnr_20260528/` and `saras_km_full_20260528/`.

---

## Data archive

The historical scrape output is large (raw HTML/PDF plus normalized records from
many collection attempts). It is intentionally **not committed**. The backup
archive is attached to the GitHub repo as a release asset:

```
district-court-api-scraper-DATA-ARCHIVE-20260529.tar.gz
```

The archive contains `output/runs/`, including the canonical Kerala NI-138
corpus and the Saras runs. To restore a deleted run:

```bash
tar -xzf district-court-api-scraper-DATA-ARCHIVE-20260529.tar.gz \
  output/runs/<run_id>
```

Root-level generated datasets under `data/` and generated reports under
`analysis/` are also excluded from git. Recreate them with the scripts in
`scripts/` after restoring the relevant `output/runs/<run_id>/` tree.

---

## Punjab NI-138 Data

The Punjab NI-138 dataset is published as release assets on this repository. Two files are available:

**[punjab-ni138-cases.zip](https://github.com/varun-heman/district-court-api-scraper/releases)** — 78,688 NI-138 (cheque bounce) cases filed in Punjab district courts between 2023 and 2026. Contains `cases.csv` (one row per case: CNR, district, filing date, decision date, status, disposal type, petitioner, respondent, advocates) and `caselists.csv` (raw case list data).

**[punjab-ni138-hearings.zip](https://github.com/varun-heman/district-court-api-scraper/releases)** — 110,738 hearing records across 13,624 disposed cases (all 23 districts). The scrape was seeded with ~100 disposed cases per district but captured additional cases encountered during the run. Contains `hearings.csv` (one row per hearing: CNR, date, business text, next purpose, nature of disposal).

**[punjab-ni138-hearings-raw.zip](https://github.com/varun-heman/district-court-api-scraper/releases)** — Raw HTML files from the hearings scrape, one file per hearing. Use this if you want to re-parse the hearing pages with different logic. The hearings.csv above is derived from these files.

Data is sourced from the public eCourts portal (`services.ecourts.gov.in`) and was scraped using this repository. It covers all 23 Punjab districts. Cases raw HTML is not included; use the scraper to re-fetch if needed.

---

## Known issues

- Live scraping depends on upstream eCourts availability, captcha success, and
  optional proxy/firewall setup. Unit and fixture-based integration tests do not
  hit the live site.
