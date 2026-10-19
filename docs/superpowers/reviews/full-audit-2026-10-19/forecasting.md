# Tasks 10 and 11 audit — 2026-10-19

## Scope and verdict

Read AGENTS.md, current-work.md, Task 10/11 acceptance in the ingestion plan, the technical architecture's typed features/tensors/model contracts, and their implementation/callers/tests. No product code, shared documentation, or existing working-tree changes were edited. All new files and database fixtures are under this forecast report directory.

| Task | Verdict | Reason |
| --- | --- | --- |
| 10 | Partial implementation; completion gate fails | Quarter/date helpers and canonical as-of wrapper exist, but labels bypass commit/retraction selection, scoped routes are fabricated, recurrent state does not align IDs or apply masks, chronological evaluation/transforms/frozen releases are missing, and PyG integration tests are skipped in the actual environment. |
| 11 | Partial implementation; completion gate fails | A read-only panel with observations, gaps, and disclaimers exists, but it accepts CPI as construction PPI, manufactures a forecast for the wrong horizon without a frozen artifact, calls unrelated residual history calibrated, and bypasses canonical fact/evidence eligibility. |

The current-work assertion that Tasks 1–11 are complete and the plan's checked Task 10/11 boxes are not supported by these checks.

## Validation actually run

Environment forced to test and paths rooted at `data/reports/full-audit/forecast/test-runtime`: `POWERLAW_MODE=test`, `POWERLAW_DATA_DIR`, `LANCEDB_PATH`, `POWERLAW_SQLITE_PATH`, `POWERLAW_CACHE_DIR`, `POWERLAW_TEMP_DIR`; `PYTHONDONTWRITEBYTECODE=1`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_forecast_dataset.py tests/test_gnn_export.py tests/test_gnn_dataset.py tests/test_temporal_gnn.py tests/test_construction_outlook.py -q -p no:cacheprovider
```

Result: **48 passed, 3 skipped in 46.10s**. The three skipped tests are the new PyG converter/receptive-field, hetero-model architecture, and hetero-training/ablation integration tests. The old `test_temporal_gnn.py` exercises the legacy demo GCN, not `TemporalHeteroGNN`.

```powershell
.\.venv\Scripts\python.exe -c "import sys,importlib.util,importlib.metadata; print(sys.version); print('torch_geometric_spec',importlib.util.find_spec('torch_geometric')); print('torch',importlib.metadata.version('torch'))"
```

Result: **Python 3.12.13; torch 2.14.1; torch_geometric_spec None**. The plan's Python 3.12.9 / PyG 2.8.1 completion record does not describe this environment. No dependencies were installed.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe data/reports/full-audit/forecast/probe_forecast.py
```

Result: exit 0; **17 adversarial observations/assertions reproduced**. Exact outputs are in `probe_results.json`. The script itself forces isolated test paths, local object storage, and writes only under this directory. It uses no HTTP/API/cloud calls. First execution was sandbox-blocked by the uv Python trampoline (`permission denied`); the same local Python command ran with escalation.

PyG limitation is explicit: converter checks execute production conversion code and real numeric torch operations using a storage-only `HeteroData` stand-in. Temporal checks execute the production `forward` and real torch GRU/head with a deterministic graph encoder. They prove conversion/sequence defects, not the missing PyG attention implementation. Full sparse attention, edge-attribute sensitivity, dense-reference equivalence, sparse memory scaling, and end-to-end heterogeneous training remain unverified.

## Confirmed Task 10 findings

### F10-1 — Labels consume uncommitted and retracted data (high)

`src/models/gnn_dataset.py:254` scans `_canonical_observations.values()` directly. It filters each row's quality/retraction flag but does not select committed batches or current revisions. A later tombstone is filtered out while its old accepted observation remains usable.

Real-store probes:

- `staged_label_visible`: a batch is still `staged`, and `query_observations` returns zero committed rows; `build_forecast_dataset` nevertheless produces **50% with y_mask=True**.
- `retracted_label_retained`: the canonical query contains only Q3 after Q4 retraction; the dataset still produces **50%** from the retracted Q4 release.

