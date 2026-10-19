# Tasks 5–7 source audit — 2026-10-19

Scope: review only. No product code, shared documentation, production stores, credentials, live services, or paid models were changed or used. Read `AGENTS.md`, `current-work.md`, exact Tasks 5–7, the old Task 5 repair prompt, and relevant ingestion/architecture/free-source requirements. Existing working-tree changes were preserved.

## Verdicts against the actual acceptance requirements

| Task | Verdict | Verified progress | Requirements still failing or unproved |
|---|---|---|---|
| 5 — legal | **Partial; completion fails** | Existing legal suite passes; original 26 defect predicates now reproduce 0 defects. XML annex/shared-definition extraction, heading parsing, checksum checking, negative passport polarity, numeric bounds, year-only null dates, source context in ordinary review, missing-anchor review, failed-review rejection, no delete-before-add, correct dispatched SPARQL regex, and mixed-PDF review have improved. | Registered collection fails; accepted unsupported legal semantics; archive outage still commits; corrections through the PDF adapter fail; removal reconciliation is lost between parser instances; article-level completeness and long review remain unsound; manual/official fixture and dependency review remain open. |
| 6 — news | **Partial; completion fails** | Existing 46 news tests pass; URL tracker normalization, no production synthetic outage fallback, null/zero magnitudes, narrow negative closure handling, and noncausal compatibility graph defaults work in tested cases. | Registered adapter treats RSS as one article and cannot publish; no canonical entities/evidence; corrections multiply events; location-distinct events collapse; non-closure negations become accepted actions; event/publication time and UTC precision are lost; unit meanings change; same-type events and text tails disappear; required 100-item held-out evaluation is absent from the acceptance test. |
| 7 — statistics/geography | **Partial; completion fails** | Existing 16 statistics/geography tests pass; official fixture values and some month/quarter bounds parse; manually constructed observation revisions preserve past cutoffs; target prohibited-substitution list exists; first-fetch availability is conservative. | Actual source acquisition is stubbed/generic; registered target identity is not produced by the official flat parser; required series definitions/coverage are absent; distinct dimensions collide; status/missing values and quarterly SDMX fail; native SMARD intervals collapse and invalid records commit; GISCO cannot publish and correction IDs collide; 20-series validation/live onboarding/historical-vintage gates remain unproved. |

All registry sources currently have `enabled=false`. That correctly prevents production activation, but it also means the completion claims cannot be read as enabled operational source integration.

## Executed validation

The virtualenv trampoline initially failed in the sandbox with `permission denied (os error 5)`. The following commands then ran with approved escalation, using the repository virtualenv. No test/code failures were hidden.

Focused existing tests:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:POWERLAW_MODE='test'
$env:POWERLAW_DATA_DIR=(Join-Path $PWD 'data/reports/full-audit/sources/test-storage')
$env:LANCEDB_PATH=(Join-Path $env:POWERLAW_DATA_DIR 'lancedb')
$env:POWERLAW_SQLITE_PATH=(Join-Path $env:POWERLAW_DATA_DIR 'ledger.sqlite')
$env:POWERLAW_CACHE_DIR=(Join-Path $env:POWERLAW_DATA_DIR 'cache')
$env:POWERLAW_TEMP_DIR=(Join-Path $env:POWERLAW_DATA_DIR 'temp')
$env:POWERLAW_OBJECT_BACKEND='local'
$env:POWERLAW_EXTRACTION_BUDGET_USD='0'
.\.venv\Scripts\python.exe -m pytest tests/test_law_ingestion.py tests/test_news_ingestion.py tests/test_news_scraper.py tests/test_statistics_ingestion.py tests/test_geography_ingestion.py -q -p no:cacheprovider --tb=short
```

Result: **73 passed in 7.00s**, exit 0.

Independent probes (each script itself forces all six data/database/cache/temp paths into temporary directories beneath this report directory; zero budget; socket connection helper blocked):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe data/reports/full-audit/sources/probe_sources.py
.\.venv\Scripts\python.exe data/reports/full-audit/sources/run_prior_probes.py
```

