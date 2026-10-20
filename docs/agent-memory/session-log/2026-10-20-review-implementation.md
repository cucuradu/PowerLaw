# 2026-10-20 Review implementation, steps 1-7

## Goal
Implement the expert review step by step (commit, tests, target ID, data, vintages, baselines, model).

## Outcome
Branch `ingestion-pilot-review`, commits f2bd899..799781f. Forecasting is now possible on real data and beats every baseline.

## Files changed (main)
`pytest.ini`, `.gitattributes`, `tests/conftest.py`, `src/config.py`, `src/storage/dashboard_data.py`, `src/storage/dual_layer_store.py`, `src/models/gnn_dataset.py`, `src/scrapers/statistics.py`, `src/ingestion.py`, `src/quant/forecasting.py`, `scripts/ops/backtest_target.py`, `config/data_sources.json`, `config/de_vat_schedule.json`, new tests and official fixtures under `tests/`.

## Validation
- `python -m pytest -q`: 576 passed.
- Live ingestion into the pilot store: Destatis (5 tables), Eurostat, ECB MIR, Bundesbank, Bundesbank real-time (84 vintages), all committed.
- `python -m scripts.ops.backtest_target`: results in `current-work.md`.

## Decisions
- Target availability from evidenced release dates (see `decisions.md`).
- VAT schedule as config, verified against Destatis incl./excl. indices.

## Unresolved
Predictor release dating; 61241 outage; SMARD retraction; cleanup of legacy data (needs user confirmation before deleting untracked data).

## Next action
Release-date predictors (start with BBKRT AFC15), then ARX.
