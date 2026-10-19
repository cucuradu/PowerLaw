# Scheduled ingestion and recovery

The CLI is the only ingestion worker. Streamlit submits jobs and reads status. One SQLite lease covers all sources; a second writer fails before publication. Keep the database, ledger, evidence archive and the worker's private environment together. Internal timestamps are UTC.

## Windows Task Scheduler

These are configuration examples, not installed or rehearsed tasks. First verify a bounded manual job under the intended Windows account. That account needs the repository, private environment/ADC and storage permissions. Do not put credentials in task arguments, exported XML or this guide. The interactive example runs only while that account is logged on; unattended service-account configuration remains an operator task.

Use an absolute interpreter and explicit working directory. Otherwise the scheduler's directory can select the wrong relative configuration/storage paths. [Microsoft's action documentation](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskaction) describes WorkingDirectory.

```powershell
$repo = 'C:\Users\Radu\Projects\Python\PowerLaw'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$action = New-ScheduledTaskAction -Execute $python `
    -WorkingDirectory $repo `
    -Argument '-m scripts.ops.ingest --consume-queue'
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
# Run only after reviewing this definition and completing manual checks:
# Register-ScheduledTask -TaskName 'PowerLaw-IngestionWorker' `
#     -Action $action -Trigger $trigger -Settings $settings -Principal $principal
```

StartWhenAvailable permits a missed start to run later; it does not replay every missed polling interval. IgnoreNew prevents overlap for this task only. Keep one scheduled queue consumer. Separate source tasks still compete for the global lease: avoid overlapping them and inspect their exit codes. A task killed by its execution limit leaves durable work for recovery after its lease expires. See [Microsoft's settings reference](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset).

PowerShell `-At '02:00'` and `schtasks /st 02:00` specify the machine's local wall clock, not UTC. On Europe/Berlin, local 02:00 changes its UTC offset with daylight saving and can fall in the transition hour. For a fixed UTC schedule, use reviewed task XML with an explicit Z/offset in StartBoundary, inspect the next-run time, and rehearse the DST boundary. [Microsoft documents offset behavior](https://learn.microsoft.com/en-us/windows/win32/taskschd/trigger-startboundary). UTC application timestamps do not configure the scheduler's timezone.

Suggested cadence after source-specific live gates pass: hourly official news, daily statistics, weekly legal reconciliation, and monthly bounded revision windows. The queue consumer executes submitted work; it does not create these source jobs by itself. Dashboard submission or an explicit operator CLI run supplies work. Do not schedule the full pilot as a daily backfill or assume RSS supplies historical coverage.

## Bounds and expansion gates

`--pilot --limit N` clips legal discovery to 20 acts and news to 300 documents. Each of the five statistical sources gets at most eight discovery items and eight distinct parsed series, for at most 40 series in one pilot. A response containing more series fails before publication. Geography is bounded to eight discovery items. Smaller requested limits remain smaller. These caps do not establish sufficient observations for training.

Every pilot report keeps `expansion_allowed=false` and publishes pending human review and unmeasured quality gates. Software tests never unlock expansion. Independent review requires 30 representative legal provisions, 100 news items and 20 statistical series; review all initial legal outputs. Expansion requires at least 95% accepted news-link precision, 95% key legal-field accuracy with zero unsupported critical obligations, and 90% discovery recall on fixed held-out items. Record counts, uncertainty, evidence and reviewer decisions before designing expanded acquisition. The current CLI has no automatic quality-gate override.

Paid processing is disabled without a positive durable run budget and verified model-specific prices. Generation counts complete request contents, uses one candidate and caps total output including thinking. Embeddings require a separate verified input-token price. Unknown/error usage retains its reservation and blocks further paid attempts, retries and restart. Budget exposure includes unresolved reservations; it is not a verified invoice. Reconcile against provider usage before retrying paid work. Shared pilot authorization persists for child-run recovery; changing a run's spending pool is rejected.

Provider contracts: [complete token counting](https://cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/projects.locations.publishers.models/countTokens), [thinking and total output limits](https://ai.google.dev/gemini-api/docs/generate-content/thinking), and [embedding input bounds and usage statistics](https://cloud.google.com/vertex-ai/generative-ai/docs/embeddings/get-text-embeddings). Verified prices must cover the selected model and request modality/tier; no price is inferred from another model.

## Operations and failed work

Run from the repository root with explicit production paths in the private environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops.ingest --status
.\.venv\Scripts\python.exe -m scripts.ops.ingest --consume-queue
.\.venv\Scripts\python.exe -m scripts.ops.ingest --review-report RUN_ID
.\.venv\Scripts\python.exe -m scripts.ops.ingest --enforce-cache
.\.venv\Scripts\python.exe -m scripts.ops.ingest --resume BATCH_ID
.\.venv\Scripts\python.exe -m scripts.ops.ingest --verify-restore MANIFEST_URI --restore-dir ./data/restored_test
```

Queue exceptions and failed reports become durable failed jobs and return a nonzero CLI exit code. They are not silently polled forever. Re-submit the same job/run through the existing submission interface after fixing the cause; the runner reuses archived documents and parsed successful items, retries failed items, and advances the cursor only after all work publishes. A failed new batch publishes nothing; earlier committed batches stay visible. Batch resume handles staged publication; it does not restart failed discovery/extraction. Unknown paid reservations still require reconciliation before any paid attempt.

Catch-up uses bounded overlapping windows and each source's persisted cursor. For example, an operator-selected revision window can use `--source DESTATIS_GENESIS --since 2026-07-01 --until 2026-10-01 --limit 8`; update dates deliberately, inspect revisions, and record gaps. A successful poll does not establish historical completeness.

Default limits: cache 2 GiB, temporary staging 512 MiB, free-disk floor 2 GiB. Respect environment overrides. Local authoritative evidence is protected from cache eviction. Restore rehearsal uses a fresh isolated directory and verifies canonical and raw evidence readback.

## Pending operational proof

Source rights declarations and offline fixtures do not establish live endpoint availability, article rights, parser robustness or extraction quality. Keep rights, release/freshness monitoring, quiet-feed/error distinction, schema drift, revisions and recovery evidence in each source's operational review.

Windows registration, service-account/ADC access, missed-start/DST/overlap rehearsal, independent legal/news review, a real bounded pilot, empty-instance restore rehearsal and seven days of observed scheduling remain pending until performed and recorded. No task was installed by this repair.
