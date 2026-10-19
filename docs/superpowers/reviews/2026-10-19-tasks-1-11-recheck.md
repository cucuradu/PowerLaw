# Tasks 1-11 recheck

Date: 2026-10-19. Includes Task 4A: 12 work units in total. This is a review of the dirty working tree, not a commit review. No product fixes, installs, production activation, paid calls, or live source/cloud checks were performed.

**Verdict: useful fixes are confirmed, but completion through Task 11 still fails.** This report supersedes the old review where it explicitly credits a fix. Do not reuse the old defect count as a current count.

## Verification and snapshot limits

The first isolated full-suite rerun returned **339 passed, 32 failed, 3 skipped, 28 subtests passed** in 291.75 seconds. `config/data_sources.json`, `src/ingestion.py`, and `src/main.py` changed during that run. The second full run returned **335 passed, 36 failed, 3 skipped, 28 subtests passed** in 289.26 seconds; `src/main.py`, `src/scrapers/news_scraper.py`, and `tests/test_geography_ingestion.py` changed during it. Neither run describes one frozen revision. A final offline pass deselected the already reproduced dashboard timeout and external-data-dependent macro test; its result is recorded below. Separate source checkpoints detected no changes during their short probe runs.

All full-suite runs used test paths, local archive mode, zero extraction budget, blocked external requests/DNS/sockets, and permitted loopback for Windows asyncio. The live `test_multimodal_single.py` was excluded. Python 3.12.13, torch 2.14.1; **torch-geometric is absent**. No real PyG attention/training result is claimed.

The focused operations suite passed **70 tests**. Independent foundation, operations, source, and forecast probes used temporary real stores or explicit mocked provider responses. These checks overlap suite coverage; their counts must not be added to the full-suite total.

**Final stable offline result: 329 passed, 40 failed, 3 skipped, 2 deselected, 28 subtests passed in 211.92 seconds** (211.09 seconds reported by pytest). Source/test hashes were unchanged throughout this run. The two deselections were `test_render_dashboard_main` and `test_macro_fred_engine`; both failed in the preceding full runs. The live multimodal smoke was excluded separately. The three skips require absent PyG. No full passing suite is established.

The stable failures include nine outlook fixture setup failures, numerous old canonical/durability fixtures that no longer meet admission, the verified migration path, a geography assertion expecting one country-level fact after the adapter again emits two, real Eurostat pilot replay conflict, PDF missing-hashlib failure, and eight news magnitude/correction tests. Details and exact messages are in `final-offline-suite/suite-results.xml`. Do not call all 40 independent implementation bugs or dismiss all 40 as fixture maintenance.

The source checkpoint recorded **no source changes during its probes**. It reran all prior source examples against the new code and added complete-run semantic admission examples. The newly registered news/geography paths and legal revision behavior now work in those offline fixtures. Injected discovery/fetch fixtures do not establish live HTTP integration.

Legacy migration dry-run again succeeded: **71 entities, 229 aliases, 150 facts, 1,237 observation revisions, 1,458 quarantined records, zero verified records, zero malformed/skipped**. Its temporary restore reconciled successfully. Verified-record migration still fails in the existing suite; the all-quarantined dry run does not cover that path.

Local evidence and runnable harnesses: `data/reports/recheck-2026-10-19/`. These are ignored audit artifacts, not new product tests. The old [full review](2026-10-19-tasks-1-11-review.md) and its detailed reports retain historical evidence. Use the [remaining-fixes prompt](2026-10-19-tasks-1-11-recheck-fix-prompt.md) for current repair priorities.

## Confirmed fixes and improvements