Both exit 0. `probe-results.json` has individual outputs, including intentional captured exceptions. `probe-output.txt` preserves final stdout. `prior-probes.txt` preserves **CONFIRMED 0 OF 26**. The prior wrapper removes only the obsolete final assertion that all 26 defects must exist; every original defect predicate runs unchanged. This proves those exact examples improved, not the broader acceptance contract. Source hashes are in `source-hashes.json`.

## Confirmed findings

### S01 — P1: registered legal discovery passes dictionaries as CELEX IDs

`src/ingestion.py:454` loops over `TARGET_LAWS` dictionaries, puts the whole dictionary into `upstream_id`, and formats it into the URL. `src/main.py:141` applies a regex to that dictionary.

Reproduction: `law_discovery_dict_identifiers` and `law_registered_runner`. The registered runner with a valid fake official XML fetch discovers/fetches one item, then reports `TypeError: expected string or bytes-like object, got 'dict'`; zero accepted/committed facts. This is independent of live endpoint availability. The discovery function also ignores date bounds and does not perform amendment closure. `EURLEX_CELLAR` only registers `publications.europa.eu` while the generated fetch URL uses `eur-lex.europa.eu`, so merely enabling it would also fail production host policy (`src/ingestion.py:338`, `config/data_sources.json` EURLEX endpoints).

### S02 — P1: deterministic legal extraction still fabricates accepted critical obligations

`src/main.py:432` treats general compliance/standard/certification language as a product passport. `src/main.py:436` supplies a hardcoded passport obligation, producer role, EU region, penalty factor, and certainty. `src/main.py:596` maps any generic application clause to CBAM obligations and C23/C24 sectors.

Reproduction: `law_noise_compliance_becomes_passport`: “Operators shall ensure compliance with noise limits.” becomes an **accepted** digital-construction-passport mandate, `parse_confidence=1.0`, `penalty_factor=1.0`, coverage COMPLETE. `law_generic_application_becomes_cbam`: “This Regulation shall apply from 2028-06-01.” becomes accepted CBAM declaration/certificate obligations regulating C23/C24. Neither source states those obligations. This fails the zero-unsupported-critical-obligation gate even though older, narrower examples now pass.

### S03 — P1: an existing anchor is treated as proof of arbitrary rule content

`src/main.py:708–711` chooses accepted versus needs_review from article presence; `validate_candidate` at line 884 checks general structural/time contracts, not whether claimed numbers/actors/scope are in the quote. No per-candidate persisted legal reviewer decision is written by this path.

Reproduction: `law_matching_anchor_accepts_unsupported_quantity` injects a fake extractor result with `invented_tax_rate=88 PERCENT`, anchored to a real passport-only Article 1. The mandate remains accepted. No live model is called. Missing anchors are repaired, but source-grounded critical-field verification is not.

### S04 — P1: archive failure still allows canonical PDF publication

`src/main.py:1348` catches upload failure; lines 1367–1375 stage and commit before the failure guard at 1378. The guard protects only the search view.

Reproduction: `law_archive_failure_still_commits` uses a real in-memory generated PDF, real isolated DualLayerStore, and `upload_law_pdf` raising an injected outage. Outcome: **4 facts, committed batch, raw_uri=null**. The old probe only checked “commit before archive attempt”; changing order made that predicate false while the failure remains unsafe.

### S05 — P1: legal updates are not recoverable through the actual entry points

`src/main.py:1337` uses `batch_<celex>` for every PDF version, so a corrected PDF cannot stage into the same store. Separately, `_eurlex_parse` (`src/ingestion.py:467`) creates a new pipeline per document without a canonical store; retractions depend on `_known_law_facts` or a nonexistent `store.ledger_store` lookup (`src/main.py:895–901`).

