# 2026-10-22: forecast ledger and 1-3 year forecasts

- User set the product horizon to 1-3 years and asked to start the professional roadmap (ledger, horizon, outside forecasts, scenarios).
- `scripts.ops.forecast_ledger`: hash-chained append-only record of published forecasts, scored only on outcomes released after the forecast; run by the weekly update. 181 entries recorded today.
- Rule-dated inputs count as public at min(rule date, fetch time).
- `src.quant.horizon` + `scripts.ops.horizon_forecast`: 4/8/12-quarter cumulative forecasts for the target and 35 trades; protocol committed (b88551e) before the first run. Holdout: chosen SAR loses to the 10-year trend at 2-3 years; intervals undercover. Dashboard shows it with the trend comparison.
- Tests: 617 passed.
- Outside benchmark: DIW Wochenberichte 2019-2026 in `config/external_forecasts.json` (collected by a fetch agent, checked by nominal/real identity), scored by `scripts.ops.benchmark_external`: on par with ours.
- Target 1-3 year ranges switched to history since 1968 (coverage 88/85/77% on 2013+ vs 75/73/44%).
- Graph research (user asked how a graph could beat the model): literature says graph gains come from cross-learning, hierarchies and structure, not lagged neighbour signals. Tested: EU-wide 1-3 year model (robust, ties trend), adding up trades (`scripts.ops.hierarchy_test`, tie; small 1-year edge), cost build-up (`src.quant.cost_model`, `scripts.ops.cost_scenarios`): with actual costs half the trend's error, so 1-3 year price risk is material cost risk. Dashboard shows cost what-ifs. Tests: 621 passed.
- Follow-up list: cost index checked (not circular), material cost forecasts (`scripts/research/material_cost_forecast.py`, `cost_inputs`), per-trade exposure (`scripts.ops.trade_cost_sensitivity`), shadow models for the April review, track record and failed-update warning on the home page. Tests: 625 passed.
