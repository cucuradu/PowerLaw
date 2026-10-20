# 2026-10-20 Dashboard speed, update schedule, commit cleanup

## Goal
User: commits without assistant traces and in plain language; delete the quarantine folder; an on/off switch for automatic updates; fix the slow dashboard.

## Outcome
- Branch history rewritten with plain commit messages (same trees, authors, dates); no assistant attribution in any message. Not pushed.
- Speed: the store loaded everything twice and cleaned every row with pandas; both fixed (cold store load 12.9 s -> 4.4 s). The other agent's evidence index removes the per-observation evidence scan. Usage telemetry to webhooks.fivetran.com disabled in `.streamlit/config.toml`. Dashboard cache now keyed on store generation and forecast file instead of a 60 s TTL. Browser timing (Playwright, `scripts.ops.web_smoke`): home 10.2 s cold / 0.16 s warm; other pages about 1 s.
- Main remaining cost: 409,007 redundant evidence links from the Bundesbank backfills. `scripts.ops.compact_evidence` removes them safely (dry run by default); running `--apply` was refused by the permission classifier, so it waits for the user.
- Automatic updates: `scripts.ops.update_forecast` (+ `.cmd` wrapper) and `scripts.ops.schedule_updates on|off|status`, plus a toggle on the Ingestion Console; off by default. One manual run: all steps OK, the 20.10.2026 PPI vintage was dated.
- Quarantine folder deletion refused by the permission classifier; left for the user.

## Validation
`pytest`: 580 passed. Manual update run logged in `data/pilot-germany/reports/update-log.jsonl`.

## Next action
User runs the evidence compaction (and deletes the quarantine folder), then re-measure with `python -m scripts.ops.web_smoke`.
