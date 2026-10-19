# Germany pilot configuration

Configured on 2026-10-19 under the user's authorization for an overall EUR 100 budget. Private credentials remain in the existing `.env`/ADC locations; no new key is required.

## Google Cloud and spending

- Authentication: ADC, using the configured Google Cloud project. ADC is the login mechanism; trial credit belongs to the project's linked billing account.
- Generation: `gemini-3.8-flash`, location `global`.
- Optional text embeddings: existing `gemini-embedding-001`, location `us-central1`, 768 dimensions. Generation and embeddings use separate cached ADC clients.
- Total authorization: `POWERLAW_EXTRACTION_TOTAL_BUDGET_EUR=100`.
- Frozen conversion: `POWERLAW_EXTRACTION_EUR_USD_RATE=1.1225`, verified at the ECB on 2026-10-19. A 10% FX/billing buffer leaves an application ceiling of USD 101.025, approximately EUR 90 at that reference rate.
- Initial per-run limit: `POWERLAW_EXTRACTION_BUDGET_USD=1.0`. This limit sits inside the overall cap; new runs/restarts do not reset the overall allowance.
- Gross, undiscounted online price estimates: input USD 0.0015/1,000 tokens, output including reasoning USD 0.0075/1,000 tokens, embeddings USD 0.00015/1,000 tokens. Promotional refunds/cached discounts are not assumed. Verify prices before changing model, location or future operation.

One immutable total spending policy and all reservations/settlements live in `data/pilot-germany/ingestion.sqlite`. Unknown usage or an overrun blocks subsequent paid calls across runs until reconciled. Preserve this ledger: another/deleted ledger cannot account for this history. The application limit does not control unrelated Google Cloud usage or replace the billing account's accounting.

The first actual structured generation succeeded with one valid fixture rule, missing dates/quantities preserved, and usage of 3,159 prompt + 338 output = 3,497 total tokens. Google omitted the zero thoughts count; the accountant now accepts zero only when the provider total proves the equality. Accounted gross cost is USD 0.0072735; no reservations remain unresolved. The saved public fixture output was not published as a legal fact. Credit deduction/balance remains unverified because Billing metadata access returned 403.

Live CountTokens can omit schema overhead from its count. Reservations now add a conservative schema-byte/framing allowance; this is an application estimate, not a provider guarantee. MAX_TOKENS output is rejected as incomplete, rather than accepted through JSON repair.

## Storage and sources

All active pilot data uses `data/pilot-germany/`:

- `lancedb/`: canonical records and rebuildable graph/vector projections.
- `ingestion.sqlite`: run/cursor/recovery state and total spending policy.
- Local evidence archive, cache and temporary files under the same configured data root.

The old `data/lancedb` and `data/production` folders were preserved. Their earlier/demo/history data was not merged into this pilot. No server or cloud storage subscription was added.

Application sample-data cleanup on 2026-10-19 removed automatic graph/law/news seeding, simulated market histories, random embedding fallbacks, fixed dashboard counts/dates, fabricated risk/volatility/regime outputs, and legacy seed exceptions to evidence checks. Missing inputs now stay unavailable. Optional news vector projection is skipped when no real embedding exists; canonical source facts are retained. Synthetic regression datasets live under `tests/` and are explicitly loaded only into isolated test storage.

Graph downloads and the HTML canvas now render current configured storage in memory. Bundled sample `visualizations/graph_data.json` and `visualizations/interactive_graph.html` were removed; opening a dashboard no longer regenerates or reads them. The old standalone seed generator and obsolete sample-text ingestion command were removed. The ingestion console reads configured feeds/model/path and reports ADC configuration without claiming that credential presence proves authentication.

A fresh audit of every active pilot table found no non-live origin records or known fixture markers. All four raw source documents still matched archived hashes/byte counts; 266 observations and three news facts remained visible. Existing unclassified legacy stores and historical audit evidence were preserved outside the active pilot paths; no unrelated records were deleted based on guessed provenance.

Enabled bounded selections: GENESIS residential construction target and registered material-context selections; Eurostat exact German residential construction producer-price dimensions; tested ECB exchange-rate series; the verified Commission Press Corner RSS endpoint. Bundesbank, SMARD, EUR-Lex and GISCO remain disabled until their acquisition gates are resolved. Pilot orchestration skips disabled sources.

The first live pilot with limit 1 committed all four enabled sources without source errors: 8 GENESIS observations, 3 Eurostat observations, 255 daily ECB observations, 3 news facts, and 4 raw documents. Canonical visibility and each raw document's SHA/byte count were independently checked. Model cost for that source pilot was USD 0 because statistics and current news parsing are deterministic.

A series is one defined variable, such as Germany's quarterly residential construction index. Eight quarterly observations can belong to one series. The old table request returned 246 distinct series; explicit selection now retains the target/context keys and keeps the eight-series pilot cap. A failed cap check prevented publication; it was not a token/authentication error.

`committed` means archived and stored after automated checks. It does not mean an expert has reviewed every news claim, or that a forecast is accurate. Technical model/budget/auth/parse failures now remain failed/retryable and do not become successful no-rules documents or advance the legal cursor.

## Commands

Restart already-running app/agent processes to reload private.env settings. This agent inherited an old process-only GEMINI_MODEL override; a new shell without that override reads the configured model. An explicit override also works:

```powershell
$env:GEMINI_MODEL = "gemini-3.8-flash"
.\.venv\Scripts\python.exe -m scripts.ops.ingest --status
```

Status includes `ai_spending` with overall limit, accounted/reserved amounts and remaining allowance. For another intentionally bounded refresh:

```powershell
.\.venv\Scripts\python.exe -m scripts.ops.ingest --pilot --limit 1
```

This refresh acquires the enabled sources; it is not a read-only preview. `--dry-run` currently plans discovery and does not prove real extraction/quality. Do not enable the other sources simply to remove disabled flags.

## Outlook and embeddings

The outlook is the dashboard panel for future construction prices and regulatory/supply context. Collecting data can proceed while forecast publication remains unavailable/experimental. Registered natural target IDs still need consistent outlook wiring, and persisted forecast/model/dataset validation and held-out calibration remain separate open gates.

The GNN learns numerical representations from node features, time history and graph neighbors. It does not automatically understand raw article/law text. Current ingestion converts text into structured facts; these can be GNN inputs without paid text embeddings. Text embeddings are optional for semantic retrieval or an explicitly chosen text-feature model. They were configured but no embedding inference was run.

User follow-up: check remaining credits and expiry in [Google Cloud Billing](https://console.cloud.google.com/billing). No further key, password, paid upgrade or hosting account is required for the tested paths.

References: [Google model prices](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing), [ECB EUR/USD](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html), [embedding endpoint](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/text-embeddings-api).

Audit evidence: `data/reports/configure-2026-10-19/`, particularly `live-pilot.json`, `pilot-integrity.json`, `gemini-live-smoke.json`, `gemini-accounting-verification.json` and the final offline suite artifacts.

Final offline verification: **519 passed, 28 subtests passed, five warnings in304.36s**, exit0, no source files changed during execution. External I/O was blocked; thirty legacy FRED attempts were denied. One unmocked multimodal smoke file was excluded; the controlled live structured-generation test above was run separately. Command: `.\.venv\Scripts\python.exe data/reports/configure-2026-10-19/run_full_final.py`.
