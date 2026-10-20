# Current Work

Last reviewed: 2026-10-20, after material prices, next-release forecast and dashboard panel (branch `ingestion-pilot-review`).

## State (verified this session)

- `pytest`: 578 passed. Tests use a temporary store with no credentials or budget.
- Pilot store `data/pilot-germany`: target 61261-0002 1958-Q1..2026-Q3, release-dated by 84 Bundesbank real-time PP300 vintages. Residential order intake (44111-0009) release-dated by 251 Bundesbank real-time AFC15 vintages (2005-11-22..2026-09-25; dates match Destatis press releases). Material producer prices (61241-0004: wood, plastics, minerals, metals, metal products; 1976+) release-dated by 254 Bundesbank real-time PP100 vintages (one Destatis release covers all 61241 indices). Other predictors are fetch-dated in the store; `config/predictor_release_lags.json` gives them flagged lag rules for models only.
- Backtest (`python -m scripts.ops.backtest_target`, MAE pp of quarterly growth; report `data/pilot-germany/reports/backtest-YYYYMMDD.json`):
  - 2 quarters ahead, no predictors, 82 origins 2006-2026: 4Q mean 0.743, AR4 seasonal + VAT 0.596.
  - 2 quarters ahead with predictors, 53 origins 2013-2026: direct AR4 seasonal + VAT 0.711; + order intake 0.681; + material prices 0.694; + both 0.638 (DM p = 0.07; shock quarters 1.93 vs 2.16); + rule-dated predictors 0.668 with 72% coverage (rejected).
  - Next release (dashboard setup), 82 origins: 4Q mean 0.675, AR4 seasonal + VAT 0.497, + order intake and materials 0.495 (tie); 90% intervals cover 82-84%.
- Dashboard: shows the 2026-Q4 forecast from `reports/forecast-latest.json` (142.9, +0.86%, 90% interval 141.3-144.6) with its track record; the release date shown is the evidenced Destatis date.
- Cleanup done: dead modules deleted (market data, EUR-Lex bulk/history, debug scripts, legacy quant suite); GNN Lab is read-only; legacy data moved to `data/_quarantine-2026-10-20/` (see its README); plan tasks marked live-/fixture-verified.

## Active priorities

1. Schedule `scripts.ops.ingest` (Destatis, Bundesbank real-time) and `scripts.ops.backtest_target` after each Destatis release, so the record never goes stale (the outlook rejects a record whose target quarter is already released).
2. Intervals undercover (82-84% vs 90%) because of the 2021-23 shock; consider regime-aware or recent-window conformal calibration.
3. Delete `data/_quarantine-2026-10-20/` once the user confirms nothing there is needed.

## Open issues

- SMARD disabled: 365 rows committed before the Berlin-date fix are dated one day early; retract before re-enabling.
- Labels/features are release-dated latest-vintage values, not first-release values (PP300 QoQ revisions max 0.35 pp; AFC15 YoY revisions mean abs 0.17 pp).
- GNN and LLM legal/news extraction stay paused until an ablation beats the VAT-aware model.
