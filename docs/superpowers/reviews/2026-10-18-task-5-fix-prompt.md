# Task 5 review and repair prompt

Task 5 is **not complete**, despite all its plan checkboxes being checked. Repair the existing implementation and acceptance tests. Do not start later tasks, bulk ingestion, training, infrastructure or paid extraction.

Read `AGENTS.md`, `docs/agent-memory/current-work.md`, Task 5 in `docs/superpowers/plans/2026-10-18-data-ingestion.md`, and the ingestion design, technical architecture and free-source readiness specifications. Preserve reviewed Tasks 1–3 contracts. Task 4 was changing in another session during this review: inspect its current interfaces and coordinate shared-file changes; do not restore its older implementation. This review did not revalidate Task 4's latest repairs.

Preserve unrelated working-tree changes. Do not reset, stage, commit, migrate production data or print credentials. Use temporary databases and mocked model/storage/network calls.

## Review evidence

- Existing `tests/test_law_ingestion.py`: **8 passed**.
- Legal/schema suites with configured storage explicitly isolated: **36 passed, 28 subtests passed**.
- `data/reports/task5_review_probe.py`: **26/26 defect reproductions confirmed**, using temporary databases, fake HTTP and mocked model calls. It makes no live paid requests. This ignored local artifact is a starting point for tracked regressions asserting corrected behavior. Its `true` output means a defect reproduced, not a passing implementation.
- Reviewed source hashes are recorded locally in `data/reports/task5-review-source-hashes.json`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m data.reports.task5_review_probe
.\.venv\Scripts\python.exe -m pytest tests/test_law_ingestion.py tests/test_ingestion_schema.py tests/test_schema.py -q -p no:cacheprovider --tb=short
```

Before running tests, explicitly redirect `POWERLAW_MODE=test`, `POWERLAW_DATA_DIR`, `LANCEDB_PATH`, `POWERLAW_SQLITE_PATH`, `POWERLAW_CACHE_DIR` and `POWERLAW_TEMP_DIR` to isolated test storage. Injecting a temporary canonical store alone does not isolate the current pipeline constructor.

## Required repairs

### 1. Restore entry points and use one canonical pipeline

Owners: `src/main.py::LawIngestionPipeline`, existing ingestion scripts and the common source runner.

- `process_pdf()` calls `self.get_celex_id()`, but that method is absent; the probe raises `AttributeError` before reading a file. `extract_chunks()` and `run()` also call it. `run_bulk()` calls another absent method, `process_law_entry()`. Repair or explicitly retire supported callers. Counting one `process_pdf` definition is not a behavior test.
- `LawIngestionPipeline(store=temporary_store)` ignores that store when connecting/creating `structured_rules`: `setup_db()` uses the global configured path. Fix ownership so canonical store, search view, origin and test paths agree. Avoid database writes for parsing-only work.
- Default `LawIngestionPipeline().run()` has `store=None`, skipping canonical publication. Wire the legal parser/discoverer into the common runner, returning all canonical facts, documents, entities and evidence through its current contract. Keep PDF a local adapter to the same pipeline, not another writer.
- Resolve CELEX from verified upstream identifiers, URL queries/ELI metadata, document metadata or validated local filenames. `Path(document.canonical_url).stem` does not parse a CELEX HTTP URL. Conflicts/missing identity require review.
- `run()` shares one pipeline across four threads, while `last_entities` and `last_evidence_links` hold mutable per-document data. Remove unsafe shared state or serialize supported ingestion; never stage another document's entities/evidence. Preserve single-writer publication.
- Replace `data_origin='local'` with configured `live`/`demo`/`test` provenance. Local storage is a backend choice, not a fourth origin.

### 2. Remove destructive replacement from both writers

Owners: `src/main.py::ingest_rules` near line 1114 and `::ingest_law_full_text` near line 940.

Both methods still delete a law's old rows before `table.add(new_rows)`. Injecting an add failure in each confirms the previous accepted law is gone. Delaying deletion until after extraction/embedding does not make replacement atomic.

Stage and validate complete immutable versions before canonical publication. Keep previous committed facts and searchable versions if extraction, review, embedding, archive, staging or projection writes fail. Make `structured_rules` a rebuildable, versioned projection selected from committed canonical state, not a separate law authority. Use a minimal compatible publication/version mechanism; do not weaken canonical immutability or use delete-then-add as a transaction.

Test successful replacement, each failure boundary, replay, restart, historical/current reads and projection rebuild. Empty/failed extraction cannot erase or silently replace accepted data.

### 3. Archive and verify before publication

Owners: `process_pdf`, `process_document`, common runner/archive interfaces.

- With the missing identifier helper bypassed only in the probe, `process_pdf()` stages and commits before calling `upload_law_pdf()`. Archive failure silently changes to a local file URI and continues writing the search view. The committed canonical document has no verified raw URI.
- Delegate archive-first checksums/readback, leases, manifests, commit and recovery to the common runner. Explicit local archive mode remains supported; the user accepts local data under approximately 10 GB. Do not silently switch backend semantics on failure.
- `process_document(document, content)` accepts bytes that do not match the document checksum; a probe emits facts from different content. Verify document/content association at the responsible boundary and emit no accepted assertions on mismatch.
- Route legal generation/embedding calls through Task 4/4A's shared persistent spending boundary. No real paid request is authorized for these repairs. Keep zero-budget deterministic/review workflows usable.

### 4. Stop fabricating legal semantics from keywords

Owner: `extract_rules_from_text`, around lines 315–500, and related rule schemas/validation.

Confirmed examples:

- `Operators shall report weekly sales figures.` becomes an environmental-product-declaration obligation for NACE C23 producers.
- `No operator shall establish a product passport.` becomes a positive `REQUIRES` mandate.
- `The rate shall not exceed 5% in 2028.` becomes `SET_VALUE`, gains a fabricated `2028-01-01` date and D35 scope.
- Branches invent passport/reporting descriptions, fallback sectors, actors, EU-wide scope, enforcement factors and confidence 1.0. Many hardcode `REGULATION` even when the source is a directive/decision.

Fix the shared extraction logic rather than adding special cases for these sentences. Deterministic extraction may emit a candidate only when it resolves the actual actor/role, obligation, operator, value/unit, scope, condition/exception and date basis. Unsupported fields remain unknown/null or require review. Preserve negation, conditional logic, sourced zeroes and verified law type. Arbitrary penalty coefficients must not be presented as sourced fines or measured exposure.

The extraction prompt tells the model to ignore reporting/procedural text, while Task 5 includes non-numeric obligations. Align prompt/schema/parser semantics. Preserve conditions/exceptions on topology assertions as well as the rule payload; a conditional scope must not become an unconditional broad `REGULATES` edge labelled direct support.

### 5. Prove complete document coverage and bounded calls

Owners: `extract_provisions`, `extract_rules_from_text`, `extract_rules_from_full_text`, coverage metadata and fixtures.

- XML parsing returns articles only, drops annexes/tables and shares recitals without definitions/referenced provisions. The probe loses an annex containing an exception.
- HTML heading fallback captures `<h2>Article 1</h2>` without the following obligation paragraph. Broad `article` class matching also treats nested `article-title`/`article-content` elements as additional provisions.
- One deterministic pattern match causes an early return that skips unmatched obligations. A passport article plus a separate import-duty obligation produces only the passport while claiming complete coverage.
- The model fallback still slices `full_text[:150000]`; the probe's tail obligation never reaches the draft prompt. The existing long-document test checks chunk titles rather than extraction and does not establish the retained PDF text really exceeds 150k characters.
- A PDF with one readable page and one unparsed page is marked `COMPLETE`. Scan detection checks only the whole-document character total; per-page/table/provision coverage is absent.

Process all relevant provisions in bounded units with shared definitions/annex context. Track discovered, parsed, extracted, skipped and review-required portions with reasons. No silent truncation or skipped obligation may produce complete coverage. Preserve page/paragraph/table selectors and parser versions; unresolved scans/garbled text/tables require review. Do not classify every valid short/empty document as a scan.

Add normal/table/mixed-scan fixtures and a genuinely long document/provision whose retained text length is asserted. Prove late obligations are processed within bounded calls. Keep incomplete coverage explicit instead of publishing a partial parse as complete.

### 6. Review against source evidence and persist decisions

Owners: `process_document` near lines 540–730, `extract_rules_from_full_text` near 825–860, and `src/prompts/rule_validation.py::RULE_REVIEW_PROMPT`.

- The reviewer receives draft JSON only, without source spans or definitions/annexes. Review failure falls back to a publishable draft. Other extraction failures become an empty result without a durable failure distinction.
- `process_document()` imports but never calls `validate_candidate`; facts default to accepted. Deterministic rules receive no saved source-grounded legal review either.
- A nonexistent article anchor falls back to the model's summary as `DIRECT` quoted evidence. The `ART_404` probe produces accepted facts supported by an invented summary absent from the document.
- Matching quotes are blindly cut at 300/500 characters; the selected prefix can omit the date, threshold, exception or clause that would support the assertion.

Require resolvable selectors and exact supporting spans for every accepted assertion; check source/quote association and critical-field support. Give review the necessary surrounding clauses, definitions and annex/table context. Missing/mismatched evidence goes to review/rejection, never a model-summary substitute.

Persist per-candidate decisions, reason codes, reviewer/version and attempts using existing contracts. Failed review preserves the old committed law and leaves new candidates unaccepted. Model confidence/schema validity cannot substitute for initial pilot legal review. Keep actual manual review/30-provision assessment pending until performed; never invent review evidence.

### 7. Correct identities, revisions and legal time

Owners: `process_document` rule/fact generation, timeline schemas, supersession/retraction logic and metadata adapters.

- Rule IDs include the document-global loop index: inserting an unrelated earlier rule changes an existing rule's identity. Use stable provision/local-rule keys with immutable version IDs and reviewed matching for changed rules.
- A changed rule summary/label under the same entity ID causes the immutable store to reject the new version. Separate stable identity from versioned description/state without weakening entity immutability.
- Successful replacement that removes an article leaves its old mandate/scope facts live indefinitely. Reconcile complete accepted versions through explicit supersession/retraction/validity, preserving historical as-of views.
- In the synthetic CBAM fixture, Article 36's general application date and later application for specified articles become one start/expiration interval for broad full-CBAM obligations. Preserve separate phases and affected provisions. A later phase start is not an expiry unless the source establishes that.
- `eurlex_history.get_metadata()` still copies document/adoption date into missing entry-into-force; the probe confirms it. Preserve adoption, publication, entry into force, application, transitional deadlines, repeal and national implementation separately with evidence/precision. Year-only dates do not justify January 1.
- `AMENDS` is currently a generic link without affected clauses/application dates or dependency processing. Discover/process needed amendments/repeals/decisions and derive the affected legal state as of the requested time. Keep German/national implementation coverage explicitly unknown when not established.
- Do not erase a real historical date simply because it starts with 1900 or 1970; distinguish legacy sentinels from sourced dates by lineage.

### 8. Fix real discovery queries and seed-law mappings

Owners: `eurlex_bulk.py::execute_sparql`, `eurlex_history.py`, `eurlex.py::TARGET_LAWS`, fixtures and discovery tests.

- The SPARQL query is an f-string. Changing `{{4}}` to `{4}` produces the runtime regex **`^3[0-9]4[RLD][0-9]+`**, rejecting normal identifiers including `32023D1534` and `32024R3110`. Restore escaped braces and test the actual captured dispatched query/response path. The existing test only constructs its own correct regex.
- Adding `D` to a list does not prove amendment/dependency discovery, pagination or historical status. Do not present a first page, failed discovery or guessed current status as comprehensive history.
- Official checks contradict hardcoded seed labels/fixtures: construction-products recast is [Regulation (EU) 2024/3110, CELEX 32024R3110](https://eur-lex.europa.eu/eli/reg/2024/3110/oj/eng). `32023D1534` concerns [European Digital Innovation Hubs](https://eur-lex.europa.eu/legal-content/RO/TXT/?uri=CELEX%3A32023D1534), not the fixture's ETS reserve text. Direct lookup of `32024R1069` failed during review and does not verify the claimed construction-products seed. Verify selected seed IDs/titles/dates from official notices, including the intended emissions-trading act.
- Mark handcrafted fixtures explicitly synthetic with separate identities/metadata. Invented provisions under real CELEX IDs are not official extraction proof. Add small official notice/HTML/XML fixtures with URL, retrieval timestamp, checksum and rights/provenance; no corpus download is required.

## Acceptance tests and handoff

Convert the isolated reproductions into focused red/green tests. Strengthen existing tests:

- Assert expected future phases/edges exist before testing their status. Current `if future_edges:` and `if active_2026_edges:` permit missing phases to pass.
- Exercise extraction, review, embedding, archive, staging and search-view write failures. The current non-destructive test only mocks extraction and does not reach real writes; supply bytes matching the declared document checksum.
- Test successful update/retraction, stable IDs after insertion/reordering, historical/current snapshots and rebuilding `structured_rules`.
- Cover unsupported quantities/dates/scope, negation, exemptions, multiple obligations, missing anchors, annex tables, mixed scans, long text, law type and unknown national coverage.
- Isolate all canonical/search/cache/ledger paths. Existing injected temporary stores do not isolate pipeline construction.

Run focused legal/schema tests, then related runner/store/API-wrapper/caller tests. If shared persistence/schema/caller contracts change, run the full offline suite with explicit isolated paths:

```powershell
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_multimodal_single.py -q -p no:cacheprovider --tb=short
```

Inspect other tests for live/paid behavior; mock or explicitly skip and report them. Run `git diff --check`. Correct Task 5 checkboxes honestly and record exact evidence in agent memory. Do not claim manual pilot review or live integration checks passed unless performed.

Return root causes/fixes, files changed, commands/results, genuine remaining blockers and coverage limits. Leave changes unstaged/uncommitted.
