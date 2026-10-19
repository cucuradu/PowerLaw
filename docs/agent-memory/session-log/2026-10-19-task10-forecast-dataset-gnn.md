# Task 10 Completion Log

Date: 2026-10-19
Status: Completed & Verified

## Overview

Task 10 delivers a strictly release-aware quarterly forecasting dataset, defensible quantitative baselines with conformal interval calibration, and an experimental sparse HeteroGNN implementation matching the technical architecture specifications:

1. **Point-in-Time Graph Export (`export_as_of`)**:
   - Implemented in `src/gnn/graph_exporter.py`.
   - Slices point-in-time graph topology and active observations from committed batches.
   - Respects temporal isolation: future nodes, edges, news corrections, and revised observations cannot alter earlier active features, topology, or text.
   - Future announced obligations are tagged as upcoming features.

2. **HeteroData Graph Representation (`to_heterodata`)**:
   - Implemented in `src/gnn/graph_exporter.py`.
   - Maps snapshots into PyTorch Geometric `HeteroData` across 8 canonical node stores (`law`, `rule`, `event`, `company`, `commodity`, `sector`, `geography`, `series`).
   - Uses type-local 0-indexed edge tensors and 12-channel edge attributes (6 continuous/indicator + 6 observed masks).
   - Establishes scoped 2-hop computational routes (`commodity -> measured_by -> series`, `rule -> context_for -> series`, `event -> context_for -> series`), allowing relevant rules and events to reach the target within a 2-layer receptive field while preventing indiscriminate edge reversal.

3. **Release-Aware Forecasting Dataset (`build_forecast_dataset`)**:
   - Implemented in `src/models/gnn_dataset.py`.
   - Slices 8 quarterly snapshots per origin.
   - Enforces strict release awareness: unpublished quarters are excluded from inputs; target labels require earliest verified release containing both the target level and comparable denominator.
   - Missing or unreleased quarters remain masked (`y_mask=False`, `y=NaN`), never fabricated with synthetic values.

4. **Defensible Baselines & Conformal Calibration (`evaluate_baselines`, `calibrate_residual_interval`)**:
   - Implemented in `src/quant/forecasting.py`.
   - Implements no-change (0.0%), seasonal (lag-4), and autoregressive (lag-1) models with MAE, RMSE, and directional accuracy metrics.
   - Calibrates a 90% symmetric prediction interval on absolute residuals using rank $\lceil (n+1) \times 0.9 \rceil$ on a chronological calibration set.
   - Enforces an eligibility gate of $\ge 20$ origins; returns `insufficient_calibration_history` when sample count is inadequate rather than reporting spurious intervals.

5. **Sparse Heterogeneous GNN (`TemporalHeteroGNN`, `HeteroGNNTrainer`)**:
   - Implemented `TemporalHeteroGNN` in `src/gnn/temporal_gnn.py`: 2-layer 32-channel `HeteroConv(TransformerConv(edge_dim=12))` with residual linears, 1-layer GRU (hidden size 32), and series regression head.
   - Implemented `HeteroGNNTrainer` in `src/gnn/trainer.py`: trains with future-only labels and masked MSE; supports ablation studies (`full_model`, `no_news`, `no_legal`, `no_topology`).

6. **Anti-Synthetic Date Regression Repair**:
   - Corrected legacy expectation in `tests/test_edge_cases.py` line 82 to assert empty string `""` on invalid dates, ensuring adherence to the strict anti-synthetic data rule.

## Verification Evidence (Iron Law Protocol)

1. GNN & Forecasting Test Suite:
   - `python -m pytest tests/test_forecast_dataset.py tests/test_gnn_export.py tests/test_gnn_dataset.py tests/test_temporal_gnn.py -q`
   - **42 passed, 5 warnings in 39.29s**

2. Edge Cases Suite:
   - `python -m pytest tests/test_edge_cases.py -q`
   - **5 passed in 2.33s**

3. Full Repository Test Suite:
   - `python -m pytest -q`
   - **346 passed, 28 subtests passed, 5 warnings in 308.70s**

## Decisions & Invariants Upheld

- `torch-geometric>=2.5.0` pinned in `requirements.txt`.
- Primary target strictly maintained as German residential construction producer price index (`series:destatis:61261-0002`). No substitution with CPI or financial proxies.
- Zero synthetic data: missing/unreleased values remain null/masked.
- Conformal calibration requires $\ge 20$ calibration points.

