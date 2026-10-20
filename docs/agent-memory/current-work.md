# Current Work

Last reviewed: 2026-10-20, after the expert review and implementation steps 1-7 (branch `ingestion-pilot-review`).

## State (verified this session)

- `pytest` (bare, via `pytest.ini`): 576 passed. Tests run against a temporary store with no credentials or budget (`tests/conftest.py`).
- Pilot store `data/pilot-germany`: target 61261-0002 1958-Q1..2026-Q3 (275 quarters); release-dated availability from 84 Bundesbank real-time vintages (2006-01-06..2026-10-09). Predictors ingested: residential material/labour cost (2000+), negotiated construction wages (1995+), residential order intake (1991+), residential permits (2003+), ECB housing-loan rate (2003+), Pfandbrief yield (2000+), Eurostat comparable PPI (1990+).
- Backtest (`python -m scripts.ops.backtest_target`, 82 origins 2006-2026, MAE pp of QoQ growth): 4Q mean 0.743, AR4 seasonal 0.718, AR4 seasonal + VAT schedule 0.596. Normal quarters 0.34 with 96% interval coverage; 2021-23 shock ~2.25 and 18% coverage. Diebold-Mariano vs 4Q mean p = 0.068.

## Active priorities

1. Date predictors by evidence so they can enter models at historical origins (all are fetch-dated now). Real vintages exist for residential order intake (Bundesbank BBKRT `AFC15`).
2. ARX on VAT-adjusted growth with cost/order/permit predictors; target the 2021-23 shock error and interval coverage.
3. Dashboard: show the VAT-aware forecast with its measured error and coverage (prediction record from the backtest model).
4. Cleanup: dead legacy modules, `data/task5-*`, stale test copies under `data/reports`, legacy/demo tables in `data/production`.

## Open issues

- Destatis 61241 (material PPIs) answers status 7000 for every request; suspended in the registry.
- SMARD disabled: 365 rows committed before the Berlin-date fix are dated one day early; retract before re-enabling.
- Labels/features are release-dated latest-vintage values (PP300 revision bound 0.35 pp), not first-release values.
- GNN and LLM legal/news extraction are not yet shown to add forecast value; keep paused until an ablation beats the VAT-aware ARX.
