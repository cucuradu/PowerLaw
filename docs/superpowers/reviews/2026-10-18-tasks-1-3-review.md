# Tasks 1–3 follow-up review

Reviewed and corrected on 2026-10-18. The previous credentials blocker was
incorrect: the existing private `GENESIS_API_TOKEN` successfully authenticated
official POST requests. No additional username/password or account is needed.

## Source verification

`config/data_sources.json` now records verified current-vintage selections:

- Destatis `61261-0002`: Germany, residential buildings, work on the building,
  including VAT, `2021=100`, February/May/August/November mapped to Q1–Q4.
  The exact codes are `DINSG=DG`, `STEMW1=STEMW1`, `BAUAR1=BPNBK`,
  `BAUAR4=BAULEISTBW`, measurement `PRE002`.
- Eurostat `sts_copi_q`: `Q.PRC_PRR.CPA_F41001_X_410014.NSA.I21.DE`.
  Producer prices and costs are separate categories. This comparison does not
  establish identical VAT treatment or permit replacing the primary target.
- Five monthly Destatis `61241-0004` context series: wood, rubber/plastics,
  nonmetallic mineral products, basic metals and fabricated metal products.
  Each selection has its exact GP2019 code and a separate series key.

Public official metadata/CSV response fields and SHA-256 checksums are retained
in `tests/fixtures/ingestion/` and the source register. Echoed authentication
parameters are excluded. The old synthetic fixture remains explicitly synthetic.
Unverified release dates, revision policy and comparable historical coverage are
null with reasons. These current responses do not prove historical first-release
vintages. Sources remain disabled for automatic ingestion.

## Corrected behavior

- Canonical records are persisted to typed LanceDB tables. Atomic publication
  selects retained table versions through `canonical_state.json`; interrupted
  writes remain invisible and retries recover. Readers stay pinned until reload.
- All writer entry points reject stale instances. OS-held locks release after
  process exit. Legacy mutations restore the published base before writing, so
  failed writes cannot reappear in a later successful operation.
- Committed replay compares canonical content, including evidence, rather than
  trusting supplied IDs. Duplicate versions retain their first committed owner.
- Unknown public availability is bounded by recorded time. Reference periods,
  application dates and arbitrary year cutoffs never establish availability.
  Latest versions are selected before applying retractions and quality filters.
- Evidence has batch ownership; staged, future and system-ineligible evidence
  or documents cannot change an earlier projection. Series geography metadata
  has its own availability boundary.
- Scope identity includes all supplied scope dimensions, nested JSON key order
  is canonical, equivalent timezone instants share identities, and malformed
  date suffixes are rejected. New fact-version hashes include source/release
  identity; explicitly stored old IDs are preserved on read.
- The legacy graph adapter delegates persistence and propagates failures.
  Metadata, audit fields, parallel edges, aliases and reopened history survive.
- Migration uses a new/empty destination, rejects unsafe parent overlap,
  preserves relation IDs/payload/validity and distinguishes series units,
  frequency, base and supplied provider dimensions. It does not turn legacy
  reference dates into publication dates. Unsupported endpoints stay quarantined;
  entity-only verified batches are handled before dependent quarantine records.

## Validation

The focused review regressions were reproduced before their corresponding
fixes. The final offline command was:

```powershell
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_multimodal_single.py -q -p no:cacheprovider --tb=short
```

Result: **256 passed, 28 subtests passed**. The paid/live multimodal smoke script
was excluded. The legacy repository-data GNN check uses a temporary copy.
An independent follow-up review found no remaining blockers in its six findings.

```powershell
.\.venv\Scripts\python.exe -m scripts.ops.migrate_data --dry-run --output data/reports/task123-review-migration.json
```

The isolated migration/backup restore reconciled 68 entities, 76 aliases,
147 source fact rows and 1,234 observation revisions. It reported 62 duplicate
fact versions explicitly. All 1,449 source records remained quarantined because
their provenance is insufficient; no records were silently skipped or malformed.
No production migration was activated and no commit was made.

## Remaining later tasks

Tasks 1–3 provide the reviewed foundation, not a mature ingestion service.
Task 4 must supply the recoverable runner, archive verification, SQLite cursor
ledger and persistent paid-call budget enforcement. Tasks 7/10 must establish
historical releases/vintages before strict historical training. Task 9 must
measure storage growth and replace full changed-table snapshots with safe delta
writes/retention before large backfills. No bulk ingestion, cloud provisioning,
paid multimodal test or model training was performed in this review.
