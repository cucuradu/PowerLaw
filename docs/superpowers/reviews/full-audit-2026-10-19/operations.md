# Operations review: Tasks 4, 4A, 8 and 9

Reviewed current working tree, 2026-10-19. No product files changed. Offline only: no external HTTP/DNS, paid SDK request, cloud operation, production database, scheduler registration, or production migration. All probe/test stores and temporary files are under this directory. Loopback sockets were allowed for Python/LanceDB's event loop.

## Verdict against exact acceptance

| Task | Verdict | Evidence |
| --- | --- | --- |
| 4 | Partial implementation; acceptance fails | Archive-first runner, durable parsed payloads, separate ledger/archive hash, same-source atomic leases, source eligibility and zero-budget blocks exist. Recovery, streaming bounds, run-wide accounting and first-publication evidence verification still fail below. Exhaustive durable-boundary crash tests and configured remote smoke test remain unverified. |
| 4A | Partial | Maintained google-genai, lazy client, public signatures, 768 dimensions, structured/PDF inline requests, transient-only retries and diagnostic states are covered by passing mocks. Paid maximum reservation/actual accounting is unsafe; metadata reachability does not verify billing/generation. Target-account model availability, real bounded SDK request and remote checks were not tested. Plan checkboxes are still unchecked, correctly reflecting incomplete acceptance. |
| 8 | Fails | Pilot loops and counts exist, but actual pages still invoke direct mutations. Queue requests cannot execute, committed data stays cached, quality/expansion gates are missing, and dry-run failures are hidden. No real bounded pilot/manual annotated review report was established. Source-adapter failures are a separate Task 5/6/7 review dependency. |
| 9 | Fails | Existing 11 operations tests pass, including raw restore and simulated seven calls. Cache can delete the only valid copy; restore trusts local manifest and escapes its target; restore is raw-only; disk floor can be breached by the next write. No bounded 2010+ real backfill, actual seven-day operation, independent complete backup/restore, measured staging-space estimate, configured schedules or per-source maturity evidence was established. |

## Validation

