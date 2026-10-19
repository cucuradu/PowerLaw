# Task 4 review and repair prompt

Task 4 is not ready to mark complete. Repair the existing implementation; do not start later ingestion/backfill/training tasks or redesign the application.

Read `AGENTS.md`, `docs/agent-memory/current-work.md`, Task 4 and Task 4A in `docs/superpowers/plans/2026-10-18-data-ingestion.md`, and the ingestion design, technical architecture and free-source readiness specifications. Preserve the reviewed Tasks 1–3 contracts. The working tree already contains other sessions' changes: do not reset, overwrite unrelated work, stage or commit without instruction. Never print `.env`, credentials, ADC contents or real request tokens.

## Evidence from this review

The current command passes **8 tests**:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest tests/test_ingestion_runner.py tests/test_object_storage.py -q -p no:cacheprovider --tb=short
```

Those tests do not establish Task 4's acceptance conditions. The isolated review script at `data/reports/task4_review_probe.py` reproduced **16 defects** with temporary stores, fake HTTP responses and a mocked SDK. It performs no real paid request or production write. It is an ignored local review artifact, not a production interface; convert the relevant reproductions into proper regression tests that assert the corrected behavior. Its `true` output means a defect reproduced, not that the implementation passed.

```powershell
.\.venv\Scripts\python.exe -m data.reports.task4_review_probe
```

## Required fixes

### 1. Separate new collection runs from retries and resumes

Owner: `src/ingestion.py:198`, `:211`, `:216` and the SQLite ledger.

- Run/batch IDs currently depend only on source, limit, since and until. After a successful run, the next invocation with identical arguments discovers a new document but returns the old committed batch without fetching it. A `committed_with_errors` batch also prevents retrying its failed items indefinitely.
- Allocate a durable run identity for a new collection operation. Retrying/resuming that operation must retain its identity and budget; a genuinely new poll must be able to import new releases with the same CLI arguments. Keep document/assertion identities content-based and deduplicate across runs.
- Persist discovered items, request parameters, ordering/cursor candidates, item states and first-fetch metadata before relying on them for recovery. Reuse that durable work on restart rather than rediscovering a potentially different page.
- After an injected failure between stage and commit, rerunning currently refetches the document, changes `available_at`, and conflicts with the immutable staged payload. Resume the original staged payload and preserve original availability.
- Read and use the saved source cursor through a documented adapter contract. Preserve failed/unprocessed work; do not advance beyond it unless that work is durably retained for retry. Support successive pages/polls and revisions, rather than returning the first committed batch forever.

### 2. Complete recovery across canonical publication and cursor updates

Owner: `src/ingestion.py:268–305`, `src/storage/ingestion_jobs.py`, and existing canonical store publication interfaces.

- `resume_batch()` currently commits directly, acquires no lease, never restores/advances the cursor, and never verifies archived bytes. The probes committed corrupted evidence while another owner held the same source lease.
- Persist enough state to recover crashes before/after raw archive, manifest archive, ledger staging, canonical staging, canonical publication, ledger committed status and cursor advancement. Keep canonical publication authoritative; reconcile ledger/cursor after a crash without republishing or skipping work.
- Recovery must distinguish a ledger `staging` record with no canonical staged payload from an actually staged batch. Reconstruct from verified durable state or report a recoverable failure; do not blindly commit a nonexistent batch.
- Verify the saved manifest and each referenced raw object, including checksums and required references, before first publication/resume publication. A missing/corrupt archive must prevent accepted graph publication.
- An already published canonical batch needs idempotent bookkeeping recovery, including its cursor. No new fetch or paid extraction should be needed to repair that bookkeeping.
- Record correct failed/interrupted/review/completed job states. The unconditional `finally: release_lease(..., 'finished')` must not label every failure successful or mask the original exception.

### 3. Make lease acquisition atomic and enforce ownership throughout a run

Owner: `src/storage/ingestion_jobs.py:120–151` and both runner entry points.

- The current SELECT followed by UPDATE is not one protected SQLite transaction. A deterministic two-connection probe lets both workers obtain a lease for the same source.
- Use an atomic transaction/conditional acquisition with an enforceable uniqueness rule. Do not hold a SQLite transaction open across network calls.
- Preserve the single-writer requirement, including normal and resume operations. Renew long-running leases, and require the current ownership token at stage/publication/cursor mutations. A worker whose lease expired or was replaced cannot continue publishing.
- Handle multiple expired rows correctly. Do not inspect an arbitrary first `running` row and ignore another live owner. Test real competing connections/processes, expiry, renewal and stale-owner rejection.
- The cursor table currently has `source_id` alone as its primary key even though the API accepts `cursor_key`; fix that mismatch if retaining multiple cursor keys, with a compatible migration for existing ledgers.

### 4. Enforce persistent spending limits at the real dispatch boundary

Owner: `src/storage/ingestion_jobs.py:173–205`, `src/ingestion.py`, and the existing `src/utils/api_wrapper.py` integration seam.

- The ledger's reservation functions currently have no production callers. The runner does not consult `settings.extraction_budget_usd`. A parser calling the existing API wrapper reaches `models.generate_content` with a zero budget; this was proved with a fake client, not a real charge.
- Connect a durable run context/budget to every paid generation/embedding attempt used by ingestion. This safety integration is required by Task 4 even though broader SDK/diagnostics work remains Task 4A. Do not create a competing API wrapper. Ingestion must fail closed when a paid path lacks the required context, verified prices or positive budget.
- Freeze/enforce the authorized run-wide budget durably. Retries/resumes/source jobs in the same run cannot reset it or increase it by passing a different number to `reserve_cost()`.
- Reserve verified worst-case input/output/thinking costs with bounded request sizes/output before dispatch; each actual retry needs its own accounting. Reconcile actual usage durably and refresh the report's final spent/reserved costs. Unknown usage must retain a conservative reservation and block unsafe further calls.
- Fix existing reservation state handling: reusing a request ID currently bypasses a newly zero budget; reusing a released reservation returns an ID while `spent_cost()` remains zero. Settled/released IDs must not authorize a new dispatch. Replays should reuse an existing durable result or create a separately budgeted attempt.
- Reject NaN/infinite/negative monetary inputs and invalid settlement transitions. `reserve_cost(inf, inf)` currently succeeds. Do not silently release an ambiguous request as zero cost; only release when non-dispatch/non-billing is established. Handle actual cost exceeding reservation explicitly.
- No live paid requests, quota exhaustion, paid upgrade or provisioning is authorized for these repairs. Use fake SDK clients and verified-price fixtures for tests.

### 5. Keep archive hashes and paths truthful and recoverable

Owner: `src/ingestion.py:244`, `:268–280`, `src/utils/minio_client.py:268–341`, and `src/storage/dual_layer_store.py:1545–1602`.

- The runner assigns the raw manifest object's SHA-256 to `BatchManifest.manifest_sha256`, but `stage_batch()` overwrites that field with its internal canonical payload digest. Therefore `archive.get(batch.manifest_uri, batch.manifest_sha256)` fails immediately for an otherwise successful run. Give the two digests distinct explicit meanings/fields, preserving Task 3 immutable replay checks and compatibility with existing records. Do not weaken checksum validation to hide the mismatch.
- Archive a versioned manifest with the actual document raw URIs/checksums, item/cursor state and required payload/recovery references. Lists of IDs alone are insufficient to find/verify archived content or reconstruct work after an interruption.
- Construct one archive from the runner's supplied settings and reuse it for documents and manifests. Currently `archive_document(..., archive=None)` constructs an archive from global settings, while the manifest uses `EvidenceArchive(settings)`. A probe confirmed two different configuration paths in the same run.
- Keep local mode explicit and supported; the user accepts local data under approximately 10 GB. Never silently switch between file and S3 semantics. For remote mode, require the configured existing bucket and TLS for nonlocal production endpoints, and report privacy/connection checks as unverified unless actually checked.
- Validate the configured prefix and restored/read URIs against the allowed root/bucket/prefix. Reject traversal/absolute prefixes and unrelated local paths or objects. Keep reads/restores bounded and checksummed. Use fake S3 tests for successful readback, corruption, failed upload, missing bucket and isolation; no cloud account setup is required for offline completion.

### 6. Enforce source eligibility and truthful processing statuses

Owner: `src/ingestion.py:160–185`, `:209–260` and registry validation in `src/config.py`.

- Production currently accepts an unregistered source, and a missing parser defaults to `{}` with `accepted_count=1`. The probe committed an arbitrary unregistered production document as accepted with no parser.
- At the production boundary require a registered, enabled, free, rights-checked source and a valid registered adapter. Use registry policies for approved endpoints/redirects and limits. Missing/disabled/unverified adapters must return an explicit unsupported/blocked status before acquisition; do not silently return successful emptiness. Keep explicit isolated test injection possible without bypassing production rules.
- Preserve source rights/attribution and parser version metadata in durable documents. Treat absent/null/malformed parse results as explicit failure/review as appropriate, never automatic acceptance. Keep fetch success separate from parse/review/rejection states.
- Pass all supported typed payload groups through the runner/staging contract, including required series definitions for observations; do not silently discard parser output. Validate counts and make duplicate/accepted/review/rejected reports reflect actual durable records.
- Bound discovery consumption with a lazy cap: `list(discoverer(...))[:limit]` currently consumes all 20 generated items at `limit=1` and would hang on an infinite iterator. Record each discovered item safely before moving on.

### 7. Redact before writing errors or reports

Owner: `src/ingestion.py:257–260`, SQLite attempt recording and CLI report output.

- An exception containing a fake query-string API key was retained verbatim in both the returned report and SQLite bytes. Omitting `error_message` from `redactable_attempts()` does not undo that leak.
- Sanitize URLs, query credentials, authorization values and known configured secrets before persistence, logging, reports or surfaced tracebacks. Prefer stable safe error codes/messages; preserve useful stage/status information without raw exception content. Never use actual keys in tests.

### 8. Finish bounded HTTP resource/error handling and the missing tests

Owner: `src/ingestion.py:83–144`, `tests/test_ingestion_runner.py`, `tests/test_object_storage.py` and focused regressions.

- HTTP error, redirect and retry responses currently bypass `response.close()`; the 503 probe confirmed the leak. Close every response and owned session on all paths.
- Bound total acquisition time as well as per-read waits, redirects, retries and bytes. Handle streaming timeouts/connection failures consistently with bounded retry policy; malformed Content-Length/Retry-After must become controlled acquisition errors. Retain private/local target rejection and test each redirect.
- Mock DNS as well as HTTP in offline tests. Include 429/503 exhaustion, Retry-After seconds/date, streaming oversize, streaming failure, permanent failures and resource cleanup.
- Add crash/restart tests at every durable boundary above, plus partial failure recovery, new-poll versus replay identity, unchanged-content deduplication, changed-content revision, lost/stolen lease, wrong/corrupt manifest/object, source-policy rejection, redaction, archive settings consistency and every budget bypass described here.
- Tests must assert corrected behavior and prove no dispatch/publication when blocked. Do not merely adapt expectations to the current implementation or remove Tasks 1–3 safety tests.

## Completion and handoff

Run the focused suites first, then the related store/schema/API-wrapper/caller tests. If changing shared persistence/schema/API contracts, run the full offline suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_multimodal_single.py -q -p no:cacheprovider --tb=short
```

Exclude or explicitly mock other live/paid tests if identified; state exactly what was skipped. Run `git diff --check` for changed files. Update Task 4 checkboxes and add a concise review/agent-memory entry backed by commands and results. Keep remote upload/read/restore smoke checks pending when no isolated test prefix is configured; do not claim they passed. No production migration, bulk ingestion, deletion or paid request is part of this repair.

Return: root causes fixed, files changed, exact validation results, remaining genuine external blockers and any remaining operational limits. Leave changes unstaged/uncommitted.
