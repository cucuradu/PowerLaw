# Task 9 Completion Log

Date: 2026-10-18
Status: Completed & Verified

## Overview

Task 9 delivers storage budget enforcement, disk floor protection, manifest-backed disaster recovery, Windows Task Scheduler automation, and source maturity validation:

1. **Cache & Storage Budget Enforcement (`enforce_cache_budget`)**:
   - Implemented in `src/utils/minio_client.py`.
   - Evicts temporary scratch files (`temp_dir`) and verified reproducible S3-backed objects under budget limits.
   - Enforces the core invariant: **the sole unverified copy of an authoritative evidence document is NEVER deleted**. In local backend mode, all stored evidence is marked protected and preserved.

2. **Disk Floor Protection (`DiskFloorError`)**:
   - Defined `DiskFloorError(ArchiveError)` in `src/ingestion.py`.
   - Added `_check_disk_floor` to `EvidenceArchive` in `src/utils/minio_client.py` checking available partition space against `settings.disk_floor_bytes` before file writes or remote uploads.
   - Gracefully interrupts operations before disk exhaustion occurs.

3. **Manifest-Backed Isolated Restore (`verify_restore`)**:
   - Implemented in `src/utils/minio_client.py`.
   - Restores all documents from a batch manifest into an isolated destination directory.
   - Cryptographically validates every document's SHA-256 digest against the manifest. If a remote object is corrupted or missing, restore cleanly aborts and raises `ArchiveError`.
   - Generates a signed `restore_receipt.json` capturing verified hashes, restoration timestamps, and file locations.

4. **Ledger Operational Status**:
   - Added `last_success(source_id)` and `oldest_pending_job()` to `IngestionJobLedger` in `src/storage/ingestion_jobs.py`.
   - Surfaced these fields in `get_production_overview()` in `src/storage/dashboard_data.py` and CLI `--status`.

5. **CLI Operations Interface (`scripts/ops/ingest.py`)**:
   - `--enforce-cache`: Scans and evicts reproducible files within budget limits.
   - `--verify-restore <uri> [--restore-dir <dir>]`: Performs end-to-end restore validation.
   - `--since <date>`, `--until <date>`, `--limit <n>`: Bounded backfills respecting cursor overlaps.

6. **Scheduling & Maturity Documentation**:
   - Created `docs/operations/scheduled_ingestion.md` documenting Windows Task Scheduler jobs for hourly news, daily pilot, weekly legal reconciliation, and monthly statistical revision updates.
   - Documented UTC timezone policy, missed-run catch-up via persistent SQLite cursors, overlap revision windows, and the 7-point source maturity checklist.
   - Updated `README.md` with CLI operations and scheduling references.

## Verification Evidence (Iron Law Protocol)

1. `tests/test_ingestion_operations.py`:
   - `test_cache_budget_enforcement_evicts_only_reproducible_files`: PASSED
   - `test_cache_budget_enforcement_never_deletes_sole_unverified_copy`: PASSED
   - `test_download_interrupted_at_disk_floor`: PASSED
   - `test_object_store_outage_raises_archive_error`: PASSED
   - `test_corrupt_remote_object_fails_restore`: PASSED
   - `test_verify_restore_successful_manifest_roundtrip`: PASSED
   - `test_ledger_last_success_and_oldest_pending_job`: PASSED
   - `test_cli_operations_flags`: PASSED
   - `test_backfill_bounded_date_range_and_cursor_overlap`: PASSED
   - `test_out_of_order_revised_release`: PASSED
   - `test_seven_day_simulated_schedule_and_isolated_restore`: PASSED
   - **11/11 passed in 4.40s**.

2. Core Regression Suite (11 test suites):
   - `pytest tests/test_ingestion_operations.py tests/test_ingestion_pilot.py tests/test_statistics_ingestion.py tests/test_geography_ingestion.py tests/test_news_ingestion.py tests/test_news_scraper.py tests/test_law_ingestion.py tests/test_ingestion_schema.py tests/test_schema.py tests/test_ingestion_runner.py tests/test_object_storage.py -q -p no:cacheprovider --tb=short`
   - **134 passed, 28 subtests passed in 13.18s**.

3. Quantitative Pipeline Suite:
   - `pytest tests/test_quant_pipeline.py -q -p no:cacheprovider --tb=short`
   - **5 passed in 23.60s**.

4. Dashboard End-to-End Suite:
   - `pytest tests/test_dashboard_suite.py -q -p no:cacheprovider --tb=short`
   - **18 passed in 81.54s**.

5. Task 5 Defect Reproduction Probe:
   - `python -m data.reports.task5_review_probe`
   - **CONFIRMED 0 OF 26** defects reproduced.

All Task 9 requirements are complete, verified, and documented.

