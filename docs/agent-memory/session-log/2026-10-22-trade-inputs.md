# 2026-10-22: trade forecasts with inputs

- Tested the published model's inputs vs each trade's tested material links as ARX inputs for 37 trade price series (scratch script, then `project_series`). Broad inputs help (0.742 vs 0.847 own AR); linked materials hurt.
- `rolling_origin_backtest`: predictor models no longer require a VAT schedule. `project_series`: `arx_vat_inputs` candidate for trade and producer prices; report adds `train_mae_by_model`.
- Projections: chosen-model error 1.407 -> 1.373 (trades 0.834 -> 0.774). Tests: 613 passed.
