# No Fabricated Data Cleanup and Pilot Audit

- Goal: Remove all hardcoded fallbacks, sample seeding, simulated price histories, random embedding vectors, and legacy fixture bypasses so missing data remains strictly unavailable.
- Outcome: Completed cleanup across dashboard, quant models, news scraper, and graph export. Verified with new test suite `tests/test_no_fabricated_data.py` and comprehensive isolated test suite: **532 passed, 24 warnings, 28 subtests passed in 276.24s (0:04:36)** with 0 failures and 0 network leaks.
- Files changed:
  - Deleted: `src/utils/seed_dual_layer.py`, `scripts/ops/dry_run_ingest.py`, `visualizations/graph_data.json`, `visualizations/interactive_graph.html`.
  - Modified: `dashboard.py`, `pages/2_📰_News_Feed.py`, `pages/3_🕸️_Graph_Explorer.py`, `pages/5_🤖_GNN_Modeling_Lab.py`, `pages/6_⚙️_Ingestion_Console.py`, `src/quant/fragility.py`, `src/quant/macro_fred.py`, `src/quant/ranking.py`, `src/quant/regime_hmm.py`, `src/quant/volatility_garch.py`, `src/scrapers/market_data.py`, `src/scrapers/news_scraper.py`, `src/storage/dashboard_data.py`, `scripts/export_graph_viz.py`, `scripts/ops/restore_specific_laws.py`, `scripts/ops/ingest.py`.
  - Added tests: `tests/test_no_fabricated_data.py`, `tests/test_configured_sources.py`, `tests/test_pilot_configuration.py`, `tests/test_total_budget.py`, `tests/sample_data.py`, `tests/news_sample_data.py`.
- Validation:
  - Isolated runner: `data/reports/no-fabricated-data/run_verification.py` -> exit code 0, 532 passed.
  - Active pilot database audit (`data/reports/no-fabricated-data/pilot-audit.json`): 0 fixture rows, 4 verified raw documents matching SHA hashes, 266 genuine observations, 3 news facts.
- Decisions:
  - Missing macro series, graph nodes, news measurements, and model predictions must strictly evaluate to `unavailable` / `None` across production, demo, and test modes.
- Unresolved:
  - External endpoints for disabled sources (Bundesbank, SMARD, EUR-Lex, GISCO).
- Next action:
  - Review readiness with user, inspect Gemini request formatting, and run bounded ingestion of enabled official sources.