This violates committed-only truth and allows failed/reviewed/retracted ingestion into training labels. Label availability after the forecast origin is expected for retrospective evaluation; the failure here is that label admission has no committed, revision, or release-eligibility boundary.

### F10-2 — Unknown vintages and non-quarterly periods manufacture comparable labels (high)

`src/models/gnn_dataset.py:264` converts every missing vintage to the same artificial `default` bucket. `:269–276` matches only period start and the two unit strings; it does not validate quarter ends, source release lineage, base/frequency definition, or the denominator's availability. Claimed first release time comes only from the numerator.

`unverified_incomparable_label`: two one-month periods beginning July 1 and October 1, both without vintage IDs, produce **20% quarterly growth**, vintage **default**, claimed first availability **2027-02-15**, even though the denominator was available only **2027-06-01**. The result is not an earliest verified comparable quarterly release.

### F10-3 — Context routes are every-rule/event-to-every-series, with fabricated scope (high)

`src/gnn/graph_exporter.py:1746–1766` nests every rule/event over every series. It never tests product, geography, time, mapping facts, or evidence. Routes have hardcoded `exact_scope_match=1`; edge stores retain only `edge_index`/`edge_attr`, with no supporting lineage. Ordinary edges also default unknown scope match to true at `:1728`.

`unscoped_routes`: a Japanese rice rule and US airline event, with **zero source edges**, each connect directly to **both German construction series**, with exact-scope values **[1,1]** and no lineage fields. This also defeats the required edge-linear memory claim: the converter adds O((rules+events) × series) edges.

`inactive_relation_used`: an edge explicitly marked expired/inactive is still included as a message edge. `to_heterodata` never consults `is_active` or `status`; upcoming obligations can likewise enter current message topology rather than only upcoming features.

### F10-4 — Recurrent state ignores identity and active masks (high)

`src/models/gnn_dataset.py:225–240` independently exports each quarter without a stable union/type map or padding. `src/gnn/temporal_gnn.py:362–379` stacks series rows by position and runs a plain GRU on every step. It never reads `node_id`, `active_mask`, or final target activity. `forward_snapshot` at `:321–351` also ignores `feature_observed_mask`, so unknown zero and measured zero are not represented distinctly to the encoder.

Production temporal-forward probes with real torch and deterministic encoder:

- `temporal_forward_new_series_crash`: an additional series in snapshot 8 raises **stack expects each tensor to be equal size, but got [1,32] at entry 0 and [2,32] at entry 7**.
- `temporal_forward_inactive_mask_ignored`: all series inactive; changing inactive features changes prediction **0.14266576 → 0.02767001**. Inactive targets still return predictions.
- `temporal_forward_stable_ids_ignored`: reordering only earlier rows, retaining identical ID/value pairs and the same final target, changes prediction **0.14266576 → 0.16496575**.
- `src/models/gnn_dataset.py:242` silently defaults a missing requested target to index zero. `absent_target_defaults_zero` requests `series:missing` but obtains `[0]` while exported IDs contain another target only.

### F10-5 — Baseline evaluation leaks later labels and has no temporal partition (high)

`src/quant/forecasting.py:22–52` extracts all valid label values in caller order and uses previous list items as lag predictions. It never reads origins, target periods, release times, or train/validation/calibration/test partitions. Missing quarters change the meaning of lag-4. Its "autoregressive" baseline is an unfitted lag-1 value, not a small fitted tabular model.

`baseline_order_and_future_labels`: all six labels are first released in 2030, after all supplied origins; nevertheless their lag values are used. Reversing the exact same dated samples changes lag-1 MAE **1.0 → 1.8333** and seasonal MAE **3.0 → 4.3333**. The standalone conformal helper correctly computes the specified order statistic for its normal finite input, but nothing wires it to a separate chronological calibration partition and held-out coverage report.

### F10-6 — Required feature pipeline, training gates, and frozen artifacts are absent (high)

`src/gnn/graph_exporter.py:1574` fixes eight channels and does not use the supplied `feature_profile`. Series features are raw unnormalized values; `:1626–1653` supplies mostly identical constants for commodities/sectors/laws/rules/events/companies/geographies. Legal status/deadlines, disruption magnitude/status, geography indicators, independent source counts, exposure channels, named native-resolution aggregation, and as-of centrality do not enter this path. Existing legacy exporter centrality is not called by `build_forecast_dataset`.

