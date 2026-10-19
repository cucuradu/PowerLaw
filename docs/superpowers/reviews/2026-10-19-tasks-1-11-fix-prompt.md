# Repair instructions following the full Tasks 1–11 review

Read `AGENTS.md`, `docs/agent-memory/current-work.md`, the original ingestion plan/specifications, and **all** of:

- `docs/superpowers/reviews/2026-10-19-tasks-1-11-review.md`
- `docs/superpowers/reviews/full-audit-2026-10-19/operations.md`
- `docs/superpowers/reviews/full-audit-2026-10-19/sources.md`
- `docs/superpowers/reviews/full-audit-2026-10-19/forecasting.md`

The current completion claim is unsupported. Repair the original accepted requirements; do not add a new architecture or unrelated features. Preserve working-tree changes and the existing canonical stores. No reset, destructive migration, production cleanup, paid request, account change, dependency replacement or commit is authorized by this prompt. Use existing Python/SQLite/LanceDB/Arrow/MinIO/Streamlit patterns. Local storage remains supported; no cloud provisioning is needed to fix offline behavior.

Audit probes/results are available under `data/reports/full-audit/`. Understand their assertions and recreate meaningful tracked regressions with corrected expectations. Do not simply change tests, match exact phrases, reduce scope, mark skips as passes, or copy prior successful counts. The old 26 Task 5 predicates all pass now, yet broader lifecycle and semantic failures still reproduce.

Work in the order below. Shared contracts must be repaired before downstream features rely on them. After each phase, report acceptance evidence and remaining gates before marking that phase complete. Do not claim complete production maturity from offline helpers alone.

## Phase 1 — Protect data and enforce canonical truth (Tasks 1–3, 9)

1. Fix `src/utils/minio_client.py::enforce_cache_budget` so remote existence is insufficient for eviction: verify the saved content hash/readback, destination and manifest/reference retention before deleting a local copy. Unknown/corrupt/unavailable remote copies protect the local original. Pause rather than claim enforced limits when protected data prevents compliance.
2. Repair `verify_restore`: validate the manifest against its trusted checksum on every URI path, validate typed records/IDs, require a fresh target, verify resolved containment for every output, prevent overwrites/traversal and enforce cumulative/disk limits. Implement a complete portable canonical+evidence+ledger backup/restore, not just raw files and a receipt. Compare identities, revisions/tombstones, aliases, committed state, cursors and as-of results after isolated restore.
3. Enforce quality, supported relations/endpoints, matching source/origin, finite values, valid intervals, accepted evidence and full series/unit compatibility at the shared publication boundary. Adapter promises or standalone validator tests are insufficient. Preserve deliberate quarantine/test workflows without accepting unbacked production assertions.
4. Give series metadata an enforceable committed ownership/visibility contract; foreign staged definitions cannot supply committed endpoints. Persist `archive_sha256` in canonical Arrow/table reads with compatible handling of older records; preserve the distinct canonical payload digest.
5. Make production law/news readers committed canonical projections. Accept canonical `live`, exclude demo/test/quarantine/uncommitted rows, and stop inventing dates, zero quantities or unit actions. Keep demo seeds explicitly isolated. Invalidate UI store caches on canonical commit generation changes.
6. Require explicit zero fee/false subscription and the four allowed review states. Make frequency-aware migration handle DAILY/ANNUAL before guessing from day values; preserve source semantics and quarantine unresolved precision.

Required regressions: corrupt remote/local-sole-copy protection; tampered manifest, path traversal, existing target and complete restore; unsupported/unbacked/mixed-origin facts; NaN/reversed/unitless/unregistered-series observations; staged metadata references; batch hash cold reopen; production reader isolation; daily/monthly/quarterly/annual migration.

## Phase 2 — Finish runner and real spending bounds (Tasks 4/4A)

1. Reserve against enforced request input/output/PDF/thinking limits and verified model-specific prices. Send output caps matching the reservation; reconcile returned usage instead of settling estimates as actual. Unknown usage retains conservative reservations and blocks unsafe new dispatches.
2. One durable authorized budget must cover every source job, retry and resume in a pilot/run. Reject nonfinite budget/money on every interface path and reject settled/released IDs as new dispatch authorization. Preserve the verified zero-budget guard and explicit ADC SDK routing.
3. Save fetched document/first-availability/archive state before parsing. Crash/restart must reuse archived work, preserve first-fetch metadata and reconcile canonical/ledger/cursor states. A committed-with-errors run must not become complete or advance past a failed item merely through resume. Failed resume must remain failed/interrupted.
4. Reverify saved raw references before initial publication as well as resume. Enforce writer ownership at publication/cursor transitions and the single operational worker across sources. Do not hold SQLite transactions during network requests.
5. Count bytes while streaming, before buffering; respect deadlines/redirect/host limits and close every response/owned session. Reserve required disk/temp/staging capacity before allocation.
6. Finish honest diagnostics: configured is not working, skipped paid checks are unverified, and no secret/raw exception leaks into durable work/logs/reports.

