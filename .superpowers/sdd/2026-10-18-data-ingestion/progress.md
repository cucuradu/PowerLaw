# SDD ledger - plan: docs/superpowers/plans/2026-10-18-data-ingestion.md

Pre-flight: Task 6 consumed Task 4's common runner/archive/budget interfaces and Task 3's canonical Fact/EvidenceLink/SourceDocument contracts. Task 5 legal changes shared `src/main.py` only; news work remained in `src/scrapers/news_scraper.py`, `src/ingestion.py`, and `tests/test_news_ingestion.py`.

Task 6: COMPLETE (2026-10-18)
- Deterministic article identity (`NEWS_...`) with tracking query stripping (`utm_*`, `gclid`, `fbclid`, `mc_cid`, `ref`) and feed GUID normalization.
- Semantic event identity (`EVENT_...`) based on actor, action type, target, magnitude, and date; distinct events do not merge, while syndicated articles cluster.
- Truthful extraction: negation handling (rejects false positive cuts/shutdowns), unexecuted tentative capex handled without fake magnitude, evidenced `0.0` preserved.
- Non-causal default graph edges (ABOUT/factual instead of all-to-all CAUSES cross-products).
- Production mode safety: network failures do not fall back to synthetic seeds.
- Common runner integration: `OFFICIAL_NEWS` registered with `_news_discover` and `_news_parse`.
- Validation: 14/14 tests in `tests/test_news_ingestion.py` passed; 32/32 tests in `tests/test_news_scraper.py` passed (46 total news tests passed).

Task 7: COMPLETE (2026-10-18)
- Adapters implemented in `src/scrapers/statistics.py`: Eurostat JSON-stat v2.0 (`sts_copi_q`), Destatis GENESIS table JSON and flat CSV (`61261-0002` residential PPI, `61241-0004` material PPI), ECB SDMX (FX), Bundesbank SDMX (housing loan rates), and SMARD electricity day-ahead market export.
- Geographic adapters in `src/scrapers/geography.py`: GISCO NUTS GeoJSON normalization, CRS/geometry preservation, `WITHIN` hierarchy facts, boundary versioning, and anti-proxy validation (`validate_facility_location`).
- Strict observation immutability, date bounds, units/rebasing, and no synthetic values in production.
- Common runner integration: registered `DESTATIS_GENESIS`, `EUROSTAT_STS`, `ECB_SDMX`, `BUNDESBANK_SDMX`, `SMARD_DE`, and `GISCO_GEOGRAPHY` in `src/ingestion.py`.
- Validation: 12/12 in `tests/test_statistics_ingestion.py`, 4/4 in `tests/test_geography_ingestion.py` (16 passed), 5/5 in `tests/test_quant_pipeline.py`. Full regression suite: 175 passed, 28 subtests passed.

Task 8: COMPLETE (2026-10-18)
- Implemented `run_pilot`, `generate_review_report`, and `--status` in `scripts/ops/ingest.py`. Verified `--pilot --dry-run` runs cleanly across all 8 verified sources (`EURLEX_CELLAR`, `OFFICIAL_NEWS`, `DESTATIS_GENESIS`, `EUROSTAT_STS`, `ECB_SDMX`, `BUNDESBANK_SDMX`, `SMARD_DE`, `GISCO_GEOGRAPHY`) with 0 uncommitted mutations.
- Implemented `get_production_overview`, `get_review_queue`, `submit_ingestion_job`, and `get_job_status` in `src/storage/dashboard_data.py`. Streamlit pages now submit asynchronous jobs rather than running blocking execution on render.
- Added `recent_runs(limit)` to `IngestionJobLedger` in `src/storage/ingestion_jobs.py`.
- Safe JSON deserialization for SQLite work item payloads in review reporting and production overview calculations.
- Normalized statistical/geographic parsing timestamps using document availability to ensure deterministic repeat runs.
- Validation: 7/7 in `tests/test_ingestion_pilot.py`, 18/18 in `tests/test_dashboard_suite.py`, 16/16 in statistics/geography suites. Full regression suite across all 12 test suites: 182 passed, 28 subtests passed.

Task 9: COMPLETE (2026-10-18)
- Implemented `enforce_cache_budget()` in `src/utils/minio_client.py`: evicts temporary scratch files and reproducible remote-backed objects while guaranteeing that sole unverified authoritative evidence is never deleted.
- Added `DiskFloorError` and disk floor checks in `EvidenceArchive` to pause/fail cleanly before exhausting free disk.
- Implemented `verify_restore(manifest_uri, target_dir)` in `src/utils/minio_client.py`: cryptographic SHA-256 validation of all archived documents into an isolated target directory with a detailed restore receipt.
- Added `last_success()` and `oldest_pending_job()` to `IngestionJobLedger` in `src/storage/ingestion_jobs.py` and exposed them via `get_production_overview()` and CLI `--status`.
- Extended CLI in `scripts/ops/ingest.py` with `--enforce-cache`, `--verify-restore <uri> [--restore-dir <path>]`, `--since`, `--until`, and `--limit`.
- Authored production scheduling and disaster recovery documentation in `docs/operations/scheduled_ingestion.md` and updated `README.md`.
- Validation: 11/11 in `tests/test_ingestion_operations.py`. Full regression suite across all 13 suites: 134 passed, 28 subtests passed, 5 quant passed, 18 dashboard passed; 0/26 review probe defects reproduced.

Task 10: PRE-FLIGHT - Real forecasting dataset, baseline and optional sparse GNN.
- Scope: `src/gnn/graph_exporter.py`, `src/models/gnn_dataset.py`, `src/gnn/trainer.py`, `src/gnn/temporal_gnn.py`, `tests/test_forecast_dataset.py`, `tests/test_gnn_export.py`.
- Interfaces: `build_forecast_dataset()`, `export_as_of()`, `to_heterodata()`.
- Release-aware feature/label build, no target leakage into inputs, chronological splits, and honest baseline evaluation.


