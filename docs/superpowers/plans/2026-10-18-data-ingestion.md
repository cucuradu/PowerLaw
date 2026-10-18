# PowerLaw Data Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build trustworthy ingestion for construction prices, supply conditions and regulation, then produce reproducible forecasting datasets.

**Architecture:** Preserve immutable evidence in configured object storage. Keep accepted versioned facts and observations in one typed LanceDB dataset; derive the graph, dashboard views and time-safe training inputs from committed batches. Reuse the existing project and keep the first run small.

**Tech Stack:** Python, Pydantic, requests, LanceDB OSS, PyArrow/Parquet, MinIO S3 client, standard-library SQLite, existing Streamlit/PyTorch, PyTorch Geometric for the optional typed GNN, and maintained `google-genai` replacing the legacy Google SDK. No new orchestration service or graph database.

**Spec:** [Data ingestion design](../specs/2026-10-18-data-ingestion-design.md), [technical architecture/schemas/user setup](../specs/2026-10-18-technical-architecture.md) and [free-source register/maturity gates](../specs/2026-10-18-free-sources-and-ingestion-readiness.md). Required reading together. Proposed, not executed. Germany first, geographic relationships, PyTorch Geometric and free data sources are user-confirmed; exact target and infrastructure/optional processing budgets still need definition.

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

- [ ] Write failing tests for all entry points resolving the same configured DB; production empty stores staying empty; failed market/news fetch producing unavailable status and zero synthetic production rows. Demo tests must opt in.
- [ ] Run `python -m pytest tests/test_ingestion_config.py tests/test_news_scraper.py tests/test_quant_pipeline.py -q`; confirm the new boundary tests fail for the intended reason before editing behavior.
- [ ] Implement settings and explicit origin selection. Replace automatic seeding in read helpers with deliberate demo initialization; update all seed-dependent dashboard test fixtures to temporary demo storage.
- [ ] Audit both existing DB directories, Parquet exports and MinIO if configured: row/schema/version counts, IDs, origin evidence, duplicate rates, raw references, coverage and bytes. Unproven legacy origins are quarantined for classification, not labelled live automatically.
- [ ] Implement the companion free-source register in `config/data_sources.json`, including selected endpoints/series, `access_fee=0`, subscription=false, rights, auth, release cadence, configurable allowable delay and source owner. Test a paid/trial/restricted candidate cannot be enabled and a source changing to paid/unavailable produces an alert/status without purchase or substitution. Mark untested candidates disabled.
- [ ] Discover the direct Destatis DE construction-price target (family 61261, candidate quarterly table 61261-0002), cross-check comparable Eurostat metadata, and lock exact building type, producer/output-price meaning, tax treatment, base, coverage, reference month/quarter convention and release metadata. Discover named material PPI/context series separately. Store full keys and fixtures; if the intended target is absent, report the gap rather than substituting a different measure.
- [ ] Run updated configuration/dashboard tests, inspect the audit report and register a proposed source/target manifest. Commit only this independently reviewed preparation change.

## Task 2: Canonical evidence, revision and quality contracts

**Files:** `src/schemas/ingestion.py`, `src/schemas/graph_temporal.py`, `src/models/dual_layer_model.py`, `src/schemas/extraction.py`, `src/schemas/rule_schema.py`, exports, `tests/test_ingestion_schema.py`, `tests/test_schema.py`, `tests/fixtures/ingestion/`.

**Interfaces:** `SourceDocument`, `Entity`, `EntityAlias`, `Fact`, `EvidenceLink`, `SeriesDefinition`, `Observation`, `QualityDecision`, `BatchManifest`, `DatasetManifest`, `PredictionRecord` implement the technical architecture's canonical tables/fields. `stable_id(namespace: str, *parts: str) -> str` uses SHA-256. `validate_candidate(candidate: Fact | Observation, source: SourceDocument) -> QualityDecision` returns `accepted`, `needs_review`, `rejected`, or `out_of_scope` plus reason codes.

