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
