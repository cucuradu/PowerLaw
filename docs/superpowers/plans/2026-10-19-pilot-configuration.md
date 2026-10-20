# Bounded Germany pilot configuration

> Implement the user's authorized configuration in the existing dirty checkout; preserve unrelated work and credentials. Use TDD for the new spending/selection behavior and independent file ownership. No commits, deployment or bulk paid processing.

**Goal:** configure ADC, an overall EUR100 application budget, verified conservative model prices, isolated local storage and only working bounded sources.

**Architecture:** keep per-run USD authorization, add one immutable ledger-wide USD ceiling across all runs and restarts, derived from EUR100 at the verified ECB rate with a10% FX/billing buffer. Generation uses3.8Flash global; existing768-dimensional text embeddings use a separate us-central1 ADC client. Preserve old data and source caps. Technical extraction errors remain failures.

**Spec:** user authorization in this session; `../reviews/2026-10-19-source-and-gemini-readiness.md`; existing architecture/ingestion specs.

Verification status (re-reviewed 2026-10-20): budget cap and SDK routing are fixture-verified only (no paid generation was run); free-source ingestion into `data/pilot-germany` is live-verified 2026-10-19/20.

- [x] Budget worker: RED/GREEN durable total-cap tests (new runs, restart, unresolved spend, concurrent reservations), implement in existing SQLite ledger.
- [x] Source worker: RED/GREEN exact source selection using retained real provider responses, enable only tested source subsets, propagate law extraction failures and preserve recovery/cursors.
- [x] Parent: RED/GREEN EUR configuration and SDK routing tests; wire ledger-wide ceiling through budget context; separate generation/embedding clients while retaining ADC and vector dimension.
- [x] Parent: populate private.env without printing or replacing credentials. Select3.8Flash; set gross undiscounted verified prices; EUR100 total with10%buffer, USD1 initial run limit, fresh data/pilot-germany paths. Safe example remains opt-in for paid usage.
- [x] Integrate focused and broad offline checks, then tiny bounded free-source ingestion/replay and ADC/model metadata. Do not start model generation merely to exhaust or prove credits.
- [x] Record exact settings, explanation of series/status/outlook/embeddings, remaining source/forecast gates and billing-credit uncertainty in memory/docs.

ECB2026-10-19:1EUR=1.1225USD. Application ceiling100*1.1225*.9=101.025USD. This is estimated application accounting, not a Google billing-account hard cap or verification of remaining credits. Gross model rates avoid assuming promotional refunds. Preserve the ledger; deleting it or using a different ledger changes the accounting scope.