- [ ] Write tests asserting identity is identical across two Python processes; exact duplicate releases share IDs; corrections change version IDs; null quantity remains null; missing dates do not become today/1900; fraction/percent/percentage-point are distinct; original units survive normalization.
- [ ] Test public availability versus reference time using a March observation first released in May and revised in June: unavailable in April, first version in May, revised version in June. A legacy value without vintage evidence is not eligible before first fetch.
- [ ] Run `python -m pytest tests/test_ingestion_schema.py -q` and confirm failure, then implement schemas and pure validation/identity functions. Add COMPANY and GEOGRAPHY plus the technical architecture's factual, geographic and measurement relations to both vocabularies and conversion tests; validate entity versus edge namespaces and supported endpoints.
- [ ] Permit non-numeric legal obligations; preserve conditions/exception structures and evidence anchors. Update prompt/model mismatches and existing fixtures without weakening boundaries to arbitrary accepted strings.
- [ ] Run `python -m pytest tests/test_ingestion_schema.py tests/test_schema.py tests/test_dual_layer_model.py tests/test_edge_cases.py -q`; review serialization/round trips and commit.

## Task 3: One durable store, version-aware reads and reversible migration

**Files:** `src/storage/dual_layer_store.py`, `src/storage/graph_store.py`, `scripts/ops/migrate_data.py`, `src/storage/__init__.py`, `tests/test_ingestion_store.py`, `tests/test_dual_layer_store.py`, `tests/test_graph_store.py`.

**Interfaces:** `stage_batch(batch: BatchManifest, documents: list[SourceDocument], facts: list[Fact], observations: list[Observation]) -> None`; `commit_batch(batch_id: str) -> None`; `query_observations(cutoff: datetime, mode: str = "public_as_of", entity_ids: list[str] | None = None) -> pa.Table`; `project_graph(cutoff: datetime, mode: str = "public_as_of") -> dict`. Modes are exactly `public_as_of` and `system_as_of`. Projected result contains canonical nodes/edges and their typed provenance; old accessors preserve legacy return shapes via the adapter.

- [ ] Test repeat staging/commit yields identical committed IDs/counts, staging stays invisible, a mid-table-write crash resumes safely, and competing writers are rejected. Test two metrics on one node retain two as-of results.
- [ ] Test expired/amended/retracted assertions remain visible at old cutoffs, future obligations appear as upcoming but not active, and relation multiplicity survives graph projection. An accepted fact cannot refer to an uncommitted/missing document or endpoint.
- [ ] Test one commodity resolves to multiple separately typed series and exact evidence; same material in DE and another market remains distinct. Test an edge-subject trade series resolves correctly. Select latest revisions including tombstones before suppressing retracted records, so old values cannot be resurrected. Freeze committed generation for each multi-table read and derived view.
- [ ] Run `python -m pytest tests/test_ingestion_store.py -q` for the expected failures. Implement append-only version tables and committed-batch reads on the typed store; avoid loading complete observations during initialization.
- [ ] Make the legacy graph store an adapter; remove `eval()` and redundant overwrite persistence. Use a multigraph or explicit edge collection where several typed relations share endpoints; consumers cannot silently collapse them.
- [ ] Implement migration into a new versioned directory with a dry-run mapping/report. Preserve old IDs as aliases, compare counts/keys/history, isolate unverified data and restore a copied backup into temporary storage. Activate by configuration only after reconciliation; rollback points at untouched old data.
- [ ] Run graph/store/schema regressions and `python -m scripts.ops.migrate_data --dry-run --output data/reports/migration.json`; inspect differences and commit. Do not execute activation as part of an audit-only request.

## Task 4: Recoverable runner and verified object storage

**Files:** `src/storage/ingestion_jobs.py`, `src/ingestion.py`, `src/utils/minio_client.py`, `scripts/ops/ingest.py`, `tests/test_ingestion_runner.py`, `tests/test_object_storage.py`.

**Interfaces:** `run_ingestion(source_id: str, *, limit: int, since: str | None = None, until: str | None = None, dry_run: bool = False) -> dict`; `archive_document(document: SourceDocument, content: bytes) -> SourceDocument`; `resume_batch(batch_id: str) -> dict`. The returned report includes discovered/fetched/staged/accepted/review/rejected/duplicate/committed counts, source errors, bytes and cost. Dry run discovers/plans without publication or paid extraction.

