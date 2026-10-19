# 2026-10-19 Expert review of project state

## Goal
Independent deep review: current state, mistakes, next steps. Read-only except this file.

## Outcome
Review delivered in chat. No code changed.

## Validation run
- `python -m pytest -q` (documented command): 41 collection errors; stale test copies under `data/reports/` are collected (no `testpaths`).
- `python -m pytest tests -q`: 13 failed, 520 passed, 28 subtests passed (295 s).
  - 10 failures: `DualLayerStore._record_content` calls `fromisoformat(...).astimezone(utc)` on every string field; naive dates shift by local TZ (`2025-01-01` -> `2024-12-31T23:00:00+00:00`) and pre-1970 dates raise `OSError 22` on Windows.
  - 3 failures: uncommitted `config/data_sources.json` enables Bundesbank/SMARD/EUR-Lex; tests still expect disabled.
- Pilot store `data/pilot-germany/lancedb` inspected: 6 documents, all `availability_basis=first_fetch`; target 61261-0002 has 8 quarters (2025-Q1..2026-Q4, Q4 null "..."), 3 revisions each (one per run, identical values).
- `get_construction_outlook(store=pilot)` returns "Missing price data": default ID `series:destatis:61261-0002` differs from ingested ID `61261-0002|DINSG=DG|...`.

## Open questions
- Vintage policy: allow a flagged pseudo-real-time mode (latest vintage + publication lag) so baselines can be trained before years of live collection?
- Scope: pause legal/news LLM and GNN work until statistical baselines exist?

## Next action
Commit current work on a branch, fix test config and `_record_content`, unify target ID, backfill full target history.
