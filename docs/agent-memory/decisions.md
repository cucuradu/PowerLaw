# Decisions

## Archive Format

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision:** Store durable project memory as concise Markdown under `docs/agent-memory/`; keep raw transcripts outside the repository.
- **Rationale:** Markdown is reviewable, versioned with the code, and does not expose private conversation content through normal project history.
- **Consequence:** Agents must write a structured session summary rather than copying a complete chat transcript.

## Bounded local Germany pilot and model spending

- **Status:** Accepted by the user, 2026-10-19.
- **Decision:** Use ADC for the existing Google Cloud project, 3.8 Flash globally and a separate regional client for optional 768-dimensional text embeddings. Keep the pilot in `data/pilot-germany` and preserve the legacy stores.
- **Budget:** EUR 100 overall authorization, converted at the verified ECB reference rate with a 10% FX/billing reserve; initially USD 1 per run. One immutable policy in the same SQLite ledger counts spending and unknown reservations across all runs/restarts. This is application accounting, not a billing-account hard limit.
- **Source scope:** Enable only verified exact statistical selections and the tested official RSS endpoint. Keep unsupported sources disabled and preserve pilot series/quality gates.
- **Consequence:** Preserve the spending ledger when relocating/restoring the pilot, verify rates when changing model/region, and never infer completed extraction from a swallowed technical failure. Historical reference periods downloaded today do not become historical release vintages.

## Application data must not fall back to fabricated examples

- **Status:** Requested by the user, 2026-10-19.
- **Decision:** Application code does not seed synthetic laws, news, market histories, embeddings, or forecasts in any mode. Missing measurements remain unavailable, including dashboard exports and quantitative summaries. Test datasets belong under `tests/` and are loaded explicitly into temporary storage.
- **Consequence:** Do not restore demo fallbacks to make empty dashboards or tests appear complete. A seed batch name cannot bypass canonical evidence checks. Preserve existing unclassified archives outside active paths until their provenance is established.

## Target availability from evidenced release dates

- **Status:** Accepted by the user ("recommend me the best way"), 2026-10-20.
- **Decision:** Historical availability of the target (Destatis 61261-0002) comes from the Bundesbank macroeconomic real-time database (BBKRT, PP300): one archived document per Destatis release (84 vintages, 2006-01-06..2026-10-09), dated end of the Berlin release day. Each target quarter is public from the first vintage containing it; quarters before 2005-Q4 use the first vintage (2006-01-06) as an upper bound. Values remain the latest committed Destatis values.
- **Evidence:** vintage 2025-04-10 equals Destatis press release PD25_137 (10 April 2025); release lag 2-13 days after quarter end; PP300 QoQ growth revisions first->latest: mean 0.06 pp, max 0.35 pp. PP300 itself differs from the target (up to 1.7 index points) and is never used as a value or label.
- **Store rule:** in public_as_of, a series definition is public no later than its earliest archived-release/provider-vintage observation; system_as_of keeps registration time.
- **Consequence:** labels and features are release-dated latest-vintage values, not first-release values; report that and the revision bound with every evaluation. Ingest vintages oldest first: a later-first order only dates quarters later (never leaks).

## Predictor availability: evidence first, flagged lag rules in the model layer only

- **Status:** Accepted by the user, 2026-10-20 ("go both as recommended").
- **Decision:** Predictors with archived release vintages are release-dated in the store like the target (Bundesbank real-time `IO1/AFC15` dates residential order intake 44111-0009). Predictors without vintages get a conservative publication-lag rule from official release timing in `config/predictor_release_lags.json`, applied only when building model inputs; the store stays evidence-only. Results are always reported with and without rule-dated predictors.
- **Outcome (2026-10-20 backtest, 54 origins 2013-2026):** order intake helps slightly (MAE 0.671 vs 0.702 for the same model without it; not significant). Rule-dated predictors make the model worse (0.752; interval coverage 0.70), so they stay out of the forecast.
- **Consequence:** The VAT-aware AR model remains the primary forecast; the order-intake ARX is a candidate. Neither fixes the 2021-23 cost shock (~2 pp error).

## Dashboard forecast: offline record, next-release target

- **Status:** Accepted, 2026-10-20 (implements the user-approved next step).
- **Decision:** `python -m scripts.ops.backtest_target` writes `data/<pilot>/reports/forecast-latest.json`; the dashboard only reads it (AGENTS.md: pages never train). The shown forecast is growth of the next unreleased Destatis quarter, evaluated at origins 20 days into each quarter (82 origins 2006-2026), the same situation a user is in.
- **Model:** `arx_vat_evidence` (VAT-aware AR + order intake + material prices, both dated by Bundesbank real-time vintages), chosen on the 2-quarter-ahead run (MAE 0.638 vs 0.711, DM p = 0.07). For the next release it ties the simpler VAT-aware AR (0.495 vs 0.497), so the dashboard also shows the reference forecasts.
- **Test statistic:** Diebold-Mariano uses Bartlett-weighted Newey-West variance; the unweighted version gave p = 0.001 for the same comparison because forecast errors are negatively autocorrelated.