- [ ] Write failing tests for transient 429/503 retry bounds and Retry-After; timeout; HTML error disguised as PDF; checksum mismatch; oversize download; hostile/private redirect; failure before/after staging; replay after commit; two writers acquiring the same source job. Assert unset/zero budget or unknown prices dispatches zero paid requests, a reservation exceeding remaining budget is rejected, and a resumed run/retry cannot reset accounting.
- [ ] Assert cursor advancement never loses unrecorded items; parsing/review failure is recorded separately from fetch status; blocked archive never creates accepted graph data. Log tests redact credentials and request tokens.
- [ ] Run `python -m pytest tests/test_ingestion_runner.py tests/test_object_storage.py -q`, then implement SQLite ledger, leases, bounded HTTP, raw manifests and batch commit orchestration. Enforce single writer, archive-first ordering and durable reserve/reconcile cost accounting against `extraction_budget_usd`; no configured budget means paid extraction disabled.
- [ ] Extend existing S3 client for configured existing private bucket/prefix, TLS, checksums and readback. Keep local-only archive mode explicit for tests/development; storage failure never silently changes URI semantics.
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

- [ ] Add fixtures for structured XML/HTML, normal/scanned/table PDFs, amended articles, conditional exemptions, future phases, missing dates and non-numeric obligations. Assert every emitted rule has exact evidence and no unsupported number/date or scope widening.
- [ ] Test failed extraction/embedding/review leaves the last committed law visible; new law versions retain old history; statutes with decision identifiers are not excluded just because a discovery regex only matches regulations/directives.
- [ ] Run `python -m pytest tests/test_law_ingestion.py -q` for intended failures. Consolidate the duplicate `process_pdf` definition, prefer structured text, bound/chunk long documents by provisions with shared context, and record complete coverage.
- [ ] Review candidates against cited evidence and relevant definitions/annexes. Replace invented dates and draft-only QA; route uncertain OCR/tables to review. Keep national transposition/implementation coverage explicitly unknown when absent.
- [ ] Stage validated rule versions before publication; publish factual law/rule/scope/amendment links with intervals and provenance. Review all initial pilot rules, run legal/schema tests and commit.

## Task 6: News discovery, identity, extraction and event resolution

**Files:** `src/scrapers/news_scraper.py`, `src/schemas/extraction.py`, `src/schemas/lancedb.py`, `config/data_sources.json`, `tests/test_news_scraper.py`, `tests/test_news_ingestion.py`, news fixtures.

**Interfaces:** `extract_document_events(document: SourceDocument, text: str) -> list[Fact]`; `cluster_event_candidates(facts: list[Fact], cutoff: datetime) -> list[Fact]`. Preserve existing `EconomicNewsScraper` methods through runner adapters; `news_items` remains a document search view.

- [ ] Test the same feed in different processes yields the same article identity; tracking links deduplicate; corrected text creates a version; syndication adds evidence without multiplying an event; distinct events involving one company do not merge.
- [ ] Test negated closure, planned investment, no action, no reaction, several unrelated actions/reactions, article-date versus event-date, ambiguous sector and contradictory sources. Assert no default invented reaction, cross-product causal links, or missing magnitude replaced with 1.0; an evidenced 0.0 stays zero.
- [ ] Run `python -m pytest tests/test_news_ingestion.py tests/test_news_scraper.py -q`; then retain upstream feed GUID/HTTP metadata, full UTC/precision dates, original publisher, allowed full text and evidence spans. Snippet-only items remain explicitly limited.
- [ ] Use cheap relevance/entity candidate matching before paid extraction; unsupported/ambiguous interpretations enter review. Resolve company/material/sector aliases through the entity registry and keep event clustering versioned as of cutoff. Remove automatic synthetic outage fallback from production.
- [ ] Publish accepted facts via the common runner; default to factual/noncausal edges. Evaluate held-out precision/recall on the 100-item review set, inspect false positives and negatives, and commit with the quality report.

## Task 7: Official construction/material statistics and measured context

**Files:** `src/scrapers/statistics.py`, `src/scrapers/geography.py`, `config/data_sources.json`, `src/scrapers/market_data.py`, `src/quant/macro_fred.py`, `tests/test_statistics_ingestion.py`, `tests/test_geography_ingestion.py`, `tests/test_quant_pipeline.py`, saved SDMX/JSON-stat/GISCO fixtures.

**Interfaces:** `fetch_series(source_id: str, series_key: str, start: str, end: str) -> list[Observation]`; `normalize_series_response(source_id: str, series_key: str, payload: bytes, fetched_at: datetime) -> list[Observation]`; `normalize_geography_response(source_id: str, payload: bytes, fetched_at: datetime) -> list[Fact]`. The registry's exact keys/definitions control target eligibility; every response is archived as a SourceDocument.

