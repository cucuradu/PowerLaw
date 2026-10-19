# Source and Gemini readiness

Decision: **ready for controlled diagnostic tests, not for unattended ingestion or reliable forecasts**. Diagnostic testing has started. Existing credentials are sufficient for the first sources; remaining blockers include configuration and real integration defects, not just missing keys.

## What was actually checked

- Fresh isolated offline run: **147 passed, one warning in 26.74s**, exit 0. This covers continuation/canonical/label/temporal tests, API/budget tests, sources, runner, pilot, operations and restore. External I/O blocked; no source files changed during the run. The other session's reported full 471-test pass was not repeated here.
- Google ADC refresh succeeded, its quota project matches the configured project, and Vertex AI API reports ENABLED.
- Model metadata is reachable for both the active `gemini-2.5-flash-lite` and the previously requested `gemini-3.8-flash` in `global`.
- Actual legal-extractor requests were captured using the installed google-genai SDK with a local mock HTTP transport. A valid fixture response passed draft/review parsing for each model. This verifies serialization and parsing, not real model quality.
- Google's live **free CountTokens** endpoint accepted the actual captured prompt and GNNRuleList schema for both models: 2,255 input tokens each. **Zero live generateContent or embedding inference calls** were made.
- GENESIS logincheck verified the existing token. Small live Destatis, Eurostat and ECB downloads parsed. The first official news endpoint returned a valid RSS feed with ten entries.

Evidence: `data/reports/source-readiness-2026-10-19/`. The folder contains safe configuration summaries, focused test XML/output, retained public provider samples, SDK request captures, live metadata/token-count results and isolated pipeline reproductions. No request authorization headers or credential values were saved by these diagnostics.

## Credentials and configuration

| Item | Observed status | Action |
| --- | --- | --- |
| Google authentication | ADC file loads and refreshes; correct quota project. | No additional Gemini API key or account password needed. |
| Legacy Gemini/Google keys | Both environment variables are present; explicit ADC Vertex requests do not send an API-key header. | They are unused by this path and can cause confusing SDK warnings. Do not add more keys; any later cleanup should preserve other applications that may use them. |
| Trial credit balance/expiry | Billing metadata request returned 403 PERMISSION_DENIED. | User must inspect the billing account covering this project in Google Cloud Console. This response does not prove billing is disabled or credits are exhausted. |
| Model | Private environment selects `gemini-2.5-flash-lite`; example/default code names 3.8 Flash. | Select one model explicitly before comparing quality, and configure prices for that exact model/region. 3.8 metadata and token preflight work. |
| Generation budget/prices | Budget 0; input/output/embedding prices absent. | Keep generation disabled until credit coverage and a small explicit run cap are established. Credit-covered generation still consumes billed usage. |
| Embeddings | `gemini-embedding-001` metadata returns 404 in global but succeeds in us-central1. | Give embeddings a separate supported regional client; preserve its 768-dimensional contract. Changing the common client region can affect the generation model. No new key is needed. |
| GENESIS | Token present; live authentication and a small table download succeeded. | No username/password signup step is needed for the current bounded token-supported requests. |
| Eurostat / ECB / official news | Tested public endpoints respond without keys. | No keys required for these paths. |
| Bundesbank | Configured URL returns HTTP 404. | Fix and verify the dataflow/series/format contract; this is not evidence of a missing key. |
| SMARD | Adapter explicitly refuses the download-center page as a data API. | Keep disabled until a verified official export or supported acquisition adapter exists. |
| EUR-Lex | First configured law page returned HTTP 202 with zero bytes, not legal XML. | Obtain/resolve the official document representation and validate nonempty legal content; do not treat the empty response as an extracted law. |
| Production paths | Mode production, ledger under data/production, but LanceDB points to legacy data/lancedb. | Use a fresh consistent pilot data/ledger/archive folder. Do not mix live pilot records into the legacy database or migrate it implicitly. |
| Source activation | All eight registry sources disabled. | Enable only the selected verified source after its bounded test; do not turn all sources on or invoke the combined pilot yet. |

The measured production and legacy data folders contain about 0.53 MiB and 2.36 MiB of files respectively. Local storage is sufficient for the initial pilot; no server, MinIO account or student-cloud allocation is required. These measurements exclude dependencies and other audit folders, and do not establish a global 10 GB archive cap.

## Integration failures reproduced despite the passing suite

