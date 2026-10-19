# Review after the other session's continuation

The next step is to finish the remaining C7/C8 software repairs and correct two temporal defaults before a live pilot. The new completion note overstates what the code proves. Preserve the completed work; do not restart ingestion/storage development.

## Fresh verification

Command (isolated local data, external socket connections blocked, no paid calls):

```powershell
.\.venv\Scripts\python.exe data/reports/repair-2026-10-19/continuation-review/run.py
```

Result: **8 failed, 25 passed, 1 warning in 12.47s**, exit 1. The 25 passing checks are the existing canonical, label, and real CPU PyG temporal acceptance tests. The eight failures are additional independent requirements, listed below. Tests and XML/output are under `data/reports/repair-2026-10-19/continuation-review/`. This is a focused review, not a fresh rerun of every ingestion, budget, restore, UI or source test.

The other session records 463 passing tests. That does not close requirements absent from those tests; this review does not independently certify that full-run count. Real PyG is installed locally and executes these temporal tests; CUDA hardware is not needed to repair these defects.

## Remaining failures and exact repairs

| Priority | Reproduced behavior | Responsible code and repair |
| --- | --- | --- |
| High | 21 daily rows in one quarter produce a supposedly calibrated quarterly interval. | `src/storage/dashboard_data.py:1422`: remove adjacent observed-level differences as calibration. Require at least 20 distinct eligible held-out quarterly forecast residuals, matching target and horizon, with release times and disjoint training/calibration/test splits. Otherwise show no calibrated interval. |
| High | With no saved model/prediction artifact, the outlook invents a point forecast of 110 from the last observation. | `src/storage/dashboard_data.py:1420`: consume a validated immutable dataset/model/prediction artifact using the existing `DatasetManifest` and `PredictionRecord` contracts. Validate checksums, target, horizon, origin, model fit time and feature profile. Missing or invalid artifact means an explicit forecast gap. Keep percentage growth forecasts distinct from observed index levels. |
| High | A withdrawn, unexpired legal rule remains ACTIVE in the outlook while the canonical eligible fact query is empty. | `src/storage/dashboard_data.py:1484`: use `query_facts()` and `documents_for()` rather than scanning raw versions. Preserve forthcoming rules as known context with their validity dates; suppress withdrawn/expired versions. Apply the same boundary to supply context. |
| High | A staged document appears in outlook evidence once unrelated committed documents exist. | `src/storage/dashboard_data.py:1598`: remove the fallback over all source documents. Only evidence documents attached to eligible displayed assertions/observations belong in the outlook. |
| High | A rule with no supporting scope relation is connected to both unrelated series. | `src/gnn/graph_exporter.py:1745`: replace Cartesian `context_for` routes with evidenced mappings to matching measured targets and explicit compatible geography. Preserve mapping fact/version lineage. Exclude inactive/expired edges from active message passing; do not default unknown scope to an exact match. |
| High | An inactive, unobserved placeholder changes a training-feature mean from 15 to 343. | `src/gnn/graph_exporter.py:1786`: fit statistics only on observed, finite, active training values per column. Keep missing transformed values at zero with their masks. Apply frozen training statistics to cloned evaluation data and honor the profile passed into `to_heterodata()`. |
| High | Default label construction advances its cutoff to a row dated 2099 and accepts that future label. | `src/models/gnn_dataset.py:200`: default the label cutoff to the actual current time; never infer it from the maximum data/batch timestamp. Future simulated test releases must supply an explicit `label_cutoff`. Keep the forecast-input cutoff separate. |
| High | A series recorded in 2025 with unknown availability appears in a 2020 query. | `src/storage/dual_layer_store.py:1735`: remove `datetime.min` as an invented public timestamp. Use verified availability or a conservative known recording/fetch time, or withhold the definition with a reason. Explicitly trusted static seed metadata needs its own auditable provenance; it must not grant arbitrary new definitions historical availability. |

## Execution order

1. Promote these eight failing audit checks into maintained regression tests. Repair the two temporal defaults first, updating simulated fixtures explicitly rather than widening production eligibility.
2. Repair scope/masks/transforms in the graph exporter. Keep the passing stable-ID recurrence and independent ablation work.
3. Implement frozen forecast publication/read validation and wire the outlook to eligible facts/evidence. Verify absent, future, mismatched and tampered artifacts, proper quarterly calibration, and forecast units.
4. Rerun these checks and the affected existing suites, then one isolated full offline suite with no code changing during execution. Reconcile the repair checklist and completion memory with actual results.
5. After those gates pass, run a bounded local Germany pilot using free official sources, audit real units/releases/raw references, and obtain the required legal/news quality review. Keep paid extraction disabled until its separate budget and authorization gates pass. Server deployment is not a prerequisite.

No application code or credentials were changed for this continuation review. No live acquisition, billing check, model accuracy evaluation, GPU test, or deployment was performed.