- [ ] Test multidimensional keys, monthly/quarterly periods, provider missing/provisional flags, rebasing, unit/adjustment differences, historical revisions, unavailable releases, pagination and date bounds. Assert only available versions enter each cutoff and cost/producer/material/house-price series never share a target identity.
- [ ] Run `python -m pytest tests/test_statistics_ingestion.py -q`; implement Eurostat and ECB adapters first using existing requests/Arrow tools. Onboard official national producer-price data if Task 1 found a target gap. Capture schema/codelist changes as review events.
- [ ] Onboard selected Destatis GENESIS construction/material series via the current POST interface and free credentials when required; do not use retired GET/SOAP examples. Add bounded Bundesbank SDMX and official SMARD export adapters for German financing/energy context. Save source formats/terms and distinguish shared underlying ECB/Bundesbank and SMARD/ENTSO-E data from independent evidence. SMARD automation must use a verified supported route; a saved official export is acceptable until then.
- [ ] Store source-resolution observations and complete revisions. Record historical data lacking vintage evidence as retrospective-only until evidence exists; begin collecting new releases immediately. Do not pretend an assumed release lag reconstructs historical revised values.
- [ ] Add DE/EU/partner geographic identities and versioned hierarchy/series coverage first; use GISCO boundary versions only at supported geographic detail. Test a boundary/classification change cannot alter earlier snapshots, headquarters cannot substitute for a plant, national prices cannot become local labels, and reporter/partner trade flows are not duplicated or reversed. Store geometry remotely with CRS/checksum; missing routes/exposure/travel times stay missing. Run `python -m pytest tests/test_geography_ingestion.py -q` before and after implementation.
- [ ] Exclude Yahoo and paid/restricted benchmark feeds from tracked dependencies. Enable public-origin ALFRED only after free-access/reuse checks on the selected series. Keep existing financial demo functionality isolated; prevent synthetic observations reaching production or target labels. Do not replace missing free lead-time/spot-price history with invented values.
- [ ] Validate 20 selected series and their dimensions/units against source fixtures; run statistics/quant tests and opt-in bounded live smoke checks. Commit accepted registry keys and reports.

## Task 8: Pilot orchestration, review and operational dashboard

**Files:** `scripts/ops/ingest.py`, `src/ingestion.py`, `src/storage/dashboard_data.py`, `dashboard.py`, all six dashboard pages as needed, `tests/test_ingestion_pilot.py`, `tests/test_dashboard_suite.py`, `README.md`.

**Interfaces:** CLI `python -m scripts.ops.ingest --pilot --dry-run`; `--pilot` executes only registered verified sources within spec caps and configured extraction/storage budgets. CLI `--resume <batch_id>` and `--review-report <run_id>` reuse runner/store interfaces. Streamlit submits a job request and reads status, rather than running long work in a page render.

- [ ] Test offline end-to-end fixtures yield identical facts/graph/history on rerun, preserve previous accepted data on failure, and expose review queues/source gaps without demo substitution. Cache invalidation follows committed-batch changes.
- [ ] Run `python -m pytest tests/test_ingestion_pilot.py tests/test_dashboard_suite.py -q`, then wire all production reads to committed canonical views. Display source origin, freshness, verified versus claimed impact, evidence links, revision history, pending review and run counts.
- [ ] Dry-run first. Then run bounded real ingestion: existing law set and relevant amendment closure up to the initial cap, up to 300 news documents, 20-40 selected statistical series. Dependencies exceeding the cap stop expansion and appear in the report; do not omit them silently.
- [ ] Review the spec's legal/news/statistical sample sets. Produce `data/reports/pilot-<run_id>.json` and a human-readable report with precision/recall, evidence/date/unit/entity coverage, duplicates, failures, bytes and measured extraction cost.
- [ ] Enforce expansion gates from the design. Commit code/docs/fixtures only, with no raw data/secrets. If a source or quality gate fails, leave the pilot explicitly incomplete while keeping accepted batches usable.

## Task 9: Bounded backfill, cache, schedules and recovery

**Files:** `src/utils/minio_client.py`, `src/storage/ingestion_jobs.py`, `scripts/ops/ingest.py`, `config/data_sources.json`, `tests/test_ingestion_operations.py`, `README.md`.

