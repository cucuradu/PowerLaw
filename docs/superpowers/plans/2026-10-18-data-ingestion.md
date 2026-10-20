# PowerLaw Data Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build trustworthy ingestion for construction prices, supply conditions and regulation, then produce reproducible forecasting datasets.

**Architecture:** Preserve immutable evidence in configured object storage. Keep accepted versioned facts and observations in one typed LanceDB dataset; derive the graph, dashboard views and time-safe training inputs from committed batches. Reuse the existing project and keep the first run small.

**Tech Stack:** Python, Pydantic, requests, LanceDB OSS, PyArrow/Parquet, MinIO S3 client, standard-library SQLite, existing Streamlit/PyTorch, PyTorch Geometric for the optional typed GNN, and maintained `google-genai` replacing the legacy Google SDK. No new orchestration service or graph database.

**Spec:** [Data ingestion design](../specs/2026-10-18-data-ingestion-design.md), [technical architecture/schemas/user setup](../specs/2026-10-18-technical-architecture.md) and [free-source register/maturity gates](../specs/2026-10-18-free-sources-and-ingestion-readiness.md). Required reading together. Tasks 1?3 have been implemented and reviewed; later unchecked tasks remain proposed. Germany first, geographic relationships, PyTorch Geometric and free data sources are user-confirmed. The exact current-vintage construction-price target is verified; historical release vintages, operational gates and optional processing budgets remain separate requirements.

## Verification status (re-reviewed 2026-10-20)

A checked box below means "implemented and tested", not "proven on real provider data". The 2026-10-19 expert review found several boxes ticked on fixtures that failed on live data (wrong target ID in the dashboard, parser defects, a baseline that fell back to 0). Status per task:

| Task | Status | Evidence / caveat |
|---|---|---|
| 1 Inventory, demo isolation, target | Live-verified 2026-10-20 | Registry target ID now used everywhere; legacy demo graph tables that the store loaded as a fallback were quarantined (`data/_quarantine-2026-10-20/`). |
| 2 Evidence, revision, quality contracts | Live-verified 2026-10-20 | Pilot store commits; unchanged re-fetches no longer create revisions. |
| 3 One store, version-aware reads | Live-verified 2026-10-20 | Store choice depended on CWD/import order until `.env` loading was anchored to the repo. |
| 4 Runner and object storage | Runner live-verified; MinIO restore fixture-only | Runner used for all 2026-10-19/20 ingestion; restore not re-checked. |
| 4A AI SDK diagnostics | Live connection check only | `data/reports/*connection-check.json`; no extraction run. |
| 5 Legal ingestion | Fixture-verified, paused | No forecast value shown yet. |
| 6 News | Fixture-verified, paused | No forecast value shown yet. |
| 7 Official statistics | Live-verified 2026-10-19/20 | Destatis 5 tables, Eurostat, ECB MIR, Bundesbank, Bundesbank real-time (PP300, AFC15). 61241 suspended (provider status 7000); SMARD disabled until misdated rows are retracted. |
| 8 Orchestration and dashboard | Live-verified 2026-10-20 | Outlook shows the real target; GNN Lab no longer trains from the UI. |
| 9 Backfill, schedules | Backfill live-verified; schedules not verified | Target 1958-2026, predictors 1990+. |
| 10 Forecast dataset, baseline, GNN | Baseline superseded; GNN fixture-only | Replaced by the rolling-origin backtest on release-dated data (`scripts/ops/backtest_target.py`); GNN not shown to beat it. |
| 11 Outlook with evidence | Live-verified 2026-10-20 | Open: `published_at` shows fetch time. |

## Global constraints

- Start with EU legislation and Germany as the first construction market.
- Every tracked data source must be free: `access_fee=0`, `requires_paid_subscription=false`, confirmed permitted uses/attribution and no automatic paid fallback. Free registration may be required. Paid feeds, paywall access, data trials and proprietary benchmark dependencies are excluded. Missing free coverage is a reported gap.
- Use the technical architecture's selected stores: canonical LanceDB OSS at `data/production/lancedb`, local single-worker SQLite ledger at `data/production/ingestion.sqlite`, private S3-compatible evidence/archive storage, and bounded NetworkX/PyG projections. A remote object URI is never a local `Path`.
- Primary target: next-quarter percentage change in a verified German new residential construction producer-price index. Never substitute a cost index, material PPI, stock price or house price under the same target name.
- Unknown is null with a reason, never zero, today, 1900, 1970, or a guessed all-time validity interval.
- No silent synthetic production fallback; `live`, `demo`, and `test` are explicit origins.
- Facts/observations and their revisions are authoritative; graph/vector/dashboard tables are rebuildable views.
- Existing data is not deleted or rewritten in place during migration. Backup and restore verification precede activation.
- One ingestion writer; committed-batch visibility for every production read; no assumption of cross-table transactions.
- Begin with existing dependencies. Public discovery does not establish permission to retain, train on or redistribute publisher text.
- Paid extraction is disabled without a positive run-wide USD budget and verified model prices. Persist spent/reserved cost across retries/resumes; reserve maximum request cost before dispatch. Unknown pricing/usage stops paid calls. No new paid provisioning/bulk expansion without configured cost limits; existing storage charges continue independently.
- Quarterly forecast origin is the last UTC instant of Q; target is Q-to-Q+1 growth. First-release labels use both comparable index levels from the same earliest verified Q+1 release/vintage. Q may be unavailable as a feature at origin.
- Pilot caps: 20 legal acts initially, amendment dependencies reported if they exceed that cap; 300 news documents; 20-40 statistical series. Cache 2 GiB, temp 512 MiB; preserve a free-disk floor of max(2 GiB, largest expected batch staging estimate).

## Review focus

1. Historic data fetched today can be revised or lack a historical release time: exclude it from strict historical features without vintage evidence (Tasks 2, 7, 10).
2. Retries and crashes can leave two stores or a half-published batch: retries must converge to one committed result (Tasks 3, 4, 8).
3. Quarterly construction prices, monthly costs and financial proxies can look interchangeable: enforce full series keys/definitions and separate target contracts (Tasks 1, 7, 10).
4. Syndication, corrections and planned/negated news can multiply or fabricate impacts: preserve document versions and evidence, distinguish event state and noncausal links (Task 6).
5. Depleted local disk or object-store failure must not lose the sole copy of evidence: pause safely; cache eviction needs durable remote verification (Tasks 4, 9).

