# 2026-10-20 Material prices, next-release forecast, dashboard panel

## Goal
Continue the next steps: forecast on the dashboard with measured error; inputs for the 2021-23 cost shock.

## Outcome
- Destatis 61241 answers again (the 2026-10-19 status 7000 was a provider outage); re-enabled and backfilled from 1976.
- Bundesbank real-time PP1/PP100 (254 vintages) dates all five material indices: one Destatis release covers them (PD25_343 on 19.09.2025; vintages 19.09.2025 and 18.09.2026 end at the matching months). One calendar can now date several series; each period is emitted once per batch.
- Forecasting: material composite with 3-month momentum; `next_release` target rule plus a live forecast at the run time; Bartlett-weighted Diebold-Mariano.
- Results: see `current-work.md`. Order intake and materials help 2 quarters ahead (0.638 vs 0.711), not for the next release (tie).
- Dashboard: reads `forecast-latest.json`; shows target quarter, model, interval as levels and growth, track record and reference forecasts; rejects stale records; release date is the evidenced Destatis date (fixes the fetch-time issue).

## Validation
`pytest`: 578 passed. Live: Destatis and Bundesbank real-time ingestion committed; dashboard rendered against the pilot store (2026-Q4 forecast 142.92).

## Next action
Schedule ingestion plus backtest after each Destatis release.