**Interfaces:** CLI `--source <id> --since <date> --until <date> --limit <n> --dry-run`; `--status`; `--resume <batch_id>`. `enforce_cache_budget() -> dict` can evict verified reproducible cached objects only; `verify_restore(manifest_uri: str, target_dir: Path) -> dict` writes to a fresh restore directory.

- [ ] Test cache/temp limits, download interrupted at disk floor, object-store outage, corrupt/missing remote object, referenced evidence protected from lifecycle deletion, cursor overlaps and out-of-order revised releases. Assert never deleting the sole unverified copy.
- [ ] Run `python -m pytest tests/test_ingestion_operations.py -q`; implement cache accounting, manifest-backed retention, compact Parquet partitions and explicit backups. Measure largest batch's temporary space rather than assuming compressed size equals required disk.
- [ ] Enable hourly news and daily legal/statistical collection through Windows Task Scheduler, single worker. Document timezone, missed-run catch-up, overlap windows, weekly legal reconciliation and monthly statistical reconciliation. UI status records last success and oldest pending job; credentials stay in environment/private config.
- [ ] Backfill bounded 2010+ official series. News history requires a separately validated archive/discovery route; do not use current RSS as a historical backfill. Keep temporal-eligibility flags for incomplete vintages.
- [ ] Observe seven days of runs and perform an isolated restore. Report storage/requests/LLM cost and freshness. Commit only after operations tests pass; do not expand sources if the pilot gates failed.
- [ ] Complete the companion maturity checklist per source: release/freshness monitoring, schema/unit drift quarantine, revision/out-of-order replay, source becoming paid, parser-error versus quiet-source diagnosis, incident runbook and restore rehearsal. Run a zero-paid-LLM path through deterministic parsing/manual review. Document all unmet gates; a seven-day rehearsal alone is not a claim of mature-service readiness.

## Task 10: Real forecasting dataset, baseline and optional sparse GNN

**Files:** `src/gnn/graph_exporter.py`, `src/models/gnn_dataset.py`, `src/gnn/trainer.py`, `src/gnn/temporal_gnn.py`, GNN lab page, `tests/test_gnn_export.py`, `tests/test_gnn_dataset.py`, `tests/test_temporal_gnn.py`, `tests/test_forecast_dataset.py`.

**Interfaces:** `build_forecast_dataset(target_series_id: str, origins: list[datetime], horizon_periods: int = 1, mode: str = "public_as_of") -> dict`; `export_as_of(cutoff: datetime, mode: str) -> dict` shared by exporter/dataset/trainer. `to_heterodata(snapshot: dict, feature_profile: dict) -> HeteroData` returns the eight node stores and type-local edge indices specified in the technical architecture. A dataset sample has eight quarterly snapshots, typed stable maps/masks, target-series indices, `y/y_mask[M,1]`, target periods/vintages and manifest. The model accepts these snapshots and predicts only selected `series` targets. Existing demo-training APIs remain explicitly demo-only until migrated.

- [ ] Test adding a future node/edge/news correction/revised observation cannot change any earlier active feature, topology or text. Test announced future obligations are upcoming features only; system-as-of excludes late-ingested data that public-as-of can include when verified public history exists.
- [ ] Test origin `2026-09-30T23:59:59.999999Z` targets `2026-Q4` versus `2026-Q3`; an unpublished Q3 level is absent from inputs. Both label levels come from the earliest verified comparable Q4 release, and the label remains masked until that release. Missing quarters/incompatible bases never fabricate labels; first-release/final-vintage scores remain separate. Feature aggregates preserve metric/unit identity and embedding dimensions/models must match.
- [ ] Run `python -m pytest tests/test_forecast_dataset.py -q`; implement release-aware quarterly construction features, per-series normalization/masks, chronological splits and train-only transforms. Use source-resolution history for aggregation and as-of graph selection, including centrality.
- [ ] Add minimal compatible `torch_geometric` to the project environment and record resolved Python/PyTorch/PyG/device versions. Test type-local mappings, all 12 edge-attribute channels/masks, inactive/new nodes, empty relation stores and no-incoming-edge types. Scoped `context_for`/measurement reverse routes must retain lineage and compatible geography/product/time; no indiscriminate reversal of legal/causal edges. Test a directly relevant rule/event can reach the target within the declared two-layer receptive field.
- [ ] Evaluate no-change/seasonal and small tabular baselines with MAE/RMSE, directional accuracy and sample counts. Calibrate a 90% symmetric interval on absolute residuals from a separate chronological calibration window with at least 20 eligible origins; use rank `ceil((n+1)*0.9)` in sorted calibration residuals, without using test outcomes. Test the exact quantile, insufficient-calibration behavior and no test leakage; report held-out coverage/width and distribution-shift limits. If vintage-safe coverage is insufficient, deliver the data gap report and only defensible baselines; do not claim GNN readiness.
- [ ] Only with sufficient honest data, implement the architecture's two 32-channel `HeteroConv`/`TransformerConv` layers (`edge_dim=12`, one head, per-type residual, no bipartite self-loops), masked one-layer GRU (hidden size 32), dropout 0.1 and target-series regression head. Start hidden states at zero per independent window, skip inactive updates and train with masked MSE on train-normalized growth. Compare sparse messages to a tiny dense reference of the same attention operation, verify an edge attribute can affect output, and assert adjacency memory scales with edges. Keep the legacy demo GCN distinct; numerical equality between different model architectures is not required.
- [ ] Train experimental GNN with future-only labels and run news/legal/topology ablations. Keep it experimental if it does not improve held-out performance. Do not reuse current metric-magnitude risk classes or describe shock simulation as causal proof.
- [ ] Run `python -m pytest tests/test_forecast_dataset.py tests/test_gnn_export.py tests/test_gnn_dataset.py tests/test_temporal_gnn.py -q`, then the full isolated suite. Save frozen dataset/checkpoint manifests and evaluation reports; commit source/tests/docs only.