Reproduction: `law_pdf_correction_reuses_batch_id`: successful first PDF, modified second PDF under the same filename, isolated real store, fake archive/embedding. Second call raises `Cannot restage or reuse ... 'batch_32024R0001' ... with conflicting records`. `law_registered_parser_no_revision_reconciliation`: stage two valid versions through two actual registered parser calls, deleting ART_2 in v2. Both outputs contain zero tombstones; ART_2 remains visible after v2. The old removal probe reused one pipeline and therefore did not test the actual lifecycle.

### S06 — P1: complete legal coverage is claimed after dropping obligations and conditions

The deterministic `if/elif` chain emits one recognized family per article; lines 628–629 mark the entire article matched, so another obligation in that article never reaches fallback. Scope edges at `src/main.py:743`, `:778`, `:813` omit the parent rule's conditions/exceptions. Long model review uses only `full_text[:250000]` (`src/main.py:1026`).

Reproduction: `law_same_article_second_obligation_dropped`: a passport plus monthly emissions-reporting requirement in one Article 1 yields one mandate and COMPLETE coverage. `law_condition_edges_lose_exception`: the mandate contains a small-enterprise exemption, but accepted REGULATES/APPLIES_IN edge payloads are null. `law_review_only_first_250k`: extraction sees a tail beyond 250,000 characters, review does not, yet reviewed rules return. Initial 150k truncation is fixed, complete reviewed coverage is not.

### S07 — P1: legal identifier conflicts are accepted

`src/main.py:141` trusts upstream CELEX before checking structured document identity; `process_document` never reconciles it with the XML's `DOCUMENT.REF`.

Reproduction: `law_conflicting_identifiers_not_reviewed`: XML says 32024R9999, upstream ID says 32023R0956. Accepted mandate is attributed to `law_32023R0956`. Conflicting identity must not become accepted legal evidence.

### S08 — P1: registered news ingestion is not article ingestion and cannot publish

`src/ingestion.py:490–508` discovers feed endpoints, not entries. `_news_parse` decodes whole RSS as plain text at 512–513; it does not preserve per-item GUID/link/date/publisher or fetch permitted article text. It returns no entities or evidence at 521–522.

Reproduction: `news_registered_parser` feeds actual RSS structure containing two different items. It emits three accepted facts from the combined feed, no entities, no evidence. `news_registered_runner` archives it then raises `ValueError ... missing or uncommitted endpoint` at real staging. There is no accepted canonical article/event path despite registration and a passing dry-run test.

### S09 — P1: news event identity both multiplies corrections and merges distinct sites

`src/scrapers/news_scraper.py:762` includes magnitude and article/publication date in event identity, but excludes plant/location/occurrence episode. Article identity at 757 includes mutable headline. Clustering at 1554–1562 keeps one winner without retaining both document/evidence links or a versioned cluster decision.

Reproduction: `news_correction_is_two_events`: correcting 20% to 15% produces separate action IDs plus separate reaction IDs, 4 clustered records, zero retractions. `news_distinct_sites_merge`: separate Bremen/Ghent cuts with the same actor/type/magnitude/publication time collapse from four action/reaction records to two. `news_title_correction_changes_article_identity` changes the article ID despite unchanged URL/GUID. This breaks correction lineage and source-diversity/event counts.

### S10 — P1: non-closure negations are published as positive news actions

`src/scrapers/news_scraper.py:1055` suppresses negation only for shutdown/production-cut action types. Any nonempty matched verb is accepted by line 1514.

Reproduction: `news_negated_investment`: “BASF will not invest 500 million EUR in a new plant.” emits a positive CAPEX_INVESTMENT of 500 EUR_MILLION. Similar generic negation handling cannot establish planned/cancelled status. Explicit lifecycle state is not retained in Fact payloads as an independently checked event status.

### S11 — P1: news number units are relabeled without conversion

`src/scrapers/news_scraper.py:1174–1197` normalizes percentage points to PERCENT, USD/t to EUR/UNIT, and USD millions to EUR_MILLION without exchange-rate evidence.

Reproduction: `news_unit_semantics`: “5 percentage points” → `[5, PERCENT]`; “500 million USD” → `[500, EUR_MILLION]`; “USD 80 per tonne” → `[80, EUR/UNIT]`. These are meaning-changing accepted measurements, not merely display labels.

