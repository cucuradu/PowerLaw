# 2026-10-20 Sector network, materials, laws and projections

## Goal
User asked to expand the data and relations around construction (suppliers, everything linked to the sector, prices, legislation), keep using the evidence/metadata/Gemini pipeline that builds the graph, and project more series.

## Outcome
- Supplier and buyer links: new source `EUROSTAT_NAIO` (input-output table naio_10_cp1700, Germany, 2010-2022) stored as FLOWS_TO facts with value, share and import share, evidence = table cell. 1,539 facts.
- Shared entity names in `config/sectors.json` and `src/utils/taxonomy.py` (`sector_entity`, `commodity_entity`, `io_sector_code`): every adapter now builds the same immutable sector/commodity entities; NACE codes map to input-output nodes (D35 -> D, F41 -> F).
- 30 detailed material prices (Destatis 61241-0006, GP2019 6-digit: cement, concrete, bricks, steel bars, timber, insulation, heating, cables, diesel, gas, electricity), 1991+, release-dated from archived Bundesbank PP100 vintages.
- Each series now carries MEASURES (series -> sector/commodity) and BELONGS_TO (commodity -> sector) facts from `config/series_names.json` "measures"; refetches add no new versions (`_new_facts_only`).
- EU laws: Gemini extraction now runs per article-aligned segment (15k chars, 16k output), splits a truncated segment, drops only schema-invalid rules (all-invalid still fails), caches paid segment answers next to the store, puts the graph vocabulary in the prompt, and reuses stored law entities. All 9 configured EU laws extracted (`scripts.ops.extract_laws`). Ledger spend to date USD 6.73 at the conservative 2027 price (about half that at current prices).
- German laws: new source `GESETZE_IM_INTERNET` (GEG, BauGB, BauNVO, UStG, BEHG, HOAI, AEntG); configured links are accepted only when their quote is found in the official XML; IMPLEMENTS links to cited EU directives (new predicate). 28 accepted facts.
- Graph Explorer default view "Sector network" (`get_sector_network`): construction, suppliers, cost blocks, buyers, materials with price changes, and laws, from accepted evidenced facts as of the cutoff.
- Projections: `scripts.ops.project_series` forecasts next-quarter change for 35 price series (model chosen on origins before 2018, scored after); shown on the home page with "better than no change?" per series. 17 of 35 beat no change.

## Validation
`pytest`: 589 passed. Live: NAIO, 61241-0006 backfill, archive re-dating, EUR-Lex (9 laws), German laws committed. `scripts.ops.web_smoke`: home 5.1 s cold / 0.3 s warm, other pages under 1.5 s; Graph Explorer screenshot checked.

## Decisions
See decisions.md, "Sector network and laws".

## Open questions
- Many extracted EU rules are needs_review (e.g. Taxonomy 0 accepted / 491 review): the deterministic grounding check is strict for paraphrased obligations.
- Input-output tables end in 2022 (Eurostat lag).
- Bundesbank bot challenge still not retested; new material prices are dated from the archive only.

## Next action
Review needs_review legal rules (or relax grounding with a quote field in the schema); test whether material-price inputs improve the main forecast.
