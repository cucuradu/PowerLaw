# 2026-10-22: forecast ledger and 1-3 year forecasts

- User set the product horizon to 1-3 years and asked to start the professional roadmap (ledger, horizon, outside forecasts, scenarios).
- `scripts.ops.forecast_ledger`: hash-chained append-only record of published forecasts, scored only on outcomes released after the forecast; run by the weekly update. 181 entries recorded today.
- Rule-dated inputs count as public at min(rule date, fetch time).
- `src.quant.horizon` + `scripts.ops.horizon_forecast`: 4/8/12-quarter cumulative forecasts for the target and 35 trades; protocol committed (b88551e) before the first run. Holdout: chosen SAR loses to the 10-year trend at 2-3 years; intervals undercover. Dashboard shows it with the trend comparison.
- Tests: 617 passed.