## Forecast inputs, events and the Bundesbank bot check (2026-10-20)

- **Model:** the published forecast (`arx_vat_selected`) uses VAT, season, orders, material and energy prices (Bundesbank-dated) and builders' price expectations from the EU construction survey (published at month end; taken as public 10 days after). Inputs are flagged `in_forecast` in `config/predictor_release_lags.json`. Best on both backtests: next release MAE 0.446 vs 0.502 without inputs (DM p = 0.054, coverage 87%); two quarters ahead 0.608 vs 0.747 (p = 0.075; 2021-23 shock 1.70 vs 2.16). Several input sets were tried, so treat the gain as promising rather than proven.
- **Events:** `config/price_events.json` lists dated, sourced events. The dashboard shows them as timing only (never as causes); the model's largest misses are listed with events from the six months before.
- **Graph:** the Graph Explorer defaults to a "Forecast drivers" view built from config; the stored-records view uses plain names and hides retired series. The GNN stays paused.
- **Bundesbank:** after ~1,500 requests on 2026-10-20 the API answered with a bot challenge (303 to `/.enodia/challenge`). Never work around it. Weekly updates fetch only the last 45 days of vintages; `scripts.ops.redate_from_archive` re-dates from archived vintages without network.

## Sector network and laws (2026-10-20)

- **Relations are facts with evidence:** supplier/buyer links come from the Eurostat input-output table (FLOWS_TO, one fact per year), series-to-node links from `config/series_names.json` (MEASURES/BELONGS_TO, claim_kind `catalog_mapping`), German law links only when a configured quote is found in the official text, EU law links from Gemini-extracted rules that pass the deterministic grounding check. Everything else stays needs_review and is not shown in the network.
- **One naming source:** sector and commodity entities are built only through `src/utils/taxonomy.py` from `config/sectors.json`; entities are immutable, so never rename an existing code. Law entities are reused if already stored.
- **Paid extraction:** run manually with `python -m scripts.ops.extract_laws` (per-run budget unchanged, USD 1; total ceiling EUR 100); segment answers are cached so interrupted laws resume. The weekly update does not call Gemini.
- **Projections:** model chosen on origins before 2018, scored on 2018+; the dashboard states per series whether it beat "no change".

## Trades layer and tested links (2026-10-20)

- **Trades:** the target's 41 trade price indices (61261-0002, excluding VAT, 1958+) are stored and release-dated with the target. Their official weights (base 2021, per mille) come from the IT.NRW report PDF (source `STATISTISCHE_BIBLIOTHEK`), stored as COMPONENT_OF facts with the table row as evidence; weights x national trade indices rebuild the national index within 0.13 points (2021-Q1..2026-Q3).
- **Material -> trade links are model output, not facts:** experts propose them (`config/trade_inputs.json`), `scripts.ops.estimate_links` tests each against the price data controlling for the common trend; the network shows only proposed and supported links (14 of 42). An unrestricted scan "found" 375 links before the common-factor control and still finds implausible ones after it, so data-only edges are not accepted.
- **Events are not training nodes:** one-off shocks (Covid, Ukraine) enter models only through their measured channels (energy, materials, survey); they stay as dated annotations for explanation and regime labels.

## EU country panel and cost chains (2026-10-20)

