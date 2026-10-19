# Pilot Verification, Gemini Live Smoke, and Source Resolution

- Goal: Execute approved Phase 1 (live Gemini request inspection), Phase 2 (bounded pilot ingestion refresh), and Phase 3 (disabled source diagnosis).
- Outcome: 
  - Phase 1 verified: Live extraction using `gemini-3.8-flash` on Vertex AI with sample EU CBAM legal text succeeded. Valid structured JSON returned matching `GNNRuleList` schema; cost of $0.008703 deducted in `ingestion.sqlite` (remaining: $101.001678).
  - Phase 2 verified: Fixed idempotency conflict on repeated ingestion (`available_at` in `_record_content`, ISO datetime normalization, and default `adjustment="NSA"` for Destatis/ECB). Successfully ran `python -m scripts.ops.ingest --pilot --limit 1` with 100% commit across all 4 enabled sources (269 accepted, 273 staged, 0 errors).
  - Phase 3 diagnosed:
    - Bundesbank SDMX REST endpoint verified working (HTTP 200 on `BBSIS` and `BBEX3`).
    - SMARD German power market open JSON API verified working (HTTP 200 on filter 410 DE-LU).
    - EUR-Lex CELLAR REST API verified working (HTTP 200 on `publications.europa.eu/resource/celex/...`), avoiding web portal HTTP 202 challenge.
- Files changed:
  - `src/ingestion.py`: Added `load_dotenv()`; defaulted `adjustment="NSA"` for Destatis and ECB series definitions.
  - `src/storage/dual_layer_store.py`: Excluded `available_at` from `_record_content` immutable record comparison; normalized ISO datetimes to standard UTC ISO format.
  - `src/storage/dashboard_data.py`: Added `_ensure_observations_loaded()` in `get_production_overview`.
- Validation:
  - Live Gemini test: `scratch/test_gemini_smoke.py` -> 1 rule extracted, $0.008703 spent, 0 schema violations.
  - Pilot ingestion: `python -m scripts.ops.ingest --pilot --limit 1` -> exit code 0, status committed.
  - Focused test suite: `python -m pytest tests/test_ingestion_store.py tests/test_task3_durability_regressions.py tests/test_canonical_acceptance.py tests/test_source_acceptance.py -q` -> 53 passed in 23.93s.
- Decisions:
  - Repeated ingestion of identical records preserves original availability timestamps without throwing spurious conflict errors.
- Next action:
  - Update `config/data_sources.json` with verified endpoints and series keys for Bundesbank, SMARD, and EUR-Lex CELLAR.