`src/gnn/trainer.py:435–484` accepts arbitrary samples, checks only `y_mask[0,0]`, and computes raw `F.mse_loss(y_pred,y_true)` at `:478`. No coverage eligibility gate, chronological partition, input/target train-only transform, inverse target transform, validation/calibration integration, or readiness report exists. Non-masked cells would enter loss for multi-target samples.

`src/models/gnn_dataset.py:317–334` returns a generic dataset ID based only on target/mode and a manifest with just sample count, target, mode, and current creation time. Origins, horizon, selected generations/revisions, release lineage, feature definitions/order/transforms, versions, split specification, and content hashes are missing. Different dataset contents reuse the same ID. `HeteroGNNTrainer` has no checkpoint/manifest persistence; checkpoint methods in the file belong to the legacy trainer. Source search finds the new dataset/trainer interfaces called only from tests, not a runnable release/evaluation workflow.

The existing `DatasetManifest` at `src/schemas/ingestion.py:387–400` requires manifest URI/hash, committed generation, target spec, and feature profile identity; the builder does not construct it. `PredictionRecord` at `:403–419` likewise is not consumed by the outlook. A read-only filename inventory found `data/checkpoints/tgnn_checkpoint.pt`, the legacy checkpoint, and no new forecast dataset/evaluation/calibration artifact. The broad data inventory encountered an inaccessible parent-audit operations temp directory; this does not affect source evidence that no new release/checkpoint workflow is implemented.

### F10-7 — Ablations continue training the previous experiment (medium)

`src/gnn/trainer.py:559–565` reuses the same model and optimizer across full/no-news/no-legal/no-topology runs, without restoring the same initialization. `ablations_not_independent` instruments the production runner: experiment starting states are **0,1,2,3**, respectively. Different budgets/history confound reported ablation comparisons. Zeroing node inputs also retains biases/topology and is not equivalent to removing news/legal context.

### F10-8 — Legacy training remains exposed without a demo gate (high)

`pages/5_🤖_GNN_Modeling_Lab.py:16–18` imports the old dataset/exporter/trainer, `:33` uses the mode-selected store, `:166–189` loads/trains it, and `:200` saves the legacy default checkpoint. There is no demo-mode check. `TGNNTrainer` itself has no origin guard. Its labels at `src/gnn/trainer.py:136–172` remain current-state regression and magnitude-based risk classes; the page still advertises live training and counterfactual shock simulation. This violates the explicit requirement to keep legacy APIs demo-only and to keep Streamlit pages read-only. Source-confirmed; no production training was run.

## Confirmed Task 11 findings

### F11-1 — Prefix allowlist accepts CPI and falsely labels it construction PPI (high)

`src/storage/dashboard_data.py:1207–1209` accepts any `series:eurostat:prc_` or `series:construction:` prefix, instead of validating the exact locked series definition/dimensions/measurement. `:1235–1239` then forces German residential PPI wording and 2021-based units.

`cpi_prefix_proxy_accepted`: a registered monthly Eurostat **prc_hicp_midx consumer_price_index**, unit **2015=100**, is accepted and returned as **German Construction PPI Residential (consumer_price_index)**, target units **INDEX_2021_100**, with forecast **300**. The anti-proxy disclaimer then claims it was prohibited. Current tests only reject names containing obvious forbidden substrings; HICP bypasses them.

### F11-2 — Outlook invents a forecast instead of consuming a frozen artifact, and targets the wrong quarter (high)

`src/storage/dashboard_data.py:1280–1285` chooses the quarter after the last observed reference period rather than the quarter after the stated forecast origin. `:1305` simply sets point forecast to the latest observation. The returned object at `:1485–1502` includes no dataset/model ID, version, manifest, artifact timestamp, or artifact lineage. No prediction artifact lookup exists.

`outlook_wrong_horizon_unfrozen`: origin **2026-09-30T23:59:59.999999Z**, latest observation **2026-Q2** released August 15, returns target **2026-Q3**, point **120**, and no frozen-artifact fields. The required origin targets **2026-Q4**. `dashboard.py:214–225` displays this as Forecast PPI.

