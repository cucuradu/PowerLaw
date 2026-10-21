# 2026-10-21: pooled projections for trades and materials

- Stepped back after the graph-signal test: lagged edges add nothing for Germany, but pooling had helped on the EU panel, so tried it on the 72 German trade/material projections shown on the home page.
- `project_series`: candidate `ar4_seasonal_pooled` (pool per metric, only public quarters at each origin); `rolling_origin_backtest` offers it without a VAT schedule when given `panel_prior`. Report adds `mae_by_model` and `coverage_by_method`.
- Out of sample 2018+: chosen-model error 1.503 -> 1.407; pooled chosen for 38 series. Intervals switched to ACI (coverage 78% -> 83%).
- Tests: 612 passed (new leak test, mutation-checked).
