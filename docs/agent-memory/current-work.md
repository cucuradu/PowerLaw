# Current Work

Last reviewed: 2026-10-19, after live Gemini smoke test, pilot refresh, and disabled source diagnosis.

## Active priorities

- All 12 implementation tasks and C1–C8 acceptance gates are verified offline (532 passed).
- Pilot ingestion is fully functional and repeatable: Destatis, Eurostat, ECB, and Official News commit idempotently.
- Live Gemini 3.8 Flash structured extraction on Vertex AI verified under application budget ledger.
- Next priority: Enable and wire the 3 verified endpoints (Bundesbank SDMX, SMARD power prices, EUR-Lex CELLAR).

## Latest independent evidence

- Live Gemini test: Extracted structured logic rule from CBAM Article 5 using `gemini-3.8-flash` on Vertex AI. Spent $0.008703; remaining ledger budget $101.001678.
- Bounded Pilot Run: `python -m scripts.ops.ingest --pilot --limit 1` completed with `status: "committed"` across all 4 enabled sources (269 accepted, 273 committed).
- Storage State: Active pilot store has 9 committed batches, 282 observations, 3 facts, 6 documents, 285 evidence links.
- Focused test suite (`tests/test_ingestion_store.py`, `tests/test_task3_durability_regressions.py`, etc.): 53 passed in 23.93s.

## Disabled source resolution status

1. **Bundesbank SDMX**: REST API verified reachable at `https://api.statistiken.bundesbank.de/rest/data/` (HTTP 200). Series key format requires update from old key to current SDMX key (e.g. `BBSIS/M.I.ZAR...`).
2. **SMARD Power Prices**: Open JSON API verified reachable at `https://www.smard.de/app/chart_data/410/DE/` (HTTP 200) for German hourly wholesale electricity prices.
3. **EUR-Lex / CELLAR**: Official Publications Office CELLAR REST API (`https://publications.europa.eu/resource/celex/...`) verified returning official Formex XML packages (HTTP 200).