### F11-3 — "Calibrated" counts arbitrary observations and latest-vintage differences, not held-out quarterly residuals (high)

`src/storage/dashboard_data.py:1308–1333` counts target observation rows and uses absolute differences between latest-vintage adjacent values on every page read. There is no forecast artifact, distinct calibration window, unique eligible-quarter check, first-release outcome pairing, model identity, or held-out coverage. Latest revisions can rewrite historical residuals. The calculation also reports index-level widths, whereas the Task 10 target and prediction schema are percentage growth.

`daily_rows_pass_quarterly_calibration`: **21 daily observations from one single quarter** produce status **calibrated**, sample_count **20**, 90% interval **[120,122]**. The dashboard at `dashboard.py:232–243` labels that "Calibrated (≥20 historical quarters)".

### F11-4 — Raw fact loops resurrect expired/retracted/rejected context (high)

`src/storage/dashboard_data.py:1363–1390` and `:1394–1417` iterate every stored fact version, checking only batch and availability. They do not use `_latest_facts`, quality, tombstones, `valid_to`, or supersession. Missing available_at is also not resolved via canonical recorded-at fallback. News corrections can remain beside superseded claims; expired obligations are marked ACTIVE solely from valid_from.

`retracted_expired_rules_still_active`: an obligation expired in 2021 and then retracted. Canonical graph query at the 2026 cutoff has **zero edges**; outlook reports it **twice as ACTIVE**, once for the original version and once for the tombstone. Existing correction tests cover only target observations, which do use the canonical query.

### F11-5 — Evidence resolution bypasses commit/cutoff boundaries (high)

`src/storage/dashboard_data.py:1353–1355` uses all evidence versions without eligibility filtering; `:1443–1458` resolves raw documents without commit/cutoff validation. The fallback at `:1463–1474` checks only availability, not committed batch ownership or whether the document supports any displayed assertion.

`uncommitted_evidence_presented`: a never-committed **staged** document appears under evidence even when there are no observations or facts. That is not committed, assertion-grounded evidence. The fallback may also present unrelated documents as primary evidence.

## Why the passing tests did not protect these contracts

- Future-perturbation test (`tests/test_forecast_dataset.py:197–281`) compares counts and node/edge ID sets, not numeric features/text/masks or actual inference. No news correction is actually added, despite the test description.
- Upcoming-law test (`:283–347`) checks the raw snapshot's `is_active=False` but never checks converted topology; converter ignores that field.
- Receptive-field test (`:524–568`) only checks that some rule→series relation exists. The all-to-all implementation satisfies it without matching any scope or preserving lineage.
- Calibration test (`:570–604`) checks a standalone hand-supplied residual list, not separation from training/test outcomes or vintage-aware eligibility.
- Edge sensitivity test (`:606–648`) leaves the model in training mode. Its dropout can produce different outputs for identical inputs, so its non-equality assertion is not proof that edge attributes matter. No same-attention dense reference or sparse-memory assertion is implemented despite its docstring.
- Hetero training/ablation test (`:650–710`) uses two mock train labels and one test label over eight repeated copies of one graph. It checks keys/types/counts, not chronology, transforms, coverage, scientific improvement, or independent initialization. This and the other two PyG tests are skipped here.
- Outlook read-only test checks only dictionary lengths, not all filesystem writes; the current method itself is read-only in the inspected implementation. Its tests do not exercise frozen artifacts, CPI-prefix bypass, fact tombstones, staged documents, wrong forecast horizon, or non-quarterly calibration rows.

## Positive evidence and remaining limits

The canonical `export_as_of` wrapper calls the existing committed as-of graph/observation APIs; eight quarterly cutoffs and basic next-quarter label arithmetic exist; missing target release produces a masked label in the narrow supplied fixture; the standalone finite-sample conformal order statistic and minimum-count branch pass; observed target corrections after cutoff are excluded through canonical observation reads; the outlook includes explicit missing/stale/calibration gaps and non-causal disclaimers.

No product fixes were made. Full repository suite is owned by the parent audit. No live source, paid model, cloud service, actual PyG encoder, real historical forecast performance, visual browser inspection, or production data mutation was exercised. Risks above remain open until implementation and meaningful acceptance tests are repaired.