- **Country is an attribute, not a node:** the EU panel (`scripts/ops/build_eu_panel.py`: 27 countries x new-home prices and costs, construction labour cost, wood/mineral/metal/energy producer prices, builders' survey) registers Eurostat series that measure the same sector nodes as Germany's; the series' geography says which country. A training graph builds (country, sector) nodes from that; a country hub node would mix unrelated sectors. Panel series are optional in a response (a country that does not publish one is not an error); German series stay required.
- **Germany-only views:** the network view, outlook context, projections and link tests use `is_german_series` (`src/utils/taxonomy.py`).
- **Pooling result (`scripts.ops.panel_backtest`, 877 country-quarters, 14 countries with all inputs, origins 2010+, model comparison only, release timing ignored):** per-country AR MAE 1.165; fully pooled 1.107 (p = 0.23); partial pooling (each country's fit shrunk towards the pooled fit, prior worth 40 quarters) 1.034 (p = 0.0004), better in 11 of 14 countries; Germany 0.486 vs 0.515. Full pooling hurts stable long-history countries (DE, AT, CZ), so any panel or graph model must keep country-specific parameters (partial pooling / node embeddings).
- **Chains:** `config/chains.json` lists the cost chains (energy, carbon, world commodities, labour, finance, public demand, subsidies, standards, coal exit/gypsum, logistics, taxes, cross-border) with mechanism, expected lag, data source and status. A link becomes a training edge only with official accounting support or a passed test.

## Policy numbers and rule identity (2026-10-21)

- **Policy numbers are series quoted from law texts** (source `LAW_PARAMETERS`, no LLM): each value is stored only if its quote is in the fetched official text; otherwise needs_review. In the store they are fetch-dated; `known_from` in the observation lineage is the date a model may assume: the promulgation date when the act itself prints it (Directive 2023/959: OJ 16.5.2023), else 1 January of the year the value applies (BEHG amendments). Stored: BEHG CO2 price 2021-2026, CBAM factor 2024-2034. VAT stays in `config/de_vat_schedule.json`.
- **Rule IDs are stable:** `rule_<celex>_<article>_<hash>` from law + article + paragraph (from the evidence anchor) + action + operator + target variable; value, dates and scope are excluded so an amendment is a new version of the same rule. Equal keys get an ordinal by content. The rule entity's metadata names its law; the LAW node stays in the knowledge layer (amendments, IMPLEMENTS, in-force dates) and is not a training hub.
- **Revisions retract the whole rule:** a law re-read now also retracts facts whose subject is one of its rules (previously only law -> rule facts were retracted, leaving rule -> sector links live), and a re-read of the unchanged document (same date) supersedes what it no longer yields.
- **Migration:** `python -m scripts.ops.rekey_rules` re-keys stored rules from their stored payloads and the archived law text (no Gemini). A first attempt on 2026-10-21 left old and new IDs side by side (retraction needed `<=`); the store was restored from `data/pilot-germany/backups/before-rule-rekey-20261021` and the failed copy deleted. Applied 2026-10-21 (store generation 64): 365 rules, no old-style IDs live, live facts per law/predicate/quality identical to before (1,545). The script's done-check uses the store's tie-break (date, then record time), so reruns skip all laws.

## World commodity prices (2026-10-21)

- **Source:** World Bank Pink Sheet monthly workbook (`WORLD_BANK_PINK_SHEET`): Brent, European gas, Australian coal, aluminium, iron ore, copper, zinc, 1960+ (5,502 values). Licence: World Bank Dataset Terms default CC BY 4.0 (no other label; attribution "The World Bank: Commodity Price Data (The Pink Sheet)"). The file URL changes monthly, so discovery reads the official landing page and requires exactly one monthly link. Read with the standard library (xlsx is zipped XML). Iron ore before 2008-12 is annual contract prices (series break).
- **Euro conversion:** ECB monthly EUR/USD (`EXR.M.USD.EUR.SP00.A`, 1999+) replaces the retired daily series; ECB requests get a 120 s read timeout (`read_timeout_seconds`), as the API answers in about 45 s.
- **Tested chain links** (`scripts.ops.estimate_links`, quarterly growth in euros, common-trend control, 2005+): supported: oil -> diesel (pass-through 0.65), bitumen, plastic pipes, insulation boards; world gas -> German gas; zinc -> sheet-metal work. Not supported: aluminium -> metal windows, copper -> plumbing. Untestable: iron ore -> steel and copper -> cables (no German series, or only since 2021). Statuses are in `config/chains.json`; supported links show in the network view.

## Pooling for the German target (2026-10-21)

- **Candidate `ar4_seasonal_vat_pooled`** (`panel_prior` in `rolling_origin_backtest`): Germany's seasonal AR on growth excluding VAT, with lag and season coefficients pulled towards a pooled fit on the other EU countries' new-home price growth (prior worth 40 quarters; intercept free). Other countries' quarters count as public 100 days after quarter end (rule; Eurostat publishes about 90 days after).
- **Result (82 origins 2006-2026):** two quarters ahead 0.576 vs 0.596 for the same model without pooling; next release 0.507 vs 0.497. No gain worth having: Germany's long own history already fixes its dynamics. The published model (`arx_vat_selected`, next release 0.446) is unchanged.
- **Consequence:** the panel's value is for countries with short or volatile histories and for cross-country (graph) models, not for Germany's univariate dynamics. A graph model must beat `arx_vat_selected`, not the AR baseline.

## Cross-border supply (FIGARO) (2026-10-21)

- **Stored:** Eurostat FIGARO product-by-product tables `naio_10_fcp_ip1`-`ip4` (2010-2024): construction in each EU country, 11 material and energy products, every origin country supplying at least 1% (19.9k FLOWS_TO facts). Countries are payload (`origin_geo`, `dest_geo`, `scope` "<origin>><dest>"), so these facts never merge with Germany's national input-output facts; the network view uses only facts without `scope`.
- **Test** (`scripts.ops.estimate_links`, `cross_border`): German producer prices of wood, minerals and metals vs. foreign EU producer prices weighted by the previous year's FIGARO import shares, with the common-trend control. Pass-through about 1 and p < 0.001 for all three, but an equal-weights placebo fits as well or better (R2 gain 0.17/0.19/0.21 vs 0.15/0.14/0.21). Verdict "co-movement only": German material prices move with European producer prices, and trade weights add nothing at this level of detail.
- **Consequence for a graph model:** an EU sector-average input captures this; trade-weighted cross-border edges are not justified by the data yet (retest with product detail or import price indices).
- **Store open:** fact copies are shallow (all fields immutable; guarded by a test): opening 8.1 -> 7.3 s. The rest is table reads and record sanitising (open issue).
- **Minimum wage (2026-10-21):** `POLICY|DE|MiLoG|minimum_wage` from MiLoG section 1 (12.00 from 2022-10, used for 2023), MiLoV4 (12.41, 12.82) and MiLoV5 (13.90, 14.60 for 2027); earlier rates are not in current texts. A series quoted from several laws fixes `dataset_id` and `dimensions` in config so each law yields the same definition. Fixed with it: law-number series sent `adjustment=None`, which reads back as "NSA", so every re-ingestion conflicted; now the default is sent (test re-ingests after a store round trip).
- **EU material prices as a forecast input (2026-10-21, rejected):** mean of mineral and basic-metal producer prices in AT, CZ, DK, ES, IT, NL, PL, SE (Eurostat sts_inpp_m, rule-dated 55 days after month end, momentum_3m). Two quarters ahead alone 0.788 vs 0.788 without inputs (45 origins); added to the published inputs 0.634 vs 0.626; next release 0.459 vs 0.448 with coverage 0.81 vs 0.87 (67 origins). German producer prices already carry the European signal. Not kept in the config, so the published comparison keeps its full sample.

## News coverage from GDELT (2026-10-21, source built, disabled)

- **Source `GDELT_DOC`** (counts only, never article text; GDELT terms: free incl. commercial use, cite and link gdeltproject.org): six German topic queries, daily matches / all monitored articles, stored as monthly articles per million. Requests paced (`min_request_interval_seconds`, no retries).
- **Access:** one full-history timeline succeeded (material shortages, 2017-01..2026-10); every further request was refused with HTTP 429 at 15 s and at 5-minute spacing, so the source is disabled. Article lists for hand validation could not be fetched.
- **Validation of the one series:** correlation 0.68 (2017-01..2023-04) and 0.71 (to 2026-09) with the EU survey share of German builders short of materials; both peak in 2021 and fall to near zero after 2023, so the post-2023 zeros look real rather than a coverage break (GDELT's all-language article total did fall from 23M to 4M a month).
- **Consequence:** this topic duplicates the survey already in the forecast. News would need another source with history (or GDELT bulk access) and should target what surveys miss (policy announcements, plant closures, strikes).

## Interval calibration methods (2026-10-21)

- **Compared** (`intervals_by_method` in every backtest report): split conformal on all past errors (published), the last 40 errors, and adaptive conformal inference (ACI, step 0.02). Published model, next release, 62 scored origins: coverage 87.1% for all three, mean half-width 0.80 / 1.05 / 1.14 pp. The VAT-aware AR baseline gains from adapting (83.9% -> 88.7%) but at wider intervals.
- **Decision:** keep split conformal for the published interval. The shortfall (8 misses vs 6 expected) is within sampling noise (+-4 points) and the adaptive methods widen intervals without catching the 2021-23 shock quarters.

## Training graph export and the graph-signal test (2026-10-21)

- **Export** (`python -m scripts.ops.export_training_graph`, to `data/<pilot>/datasets/training-graph-<date>/`): 339 series nodes (no law, event or company nodes), 38,685 quarterly values each with the time it was public (archived release date when an archived-release or provider-vintage document supports the value, else period end + a per-source lag rule; law numbers from the period start), and edges: cost_share 546, component 108, belongs_to 42, tested_link 20, acts_on 6, same_sector 6,162. FIGARO weights are excluded. Check: no non-policy value is public before its quarter ends; the target's 2026-Q3 carries its release date 2026-10-09.
- **Graph-signal test** (`scripts.ops.graph_signal_test`, next-quarter growth, partial pooling, origins 2012+, release timing ignored): structural neighbours (29 German nodes) 1.355 vs 1.349 for own AR (p = 0.70, 13/29 improved); same sector in other EU countries (154 nodes) 2.074 vs 2.131 (p = 0.057, 109/154 improved) but not for German nodes (1.775 vs 1.763).
- **Consequence:** there is no linear lag-1 signal in Germany's structural edges beyond each series' own history, and only a weak peer signal elsewhere. The GNN stays paused for the German target; if trained, it should start on the EU panel with peer edges and must beat partial pooling there and `arx_vat_selected` for Germany.


## Pooled projections for trades and materials (2026-10-21)

- **Change** (`scripts.ops.project_series`): a fifth candidate, seasonal AR partially pooled (prior worth 40 quarters, free intercept) over the other German series of the same metric, using only their quarters public at each origin (test with a mutation check). Pools: 37 trade output prices, 32 material producer prices; singleton metrics (building cost, wages) get no pooled candidate. Model choice stays on origins before 2018.
- **Result (2018+, out of sample):** pooled beats own seasonal AR for 27/32 material prices (2.046 vs 2.293) and 25/37 trades (0.837 vs 0.868). Chosen for 38 of 72 series; mean error of the chosen models 1.503 -> 1.407, and 56 instead of 52 beat "no change". Caveat: the per-series Wilcoxon p-values (< 0.01) treat series as independent although their errors share shock quarters.
- **Intervals:** split conformal covered 78% on average over these series (28 of 72 below 75%), the last-40-errors window 79%, ACI 83% (2 below 75%) at wider ranges (mean half-width 2.9 vs 2.1 pp). Projections now use ACI; the published target keeps split (no gain there, see "Interval calibration methods"). Still short of 90%: the shock quarters dominate.
- **Consequence:** the panel helps where single series are short or noisy, as on the EU panel. This is the graph's first measured use for Germany: same-metric peers as a shrinkage prior, not as lagged inputs.

## Trade forecasts with inputs (2026-10-22)

- **Test** (2018+, next release, inputs at their release dates): the published target model's inputs (orders, material and energy producer prices, builders' price expectations) in a direct ARX for each of 37 trade price series (61261-0002, excl. VAT): 0.742 vs 0.847 for the trade's own direct AR (34/37 better) and vs 0.819 pooled (30/37). Each trade's own tested material links (graph `tested_link`, 10 trades) instead: 1.164 vs 1.056, worse for 8 of 10. Same-period pass-through does not make a material a useful leading input for a single trade; the broad cost and demand signals do.
- **Change:** `project_series` offers that ARX (`arx_vat_inputs`) to trade and producer prices (building-cost indices may include VAT, so no inputs there); `rolling_origin_backtest` now fits predictor models without a VAT schedule on the series' own growth. Per-series choice on origins before 2018: trades 0.834 -> 0.774, material prices 2.111 -> 2.103, all 72 series 1.407 -> 1.373.
- **Selection limit:** before 2018 the ARX and the pooled AR tie for trades (0.324 vs 0.322); the ARX only pulls ahead in the 2021-23 shocks (0.744 vs 0.839). Choosing one model per metric on pre-2018 data would pick pooled (0.837), so the per-series choice stays; a combination rule would be chosen after seeing the test and is not adopted.

## Forecast ledger: prospective evaluation (2026-10-22)

- Every 2018+ comparison since 2026-10-20 reused the same test window, so later gains are partly selection on it. From now on the evidence that counts is prospective: `scripts.ops.forecast_ledger record` appends each published forecast (target, projections, 1-3 year forecasts) to `reports/forecast-ledger.jsonl` once per series, model, base and target quarter, each line chained to the previous one by SHA-256; `score` scores only outcomes first released after the forecast was made. The weekly update runs both.
- Rule-dated inputs now count as public at the rule date or their fetch time, whichever is earlier (the fetch proves publication). History keeps rule dates; the live forecast no longer treats a fetched quarter (building cost 2026-Q2) as unreleased.
- Review cadence: model or input changes only at scheduled reviews (proposed: April and October), judged on the ledger.

## 1-3 year forecasts: protocol (fixed 2026-10-22 before the first run)

- **Need:** budgets and escalation clauses price 1-3 years ahead, so the product question is the cumulative change of the level 4, 8 and 12 quarters after the last released quarter (`src.quant.horizon`, `scripts.ops.horizon_forecast`).
- **Series:** the target (incl. VAT, modelled ex VAT with known VAT steps) and the 41 trade price series (excl. VAT).
- **Models:** no change, 10-year drift, seasonal AR iterated, direct AR (h-quarter growth on last year's growth), direct AR + the published model's inputs.
- **Split:** origins 1998-2012 to choose (target: lowest MAE per horizon; trades: lowest mean MAE, one model for the group); origins 2013+ held out, scored once with the chosen model. Bug fixes may rerun; the model list, inputs and choice rule do not change after seeing the holdout.
- **Known weakness:** 12-quarter labels overlap, so 2013-2023 holds about 4 independent 3-year windows for the target; the trades add cross-section but share the same shocks. The design was set knowing that 2021-23 had a shock.

## 1-3 year forecasts: held-out result (first run 2026-10-22, per the protocol above)

- **Target** (MAE in points of cumulative change; development 28 origins, holdout 2013+):

  | horizon | chosen on 1998-2012 | holdout chosen | drift_10y | direct_inputs | no change | coverage (90%) |
  |---|---|---|---|---|---|---|
  | 1 year | sar | 2.75 | 2.75 | 2.56 | 4.84 | 75% |
  | 2 years | sar | 7.78 | 5.72 | 5.83 | 10.3 | 73% |
  | 3 years | sar | 15.33 | 9.22 | 10.39 | 16.56 | 44% |

  Diebold-Mariano (overlap-corrected) finds no significant difference from no change or drift at any horizon.
- **Trades** (35 series, one model for the group): 1 year direct_inputs 2.50 (drift 2.79, no change 4.90); 2 years drift 5.97 (inputs 5.96); 3 years drift 9.66 (inputs 10.58). The iterated seasonal AR explodes for some trades at 2-3 years (mean holdout error in the hundreds); it was not chosen there.
- **Reading:** the calm 1998-2012 period chose the seasonal AR for the target, which extrapolates recent momentum and failed after 2021; the trend and the input model held up better. At 2-3 years nothing beats a 10-year trend reliably, and intervals calibrated on calm years held 44-75% of the time. A 1-3 year price path is mainly an inflation-regime question; the honest product is a trend with scenario ranges, not a point forecast.
- **Per protocol the chosen models stay published** (dashboard section "Construction prices over the next 1-3 years" shows the trend's holdout error beside them) and every forecast goes into the ledger. Changing the choice rule (e.g. to the trend or a combination) is for the next scheduled review, and the holdout can no longer test that choice; only the ledger can.

## Outside forecasts as benchmarks (2026-10-22)

- **Source:** DIW Berlin's annual construction-volume report (Wochenbericht, January), the only free German construction price forecast with a multi-year history found. Entered by hand in `config/external_forecasts.json` with URL, quote and a check level: residential price row for editions 2023-2026, total construction prices for 2019, 2020, 2022 (no residential price row then); 2021 conflicts with its own text and is not scored; 2014-2018 could not be read (HTML abstract only, PDFs not machine-readable here). Publication dates are approximate (early January).
- **Comparison** (`scripts.ops.benchmark_external`): our models forecast the same annual-average change from the data public on each publication date; outcome = the target index's annual-average change as released now. DIW forecasts the construction-volume price deflator, not this index, so the comparison measures usefulness for our target, not DIW's accuracy on its own measure.
- **Result** (MAE, points): current year (6 editions) DIW 2.72, 10-year trend 2.73, seasonal AR 2.68, inputs model 2.49; next year (5) DIW 3.53, trend 3.22, seasonal AR 4.45, inputs model 3.88. Nobody foresaw 2021-22 (DIW 3.1 for 2021, actual 9.2; 10.1 for 2022, actual 16.3). The samples are tiny; the reading is "on par with the published expert forecast", not "better".
- **Live (January 2026 edition; our models from the data public that day):** DIW 2.8% for 2026 and 2027 (residential); seasonal AR 2.9 / 2.7, inputs model 3.5 / 4.6, trend 4.3 / 5.5.

## Ranges for 1-3 years: history instead of model errors (2026-10-22)

- **Test** (target, origins 2013+): the 5th-95th percentile of every 4/8/12-quarter change since 1968-07 public at the origin (VAT steps as known) held 88% / 85% / 77% of outcomes, mean width 10.9 / 20.9 / 28.0 points; the model-error (split conformal) intervals held 75% / 73% / 44% at about twice the width at 2-3 years.
- **Decision:** the target's published 1-3 year range is the history range (`range_basis` in horizon-latest.json); the model-error interval stays in the report. Correction (same day): the trades' ranges first used history from 1995 only (a reused fit setting; the trades go back to 1968) and held 67% / 52% / 38%; with 1968+ they held 84% / 81% / 77% vs 80% / 72% / 56% for model errors, so the trades use history ranges too. The choice was made after seeing the 2013+ holdout, so the ledger is the only clean test from here; the first ledger entries (2026-10-22) still carry model-error intervals because the first forecast per slot stands.
- **Reading:** the range describes what 1-3 years have done since 1968 (low about 0%, high +11% / +23% / +32%), not uncertainty around the point forecast. Scenario work (inflation regime, CO2 price path, wage settlements) would narrow it; not started.

## EU-wide 1-3 year model for the German target (2026-10-22, exploratory)

- **Test** (scratch, not in the pipeline): one direct regression of h-quarter log growth on [1, last-year growth, last-10-year mean growth], fitted on every country-quarter of the 26 other EU new-home price series (rule-dated, quarter end + 100 days) plus Germany ex VAT, with outcomes public at each origin; same German origins as `horizon_forecast`.
- **Result** (MAE): 1 year dev 1.25 (best of all), holdout 2.67 (trend 2.75, seasonal AR 2.75, inputs model 2.56); 2 years holdout 5.91 (trend 5.72, seasonal AR 7.78); 3 years 9.88 (trend 9.22, seasonal AR 15.33). The country cross-section does not beat the trend at 2-3 years but never fails the way the German-only AR did; had it been a candidate, the development rule would have chosen it at 1 year. Exploratory and run after the holdout was seen: a candidate for the April review, not a result.

## Index hierarchy: adding up the trades (2026-10-22)

- **Test** (`scripts.ops.hierarchy_test`, next release, the published backtest's 82 origins): each of the 34 weighted trades (99.5% of the weight) gets its own model chosen before 2018 as in `project_series`; growths are aggregated by weight x level at the base quarter (aggregation error with actual trade growths: 0.05 points), then the known VAT step. From 2018 (trade choice out of sample): direct published model 0.655, bottom-up 0.672, 50/50 average 0.648; all 82 origins 0.446 / 0.431 / 0.431; Diebold-Mariano p 0.88 / 0.67.
- **1 year** (scratch, horizon protocol origins): bottom-up of the trades' inputs models 2.42 on 2013+ vs 2.56 for the target's own inputs model and 2.75 for the published seasonal AR; development 1.47 vs 1.50 (seasonal AR 1.35). Found after the holdout was seen: a candidate for the April review.
- **Reading:** the hierarchy is consistent and costs nothing, but the trades carry little information the total lacks; at best a small gain at 1 year. Not adopted.

## Cost build-up: prices follow costs (2026-10-22)

- **Ceiling test** (target, 1-3 years, origins 2013+): a fixed pass-through, price change ex VAT = 0.5-0.6 x material cost change + rest x labour cost change (Destatis 61261-0014 components, 2000+), applied to the costs that actually followed, misses by 1.49 / 2.92 / 4.73 points vs 2.81 / 5.80 / 9.33 for the 10-year trend. A regression fitted on pre-2013 windows does much worse (2.45 / 5.41 / 8.28): calm years cannot identify the pass-through. The material weight (0.6) was chosen on pre-2013 origins; 0.5-0.7 are all within 0.1 point.
- **What is knowable:** with labour known and materials trended 2.62 / 5.26 / 8.31; with materials known and labour trended 1.95 / 4.30 / 7.34; neither 2.81 / 6.01 / 10.0. Material costs carry most of the uncertainty; knowing labour (signed wage agreements) is worth 7-10%. Agreed construction wages (62221) as the labour proxy did not help in a fitted regression.
- **Forecast vs what-if:** as a forecast (median material path, labour trend) the build-up ties with the trend (2.97 / 6.34 / 9.99) and its 10th-90th material scenarios held only 69% / 49% / 37% (material history before 2013 is short and calm; producer prices 1976+ as a longer history did worse because they rise more slowly than the cost index). So it is published as what-if rows (`scripts.ops.cost_scenarios`, dashboard "What would move it"), next to the history range, and its central path goes into the ledger as `cost_central`.
- **Consequence for the graph:** this is the graph's real job for 1-3 years: it turns the question into cost paths. The open problem is forecasting material costs (and energy) at 1-3 years, not the price model.
- **Hierarchy and EU-wide model** (sections above) stay review candidates; nothing here changes the published models before the April review.

## April 2027 review rule (fixed 2026-10-22, before any ledger forecast is scored)

- **Why now:** by April 2027 the ledger will hold only two scored target releases, too few to choose models; every other piece of evidence (2013+ holdout, DIW comparison) has already been seen. Writing the rule now stops the review from becoming another round of choosing on seen data.
- **1-3 year point forecast (target):** replace the per-horizon choice with the equal-weight average of three forecasts: 10-year trend, EU-wide model (`scripts/research/global_horizon.py`, moved into the pipeline), and the target's inputs model. Averaging needs no selection and none of the three failed badly on 2013+.
- **1 year:** add the trade bottom-up (inputs models) as a fourth member of that average only at the 1-year horizon.
- **Ranges:** history ranges since 1968 stay (target and trades). Cost what-ifs stay as published.
- **Next quarter:** `arx_vat_selected` stays unless the ledger shows its error above 0.70 (the 2018+ backtest level) over at least 4 scored releases; then fall back to the VAT-aware seasonal AR.
- **Afterwards:** changes only at October and April reviews, each decided by a rule written before the ledger data it uses.

## Validity of the cost index and material cost forecasts (2026-10-22)

- **Not circular:** Destatis states the building-cost index measures contractors' outlays, not builders' prices. Quarterly changes of its material component track material producer prices (correlation 0.76 for 2000-2020, 0.97 from 2021) more closely than construction prices (0.60, 0.92); its labour component tracks Eurostat's construction labour cost index (annual changes 0.89) and is unrelated to construction prices (0.11). No sign that it is derived from the price index.
- **Wage agreements:** agreed construction wages (62221) track the labour cost component only loosely (annual changes 0.41), so signed agreements would capture part of the 7-10% "labour known" gain at most. Deprioritised.
- **Material costs 1-3 years** (`scripts/research/material_cost_forecast.py`, dev only 9 origins): direct regressions of the material cost change on its last-year change plus one input set (euro Brent and metals, energy producer prices, builders' surveys). Equal-weight average of the four (no selection) on 2013+: 2.46 / 4.66 / 6.85 vs material trend 3.15 / 5.87 / 7.93; all inputs in one regression overfit beyond 1 year (9.69 at 3 years).
- **Into the price:** the cost rule with these material forecasts (`cost_inputs`, `scripts.ops.cost_scenarios`) misses by 2.76 / 5.97 / 9.89 vs the target trend 2.81 / 5.80 / 9.33. Better material forecasts barely reach the price: labour on trend and margin swings dominate once materials are not known exactly. Recorded in the ledger as a candidate and shown on the dashboard as the first what-if row; not a published forecast.

## Per-trade cost exposure (2026-10-22)

- **Estimate** (`scripts.ops.trade_cost_sensitivity`, weekly): each weighted trade's annual price change on the annual material and labour cost changes of the same year (2001+), pulled towards the pooled fit (prior 40 windows). Pooled: materials 0.63, labour 0.57 (sum above 1: prices outran costs in 2021-23). Highest material exposure: structural steelwork 0.90, carpentry/timber 0.87, concrete 0.82; lowest: wallpapering 0.42, natural stone 0.43, tiling 0.44.
- **Check** (costs known, fitted before 2013, scored 2013+): trade-specific 2.20, pooled 2.20, single 60/40 rule 1.88 points. Calm years cannot identify pass-through (same as for the target), so the per-trade figures are shown as an exposure ranking (dashboard expander "Which trades move most with building costs") with that caveat, not as validated what-ifs.

## Shadow models for the April review (2026-10-22)

- `scripts.ops.horizon_forecast` now computes, for the target, the EU-wide model (`src.quant.horizon.panel_direct`, moved from `scripts/research/global_horizon.py`) and the review average (trend, EU-wide, inputs model) at every origin and live. They are written to horizon-latest.json with `"shadow": true`, logged in the ledger, and not shown as the forecast. 2013+ errors (seen data): review average 2.43 / 5.66 / 9.67 vs published seasonal AR 2.75 / 7.78 / 15.33 and trend 2.75 / 5.72 / 9.22.
- The April 2027 review compares the ledger entries of `sar`/`direct_inputs`/`drift_10y` (published per horizon), `eu_panel`, `review_average`, `cost_central` and `cost_inputs`; the decision rule stays as written above.

## Web app simplified (2026-10-23)

- `dashboard.py` is now only the menu (st.navigation): Overview, Laws, Graph, Data, Ingestion. News Feed (source disabled) and GNN Lab (paused) stay as files but are not in the menu.
- `overview.py` replaces the old home page (KPI telemetry, power-law, architecture and "GNN impulse" panels removed): headline cards (latest, next quarter, 1-3 years), index chart with the forecast path and history range, cost what-ifs, accuracy relative to "no change", trades/materials next quarter and per-trade cost exposure.
- Backend: the overview reads only report files through `src.storage.home_data` (one cache keyed on file times); `backtest_target` now writes the quarterly history into forecast-latest.json. Overview cold load 2.6 s (was about 12 s), warm 0.3 s. The Laws page caches its data per store generation (warm 20 s -> 1.4 s). The first store-backed page opened still pays the store open (about 20 s).

## Graph and Laws pages rebuilt; obsolete pages removed (2026-10-23)

- **Menu:** Overview, Graph, Laws, Ingestion (`dashboard.py` routes to `views/`; pages live outside `pages/` so Streamlit's file-based pages cannot bypass the menu). Removed: News Feed (source disabled), Data/Temporal Table (empty), GNN Lab (paused), the old Law Registry and Graph Explorer.
- **Graph snapshot** (`scripts.ops.build_graph_view`, weekly): every law, rule, sector, trade, material and series as nodes with descriptions; relations with weights, years and the evidence quote: law contains rule, implements, amends, regulates, input-output supplies/buys (all suppliers and buyers per year, 2010-2022: Eurostat's tables in this classification start in 2010), trade weights, product classification, series measures, law sets number, tested price links, cost-chain steps (proposed stages without data shown as hollow "planned" nodes). Cross-border FIGARO flows are counted, not drawn.
- **Graph page:** two views. Store map: five zones as bubbles (counts, bundled relations); click to open a zone. Relations: rings around any node (inputs left, buyers right) or one cost chain left to right with step status. Click a node or arrow for details (description, parameters, relations, evidence quote, source); laws open in the Laws page. vis-network vendored in `src/ui/vendor` (MIT/Apache) and drawn through `st.components.v2` with Python-computed positions, so a click does not reshuffle the picture.
- **Laws page:** one card per law from `config/law_profiles.json` (hand-written description, how it reaches prices, type, year, channel, relevance), plus what the store holds: rules (if/then, binding), implements/implemented by, regulates, numbers the law sets (with quotes). `32015L1814` is flagged: a decision's CELEX type is D.
- **Ingestion page:** read-only apart from the schedule toggle (the old job-enqueue buttons are gone: pages must not ingest).

## Graph page: Architecture view (2026-10-23)

- The Graph page opens on an **Architecture** view: one box per kind of node (per zone) with its count, one arrow per kind of relation with how many exist. It is computed from the same snapshot as the Store map and Relations views, so it cannot drift from the data (the old home-page "dual-layer architecture" banner was hand-written and is not coming back).
- Clicking a box lists its members (open one in Relations, or open the zone on the Store map); clicking an arrow lists the relations behind it.