## Task 11: Show the construction outlook with evidence and limitations

**Files:** `src/storage/dashboard_data.py`, `dashboard.py`, the existing temporal/news/GNN pages where needed, `tests/test_construction_outlook.py`, `tests/test_dashboard_suite.py`.

**Interfaces:** `get_construction_outlook(cutoff: datetime, target_series_id: str) -> dict` returns target definition, latest available observed level/period/vintage, forecast origin/target period, baseline or model/version, point forecast, interval/calibration status, supply/regulatory context, source gaps and evidence links. It consumes committed observations/facts and a frozen forecast artifact; page reads do not trigger ingestion or training.

- [ ] Test stale/missing price data, unavailable forecast/calibration, forthcoming obligation, reported closure versus measured capacity, and correction after cutoff. Assert no financial proxy substitutes for the construction measure and no evidence list is presented as proven forecast causation.
- [ ] Run `python -m pytest tests/test_construction_outlook.py -q` for intended failure, then add a small outlook panel to the existing dashboard using the interfaces above. Show separate observed/forecast/reported/scenario labels, target units, publication/reference dates and source links; show unavailable explicitly where data or calibration is insufficient.
- [ ] Run `python -m pytest tests/test_construction_outlook.py tests/test_dashboard_suite.py -q`; inspect the panel with the small pilot and insufficient-history fixture. Commit the user-facing result and documented limitations.

## Execution and validation notes

Long-term portability is part of Tasks 3/9/10: immutable evidence, complete versioned canonical exports, stable IDs and frozen Parquet/Arrow/tensor training releases must survive a change of database engine. Keep the pilot's LanceDB/SQLite store until measured limits or shared-service requirements justify migration. The preferred shared operational destination is PostgreSQL plus object storage; a future migration must preserve revision/tombstone/as-of/dataset results, reconcile a full backup and cut over one canonical writer. No PostgreSQL/Neo4j/Timescale service is provisioned by this plan.

Task order: 1 -> 2 -> 3 -> 4 -> 4A, then 5/6/7 through the common contracts, then 8 -> 9 -> 10 -> 11. Execute one task at a time with its failing/passing checks and reviewable commit. Broader historical forecasting may remain blocked by source vintages even after current ingestion works; report that limitation rather than inventing historical availability. Task 11 can still show observed context and explicit forecast gaps while model eligibility remains insufficient.

First implementation action is to locate the project's Python interpreter or create a project-local environment and install the existing requirements; `python` was absent from PATH during planning. Verify actual installed versions and snapshot the dependency resolution used for the pilot. Do not rely on the README's historical test-count/pass claim. Existing dashboard tests use default stores and need isolation before a full suite can safely run.

Source/API/price documentation was researched, but endpoint credentials, live retrieval, remote storage compatibility, parser accuracy and production test results remain unverified. This plan is complete as a proposal; ingestion and forecasting are future work.
