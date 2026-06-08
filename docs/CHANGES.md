# CHANGES — Punjab NI-138 fork (2026-06)

Fork of [xKDR/district-court-api-scraper](https://github.com/xKDR/district-court-api-scraper) by Varun Hemachandran. This pass extends the scraper for a Punjab-wide NI-138 analysis and adds several operational improvements to the batch scrape pipeline.

## Summary

Five changes from upstream: session rotation to reduce rate-limiting, a `fetch_pdfs` flag to skip PDF downloads on hearing-only runs, progress bar improvements, commercial proxy pool support, and a new district-sampling script. A Punjab-specific batch config and analysis data are also added.

## New / modified files

| File | Kind | Change |
|------|------|--------|
| `src/district_court_api_scraper/transport.py` | MOD | `ECourtsTransport` now rotates its HTTP session (fresh session + app_token reset) every 150–250 requests (randomised via `random.randint`). Accepts `proxy_pool` parameter; on rotation, advances to the next proxy in the pool. This spreads the session fingerprint across the run and reduces the impact of server-side rate limiting. |
| `src/district_court_api_scraper/configurable_batch.py` | MOD | (a) `BatchQuerySpec` gains a `fetch_pdfs: bool` field (default `True`). When `False`, all PDF download steps are skipped — useful for case-metadata-only or hearings-only runs that do not need order PDFs and would otherwise be slowed significantly. (b) tqdm progress bar now shows elapsed time in H:MM:SS and a rolling ETA; bar format uses `dynamic_ncols=True` so it reflows to terminal width. (c) Each completed case logs a green-tick line (`✓`) with district, court complex, CNR, and hearing count. (d) Proxy pool is loaded from `AppConfig.proxy_file` and passed to each worker's `ECourtsTransport`. |
| `src/district_court_api_scraper/proxy_pool.py` | MOD | Added support for commercial rotating residential proxy formats (e.g. `http://user:pass@host:port` from Webshare). Original file supported bare `http://IP:PORT` only. Note: Webshare rotating residential IPs are blocked by eCourts (502); static residential or datacenter proxies are required. |
| `scripts/sample_cnrs_by_district.py` | NEW | Reads a `cases.csv` export, filters to `Status == "Disposed"`, and samples up to N CNRs per district (default 100, seed 42). Writes a flat CNR list to `configs/runtime/punjab_sample_cnrs.txt` for use with `allowed_case_keys_path`. Prints per-district counts. |
| `configs/punjab_ni138.json` | NEW | Batch config for all 23 Punjab districts, NI-138 (Act ID 732, Section 138), `fetch_pdfs: false`, `fetch_hearings: true`, disposed cases only, 8 workers. Uses `allowed_case_keys_path` to restrict to the sampled CNR list. Covers 2024–2026 reliably; 2023 scrape was interrupted and is incomplete. |
| `analysis/punjab_map_data.csv` | NEW | Flourish choropleth input: district, Flourish numeric region ID, total cases, pending, disposed, disposal rate %, category label. |

## `fetch_pdfs` flag — usage

Add to your batch JSON config:

```json
{
  "fetch_pdfs": false,
  "fetch_hearings": true
}
```

When `fetch_pdfs` is `false` the scraper skips all PDF download steps at both case-detail and hearing level. Case metadata and hearing history are still fetched normally. This roughly halves run time for large hearing-focused scrapes and avoids filling disk with PDFs that are not needed for the analysis.

The flag defaults to `true` to preserve backward compatibility with existing configs.

## Session rotation — rationale

eCourts rate-limits by session fingerprint (cookie + app_token). Long-running multithreaded scrapes with a fixed session eventually trigger 500-series errors and slow degradation. Rotating the session every ~200 requests resets the fingerprint without interrupting the run. The rotation threshold is randomised (150–250) to avoid a synchronised rotation pulse across all workers.

## Proxy notes

Commercial rotating residential proxies (tested: Webshare) return 502 Bad Gateway from eCourts — the rotation pattern is detectable. Static residential or datacenter proxies work. If no proxy is configured the scraper runs direct; this works on a home/office connection with modest worker counts (≤8) but will degrade over multi-hour runs.

## New script — `sample_cnrs_by_district.py`

```bash
# From repo root, after a full case-list scrape:
.venv/bin/python scripts/sample_cnrs_by_district.py
# → configs/runtime/punjab_sample_cnrs.txt  (2,182 CNRs across 22 districts)
```

Edit `CASES_CSV`, `OUTPUT_FILE`, and `SAMPLE_PER_DISTRICT` at the top of the script to adapt to a different run or sample size.

---

# CHANGES — Saras / Ahmedabad enhancement pass (2026-05-28)

Track changes that need to flow back into `README.md` (when one exists) and
`district-courts-api-explainer.md`. Each entry pairs the code change with the
doc section it impacts.

## Summary

Enhancement to support a 100-CNR Saras Ahmedabad batch run (`configs/saras-ahmedabad-cnr-sample.csv`),
extracting fields the case-detail HTML carries beyond the current scraper's reach:
**Acts / Sections**, **Processes**, **Final Orders / Judgements** (with PDFs),
and per-hearing **Nature of Disposal** / **Disposal Date** from the Daily Status page.
Also: proxy rotation ported from `ecourts-api-complete`.

## New / modified files

| File | Kind | Purpose |
|------|------|---------|
| `src/district_court_api_scraper/proxy_pool.py` | NEW | `RoundRobinProxyPool` — port of `ecourts-api-complete/scraper/delhi_hc_api/proxy_pool.py`. Reads a newline-delimited file of `http://IP:PORT` URLs; thread-safe `.next()`. Fails fast (`ProxyPoolEmptyError`) on missing/empty file. |
| `src/district_court_api_scraper/transport.py` | MOD | `ECourtsTransport.__init__` now accepts `proxy_url`; when set, pins `session.proxies = {http,https: proxy_url}` for the lifetime of the worker (app_token is session-bound, so the proxy must stay sticky). |
| `src/district_court_api_scraper/config.py` | MOD | `AppConfig.proxy_file` field; resolved from `ECOURTS_PROXY_FILE` env var. |
| `src/district_court_api_scraper/pipelines.py` | MOD | (a) `PipelineRunner` reads the proxy pool when `config.proxy_file` is set and pins one egress IP. (b) `_handle_case_detail` / `_handle_cnr_case` call the new `_download_final_order_pdfs(...)`. (c) `run_fetch_cnr` uses `_read_cnr_input(...)` which accepts text or CSV with header. |
| `src/district_court_api_scraper/parsers.py` | MOD | Adds `parse_acts_rows`, `parse_processes_rows`, `parse_final_orders_rows`, `_find_table_after_heading`. `parse_case_detail_summary` now populates `acts`, `processes`, `final_orders` keys on the summary dict. |
| `src/district_court_api_scraper/structured_exports.py` | MOD | Adds cases.csv columns: `acts_under`, `sections_under`, `disposal_nature`, `disposal_date`, `processes_count`, `final_orders_count`. Adds hearings.csv columns: `row_source` (hearing\|final_order), `nature_of_disposal` (from Daily Status), `pdf_path` (from final-order PDF download). Emits one hearings.csv row per Final Order from the case-detail page (so orders surface even when no per-hearing Daily Status is fetched). New helpers: `_extract_daily_status_nature_of_disposal`, `_extract_daily_status_disposal_date`, `_read_daily_status_disposal`, `_extract_label_value`. |
| `src/district_court_api_scraper/cli.py` | MOD | `fetch-cnr` now accepts `--input` as text-or-CSV and gains a `--proxy-file` flag. |
| `configs/saras-ahmedabad-cnr-sample.csv` | INPUT | Provided by user. 100 unique CNRs (single column `CNR_Number`). |
| `configs/runtime/proxy_ips.txt` | INPUT | 16 GCP tinyproxy endpoints; pulled from `qa-prof:/opt/lsd-scrapers/nclt-api-complete/data/nclt_proxy_ips.txt`. Stays out of git (add to `.gitignore` if not already). |

## New per-CNR PDF policy (BREAKING)

Existing behavior: a case is marked `INCOMPLETE` only when **all** PDFs the case
detail HTML lists fail to download.

New behavior: **any** Final Orders / Judgements PDF that fails to download flips
the case to `INCOMPLETE` for retry. Files land under
`output/runs/<run_id>/raw/pdfs/<CNR>/<order_no>.pdf`.

The legacy `_download_pdfs` path is still in place for non-final-order PDFs (e.g.
order_table content embedded in hearing HTML), and keeps its old all-fail
policy.

## New CLI knob

```bash
ECOURTS_PROXY_FILE=configs/runtime/proxy_ips.txt \
  ./.venv/bin/district-court-api-scraper fetch-cnr \
    --input configs/saras-ahmedabad-cnr-sample.csv \
    --run-id saras_ahmedabad_100cnr_20260528
```

Or:

```bash
./.venv/bin/district-court-api-scraper fetch-cnr \
  --input configs/saras-ahmedabad-cnr-sample.csv \
  --proxy-file configs/runtime/proxy_ips.txt \
  --run-id saras_ahmedabad_100cnr_20260528
```

## Operational prerequisites (not code changes)

These are the steps the operator needs to run **before** the scrape can hit
upstream eCourts:

1. **Firewall whitelist** — the GCP firewall rule `allow-tinyproxy-from-professeer`
   on the `bomhc-proxy-*` VMs currently allows `136.243.170.253/32` (professeer)
   and `49.12.80.38/32` (qa-prof). The Mac running the scrape must be added:

   ```bash
   gcloud compute firewall-rules update allow-tinyproxy-from-professeer \
     --source-ranges=136.243.170.253/32,49.12.80.38/32,<MAC_EGRESS_IP>/32
   ```

   Caveat: a residential / mobile-network Mac IP is dynamic. Prefer running
   the scrape on qa-prof (already whitelisted) for any sustained batch.

2. **Regenerate proxy list when VMs rotate** — see
   `qa-prof:/opt/lsd-scrapers/plans/20260420_gcp_proxy_current_state.md`:

   ```bash
   gcloud compute instances list \
     --filter="name~'^bomhc-proxy-' AND status=RUNNING" \
     --format="value(networkInterfaces[0].accessConfigs[0].natIP)" | \
     sed 's|^|http://|; s|$|:8888|' > configs/runtime/proxy_ips.txt
   ```

## Doc sections to update

- **README.md** — does not exist in this repo yet; needs creation covering: usage
  (`fetch-cnr`, `batch-scrape`, `export-format`), env vars (`MIN_REQUEST_DELAY`,
  `HTTP_MAX_RETRIES`, `HTTP_TIMEOUT_SECONDS`, `ECOURTS_PROXY_FILE`,
  `GEMINI_API_KEY`), the captcha policy, and the PDF download policy described
  above.

- **district-courts-api-explainer.md** — extend `Parsing Chain` to cover the
  Acts / Processes / Final Orders / Daily Status disposal fields; replace the
  `PDF Strategy` section's *step 5* with the new strict per-order INCOMPLETE
  rule; add a `Proxy Rotation` section pointing at `proxy_pool.py` and the
  gcloud setup notes on qa-prof.

## Validation done

- Parser unit smoke against
  `output/runs/kerala_backfill_smoke_kas_dcourt_disposed_20260310/raw/case_details/Kerala_Kasaragod_DISTRICT_COURT_KASARGOD_2190_Disposed_est_3_KLKG040019362025_206603000782025.html`:
  Acts extracts `NEGOTIABLE INSTRUMENTS ACT / 138`, Processes empty (sample
  has none), Final Orders extracts `order_no=1, order_date=01-08-2025`.
- CSV loader against `configs/saras-ahmedabad-cnr-sample.csv`: header dropped,
  100 unique CNRs parsed.
- End-to-end `export_structured_outputs` against the existing Kasaragod run:
  new columns populate correctly (`disposal_nature`, `disposal_date`,
  `processes_count`, `final_orders_count`). Historical runs (data captured
  before this change) leave `acts_under` / `sections_under` blank because the
  new keys weren't written into `case_details.jsonl` at scrape time — this is
  expected; new runs will populate them.

## Not yet validated

- Live scrape against eCourts (requires firewall whitelist + run).
- Final-order PDF download against a case that actually has a PDF link on the
  case-detail Final Orders table (the Kasaragod sample has none).