### S12 — P2: news loses time precision, occurrence date, and event coverage

Date parsing at `src/scrapers/news_scraper.py:881`/`:889` returns only a local calendar date, discarding time/offset and not converting to UTC. Fact `event_at` remains null, while `valid_from=event.timestamp` at 1512 uses publication time. `seen_action_keys` (1058) suppresses later same-actor/type actions; `seen_reaction_types` (1151) suppresses later same-type reactions. Input/evidence is silently limited to 2,000 characters at 1487/1509.

Reproduction: `news_date_precision` turns `2026-05-15 00:34:56 +0200` into `2026-05-15` (UTC date should be May 14). `news_event_date_is_publication_date` reports a May 1 event as valid from May 10. `news_several_same_type_actions_drop` retains the EUR 200m German investment but drops the EUR 300m French investment. `news_tail_after_2000_ignored` emits zero facts when the real event follows harmless background text. No limited-coverage marker explains these losses.

### S13 — P1: source-specific statistical acquisition is not implemented

`src/scrapers/statistics.py:468–475` is an unconditional empty `fetch_series` stub. `src/ingestion.py:539–550` sends every key to the same root URL; `bounded_fetch` only performs GET (`:144`). Series/date parameters occur only in a descriptive `canonical_url`, never the fetch request. There is no GENESIS POST payload/auth, Eurostat dataset/selection query, SDMX key/date path, or configured saved-SMARD-export ingestion route.

Reproduction: `statistics_fetch_is_empty` returns 0; `statistics_discovery` shows identical fetch URLs for distinct table/dataset/series selections and no since/until bounds. Fake fetched fixtures make the runner test pass because it ignores the URL. A live call was unnecessary and not performed.

### S14 — P1: official Destatis flat parsing breaks the target's time-series identity

`src/scrapers/statistics.py:216` includes QUARM1 reporting month in the series identity; line 232 uses statistics-family code 61261 as a table ID, omitting requested table and metric code. `normalize_series_response` ignores the requested selection. Table-JSON output uses a third key form at line 307. Registry target uses the exact full provider key (`config/data_sources.json:119`).

Reproduction: `destatis_primary_key_not_normalized_or_selected` parses the repository's official 2025 flat fixture: **984 rows → 984 distinct series IDs**, none equals the registered target. Sample `DESTATIS_61261|QUARM1=MONAT11|...` cannot join Q1/Q2/Q3/Q4 into one target history. Material selection likewise does not filter to the registered product keys. This prevents the real target pipeline from reaching the handcrafted forecasting fixture behavior.

### S15 — P1: Eurostat drops required dimensions and relabels every dataset as construction prices

`src/scrapers/statistics.py:147` hardcodes `sts_copi_q` and a fixed subset of dimensions, omitting NACE/frequency/other provider dimensions. Status is not read; `positions-with-no-data` is loaded but unused at 107. `_make_stats_parser` returns zero SeriesDefinitions/entities at `src/ingestion.py:583–584`.

Reproduction: `eurostat_industry_dimensions_collide` supplies an offline monthly material-PPI dataset with C23=100 and C24=200. Both rows get the **same series_id and observation_id**, labeled sts_copi_q. `eurostat_hardcoded_dataset_and_lost_status` drops provider status `p`. `statistics_series_definitions_missing` commits 8 accepted observations from an official fixture with **zero canonical series definitions**, so metric/geography/base/classification/target definitions do not reach downstream consumers through the required contract.

### S16 — P1: SMARD destroys hourly observations and commits invalid intervals

`src/scrapers/statistics.py:414–418` drops time-of-day and sets end equal to start for same-day slots. Every hour then shares one observation_id and appears to be a revision of the same zero-length period. No timezone/DST normalization or explicit aggregation exists. Neither the adapter nor staging validates these intervals.