Executed:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe data/reports/full-audit/operations/run_focused_tests.py
```

Harness sets mode=test and isolated data/LanceDB/SQLite/cache/temp/TEMP/TMP, denies external sockets, substitutes deterministic example.com/example.org DNS, and runs:

```text
tests/test_ingestion_runner.py tests/test_object_storage.py tests/test_api_wrapper.py tests/test_ingestion_pilot.py tests/test_ingestion_operations.py -q -p no:cacheprovider --tb=short
56 passed in 12.14s
```

Full output: `focused-tests.txt`. The parent owns the full-suite run. Interpreter required sandbox escalation because the uv trampoline otherwise reports permission denied.

Executed offline reproduction script:

```powershell
.\.venv\Scripts\python.exe data/reports/full-audit/operations/probe_operations.py
```

Exact output: `probe_results.json` and `probe-output.txt`. It creates a unique isolated directory per run. Probe results report observations, not expected passing assertions.

Also reproduced the documented scheduled command from a non-repository working directory, with isolated test storage and `--status`: exit 1, `ModuleNotFoundError: No module named 'scripts'`. Output: `scheduler-cwd.txt`.

## Material findings

1. **P1: Paid requests are not bounded by the reservation, and provider usage is ignored.** `src/utils/api_wrapper.py:38-54`, `:139-182`, `:241-259`. `_reserve_paid_attempt` assumes characters/4 input tokens, reserves 1024 or 2048 output tokens, excludes PDF bytes/thinking costs, but generation config often sends `max_output_tokens=None`. `_settle_reservation` always marks the estimate as actual without reading usage, even on errors/unknown usage. `budget_bound`: two fake SDK responses reporting 108.001 USD each dispatch under a 2.1 USD budget; ledger records 2.05 USD and configured max_output_tokens is null. Smallest correction: cap exactly what is reserved, account for all billable input/output/thinking/model-specific prices, and reconcile actual usage or conservatively block further calls when unknown.

2. **P1: Pilot resets the authorized budget for every source.** `scripts/ops/ingest.py:62-93`, `src/ingestion.py:284-286`. `pilot_id` is never supplied to the runner; each source gets a fresh UUID and the full settings budget. `pilot_budget` calls the real runner for two injected sources with fake SDK: configured budget 1.1 USD, total admitted cost 2.002 USD, status committed, distinct durable run IDs. Use one durable authorized budget for all source jobs in the pilot.

3. **P1: Cache eviction deletes the only valid evidence copy when the remote object is corrupt.** `src/utils/minio_client.py:448-475`. It calls `object_exists`, never reads/checksums the remote bytes, then unlinks the local file. `cache_corruption`: valid_local_copy_deleted=true, remote_checksum_read_count=0, evicted_files_count=1 while fake remote bytes are corrupt. Verify the content digest and configured destination before eviction; protect in-use/unverified objects. The optional ledger argument is unused, so there is also no manifest/reference retention check here.

4. **P1: Restore accepts tampered local manifests, overwrites existing files, and can write outside the restore directory.** `src/utils/minio_client.py:520-545`, `:559-577`. Existing file:// manifests are read directly instead of `archive.get`; document dictionaries are not schema validated; version_id is appended to target without resolving/checking containment, and target need not be fresh. `restore_integrity`: modified content-addressed manifest accepted as batch TAMPERED and existing USER_EXISTING_DATA overwritten. `restore_escape`: version_id='../outside.txt' creates a file outside target and returns restored. Verify the manifest checksum through the archive, validate IDs, reject nonfresh targets, and enforce resolved destination containment before any write.

5. **P1: Resume advances past failed items and reports success without retrying them.** `src/ingestion.py:378`, `:390-393`, `:406-448`. Initial partial run correctly avoids cursor advancement, but its manifest saves the last successful item's cursor. `resume_partial`: failed A, successful B => original committed_with_errors, cursor null; --resume then returns committed and advances cursor to B while A remains failed. Manifest excludes failed work and resume has no parsing path. Future incremental polls can skip A. Preserve partial-run outcome and failed-work recovery; advance only when unresolved items are retained in an executable retry route.

6. **P1: Source corruption after archive is still published on the first run.** `src/ingestion.py:350-387`. Raw put verifies at archive time, but neither raw objects nor manifest references are reverified before first stage/commit. `corrupt_before_publish` alters raw bytes during the parser callback after archive: initial status committed and one accepted graph edge is published. Later resume correctly raises ArchiveError. Verify all saved references immediately before initial publication as well as resume.

7. **P1: Actual Streamlit controls still mutate directly and fabricate a PDF on download failure.** `pages/6_⚙️_Ingestion_Console.py:46-66`, `:78-82`, `:96-99`; `pages/2_📰_News_Feed.py:41-57`; `src/storage/dashboard_data.py:985-992`. The console synchronously downloads laws, scrapes and seeds; the news page synchronously scrapes/ingests. Download failure writes `%PDF-1.4 Mock Legal Document for ...` to hardcoded `data/raw/laws` and counts it as processed. No mode gate surrounds these actions. `rg` found no calls to submit_ingestion_job/get_job_status in dashboard.py/pages. This violates the read-only/queued UI requirement and fake-production-data prohibition. Replace these actual callbacks with durable job submission; remove mock fallback and production seeding.

8. **P2: The submitted job has no durable executable request or consumer.** `src/storage/dashboard_data.py:1124-1172`, `scripts/ops/ingest.py:177-259`. `submit_ingestion_job` persists only run_context, losing limit and dry_run, creates no jobs/work record, and CLI has no queue consumer. `queue_request`: returned submitted; get_job_status created; persisted fields only run_id/source_id/budget_usd/created_at; jobs_rows=0; oldest_pending_job=null. Persist the complete request and connect a single CLI worker to consume it.

9. **P2: Committed CLI data never invalidates the dashboard store cache.** `src/storage/dashboard_data.py:747-767`. Cache key is absolute path only and returns the old store indefinitely. `stale_dashboard_cache`: before0 nodes; real runner commits; cached store still0 nodes; freshly opened store2 nodes. Invalidate/reload on committed generation changes.

10. **P2: HTTP byte cap is applied only after the whole response is buffered.** `src/ingestion.py:170-187`. Worker does `list(response.iter_content(...))`, then sums/checks size. `stream_limit`: max15 bytes consumes200 bytes before rejecting, closes response afterward. A long/infinite response can exhaust RAM before the cap or leave an accumulating worker after timeout. Bound chunks and bytes during consumption, and constrain connection/header time against total deadline too.

11. **P2: First-fetch recovery metadata is lost across a crash after raw archive.** `src/ingestion.py:341-363`. Document/raw URI/first availability are saved only after parsing returns. `fetch_metadata_crash`: parser crashes after archive, work document_json=null; same durable run refetches (2 fetches), changes first availability, finally commits. Save fetched document/archive state before parsing and resume it.

12. **P2: A failed resume is marked completed.** `src/ingestion.py:445-447`. Finally always calls release_lease(...,'completed'). `corrupt_before_publish`: resume raises ArchiveError but jobs row becomes completed. Track success/failure/interruption separately. Failed partial original runs also have committed ledger status, so last_success can claim success despite failed work.

13. **P2: Remaining ledger monetary validation holes.** `src/storage/ingestion_jobs.py:255-280`. Existing reserved *or settled* request IDs return an authorization ID. Existing context budget is not checked finite, so NaN defeats immutable-budget and remaining-limit comparisons. `budget_replays`: settled_id_authorizes_replay=true; nan_budget_accepted_with_existing_context=true. Current wrapper uses UUID per attempt, so settled-ID replay is a ledger interface defect rather than an observed current-wrapper double dispatch. Reject settled replay as authorization and validate finite values every time.

14. **P2: Pilot completeness and caps are based only on source statuses.** `scripts/ops/ingest.py:71-121`, `:126-174`, `:228-233`. It forwards one arbitrary limit to all sources, gives dry_run status even if every source fails, has no annotated precision/recall/evidence/date/unit/entity coverage computation or expansion gate, and only writes JSON when --output is supplied. `pilot_status`: limit1000 forwarded to all eight sources; unreviewed run reports committed; eight failed dry-run sources still produce overall dry_run. Current fixed discovery sets may happen to stay below some caps; the orchestrator does not enforce them. Dry-run/error status, per-kind caps and explicit incomplete quality gates are needed before claiming Task8 complete.

15. **P2: Restore verifies raw documents only, not a recoverable canonical backup.** `src/utils/minio_client.py:508-611`. A normal archived manifest contains facts/observations/evidence/entities/series/cursor, but restore writes only raw files and a receipt. `restore_integrity` manifest includes fact/observation entries; output files are doc and restore_receipt.json, with no canonical records, source manifest or ledger. Seven-day test restores only day7 raw content and its final assertion even accepts any output file. Need a separate complete portable backup plus isolated canonical/temporal/ledger comparison before Task9 recovery completion. Migration's separate legacy backup helper does not restore operational runs here.

16. **P2: Disk and storage budgets are not enforced before the actual allocation.** `src/utils/minio_client.py:301-333`, `:388-504`; `src/ingestion.py:170-187`, `:383-387`. Disk floor compares current free space only, excludes next raw/manifest/staging size, and cache enforcement is a separate manual CLI operation. `disk_reserve`: free105, floor100, write10 still allowed. Local evidence is protected, so enforce_cache_budget can return enforced while above the configured cache limit; no runner integration pauses expansion. Restore has no disk-floor/cumulative-size check. Account for required bytes/largest staging estimate before writes/downloads; report blocked over-budget state.

17. **P2: Review items disappear after 20 newer runs.** `src/storage/dashboard_data.py:1053-1063`, `:1101-1121`. Both review count and queue scan only 20 recent runs. `old_review`: one durable pending review, then21 newer runs => visible_review_items=0. Query outstanding reviews independently of recent activity.

18. **P2: Documented schedules do not establish a working scheduled worker.** `docs/operations/scheduled_ingestion.md:27-53`. Commands invoke -m scripts.ops.ingest with no working-directory setup, and monthly --since currently depends on incomplete source adapters. Running the same Python -m command from the operations directory reproduced ModuleNotFoundError before any acquisition. `/st` times are called UTC in prose but no timezone conversion/trigger configuration exists; missed-run catch-up and single-worker scheduler settings are absent. The PowerShell snippets also pass literal `%USERNAME%` rather than PowerShell `$env:USERNAME`. No actual task registration or seven-day operation was checked. Supply a repo-root working directory, explicit trigger timezone/catch-up/overlap settings, and validate installed tasks before claiming enabled scheduling.

19. **P2: Lease scope is per source, not one ingestion worker.** `src/storage/ingestion_jobs.py:157-174`. `source_leases` obtains SOURCE_A/runA and SOURCE_B/runB simultaneously in one ledger. Canonical store's per-mutation lock still protects individual mutations, so this probe does not claim canonical corruption; it proves the required global single-worker operational policy is unenforced during fetch/parse/budget handling. Coordinate all normal/resume jobs through one worker/lease.

## Recheck of the historical Task4 repair prompt

Do not rerun `data/reports/task4_review_probe.py` and interpret it as a repaired 16/26-case regression: it assumes unregistered production acquisition succeeds and now aborts at the first correctly rejected source; other assumptions about missing parser, SDK and hashes are stale. This review used current interfaces.

| Historical required group | Current evidence |
| --- | --- |
| New runs vs retry/resume | New UUID runs and unchanged content dedup pass current tests. Parsed work reuse after commit crash passes. Raw-before-parse crash and failed-item resume still fail (5,11). Discovery parameters/durable incomplete pages and overlap/revision source behavior are not comprehensively covered. |
| Publication/cursor recovery | Ledger manifest stores all typed payloads and hashes, resume verifies manifest/raw and reconstructs missing canonical stage. Additional `cold_resume` probe confirms injected commit failure resumes on a fresh store/ledger with one total fetch and restored cursor A. First publication verification, partial resume and failed status still fail (5,6,12). Every boundary/reconciliation timing was not exhaustively rehearsed. |
| Leases | BEGIN IMMEDIATE fixes historical same-source SELECT/UPDATE acquisition race: audit `same_source_competition` starts two concurrent ledger connections and observes one acquired, one JobLeaseError. Renew/assert methods and composite cursor migration exist. Competing processes, lease-steal-during-publication, long single-item expiry and all expired-row cases were not checked; global single worker fails (19). Resume cursor mutation omits run_id/token at line442. |
| Persistent paid budget | All five zero-budget wrappers dispatch zero (probe) and per-attempt UUID persistence exists. Worst-case cap/usage, pilot scope, settled-ID replay and NaN remain unsafe (1,2,13). |
| Hashes/paths/archive | archive_sha256 is separate from manifest_sha256 in models/ledger and ledger manifest reads succeed. Parent found canonical Arrow batch schema drops archive_sha256 on reopening; resume currently uses ledger hash, so not an independent resume failure. One supplied archive is reused. TLS/existing-bucket logic exists. Restore bypasses isolation/checksums (4); real remote privacy/read/restore remain untested. |
| Source policy/status | Unregistered production source rejection, lazy cap, {} parser rejection and typed groups pass current tests. Counts can still claim accepted with no actual assertion; parent owns canonical trust validation. Real registered adapter integration is separately reviewed by source agent. |
| Redaction | Query-key redaction and bounded safe returned errors pass current tests. All secret spellings, raw work item URLs and configured credentials in every persistence/log surface were not exhaustively tested. |
| HTTP resources/tests | Response/session close exists; retry/oversize/private redirect/PDF/timeout tests pass. Streaming byte cap remains post-buffer (10); DNS mocking is absent from repository tests, supplied by audit harness. Comprehensive 429/date Retry-After/malformed headers/network failures and durable-boundary crash matrix remain incomplete. |

## Not established

No live provider/model/S3 test, no remote bucket privacy proof, no real paid usage or pricing validation, no seven-day live schedule, no actual Task Scheduler installation/timezone proof, no real historical backfill, no manually annotated pilot precision/recall assessment, no complete independent canonical/ledger restore, and no full-disk hardware exhaustion rehearsal. These are open acceptance obligations, not passing tests.