Required tests: first-publication corruption; failed A/success B then resume; every durable crash boundary; concurrent source jobs/lease loss; maximum input/output costs, usage/unknown usage, retries, resume and pilot-wide caps; streaming oversize before full consumption; redaction and persisted diagnostics. Use fake SDK/HTTP/S3 only.

## Phase 3 — Make legal/news/official data actually enter canonical storage (Tasks 5–7)

1. Legal discovery must extract CELEX strings from seed dictionaries, reconcile upstream/URL/XML IDs, use allowed official hosts/date bounds and report amendment/dependency closure. Test the real registered runner, not only direct parser calls.
2. Legal extraction must preserve actual obligations/actors/conditions/quantities/law types and review unsupported semantics. Compliance with noise limits is not a passport; a generic application date is not a CBAM obligation. A real article anchor does not prove arbitrary extracted numbers. Process every obligation in each provision with bounded complete definitions/annex/reviewer context.
3. Archive failure must block canonical PDF publication. Corrected PDFs need version-specific batches. Reconcile removed/amended rules after a fresh pipeline/process using durable canonical history, with stable identities, tombstones and preserved old as-of queries. Persist review decisions; uncertain candidates cannot default accepted.
4. News discovery must parse RSS entries/GUID/link/UTC publication times, fetch only permitted article text, retain coverage limitations, and return canonical entities/evidence. Fix article/event/version/cluster identities so corrections supersede rather than multiply, distinct sites stay separate, and all supporting sources remain linked.
5. Preserve event time/status, negation/plans/cancellation, multiple same-type events and relevant text tails. Keep percentage points, percent, USD/EUR and mass units distinct; conversions require source lineage. Do not synthesize reactions/causality or unknown magnitudes.
6. Implement real bounded source-specific acquisition: GENESIS POST/auth/selection, Eurostat datasets/dimensions, SDMX keys/date bounds/pagination and verified supported SMARD route or explicit saved export. Remove unconditional `fetch_series=[]` success and generic-root requests.
7. One complete canonical provider series identity must join periods/revisions across all input formats. Destatis QUARM1 belongs to time, not series dimensions. Filter the exact registered target/material keys and preserve dataset/metric/tax/base/adjustment/codes. Retain all Eurostat dimensions/status/missingness and SDMX quarter/missing cases. Return definitions/entities/coverage/evidence alongside observations and validate them.
8. Preserve native SMARD UTC intervals/DST and meaningful hourly identities; explicitly aggregate with lineage where needed. GISCO must publish valid geography entities/hierarchy/coverage, with edition/content versions and archived geometry references; corrected geometry cannot reuse an immutable version ID.
9. Correct official seed mappings and mark handcrafted fixtures synthetic. Do not claim legal30/news100/stat20 reviews happened; produce actual fixed reviewed sets/reports or leave those gates pending.

Acceptance: offline **real registered runner** to archived document, complete definitions, evidence, accepted/review/rejected records, commit and as-of query for each source. Repeat/correct/retract/restart through that same path. Fake successful fetches must assert the actual request method/URL/query/payload, not ignore them.

## Phase 4 — Make the pilot, queue and operating gates real (Tasks 8–9)

1. Persist complete queued requests (source, bounds, limit, mode/dry_run, run/budget identity) and implement one CLI worker consuming them with durable states. Wire actual dashboard/page buttons to submission/status. Remove direct scrape/download/train/seed mutations and fabricated PDF fallback from production pages.
2. Enforce per-kind pilot caps, dependency expansion bounds and one run-wide budget. A dry-run with all failed sources is not success. Write explicit complete/incomplete reports with counts, bytes, cost, source/evidence/time/unit/entity coverage and actual annotated precision/recall. Source schedules/expansion stay gated by review requirements.
3. Query all outstanding review items rather than only 20 recent runs; compute real per-source freshness/errors separately from commit age and distinguish quiet feeds from parse failure. Keep dashboard cache coherent after external commits.
4. Implement bounded revision overlap/backfill, retention, compaction/delta-write/measurement requirements from the original plan. Do not claim 2010+ historical eligibility without vintage evidence. Preserve pinned backup/dataset versions; measure local/canonical growth before expansion.
5. Correct Windows scheduler working directory, PowerShell environment expansion, timezone/DST, overlap/missed-run behavior and reconciliation routes. Provide inspectable task definitions/instructions; do not silently create OS tasks. Seven immediate test calls are not seven days of operations. Keep live/installed/seven-day gates pending until actual evidence exists.

