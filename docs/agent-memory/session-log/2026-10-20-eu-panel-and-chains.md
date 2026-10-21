# 2026-10-20: EU panel and cost chains

- Added the EU-27 panel (240 Eurostat series, 115,372 observations): new-home prices (PRC_PRR) and costs, labour cost in construction, producer prices for wood, minerals, metals, energy, builders' survey. Generator: `scripts/ops/build_eu_panel.py`; fixtures `eurostat_*_eu27_2025_official.json`.
- `scripts/ops/panel_backtest.py`: partial pooling beats per-country models (MAE 1.034 vs 1.165, p = 0.0004); full pooling does not. Report `reports/panel-backtest-latest.json`.
- `config/chains.json`: 12 chains, 24 proposed new nodes with free sources; statuses checked against `graph-links-latest.json`.
- Observation queries take `series_ids`; the home page's cold load went from 25.6 s (after the panel) to about 11 s, of which about 7 s is opening the store.
- Not done: rule-identity refactor (waits for approval: it retracts stored rule facts), FIGARO cross-border links, policy series, news indices.