- **Contracts/store:** missing fee/subscription attestations and unsupported quality decisions reject. Unsupported predicates, unregistered series, non-finite values, reversed intervals, mismatched units and mixed record/batch origins reject in the tested cases. Archive hashes survive canonical reopen. Explicit daily and annual migration intervals are preserved.
- **Readers:** canonical `live` news is retained; unknown dates and magnitudes stay null. Dashboard store cache refreshes after a committed generation changes. This does not resolve loose legacy reader eligibility.
- **Runner:** streaming stops at the chunk crossing the byte limit; fetched raw metadata survives a parser crash without refetching or changing availability; raw corruption blocks first publication. Failed resume is interrupted; partially failed work cannot advance the cursor via resume. This is safe rejection, not an executable failed-item recovery implementation.
- **Budget/worker:** generated output is capped at 1,024 tokens by default and recognized provider usage is settled. Non-finite monetary inputs and reuse of settled authorization IDs reject. Different-source worker competition rejects in the tested ledger. Positive-budget and same-run ownership gaps remain below.
- **Storage/recovery:** corrupt remote data protects the valid local evidence copy and pauses eviction. Archive put accounts for the next object's disk usage. Hash-named tampered manifests, traversal IDs, and nonfresh restore destinations reject. Portable canonical JSON files are now written. Complete restore is still broken below.
- **Review queue:** an old outstanding review remains visible after 21 newer runs. The overview count still misses it.
- **Law:** dictionary discovery no longer causes the old type error; registered fixture runs publish. Both earlier generic compliance/application examples now emit no obligations. Multiple obligation families in one article are retained; exception payloads propagate to projected scope edges. Identifier conflicts move to review. Fresh parser instances retract removed article rules; long-document review now includes the tail. Earlier in this recheck, local PDF corrections used new batches and archive failure published nothing; subsequent edits broke `process_pdf` with a missing `hashlib` name, so those paths are not currently passing. The old anchored `invented_tax_rate=88` example moves to review at extraction. Its complete runner fixture still encounters an endpoint error; that error is not an end-to-end success.
- **News:** the registered RSS fixture publishes entities and evidence. Previously failing title correction identity, event correction/retraction, different locations, investment negation, tail extraction, multiple actions, currency and percentage-point examples now improve or pass. These examples do not establish a held-out quality assessment or a durable per-article ingestion lifecycle.
- **Statistics/geography:** Destatis CSV target identity now joins four quarterly rows to the configured target; the official fixture has 984 observations across 246 series rather than 984 separate time-containing series. Eurostat retains requested dataset and industry dimensions in IDs. SDMX quarterly and missing observations parse. SMARD hours have distinct IDs and nonzero hourly intervals. The runner writes series definitions. GISCO commits entities/evidence with `WITHIN` and content-sensitive geometry versions.

## Remaining findings, in repair order

### R1. Canonical publication and production readers still allow unsupported truth

`src/storage/dual_layer_store.py:1548` checks the predicate name but does not enforce its endpoint type pair. A GEOGRAPHY-to-GEOGRAPHY `MANDATES` edge with evidence commits. Accepted observations can commit with a registered series and no source document or evidence. An accepted fact needs no EvidenceLink when any unrelated document is in the batch (`:1611`).

`project_graph` (`:1777`) permits an accepted fact with no eligible evidence when its endpoints exist in the cached graph. A fact available in 2020 with its only source/evidence available in 2025 is visible at a 2021 cutoff, without eligible evidence. Derived cache membership must not authorize canonical truth.

`get_all_laws_data` (`src/storage/dashboard_data.py:830`) and `get_all_news_data` (`:886`) now filter origin labels but still display loose uncommitted legacy rows/JSON labelled `live`. A label is insufficient proof of canonical commit, quality, origin and temporal eligibility.

Evidence: `foundations-results.json`, six remaining counterexamples, with 13 positive controls. New fixture rejection is a useful boundary fix, but it is not proof that every protected behavior still works. Existing valid-contract regression paths must be rebuilt and exercised.

### R2. Positive budgets can still be exceeded

`src/utils/api_wrapper.py:_reserve_paid_attempt` estimates text tokens by characters/4. One mocked input reserved 0.251 USD against 0.3 USD, dispatched, and settled **1.001 USD**. A 1 MiB inline PDF receives exactly the same reservation as no file because the accounting inspects the wrong SDK Part field. Missing provider usage settles the estimate as final and permits another dispatch; it does not leave an unresolved reservation that blocks further work.

`scripts/ops/ingest.py:run_pilot` subtracts successful returned costs in memory. On a source exception, already incurred durable spend is lost from that total. The probe admitted **2.002 USD against a 1.1 USD pilot budget while reporting zero**. Use durable shared authorization and reconcile failures/restarts, not only successful report totals. Mocked prices/usage prove local accounting defects, not real cloud billing.

### R3. Restore remains incomplete and can overwrite restored raw content

`src/utils/minio_client.py:verify_restore` omits EvidenceLinks from portable files and canonical reconstruction. Restoring a manifest containing evidence produces zero canonical evidence records. The ledger path calls nonexistent `update_cursor` (`:667`) and raises `AttributeError`.

A raw document whose version ID is `manifest.json` is accepted and then overwritten by the generated metadata file; the receipt still says restored. Reserve metadata names or isolate raw files. Restore's free-space check considers current free bytes, not the allocation: free 101, floor 100, three-byte raw input succeeds. Typed record/ownership/completeness validation and a complete isolated canonical/ledger comparison remain required.

A non-content-addressed local manifest without an independently supplied digest is also accepted. Treat that as unverified input or require a trusted digest; do not claim authenticated verification merely because the referenced raw object hashes match. The normal CLI currently writes portable files; it does not itself demonstrate a working cold canonical/ledger restore.