1. **Real GENESIS response exceeds the pilot cap.** The first bounded 2025 request returns 984 observations across 246 series. Replaying those exact bytes through the real runner with the existing eight-series cap fails with PILOT_SERIES_CAP and publishes zero records. Request/filter the exact residential target dimensions and a small explicit context selection; do not remove the cap merely to make this pass.
2. **Live target identity is not connected to the outlook.** The configured provider natural key `61261-0002|DINSG=DG|STEMW1=STEMW1|BAUAR1=BPNBK|BAUAR4=BAULEISTBW|PRE002|2021=100` is emitted by the actual parser, but get_construction_outlook rejects it as an unrecognized construction index. Resolve the target from its registered definition consistently across parser, dataset, UI and prediction records; retire hardcoded synthetic aliases safely.
3. **Technical Gemini failure becomes a successful no-rules document.** Injecting BudgetError into the actual extractor and running the registered law parser produces status committed, accepted 0, review 0, committed 1 and no source errors. An empty result is not evidence that extraction completed. Propagate budget/auth/API/parse/truncation failures to durable failed/retry/review work; preserve the raw archive and cursor boundary so later authorized retries can actually extract it.
4. **Frozen artifact validation remains incomplete.** A typed PredictionRecord naming nonexistent dataset/model IDs, target quarter in 2099, future creation, no manifest and caller-supplied CALIBRATED bounds is accepted by the outlook. The probe produces point 165 and a claimed calibrated interval. Checks for an absent prediction alone do not prove valid publication. Persist and verify dataset/model/prediction artifacts, exact origin/horizon/creation/fit times, target identity, checksums and held-out residual lineage before displaying forecasts. This need not delay inspecting raw source downloads, but forecasting must remain experimental/unavailable until repaired.

These are isolated offline reproductions on audit stores, not production mutations. Further positive-path coverage is needed; a green suite alone cannot establish live readiness.

## What the Gemini requests look like

For each short fixture, the actual extractor sends:

1. countTokens on the full extraction instructions, source text and structured schema.
2. generateContent with that same prompt/schema, JSON MIME type, one candidate and a 1,024-token output cap.
3. countTokens on the original source plus the draft rules and review instructions.
4. generateContent for review, again using the same selected Flash model. `use_pro=True` means deeper thinking, not a separate Pro model.

The current Flash Lite configuration sends thinking budgets 0 for extraction and 8,192 for review. The 3.8 capture sends LOW and HIGH. Google accepted the draft token-count preflight; no claim is made that a real generation server has accepted every generation option.

The named system prompt is currently concatenated into the user content; there is no separate systemInstruction field. Before scaling, separate trusted instructions from imported source text, verify injection resistance, use a bounded provision/document selection that preserves annex context, and check response finish reasons before accepting repaired JSON. A fixed 1,024-token cap needs a real small-output comparison before asking for every rule from a long law; the adapter currently checks nonempty text but does not reject MAX_TOKENS explicitly.

Open the captured 3.8 draft and review JSON in:

- `data/reports/source-readiness-2026-10-19/request-capture/gemini-3.8-flash/02-request.json`
- `data/reports/source-readiness-2026-10-19/request-capture/gemini-3.8-flash/04-request.json`

These contain an explicitly marked offline fixture, not secret inputs or a real model's legal conclusions. Registered news ingestion currently uses the deterministic extractor (`offline=True`); it is not sending every news article to Gemini. Statistical CSV/JSON is parsed deterministically too.

## Next steps and the user's part

**Software work first:** fix extraction error propagation, select the exact target series, align canonical target IDs and isolated paths, correct law/Bundesbank acquisition, and split embedding routing. Keep unsupported sources off. Add regressions using the retained real response envelopes and a valid/invalid artifact pair.

**User action:** open [Google Cloud Billing](https://console.cloud.google.com/billing), select the account linked to this project, and check remaining trial credits and expiry under Credits. No additional keys need to be pasted into chat. Establish a small total generation cap only after confirming coverage; budget 0 remains appropriate until then.

**First trial after repairs:** one small Eurostat or exact GENESIS series in isolated local storage; verify raw SHA, values, units, dimensions, availability, replay and cursor behavior. Then one short legal provision with a reviewed expected JSON output, capturing the real request, response finish reason, token usage and ledger settlement under an explicit cap. Compare extraction/review outputs and confirm credit deduction in Billing after reporting catches up. Expand only after reviewing these records. Collect current vintages honestly; downloading old reference periods today does not create historical first-release labels.

Official references checked:

- [GENESIS POST/token examples](https://genesis.destatis.de/datenbank/online/docs/20250505_python_post_logincheck_tablefile_cubefile.pdf): token in headers, read-only requests and bounded downloads.
- [Gemini 3.8 Flash model documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash): supported model, locations and thinking levels.
- [CountTokens documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/get-token-count): no charge for token counting.
- [Text embeddings reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/text-embeddings-api): regional embedding endpoint.
- [Google pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing) and [trial terms](https://docs.cloud.google.com/free/docs/free-cloud-features?hl=en): model/region prices and account-specific credit restrictions must be checked before generation.

No .env changes, registry activation, product-code edits, model generation, embedding inference, paid upgrades, deployment, or production ingestion were performed in this review.
