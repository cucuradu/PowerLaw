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