### R4. Semantic legal grounding is still too weak

Complete registered-run probes commit accepted fabricated obligations from archived content:

- `Operators shall ensure passport inspection procedures are followed.` becomes **establish a digital construction passport**.
- `CBAM document formatting requirements shall apply from 2028-06-01.` becomes **full CBAM declarations/certificates**, with inferred steel/cement scope.
- An injected extraction result `tax_rate=88 PERCENT` is accepted against `Operators shall file a report within 88 days.` Same article and matching digits do not support the claimed quantity/unit/mechanism.

The last example uses empty sector/commodity scope and real generated endpoints/evidence, so it reaches commit. It avoids the earlier forged fixture's missing endpoint. The implementation's literal `invented` target check and numeric substring check are insufficient. Move unsupported mechanisms, scope, numbers/units and dates to review; do not add example-specific keyword exclusions. Manual legal coverage/precision gates remain open.

The final source checkpoint also reproduces **`NameError: name 'hashlib' is not defined` in `process_pdf`** for both the archive-failure and correction probes. Repair the real caller and rerun verified archive/correction behavior; treating an early exception as successful archive protection would hide this regression. A separate complete registered legal fixture with sector scope still fails because its `sec_C24` endpoint is not emitted.

### R5. Acquisition and series definitions still do not represent a complete source workflow

`src/ingestion.py:_stats_discover` still builds generic `?series=...&since=...` URLs against provider roots; that does not implement each provider's endpoint/authentication/pagination/revision protocol. The registered runner fetches those generic URLs, not `fetch_series`. The current `fetch_series` (`src/scrapers/statistics.py`) references an unimported `Path` and raises `NameError`. Its new body reads test fixtures without a mode gate rather than performing production acquisition. Fixing only the import would expose fixture data through a production-looking API.

`_make_stats_parser` creates definitions with default quarterly producer-price semantics: the ECB daily FX series is **QUARTERLY / PRODUCER_PRICE_INDEX**, with null dimensions, geography, base and availability. Eurostat definitions likewise lose dimensions/geography/base metadata already present in IDs. A successful commit count cannot validate these semantics. Canonical frequency/period and measurement-kind compatibility must be checked.

RSS entries now produce facts, but `_news_parse` reuses the feed document version/batch and copies only article URL/upstream ID. It reads but does not apply the per-item date; evidence refers back to the feed with an empty or weak quote selector. There is no separately fetched/archived article document lifecycle here. Test entry updates/removals, availability and identity on a fresh process, and distinguish archived feed evidence from archived article evidence.

The latest news magnitude regex (`src/scrapers/news_scraper.py:1193`) applies a word boundary after the `%` alternative. In common text such as `20% in Germany`, both `%` and the following space are nonword characters, so there is no boundary. The stable suite reproduces eight failures across production cuts, tariffs, price reactions, correction values, negative percentages and explicit zero: known numeric values become null. Preserve the corrected currency/percentage-point semantics while restoring symbolic percentages; do not change these assertions to accept missing values.

### R6. Operational execution and maturity gates remain open

`submit_ingestion_job` persists only run context: queued status remains created, no executable job exists, and limit/dry-run request fields are lost. The CLI has no queue consumer. Existing Streamlit controls still directly download/scrape/seed, including the mock PDF fallback on failure. These are unchanged from the detailed original operations review.

Outstanding reviews are now queryable, but `get_production_overview` still scans only 20 recent runs and reports zero for the old pending review. Canonical counters also count stored versions rather than an explicitly eligible committed projection. The same active run can reacquire its lease and invalidate the original token; reject a second owner while active, not just a different source.

Pilot limits remain arbitrary shared source limits; a mixed planned/failed dry run reports `dry_run`. Required annotated quality gates, durable pilot authorization, bounded backfill and real seven-day proof remain incomplete. Schedule documentation still omits repository working directory, explicit timezone/catch-up/overlap behavior, and uses literal `%USERNAME%` in PowerShell. Installed task state was not rechecked; the previous read-only query was denied by Windows.

The existing `test_offline_pilot_rerun_yields_deterministic_state` also fails on the second real Eurostat fixture run with conflicting immutable observation content. This is a repeatability regression, not merely a fixture rejected for missing evidence. Preserve stable identities and first availability while reconciling replay metadata.

### R7. Training labels and graph/temporal evaluation still fail acceptance

The five dataset/export/model/trainer/forecast files are unchanged from the preceding full audit. New probes use registered series, matching units, source documents and evidence, so stricter upstream admission cannot explain away these failures:

- A staged batch with zero canonical query rows still produces a **50% training label**. Retracted data likewise produces 50%.
- Missing vintage becomes `default`; monthly bounds can supply a quarterly label, with the denominator released after the declared first label release.
- Lag evaluation uses another sample's target even when neither label was released at the next forecast origin.