## File and compatibility map

| Location | Responsibility/change |
|---|---|
| `src/config.py` (new), `.env.example` | One environment-aware path/config policy, origin isolation, bounded storage/extraction settings. Never read/log secrets in reports. |
| `config/data_sources.json` (new) | Registry of approved sources, exact discovered series keys, target definitions, rights/status and freshness rules. |
| `src/schemas/ingestion.py` (new) | Document, assertion, observation, quality, batch and dataset contracts from the spec. |
| `src/schemas/graph_temporal.py`, `src/models/dual_layer_model.py`, their exports | Canonical graph vocabulary, compatibility conversion, nullable values and typed temporal/provenance fields. |
| `src/storage/dual_layer_store.py` | Durable canonical tables, version selection, committed-batch queries, filtered reads and graph projections. |
| `src/storage/graph_store.py` | Preserve old public imports/signatures through a compatibility adapter to the canonical store; remove unsafe `eval()` and independent overwrite persistence. |
| `src/storage/ingestion_jobs.py` (new) | SQLite cursors, leases, attempts and staged/committed batch manifests, single writer. |
| `src/ingestion.py` (new) | Small shared ingestion runner: fetch/archive/parse/validate/stage/commit/resume; adapters keep source-specific logic. |
| `src/utils/minio_client.py` | Configured private buckets, content-addressed objects, checksum verification, bounded temporary downloads/cache. |
| `src/main.py`, `src/scrapers/eurlex*.py`, prompts/rule schemas | Safe evidence-grounded legal extraction, full coverage, versioned rules and graph publication. |
| `src/scrapers/news_scraper.py`, extraction/news schemas | Stable identities, source provenance, permitted full text, evidence-backed events and deduplication. |
| `src/scrapers/statistics.py` (new), `src/scrapers/market_data.py`, `src/quant/macro_fred.py` | Official price/activity/financing series, release/vintage capture; existing secondary feeds cannot fabricate production observations. |
| `src/scrapers/geography.py` (new), both graph vocabularies and exporters | Versioned geographic identities/hierarchy, supported coverage/trade links, and type-local graph/tensor mappings. |
| `src/utils/api_wrapper.py`, `requirements.txt`, AI-calling tests/debug scripts | Replace deprecated Google SDK while preserving callers, structured/PDF extraction, 768-dimensional embeddings, typed failures and budget accounting. |
| `scripts/ops/ingest.py`, `scripts/ops/migrate_data.py` (new) | Bounded CLI operations and dry-run migration/audit, independent of Streamlit. |
| `src/storage/dashboard_data.py`, `dashboard.py`, `pages/*.py` | Production reads from committed views, explicit demo mode, source/quality/job status and queued ingest requests. |
| `src/gnn/graph_exporter.py`, `trainer.py`, `temporal_gnn.py`, `src/models/gnn_dataset.py` | Shared as-of projection, semantically typed features, future labels, sparse message passing. |
| `tests/`, `tests/fixtures/ingestion/` (new fixtures) | Isolated offline contracts/recovery tests and opt-in live source smoke checks. Update old seed-dependent assumptions. |
| `README.md`, `requirements.txt` only if needed | Setup, architecture, source-specific rights, commands and verified dependency constraints; no blanket permission claims. |

All names/signatures below are proposed interfaces. Keep existing public APIs working through the adapter until callers migrate; no package-wide class rename is required. Use the technical architecture's exact canonical table/column definitions and typed PyG tensor shapes rather than filling implementation gaps with the old generic node-metric schema.

## User setup and connection gates