## Phase 5 — Build an honest forecast release (Task 10)

1. Labels must use committed eligible revisions/tombstones and earliest **verified same release** comparable denominator/numerator, with full quarterly intervals and exact locked target definition. Unknown vintages stay unavailable, never a shared `default` vintage. Check both levels' availability; missing target never falls back to series index zero.
2. Build scoped computational routes only from accepted geography/product/time/measurement relations, retaining evidence and transform lineage. Remove Cartesian every-rule/event-to-every-series links and fabricated scope flags. Exclude inactive/expired edges; announced future obligations are upcoming features only.
3. Use stable per-type identity maps across a sequence, active/observed masks and explicit inactive recurrent updates; new nodes, row permutations and missing targets must not corrupt history or crash stacking. Implement the named feature profile and train-only input/target transforms, not constant placeholder channels.
4. Sort/partition by real origins, respect outcome release availability, fit baselines/transforms on training only, and keep validation/calibration/test separate. Lag-4 must mean four real quarters. Generate out-of-sample residuals for the same frozen model/target policy; preserve the correct conformal quantile helper and report insufficient history honestly.
5. Gate GNN training by honest data eligibility. The actual virtualenv lacks PyG; do not claim skipped integration tests passed. Validate the specified sparse encoder, edge sensitivity in eval mode, equivalent dense reference, masks/GRU and edge-linear memory when the dependency is available. Do not replace missing execution with an unlabeled stand-in.
6. Independent ablations must reset identical model/optimizer state and use identical splits/budgets. Persist complete content-addressed dataset/feature/label/split/transform/checkpoint/evaluation manifests; different origins/content cannot share a generic dataset ID. Keep legacy GCN and shock demos explicitly demo-only and remove live training from production Streamlit paths.

If source vintages/sample counts cannot support training or calibration, finish the data-gap report and defensible baseline/observed state. Do not manufacture labels or a claim of GNN readiness to check a box.

## Phase 6 — Show only an auditable frozen outlook (Task 11)

1. Validate exact target measurement, building type, geography, tax/base/adjustment and dimensions; prefixes/forbidden-word checks cannot certify construction PPI. HICP must be rejected.
2. Forecast horizon follows the origin: `2026-09-30T23:59:59.999999Z` means 2026-Q4 even if Q2 is the latest observed level. Keep growth target units separate from derived index levels.
3. Load a committed frozen baseline/model prediction artifact with dataset/model/version/creation/target/vintage lineage. No artifact means forecast unavailable; do not generate last-level forecasts or interval residuals during page reads.
4. Show calibration only for sufficient distinct eligible chronological forecast origins and the matching frozen model/outcome policy. Twenty daily rows in one quarter are not 20 calibration quarters. Do not compute calibration from latest adjacent observed levels.
5. Use canonical latest/quality/retraction/validity queries for rules/events and eligible assertion-linked evidence. Tombstones, expired/rejected claims and staged/unrelated documents cannot become active verified context. Preserve observed/forecast/reported/scenario distinctions and noncausal limits.

## Final proof and truthful documentation

- Reproduce each applicable defect before fixing, then prove corrected behavior with focused regressions and an end-to-end source-to-outlook fixture. Do not change tests merely to pass or weaken canonical safeguards.
- Isolate all test paths, mock external requests and reset settings/cache/budget state between tests. Fix the dashboard timeout path and macro test's unmocked network/cached-mode assumptions. No paid/cloud calls are authorized here.
- Run the related suites after each phase and the complete offline suite after shared contracts stabilize. Report passes, failures, skips and exclusions separately. Fix relevant `git diff --check` errors.
- Update plan/current-work/session records from evidence. Separate code implemented, offline acceptance passed, live verified and operational/manual gates pending. Do not mark all Tasks 1–11 complete while material requirements remain unimplemented.
- Return exact commands/results, unresolved gates/real user actions, source/data limitations and the files changed. Leave work unstaged/uncommitted unless separately instructed.
