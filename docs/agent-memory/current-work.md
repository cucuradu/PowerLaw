# Current Work

Last reviewed: 2026-10-20, after review steps 1-8 and predictor release dating (branch `ingestion-pilot-review`).

## State (verified this session)

- `pytest`: 569 passed. Tests use a temporary store with no credentials or budget.
- Pilot store `data/pilot-germany`: target 61261-0002 1958-Q1..2026-Q3, release-dated by 84 Bundesbank real-time PP300 vintages. Residential order intake (44111-0009) release-dated by 251 Bundesbank real-time AFC15 vintages (2005-11-22..2026-09-25; dates match Destatis press releases). Other predictors are fetch-dated in the store; `config/predictor_release_lags.json` gives them flagged lag rules for models only.
- Backtest (`python -m scripts.ops.backtest_target`, MAE pp of next-quarter growth):
  - Without predictors, 82 origins 2006-2026: 4Q mean 0.743, AR4 seasonal 0.718, AR4 seasonal + VAT 0.596 (normal quarters 0.34, 2021-23 shock 2.26). Diebold-Mariano vs 4Q mean: p = 0.035 on absolute errors, p = 0.27 on squared errors (dominated by the shock quarters).
  - With predictors, 54 origins 2013-2026: direct AR4 seasonal + VAT 0.702, + order intake 0.671 (not significant), + all rule-dated predictors 0.752 (worse; coverage 0.70).
- Cleanup done: dead modules deleted (market data, EUR-Lex bulk/history, debug scripts, legacy quant suite); GNN Lab is read-only; legacy data moved to `data/_quarantine-2026-10-20/` (see its README); plan tasks marked live-/fixture-verified.

## Active priorities

1. Dashboard: show the VAT-aware forecast with its measured error and interval coverage.
2. Shock regime: the 2021-23 cost shock is the main remaining error; candidate inputs need real vintages (e.g. material producer prices once Destatis 61241 answers again).
3. Delete `data/_quarantine-2026-10-20/` once the user confirms nothing there is needed.

## Open issues

- Destatis 61241 (material PPIs) answers status 7000 for every request; suspended.
- SMARD disabled: 365 rows committed before the Berlin-date fix are dated one day early; retract before re-enabling.
- Labels/features are release-dated latest-vintage values, not first-release values (PP300 QoQ revisions max 0.35 pp; AFC15 YoY revisions mean abs 0.17 pp).
- `published_at` in the outlook shows fetch time.
- GNN and LLM legal/news extraction stay paused until an ablation beats the VAT-aware model.