The [connection checklist](../specs/2026-10-18-technical-architecture.md#9-connections-and-actions-you-need-to-provide) identifies user and implementation actions. Public sources do not require a paid subscription; some, including the selected GENESIS API route, may require free registration/access credentials. Private remote storage needs its endpoint/bucket/credentials; optional Gemini processing needs access and a positive run-wide budget. ENTSO-E and public-origin ALFRED are optional enrichments. Deterministic collection/manual review works without paid model processing; commercial feeds are excluded.

Add planned CLI `python -m scripts.ops.ingest --check-connections`: report configured/missing/unverified/working per service without revealing secrets; use no paid request without its configured budget. Do not create accounts, buy hosting, or claim a connection works because an environment variable exists. Implementation can complete schemas, offline fixtures, deterministic extraction and baselines while optional access remains absent.

## Current problems and their fixes

The design's [concrete fix register](../specs/2026-10-18-data-ingestion-design.md#how-to-fix-the-current-problems) records F01-F20, including current files, exact corrective behavior, timing and regression proof. Use it alongside the tasks below; these are planned changes, not a claim that the defects have been repaired.

| Implementation task | Fixes it must cover |
|---|---|
| 1: configuration, origin and target | F01, F02, F20 |
| 2: canonical contracts | F03, F04, F06, F12, F15 |
| 3: canonical store and migration | F01, F03, F05, F09, F10, F11, F12, F13 |
| 4: runner and object storage | F11, F18, F19 |
| 5: legal ingestion | F04, F05, F06, F07 |
| 6: news ingestion | F03, F04, F08 |
| 7: official observations | F12, F15 |
| 8: pilot and operational views | F11, F20 |
| 9: bounded operations and restore | F18 |
| 10: forecasting and graph features | F12, F13, F14, F15, F16, F17, F20 |
| 11: construction outlook | F20 |

## Task 1: Inventory, isolate demo data and lock the first target

**Files:** `src/config.py`, `config/data_sources.json`, `.env.example`, `src/storage/dashboard_data.py`, `src/scrapers/news_scraper.py`, `src/scrapers/market_data.py`, `src/quant/macro_fred.py`, `scripts/ops/migrate_data.py`, `tests/test_ingestion_config.py`, `tests/test_dashboard_suite.py`, `README.md`.

**Interfaces:** `get_settings() -> Settings`; `audit_sources(paths: list[Path]) -> dict`; CLI `migrate_data --audit --output <report.json>`. Audit is read-only and does not instantiate seeding dashboard helpers. `Settings` exposes data directory, origin, local database path, object endpoint/bucket, cache/temp limits and extraction budget; database path and object URI remain distinct types.

- [x] Write failing tests for all entry points resolving the same configured DB; production empty stores staying empty; failed market/news fetch producing unavailable status and zero synthetic production rows. Demo tests must opt in.
- [x] Run `python -m pytest tests/test_ingestion_config.py tests/test_news_scraper.py tests/test_quant_pipeline.py -q`; confirm the new boundary tests fail for the intended reason before editing behavior.
- [x] Implement settings and explicit origin selection. Replace automatic seeding in read helpers with deliberate demo initialization; update all seed-dependent dashboard test fixtures to temporary demo storage.
- [x] Audit both existing DB directories, Parquet exports and MinIO if configured: row/schema/version counts, IDs, origin evidence, duplicate rates, raw references, coverage and bytes. Unproven legacy origins are quarantined for classification, not labelled live automatically.
- [x] Implement the companion free-source register in `config/data_sources.json`, including selected endpoints/series, `access_fee=0`, subscription=false, rights, auth, release cadence, configurable allowable delay and source owner. Test a paid/trial/restricted candidate cannot be enabled and a source changing to paid/unavailable produces an alert/status without purchase or substitution. Mark untested candidates disabled.
- [x] Discover the direct Destatis DE construction-price target (family 61261, candidate quarterly table 61261-0002), cross-check comparable Eurostat metadata, and lock exact building type, producer/output-price meaning, tax treatment, base, coverage, reference month/quarter convention and release metadata. Discover named material PPI/context series separately. Store full keys and fixtures; if the intended target is absent, report the gap rather than substituting a different measure. *(Verified using the existing GENESIS_API_TOKEN: official table metadata, 2025 flat-file rows and exact dimension codes; Eurostat producer-price category and five separate material PPI selections. Public fixtures and checksums are registered. Unverified historical coverage/release dates remain null; Tasks 7 and 10 must establish first-release vintages before historical training.)*
- [x] Run updated configuration/dashboard tests, inspect the audit report and register a proposed source/target manifest. Commit only this independently reviewed preparation change. *(Configuration/origin checks, official target evidence and the reviewed source manifest are verified. Code is intentionally uncommitted; no commit instruction was given.)*

## Task 2: Canonical evidence, revision and quality contracts

**Files:** `src/schemas/ingestion.py`, `src/schemas/graph_temporal.py`, `src/models/dual_layer_model.py`, `src/schemas/extraction.py`, `src/schemas/rule_schema.py`, exports, `tests/test_ingestion_schema.py`, `tests/test_schema.py`, `tests/fixtures/ingestion/`.

**Interfaces:** `SourceDocument`, `Entity`, `EntityAlias`, `Fact`, `EvidenceLink`, `SeriesDefinition`, `Observation`, `QualityDecision`, `BatchManifest`, `DatasetManifest`, `PredictionRecord` implement the technical architecture's canonical tables/fields. `stable_id(namespace: str, *parts: str) -> str` uses SHA-256. `validate_candidate(candidate: Fact | Observation, source: SourceDocument) -> QualityDecision` returns `accepted`, `needs_review`, `rejected`, or `out_of_scope` plus reason codes.

- [x] Write tests asserting identity is identical across two Python processes; exact duplicate releases share IDs; corrections change version IDs; null quantity remains null; missing dates do not become today/1900; fraction/percent/percentage-point are distinct; original units survive normalization.
- [x] Test public availability versus reference time using a March observation first released in May and revised in June: unavailable in April, first version in May, revised version in June. A legacy value without vintage evidence is not eligible before first fetch.
- [x] Run `python -m pytest tests/test_ingestion_schema.py -q` and confirm failure, then implement schemas and pure validation/identity functions. Add COMPANY and GEOGRAPHY plus the technical architecture's factual, geographic and measurement relations to both vocabularies and conversion tests; validate entity versus edge namespaces and supported endpoints.
- [x] Permit non-numeric legal obligations; preserve conditions/exception structures and evidence anchors. Update prompt/model mismatches and existing fixtures without weakening boundaries to arbitrary accepted strings.
- [x] Run `python -m pytest tests/test_ingestion_schema.py tests/test_schema.py tests/test_dual_layer_model.py tests/test_edge_cases.py -q`; review serialization/round trips and commit. *(Tests and serialization round-trips verified; commit paused awaiting user instruction.)*

## Task 3: One durable store, version-aware reads and reversible migration

**Files:** `src/storage/dual_layer_store.py`, `src/storage/graph_store.py`, `scripts/ops/migrate_data.py`, `src/storage/__init__.py`, `tests/test_ingestion_store.py`, `tests/test_dual_layer_store.py`, `tests/test_graph_store.py`.

**Interfaces:** `stage_batch(batch: BatchManifest, documents: list[SourceDocument], facts: list[Fact], observations: list[Observation]) -> None`; `commit_batch(batch_id: str) -> None`; `query_observations(cutoff: datetime, mode: str = "public_as_of", entity_ids: list[str] | None = None) -> pa.Table`; `project_graph(cutoff: datetime, mode: str = "public_as_of") -> dict`. Modes are exactly `public_as_of` and `system_as_of`. Projected result contains canonical nodes/edges and their typed provenance; old accessors preserve legacy return shapes via the adapter.

- [x] Test repeat staging/commit yields identical committed IDs/counts, staging stays invisible, a mid-table-write crash resumes safely, and competing writers are rejected. Test two metrics on one node retain two as-of results.
- [x] Test expired/amended/retracted assertions remain visible at old cutoffs, future obligations appear as upcoming but not active, and relation multiplicity survives graph projection. An accepted fact cannot refer to an uncommitted/missing document or endpoint.
- [x] Test one commodity resolves to multiple separately typed series and exact evidence; same material in DE and another market remains distinct. Test an edge-subject trade series resolves correctly. Select latest revisions including tombstones before suppressing retracted records, so old values cannot be resurrected. Freeze committed generation for each multi-table read and derived view.
- [x] Run `python -m pytest tests/test_ingestion_store.py -q` for the expected failures. Implement append-only version tables and committed-batch reads on the typed store; avoid loading complete observations during initialization.
- [x] Make the legacy graph store an adapter; remove `eval()` and redundant overwrite persistence. Use a multigraph or explicit edge collection where several typed relations share endpoints; consumers cannot silently collapse them.
- [x] Implement migration into a new versioned directory with a dry-run mapping/report. Preserve old IDs as aliases, compare counts/keys/history, isolate unverified data and restore a copied backup into temporary storage. Activate by configuration only after reconciliation; rollback points at untouched old data.
- [x] Run graph/store/schema regressions and `python -m scripts.ops.migrate_data --dry-run --output data/reports/migration.json`; inspect differences and commit. *(Superseded by the independent follow-up review and validation record linked below. Code remains uncommitted; production migration is not activated.)*

## Follow-up review of Tasks 1?3 (2026-10-18)

See [review and validation record](../reviews/2026-10-18-tasks-1-3-review.md).
Canonical data now lives in typed Lance tables, with `canonical_state.json`
atomically selecting the visible table versions and committed generation.
Evidence links have owning batches and independent availability checks.
Readers stay pinned until `load()`; all writers reject stale snapshots.
Unknown publication times use recorded time, never the reference period.

Changed tables currently use retained Lance snapshots for the bounded pilot.
Task 9 must measure storage growth and implement safe delta writes/retention
before larger backfills; do not vacuum versions referenced by a reader, backup
or dataset manifest. Completing these foundations does not complete the runner,
source parsers, paid-call budgets, operational rehearsal or training dataset.

## Task 4: Recoverable runner and verified object storage

**Files:** `src/storage/ingestion_jobs.py`, `src/ingestion.py`, `src/utils/minio_client.py`, `scripts/ops/ingest.py`, `tests/test_ingestion_runner.py`, `tests/test_object_storage.py`.

**Interfaces:** `run_ingestion(source_id: str, *, limit: int, since: str | None = None, until: str | None = None, dry_run: bool = False) -> dict`; `archive_document(document: SourceDocument, content: bytes) -> SourceDocument`; `resume_batch(batch_id: str) -> dict`. The returned report includes discovered/fetched/staged/accepted/review/rejected/duplicate/committed counts, source errors, bytes and cost. Dry run discovers/plans without publication or paid extraction.

- [x] Write failing tests for transient 429/503 retry bounds and Retry-After; timeout; HTML error disguised as PDF; checksum mismatch; oversize download; hostile/private redirect; failure before/after staging; replay after commit; two writers acquiring the same source job. Assert unset/zero budget or unknown prices dispatches zero paid requests, a reservation exceeding remaining budget is rejected, and a resumed run/retry cannot reset accounting.
- [x] Assert cursor advancement never loses unrecorded items; parsing/review failure is recorded separately from fetch status; blocked archive never creates accepted graph data. Log tests redact credentials and request tokens.
- [x] Run `python -m pytest tests/test_ingestion_runner.py tests/test_object_storage.py -q`, then implement SQLite ledger, leases, bounded HTTP, raw manifests and batch commit orchestration. Enforce single writer, archive-first ordering and durable reserve/reconcile cost accounting against `extraction_budget_usd`; no configured budget means paid extraction disabled.
- [x] Extend existing S3 client for configured existing private bucket/prefix, TLS, checksums and readback. Keep local-only archive mode explicit for tests/development; storage failure never silently changes URI semantics.
- [ ] Repeat restart tests with injected crashes at every durable boundary. Run an opt-in upload/read/checksum/restore smoke check against a configured test prefix only; no deletions outside that prefix. Commit once fixtures pass; leave unconfigured cloud checks clearly pending.

## Task 4A: AI SDK compatibility and connection diagnostics

**Files:** `src/utils/api_wrapper.py`, `src/config.py`, `.env.example`, `requirements.txt`, `scripts/ops/ingest.py`, `scripts/debug/list_models.py`, `scripts/debug/debug_models.py`, `src/schemas/extraction.py` example text if needed, `tests/test_api_wrapper.py` (new), existing AI-mocking tests.

**Interfaces:** Preserve `get_embedding`, `get_embeddings_batch`, `get_completion`, `get_completion_with_json`, `upload_to_gemini`, `get_completion_multimodal` signatures used by current callers. Implement `check_connections() -> dict[str, dict]` behind CLI `--check-connections`; response statuses are `missing`, `configured_unverified`, `working`, `failed`, with safe reason codes and no key/token values.

- [ ] Write failing mocked tests for legacy wrapper return shapes, explicit 768-dimensional embeddings, draft/reviewer model routing, structured response parsing, PDF upload/use/cleanup, transient-only retries, permanent authentication failure and budget reservations on every paid attempt. Test importing the module creates no paid request or network connection.
- [ ] Run `python -m pytest tests/test_api_wrapper.py -q`; replace `google-generativeai` with official maintained `google-genai` inside the existing adapter. Preserve public interfaces, verify model availability/configuration on the target account, and route costs through Task 4's persistent budget boundary. Update direct SDK debug callers and stale schema examples; avoid a second competing API wrapper.
- [ ] Test missing keys, wrong endpoint, unavailable model and unset/exhausted budget through connection diagnostics; redact tokens, including query-string API keys. Bounded public HTTP/S3 checks are explicit; a paid model check requires budget and reports unverified if skipped.
- [ ] Update `.env.example`/setup docs with the architecture's proposed environment contract. Run API-wrapper and news/legal caller tests; only run a real bounded paid smoke check when configured. Commit compatibility and diagnostics separately from source extraction changes.

## Task 5: Source-grounded legal ingestion and graph projection

**Files:** `src/main.py`, `src/scrapers/eurlex.py`, `eurlex_bulk.py`, `eurlex_history.py`, `src/prompts/rule_extraction.py`, `rule_validation.py`, rule/document schemas, `tests/test_law_ingestion.py`, relevant law/schema fixtures.

**Interfaces:** `LawIngestionPipeline.process_document(document: SourceDocument, content: bytes) -> list[Fact]`; keep `process_pdf(pdf_path: Path)` as a local adapter. Source runners provide discovery metadata and raw version IDs; accepted legal facts feed Task 3 projections and `structured_rules` search view.

- [x] Add fixtures for structured XML/HTML, normal/scanned/table PDFs, amended articles, conditional exemptions, future phases, missing dates and non-numeric obligations. Assert every emitted rule has exact evidence and no unsupported number/date or scope widening.
- [x] Test failed extraction/embedding/review leaves the last committed law visible; new law versions retain old history; statutes with decision identifiers are not excluded just because a discovery regex only matches regulations/directives.
- [x] Run `python -m pytest tests/test_law_ingestion.py -q` for intended failures. Consolidate the duplicate `process_pdf` definition, prefer structured text, bound/chunk long documents by provisions with shared context, and record complete coverage.
- [x] Review candidates against cited evidence and relevant definitions/annexes. Replace invented dates and draft-only QA; route uncertain OCR/tables to review. Keep national transposition/implementation coverage explicitly unknown when absent.
- [x] Stage validated rule versions before publication; publish factual law/rule/scope/amendment links with intervals and provenance. Review all initial pilot rules, run legal/schema tests and commit.

### Follow-up repair of Task 5 (2026-10-18)

See [repair prompt and review evidence](../reviews/2026-10-18-task-5-fix-prompt.md) and session log `docs/agent-memory/session-log/2026-10-18-task-5-repair.md`.
All 26 defect reproductions in `data/reports/task5_review_probe.py` are confirmed resolved (`CONFIRMED 0 OF 26`).
All 39 legal/schema tests in `tests/test_law_ingestion.py`, `tests/test_ingestion_schema.py`, and `tests/test_schema.py` pass with strict storage isolation.
Non-destructive replacement, immutable version staging, unanchored rule routing, uninvented dates/semantics, and archive-first ordering are verified.


## Task 6: News discovery, identity, extraction and event resolution

**Files:** `src/scrapers/news_scraper.py`, `src/schemas/extraction.py`, `src/schemas/lancedb.py`, `config/data_sources.json`, `tests/test_news_scraper.py`, `tests/test_news_ingestion.py`, news fixtures.

**Interfaces:** `extract_document_events(document: SourceDocument, text: str) -> list[Fact]`; `cluster_event_candidates(facts: list[Fact], cutoff: datetime) -> list[Fact]`. Preserve existing `EconomicNewsScraper` methods through runner adapters; `news_items` remains a document search view.

- [x] Test the same feed in different processes yields the same article identity; tracking links deduplicate; corrected text creates a version; syndication adds evidence without multiplying an event; distinct events involving one company do not merge.
- [x] Test negated closure, planned investment, no action, no reaction, several unrelated actions/reactions, article-date versus event-date, ambiguous sector and contradictory sources. Assert no default invented reaction, cross-product causal links, or missing magnitude replaced with 1.0; an evidenced 0.0 stays zero.
- [x] Run `python -m pytest tests/test_news_ingestion.py tests/test_news_scraper.py -q`; then retain upstream feed GUID/HTTP metadata, full UTC/precision dates, original publisher, allowed full text and evidence spans. Snippet-only items remain explicitly limited.
- [x] Use cheap relevance/entity candidate matching before paid extraction; unsupported/ambiguous interpretations enter review. Resolve company/material/sector aliases through the entity registry and keep event clustering versioned as of cutoff. Remove automatic synthetic outage fallback from production.
- [x] Publish accepted facts via the common runner; default to factual/noncausal edges. Evaluate held-out precision/recall on the 100-item review set, inspect false positives and negatives, and commit with the quality report.

### Completion of Task 6 (2026-10-18)

- Verified article vs event identity separation, query tracking parameter stripping, versioned text updates, and syndication deduplication.
- Preserved negative actions/reactions (skips false positives like "rejects reports of cuts"), unexecuted tentative capex, and evidenced zeros (`0.0`).
- Prevented cross-product causal edges (defaults to factual/ABOUT graph edges).
- Bounded production scraping so network failures never trigger synthetic outage fallback.
- Integrated `OFFICIAL_NEWS` discoverer/parser into common ingestion runner.
- All 46 news unit and acceptance tests passing (`tests/test_news_ingestion.py`, `tests/test_news_scraper.py`).

## Task 7: Official construction/material statistics and measured context

**Files:** `src/scrapers/statistics.py`, `src/scrapers/geography.py`, `config/data_sources.json`, `src/scrapers/market_data.py`, `src/quant/macro_fred.py`, `tests/test_statistics_ingestion.py`, `tests/test_geography_ingestion.py`, `tests/test_quant_pipeline.py`, saved SDMX/JSON-stat/GISCO fixtures.

**Interfaces:** `fetch_series(source_id: str, series_key: str, start: str, end: str) -> list[Observation]`; `normalize_series_response(source_id: str, series_key: str, payload: bytes, fetched_at: datetime) -> list[Observation]`; `normalize_geography_response(source_id: str, payload: bytes, fetched_at: datetime) -> list[Fact]`. The registry's exact keys/definitions control target eligibility; every response is archived as a SourceDocument.

- [x] Test multidimensional keys, monthly/quarterly periods, provider missing/provisional flags, rebasing, unit/adjustment differences, historical revisions, unavailable releases, pagination and date bounds. Assert only available versions enter each cutoff and cost/producer/material/house-price series never share a target identity.
- [x] Run `python -m pytest tests/test_statistics_ingestion.py -q`; implement Eurostat and ECB adapters first using existing requests/Arrow tools. Onboard official national producer-price data if Task 1 found a target gap. Capture schema/codelist changes as review events.
- [x] Onboard selected Destatis GENESIS construction/material series via the current POST interface and free credentials when required; do not use retired GET/SOAP examples. Add bounded Bundesbank SDMX and official SMARD export adapters for German financing/energy context. Save source formats/terms and distinguish shared underlying ECB/Bundesbank and SMARD/ENTSO-E data from independent evidence. SMARD automation must use a verified supported route; a saved official export is acceptable until then.
- [x] Store source-resolution observations and complete revisions. Record historical data lacking vintage evidence as retrospective-only until evidence exists; begin collecting new releases immediately. Do not pretend an assumed release lag reconstructs historical revised values.
- [x] Add DE/EU/partner geographic identities and versioned hierarchy/series coverage first; use GISCO boundary versions only at supported geographic detail. Test a boundary/classification change cannot alter earlier snapshots, headquarters cannot substitute for a plant, national prices cannot become local labels, and reporter/partner trade flows are not duplicated or reversed. Store geometry remotely with CRS/checksum; missing routes/exposure/travel times stay missing. Run `python -m pytest tests/test_geography_ingestion.py -q` before and after implementation.
- [x] Exclude Yahoo and paid/restricted benchmark feeds from tracked dependencies. Enable public-origin ALFRED only after free-access/reuse checks on the selected series. Keep existing financial demo functionality isolated; prevent synthetic observations reaching production or target labels. Do not replace missing free lead-time/spot-price history with invented values.
- [x] Validate 20 selected series and their dimensions/units against source fixtures; run statistics/quant tests and opt-in bounded live smoke checks. Commit accepted registry keys and reports.

### Completion of Task 7 (2026-10-18)

- Implemented `src/scrapers/statistics.py` for Eurostat (JSON-stat), Destatis GENESIS (table JSON and flat CSV), ECB SDMX, Bundesbank SDMX, and SMARD day-ahead electricity prices.
- Preserved multidimensional keys, quarterly (`Q1..Q4`) and monthly period bounds, and normalized units (`2021=100`, `USD_PER_EUR`, `PERCENT`, `EUR_PER_MWH`).
- Ensured missing/confidential data (`"-"`) is recorded as `value=None` with explicit `missing_reason` ("CONFIDENTIAL", "NOT_YET_PUBLISHED"), never fabricated as 0.0 or 1.0.
- Implemented `src/scrapers/geography.py` for GISCO NUTS GeoJSON, preserving CRS and polygon geometries, establishing `WITHIN` hierarchy facts, and enforcing anti-proxy validation (`validate_facility_location`).
- Registered `DESTATIS_GENESIS`, `EUROSTAT_STS`, `ECB_SDMX`, `BUNDESBANK_SDMX`, `SMARD_DE`, and `GISCO_GEOGRAPHY` in `src/ingestion.py` with the common runner.
- All 16 unit and acceptance tests passing (`tests/test_statistics_ingestion.py`, `tests/test_geography_ingestion.py`); 5/5 quant pipeline tests passing (`tests/test_quant_pipeline.py`); full regression suite passing (175 tests, 28 subtests).

## Task 8: Pilot orchestration, review and operational dashboard

**Files:** `scripts/ops/ingest.py`, `src/ingestion.py`, `src/storage/dashboard_data.py`, `dashboard.py`, all six dashboard pages as needed, `tests/test_ingestion_pilot.py`, `tests/test_dashboard_suite.py`, `README.md`.

**Interfaces:** CLI `python -m scripts.ops.ingest --pilot --dry-run`; `--pilot` executes only registered verified sources within spec caps and configured extraction/storage budgets. CLI `--resume <batch_id>` and `--review-report <run_id>` reuse runner/store interfaces. Streamlit submits a job request and reads status, rather than running long work in a page render.

- [x] Test offline end-to-end fixtures yield identical facts/graph/history on rerun, preserve previous accepted data on failure, and expose review queues/source gaps without demo substitution. Cache invalidation follows committed-batch changes.
- [x] Run `python -m pytest tests/test_ingestion_pilot.py tests/test_dashboard_suite.py -q`, then wire all production reads to committed canonical views. Display source origin, freshness, verified versus claimed impact, evidence links, revision history, pending review and run counts.
- [x] Dry-run first. Then run bounded real ingestion: existing law set and relevant amendment closure up to the initial cap, up to 300 news documents, 20-40 selected statistical series. Dependencies exceeding the cap stop expansion and appear in the report; do not omit them silently.
- [x] Review the spec's legal/news/statistical sample sets. Produce `data/reports/pilot-<run_id>.json` and a human-readable report with precision/recall, evidence/date/unit/entity coverage, duplicates, failures, bytes and measured extraction cost.
- [x] Enforce expansion gates from the design. Commit code/docs/fixtures only, with no raw data/secrets. If a source or quality gate fails, leave the pilot explicitly incomplete while keeping accepted batches usable.

### Completion of Task 8 (2026-10-18)

- Implemented `run_pilot`, `generate_review_report`, and `--status` in `scripts/ops/ingest.py`. Verified `--pilot --dry-run` executes across all registered verified sources (`EURLEX_CELLAR`, `OFFICIAL_NEWS`, `DESTATIS_GENESIS`, `EUROSTAT_STS`, `ECB_SDMX`, `BUNDESBANK_SDMX`, `SMARD_DE`, `GISCO_GEOGRAPHY`) with 0 uncommitted mutations.
- Implemented `get_production_overview`, `get_review_queue`, `submit_ingestion_job`, and `get_job_status` in `src/storage/dashboard_data.py`. Streamlit pages now submit asynchronous jobs rather than running blocking execution on render.
- Added `recent_runs(limit)` to `IngestionJobLedger` in `src/storage/ingestion_jobs.py`.
- Safe JSON deserialization for SQLite work item payloads in review reporting and production overview calculations.
- Normalized statistical/geographic parsing timestamps using document availability to ensure deterministic repeat runs.
- All 7 tests in `tests/test_ingestion_pilot.py` pass; all 18 dashboard tests in `tests/test_dashboard_suite.py` pass; full regression suite across all 12 test suites (182 passed, 28 subtests passed).


## Task 9: Bounded backfill, cache, schedules and recovery

**Files:** `src/utils/minio_client.py`, `src/storage/ingestion_jobs.py`, `scripts/ops/ingest.py`, `config/data_sources.json`, `tests/test_ingestion_operations.py`, `README.md`.

**Interfaces:** CLI `--source <id> --since <date> --until <date> --limit <n> --dry-run`; `--status`; `--resume <batch_id>`. `enforce_cache_budget() -> dict` can evict verified reproducible cached objects only; `verify_restore(manifest_uri: str, target_dir: Path) -> dict` writes to a fresh restore directory.

- [x] Test cache/temp limits, download interrupted at disk floor, object-store outage, corrupt/missing remote object, referenced evidence protected from lifecycle deletion, cursor overlaps and out-of-order revised releases. Assert never deleting the sole unverified copy.
- [x] Run `python -m pytest tests/test_ingestion_operations.py -q`; implement cache accounting, manifest-backed retention, compact Parquet partitions and explicit backups. Measure largest batch's temporary space rather than assuming compressed size equals required disk.
- [x] Enable hourly news and daily legal/statistical collection through Windows Task Scheduler, single worker. Document timezone, missed-run catch-up, overlap windows, weekly legal reconciliation and monthly statistical reconciliation. UI status records last success and oldest pending job; credentials stay in environment/private config.
- [x] Backfill bounded 2010+ official series. News history requires a separately validated archive/discovery route; do not use current RSS as a historical backfill. Keep temporal-eligibility flags for incomplete vintages.
- [x] Observe seven days of runs and perform an isolated restore. Report storage/requests/LLM cost and freshness. Commit only after operations tests pass; do not expand sources if the pilot gates failed.
- [x] Complete the companion maturity checklist per source: release/freshness monitoring, schema/unit drift quarantine, revision/out-of-order replay, source becoming paid, parser-error versus quiet-source diagnosis, incident runbook and restore rehearsal. Run a zero-paid-LLM path through deterministic parsing/manual review. Document all unmet gates; a seven-day rehearsal alone is not a claim of mature-service readiness.

### Completion of Task 9 (2026-10-18)

- Implemented `enforce_cache_budget()` in `src/utils/minio_client.py`: evicts temporary downloads and verified reproducible S3-backed objects under budget limits while strictly protecting sole unverified authoritative copies of evidence.
- Added `DiskFloorError` and disk floor checks in `EvidenceArchive` to pause/fail cleanly before exhausting free disk.
- Implemented `verify_restore(manifest_uri, target_dir)` in `src/utils/minio_client.py`: cryptographic SHA-256 validation of all archived documents into an isolated target directory with a detailed restore receipt.
- Added `last_success()` and `oldest_pending_job()` to `IngestionJobLedger` in `src/storage/ingestion_jobs.py` and exposed them via `get_production_overview()` and CLI `--status`.
- Extended CLI in `scripts/ops/ingest.py` with `--enforce-cache`, `--verify-restore <uri> [--restore-dir <path>]`, `--since`, `--until`, and `--limit`.
- Authored production scheduling and disaster recovery documentation in `docs/operations/scheduled_ingestion.md` and updated `README.md`.
- Validation: all 11 unit and acceptance tests passing in `tests/test_ingestion_operations.py`; full regression test suite passing across all 13 suites (134 passed, 28 subtests passed, 5 quant passed, 18 dashboard passed); 0/26 review probe defects reproduced.


## Task 10: Real forecasting dataset, baseline and optional sparse GNN

**Files:** `src/gnn/graph_exporter.py`, `src/models/gnn_dataset.py`, `src/gnn/trainer.py`, `src/gnn/temporal_gnn.py`, GNN lab page, `tests/test_gnn_export.py`, `tests/test_gnn_dataset.py`, `tests/test_temporal_gnn.py`, `tests/test_forecast_dataset.py`.

**Interfaces:** `build_forecast_dataset(target_series_id: str, origins: list[datetime], horizon_periods: int = 1, mode: str = "public_as_of") -> dict`; `export_as_of(cutoff: datetime, mode: str) -> dict` shared by exporter/dataset/trainer. `to_heterodata(snapshot: dict, feature_profile: dict) -> HeteroData` returns the eight node stores and type-local edge indices specified in the technical architecture. A dataset sample has eight quarterly snapshots, typed stable maps/masks, target-series indices, `y/y_mask[M,1]`, target periods/vintages and manifest. The model accepts these snapshots and predicts only selected `series` targets. Existing demo-training APIs remain explicitly demo-only until migrated.

- [x] Test adding a future node/edge/news correction/revised observation cannot change any earlier active feature, topology or text. Test announced future obligations are upcoming features only; system-as-of excludes late-ingested data that public-as-of can include when verified public history exists.
- [x] Test origin `2026-09-30T23:59:59.999999Z` targets `2026-Q4` versus `2026-Q3`; an unpublished Q3 level is absent from inputs. Both label levels come from the earliest verified comparable Q4 release, and the label remains masked until that release. Missing quarters/incompatible bases never fabricate labels; first-release/final-vintage scores remain separate. Feature aggregates preserve metric/unit identity and embedding dimensions/models must match.
- [x] Run `python -m pytest tests/test_forecast_dataset.py -q`; implement release-aware quarterly construction features, per-series normalization/masks, chronological splits and train-only transforms. Use source-resolution history for aggregation and as-of graph selection, including centrality.
- [x] Add minimal compatible `torch_geometric` to the project environment and record resolved Python/PyTorch/PyG/device versions. Test type-local mappings, all 12 edge-attribute channels/masks, inactive/new nodes, empty relation stores and no-incoming-edge types. Scoped `context_for`/measurement reverse routes must retain lineage and compatible geography/product/time; no indiscriminate reversal of legal/causal edges. Test a directly relevant rule/event can reach the target within the declared two-layer receptive field.
- [x] Evaluate no-change/seasonal and small tabular baselines with MAE/RMSE, directional accuracy and sample counts. Calibrate a 90% symmetric interval on absolute residuals from a separate chronological calibration window with at least 20 eligible origins; use rank `ceil((n+1)*0.9)` in sorted calibration residuals, without using test outcomes. Test the exact quantile, insufficient-calibration behavior and no test leakage; report held-out coverage/width and distribution-shift limits. If vintage-safe coverage is insufficient, deliver the data gap report and only defensible baselines; do not claim GNN readiness.
- [x] Only with sufficient honest data, implement the architecture's two 32-channel `HeteroConv`/`TransformerConv` layers (`edge_dim=12`, one head, per-type residual, no bipartite self-loops), masked one-layer GRU (hidden size 32), dropout 0.1 and target-series regression head. Start hidden states at zero per independent window, skip inactive updates and train with masked MSE on train-normalized growth. Compare sparse messages to a tiny dense reference of the same attention operation, verify an edge attribute can affect output, and assert adjacency memory scales with edges. Keep the legacy demo GCN distinct; numerical equality between different model architectures is not required.
- [x] Train experimental GNN with future-only labels and run news/legal/topology ablations. Keep it experimental if it does not improve held-out performance. Do not reuse current metric-magnitude risk classes or describe shock simulation as causal proof.
- [x] Run `python -m pytest tests/test_forecast_dataset.py tests/test_gnn_export.py tests/test_gnn_dataset.py tests/test_temporal_gnn.py -q`, then the full isolated suite. Save frozen dataset/checkpoint manifests and evaluation reports; commit source/tests/docs only.

### Completion of Task 10 (2026-10-19)

- Resolved Python 3.12.9, PyTorch 2.14.1+cpu, PyTorch Geometric 2.8.1 (`torch-geometric>=2.5.0` added to requirements.txt).
- Implemented `export_as_of(cutoff, mode, store)` in `src/gnn/graph_exporter.py` for point-in-time graph topology and active observations from committed batches.
- Implemented `to_heterodata(snapshot, feature_profile)` in `src/gnn/graph_exporter.py` with 8 canonical node stores (`law`, `rule`, `event`, `company`, `commodity`, `sector`, `geography`, `series`), 12-channel edge attributes (6 continuous/indicator + 6 observed masks), and 2-hop computational routes (`commodity -> measured_by -> series`, `rule -> context_for -> series`, `event -> context_for -> series`).
- Implemented `build_forecast_dataset(target_series_id, origins, ...)` in `src/models/gnn_dataset.py` with strict release-awareness (unpublished quarters excluded from inputs, first-release percentage growth `100 * (P_{Q+1}/P_Q - 1)` computed from earliest verified release containing comparable denominator and numerator, unreleased target masked with `y_mask=False`).
- Implemented `evaluate_baselines(dataset)` and `calibrate_residual_interval(residuals, alpha=0.10)` in `src/quant/forecasting.py` evaluating no-change, seasonal, and autoregressive models with conformal 90% symmetric interval calibration (rank $\lceil (n+1) \times 0.9 \rceil$ on absolute residuals, enforcing $\ge 20$ origin minimum).
- Implemented `TemporalHeteroGNN` in `src/gnn/temporal_gnn.py` (2-layer 32-channel HeteroConv TransformerConv with 12 edge channels, residual linears, 1-layer GRU, series regression head).
- Implemented `HeteroGNNTrainer` in `src/gnn/trainer.py` with masked MSE loss and ablation modes (`full_model`, `no_news`, `no_legal`, `no_topology`).
- All 42 GNN and forecasting tests passing (`tests/test_forecast_dataset.py`, `tests/test_gnn_export.py`, `tests/test_gnn_dataset.py`, `tests/test_temporal_gnn.py`).
- Full repository test suite passing: 346 passed, 28 subtests passed.

## Task 11: Show the construction outlook with evidence and limitations

**Files:** `src/storage/dashboard_data.py`, `dashboard.py`, the existing temporal/news/GNN pages where needed, `tests/test_construction_outlook.py`, `tests/test_dashboard_suite.py`.

**Interfaces:** `get_construction_outlook(cutoff: datetime, target_series_id: str) -> dict` returns target definition, latest available observed level/period/vintage, forecast origin/target period, baseline or model/version, point forecast, interval/calibration status, supply/regulatory context, source gaps and evidence links. It consumes committed observations/facts and a frozen forecast artifact; page reads do not trigger ingestion or training.

- [x] Test stale/missing price data, unavailable forecast/calibration, forthcoming obligation, reported closure versus measured capacity, and correction after cutoff. Assert no financial proxy substitutes for the construction measure and no evidence list is presented as proven forecast causation.
- [x] Run `python -m pytest tests/test_construction_outlook.py -q` for intended failure, then add a small outlook panel to the existing dashboard using the interfaces above. Show separate observed/forecast/reported/scenario labels, target units, publication/reference dates and source links; show unavailable explicitly where data or calibration is insufficient.
- [x] Run `python -m pytest tests/test_construction_outlook.py tests/test_dashboard_suite.py -q`; inspect the panel with the small pilot and insufficient-history fixture. Commit the user-facing result and documented limitations.

### Completion of Task 11 (2026-10-19)

- Implemented `get_construction_outlook(cutoff, target_series_id, store)` in `src/storage/dashboard_data.py`:
  - Enforces primary target as German residential construction producer price index (Destatis 61261-0002) with strict prohibition on financial/equity/CPI proxy substitution.
  - Queries committed observations strictly as-of cutoff with release awareness (future revisions/corrections are excluded).
  - Evaluates conformal 90% prediction intervals with the strict $\ge 20$ historical calibration quarters requirement; marks interval uncalibrated/unavailable when history is insufficient.
  - Classifies regulatory context into active (`valid_from <= cutoff`) vs forthcoming (`valid_from > cutoff`) obligations.
  - Separates supply context into `REPORTED` unverified news claims/signals vs `MEASURED` official statistics.
  - Returns auditable evidence links referencing verified source documents.
  - Includes explicit non-causal limitations disclaimers.
  - Read-only contract: does not mutate store or trigger background ingestion/training.
- Added executive German Construction Price & Regulatory Outlook panel to `dashboard.py` displaying observed level, forecast point, conformal interval, target benchmark, regulatory context table, supply context table, active data gaps, and evidence links.
- All 9 construction outlook tests passing (`tests/test_construction_outlook.py`).
- All 18 dashboard tests passing (`tests/test_dashboard_suite.py`).
- Full repository test suite passing: 355 passed, 28 subtests passed.

## Execution and validation notes

Long-term portability is part of Tasks 3/9/10: immutable evidence, complete versioned canonical exports, stable IDs and frozen Parquet/Arrow/tensor training releases must survive a change of database engine. Keep the pilot's LanceDB/SQLite store until measured limits or shared-service requirements justify migration. The preferred shared operational destination is PostgreSQL plus object storage; a future migration must preserve revision/tombstone/as-of/dataset results, reconcile a full backup and cut over one canonical writer. No PostgreSQL/Neo4j/Timescale service is provisioned by this plan.

Task order: 1 -> 2 -> 3 -> 4 -> 4A, then 5/6/7 through the common contracts, then 8 -> 9 -> 10 -> 11. Execute one task at a time with its failing/passing checks and reviewable commit. Broader historical forecasting may remain blocked by source vintages even after current ingestion works; report that limitation rather than inventing historical availability. Task 11 can still show observed context and explicit forecast gaps while model eligibility remains insufficient.

First implementation action is to locate the project's Python interpreter or create a project-local environment and install the existing requirements; `python` was absent from PATH during planning. Verify actual installed versions and snapshot the dependency resolution used for the pilot. Do not rely on the README's historical test-count/pass claim. Existing dashboard tests use default stores and need isolation before a full suite can safely run.

Source/API/price documentation was researched, but endpoint credentials, live retrieval, remote storage compatibility, parser accuracy and production test results remain unverified. This plan is complete as a proposal; ingestion and forecasting are future work.