Remaining prior code findings also stand: fabricated Cartesian context scope, expired message edges, recurrent state without ID/mask alignment, variable node-set failure, unused feature profile/normalization, missing chronological split/release artifact lifecycle, and ablations that do not establish independent fits. Future input exclusion and numerical conformal rank controls still pass. Actual PyG execution remains unavailable; substitutes are not evidence that the sparse GNN works.

### R8. The outlook still presents invalid forecast/context results

Current-contract real-store probes reproduce all five earlier critical Task 11 findings:

- HICP consumer prices are accepted and labelled German residential construction PPI.
- Origin 2026-09-30 predicts Q3 instead of Q4 and synthesizes index 100 with no frozen artifact.
- Twenty-one daily rows within one quarter produce an interval labelled calibrated with 20 samples.
- Expired/retracted rules are labelled ACTIVE twice even though the canonical graph has zero edges.
- A staged document is presented as evidence.

Until a valid frozen prediction artifact passes target, release, horizon and calibration checks, show an explicit forecast gap. Observed context must use committed point-in-time projections.

### R9. Existing regression coverage needs reconciliation, not weaker admission

Many suite failures now occur during setup: old facts use unsupported relations or lack source/evidence, observations lack registered series, and batches mix test/live origins. These failures do not prove that the named replay, durability, historical retraction or outlook behavior regressed; they mean those assertions are no longer reached. Rebuild valid fixtures and preserve the original behavior checks. The verified-record migration path also calls `stage_batch` with no documents/evidence and now fails; it needs an explicit evidence/quarantine policy.

Two new store tests give weaker evidence than their names imply. `test_batch_hash_cold_reopen` catches all exceptions around opening `batches` rather than the actual `canonical_batches` table, swallowing that persistence assertion. The independent reopen probe confirms the hash fix, but this test should assert it directly. `test_production_reader_isolation_and_cache_invalidation` checks origin via loops over empty reader results; it inserts no unsafe legacy rows. The independent nonempty reader probes still fail. Add explicit wrong-origin and uncommitted live-labelled records, then assert exclusion.

## Task-by-task decision

| Task | Recheck decision |
| --- | --- |
| 1 | Partial. Free-source attestation and reader defaults improve; production eligibility still bypassed. |
| 2 | Partial. State/ownership/hash improvements; mandatory evidence/type/semantic contracts still incomplete. |
| 3 | Partial. Invalid input rejection and migration intervals improve; evidence/as-of bypasses and verified migration regression path remain. |
| 4 | Partial. Recovery/archive integrity improves; failed-item retry and ownership/complete operational execution remain. |
| 4A | Partial. Output/usage accounting improves; positive-budget authorization is still unsafe. |
| 5 | Partial. Many concrete source/revision defects repaired; accepted semantic fabrication remains. |
| 6 | Partial. Registered publication and earlier extraction examples improve; per-article provenance/restart and held-out quality gate remain. |
| 7 | Partial. Identity/period/geography fixture repairs confirmed; real acquisition and typed series definitions remain. |
| 8 | Incomplete. Queue execution, actual UI integration, caps and human quality gate remain. |
| 9 | Incomplete. Safer cache and some restore defenses; complete restore/schedules/maturity proof remain. |
| 10 | Not ready. Training truth, temporal GNN/evaluation/release lifecycle remain. |
| 11 | Not ready. Wrong target/horizon/calibration and ineligible context remain. |

## Reproduction commands

Run from the repository root with this virtualenv. The audit harnesses enforce offline/test isolation; the migration command is dry-run only.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m data.reports.recheck-2026-10-19.run_final_suite
.\.venv\Scripts\python.exe -m data.reports.recheck-2026-10-19.run_final_offline_suite
.\.venv\Scripts\python.exe -m data.reports.recheck-2026-10-19.probe_foundations
.\.venv\Scripts\python.exe -m data.reports.recheck-2026-10-19.operations.recheck
.\.venv\Scripts\python.exe -m data.reports.recheck-2026-10-19.check_sources_checkpoint
.\.venv\Scripts\python.exe -m data.reports.recheck-2026-10-19.forecast.recheck_forecast
.\.venv\Scripts\python.exe -m scripts.ops.migrate_data --dry-run --target data/reports/recheck-2026-10-19/migration-target --output data/reports/recheck-2026-10-19/migration.json
```

The final stable result is documented above; `final-offline-suite/source-hashes-before.json` identifies the checked code. No live provider, remote privacy, actual credit deduction, installed scheduler, browser visual, held-out human quality assessment or real PyG training was verified.