Reproduction: `smard_hours_collapse`: all three official-format fixture hours share the same observation_id with start=end `2024-05-01`. `smard_invalid_observations_committed`: real registered runner reports accepted=3, committed=4 (including document) with no error. `validate_candidate` would reject these intervals; that validator is bypassed on this adapter path.

### S17 — P2: SDMX quarterly and missing-value responses are mishandled

`src/scrapers/statistics.py:356` interprets seven-character `2026-Q1` as a month before reaching the quarter branch; `:345` skips missing observations entirely.

Reproduction: `sdmx_quarterly_response_crashes` raises `ValueError: invalid literal for int() with base 10: 'Q1'`. `sdmx_missing_observation_dropped` returns no row for an explicit provider-missing observation, losing missingness/status/revision evidence. Current selected monthly/daily fixtures do not exercise either case.

### S18 — P1/P2: GISCO cannot publish and same-edition corrections reuse immutable IDs

`src/ingestion.py:636` supplies no geography entities. `src/scrapers/geography.py:65` uses GEOGRAPHY → LOCATED_IN → GEOGRAPHY, which is outside the architectural/canonical endpoint contract. Version ID at `:69` contains only subject/edition/level, not geometry/content version. Full geometry is also copied into fact payloads (`:56`) instead of keeping only supported geometry metadata/archived-object references.

Reproduction: `gisco_registered_runner` raises a missing/uncommitted endpoint error. `gisco_invalid_subject_type` reaches the canonical validator with correctly aligned origin and returns `INCOMPATIBLE_RELATION_ENDPOINTS`. `gisco_same_edition_correction_id_collision` changes a coordinate: payload changes but fact_version_id stays identical, so corrected evidence cannot be stored append-only under that version. The HQ helper is tested independently; no ingestion caller uses it to prove actual facility-location enforcement.

## Static acceptance gaps and limits (not live defect claims)

- Legal fixtures `eurlex_sample_act.xml:4` and `eurlex_sample_decision.html:5` still use the old, contradicted real CELEX IDs with handcrafted construction/ETS content and no explicit synthetic provenance manifest. Corrected seeds do not make these official-source extraction proof. No live legal notice was fetched in this audit.
- `tests/test_law_ingestion.py:225` and `:235` still guard future/active assertions with `if ...`, allowing missing phases. The long-PDF test (`:298`) checks article titles, not retained text length or complete extracted/reviewed obligations. There is no demonstrated table-PDF/manual 30-provision assessment here.
- The Task 6 “quality report” test (`tests/test_news_ingestion.py:297–320`) contains **11** hand-authored snippets, tests action/reaction presence, and accepts 0.90 precision/recall; it is not the required held-out 100-item 95% event/entity-link review. It writes no actual reviewed-corpus report.
- The 20-series source-dimension/missing/revision assessment, complete schema/codelist-change quarantine, free-rights endpoint smoke checks, and actual first-release-vintage collection are not established by these suites. Registry historical-vintage fields explicitly remain unverified. Conservative first-fetch availability is good, but cannot manufacture historical training eligibility.
- Date-bounded acquisition, source pagination, legal dependency closure, national implementation coverage, news article rights modes, geography series-coverage links, and source-specific shared-origin lineage are not implemented merely by adapter registration or registry labels. COMEXT trade is later enrichment; no claim is made that absent trade ingestion alone invalidates the initial geography slice.

## Outcome and next action

Only audit probes/results/checksums and this report were written beneath `data/reports/full-audit/sources/`; temporary probe stores clean themselves up. The focused-test isolated store is retained there for traceability. No product fixes or commits were made.

Reopen Tasks 5–7 acceptance, repair source/identity/publication paths with regression cases above, then perform the genuinely missing official/manual/live gates. Do not reinterpret passing fixtures or 0/26 old predicates as proof that collection, source grounding, or the pilot is complete.

Not checked: live endpoints, live rights notices, remote storage, manual pilot review, or paid extraction. Risk: current completion claims overstate safe end-to-end ingestion; accepted false obligations and corrupted event/series identities remain reproducible offline.
