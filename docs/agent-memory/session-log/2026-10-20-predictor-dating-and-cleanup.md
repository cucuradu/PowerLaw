# 2026-10-20 Predictor release dating, ARX, cleanup

## Goal
User approved both recommendations: release-date predictors (evidence where it exists, flagged lag rules otherwise) and quarantine legacy data instead of deleting it.

## Outcome
- Bundesbank real-time ingestion now handles several release calendars; AFC15 (orders received, main construction, residential, constant prices) dates 44111-0009. Its latest vintage equals Destatis on all 427 months; vintage 2025-09-25 ends at 2025-07 (Destatis PD25_348). Parsing is idempotent and caches the store scan (run time 30+ min down to 2 min).
- `config/predictor_release_lags.json`: permits 60 d, wages 75 d, cost indices 120 d (no official calendar found), mortgage rate 45 d; each with its source.
- `src/quant/forecasting.py`: direct ARX on growth excluding VAT, features aligned to each series' ragged edge at the origin; Diebold-Mariano test. Backtest script reports runs with and without predictors.
- Results: see `current-work.md`. Order intake: small gain, not significant; rule-dated predictors: worse.
- Correction: the earlier "p = 0.068 vs 4Q mean" depends on the loss; now p = 0.035 (absolute errors) and 0.27 (squared errors).
- Cleanup: dead modules and three unused dependencies removed; GNN Lab read-only; legacy data quarantined (179 MB); plans re-marked.

## Validation
`pytest`: 569 passed; live ingestion committed (run `run_bd5415777fde495388bf49888e683c29`); public-as-of check: July 2025 orders visible on 2025-09-26, not on 2025-09-24.

## Next action
Dashboard forecast panel with measured error and coverage.
