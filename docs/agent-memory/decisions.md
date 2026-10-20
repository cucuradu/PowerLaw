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
