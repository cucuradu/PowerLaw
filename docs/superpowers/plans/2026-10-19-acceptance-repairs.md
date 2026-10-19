# Source-to-forecast acceptance repairs

> **For agentic workers:** Use the existing approved C1-C8 repair scope with test-driven execution. Independent file areas may run in parallel; integration and final checks are sequential. Progress ledger: `data/reports/repair-2026-10-19/progress.md`.

**Goal:** Repair the independently reproduced C1-C8 failures without replacing the existing stack or weakening acceptance.

**Architecture:** Canonical committed/evidence-eligible records supply production readers, graph and training truth. Source adapters preserve typed identity/provenance. Paid calls and restore are bounded before side effects. Forecast reads consume validated frozen predictions or show an explicit gap.

**Tech Stack:** Existing Python/Pydantic/LanceDB/SQLite/Streamlit/PyTorch/google-genai; optional real PyG, no substitute pretending to verify it.

**Spec:** [Approved repair description](../reviews/2026-10-19-tasks-1-11-recheck2-fix-prompt.md), [fresh counterexamples](../reviews/2026-10-19-completion-claim-verification.md), [architecture](../specs/2026-10-18-technical-architecture.md), [ingestion](../specs/2026-10-18-data-ingestion-design.md), [readiness](../specs/2026-10-18-free-sources-and-ingestion-readiness.md).

## Global constraints and rulings

- User approved implementing the written repairs. No further design/implementation permission is needed.
- Preserve the dirty checkout; baseline source/test/config/docs copies and hashes are in `data/reports/repair-2026-10-19/baseline/`. No resets, commits, deployment, live extraction or production migration.
- Ruling: work in this checkout because the implementation is uncommitted/untracked and must be repaired here; a clean worktree would omit it. Test data and logs remain isolated under ignored audit paths. No dependency reinstall for an already working environment.
- Free sources only; unknown values remain null; one ingestion writer; immutable versions and committed point-in-time visibility.
- RED then GREEN per defect group, followed by affected existing tests. Final broad run only after edits settle.

## Review focus

1. Cross-batch/cross-origin evidence must not publish or reveal demo/test information as live truth (Task1).
2. Failed responses and retries cannot finalize unknown costs or bypass a shared budget (Task2).
3. A restore must work without its original archive and must reserve metadata as well as raw bytes (Task3).
4. An exact anchor with the wrong mechanism, unit, date or scope is insufficient evidence (Task4).
5. Unknown/retracted vintages, changing node sets and unrelated context must not contaminate forecast labels or artifacts (Tasks5-6).

## Task1: canonical eligibility and production readers (C1)

**Files:** `src/storage/dual_layer_store.py`, `src/storage/dashboard_data.py`, `tests/test_canonical_acceptance.py`.
**Interface:** reuse `stage_batch`, `query_observations`, `project_graph`; add one public eligible-fact read if needed by reader/model callers.

- [ ] Add and run failing tests for live assertions backed by demo evidence, an uncommitted chunk sharing a legitimate law ID, loose news JSON, and retraction/availability controls.
- [ ] Enforce linked document/assertion kind/origin eligibility and construct production law/news views from canonical assertions rather than a legacy allowlist.
- [ ] Run new tests and existing store/reader suites; retain demo compatibility explicitly.

## Task2: paid authorization and operational bounds (C2/C6)

**Files:** `src/utils/api_wrapper.py`, `src/storage/ingestion_jobs.py`, `scripts/ops/ingest.py`, relevant API/pilot tests, `docs/operations/scheduled_ingestion.md`.

- [ ] Add failing tests for ASCII under-reservation, response loss, embeddings, output/thinking/multimodal authorization, failed pilot spend and request retries.
- [ ] Authorize a defensible complete request bound and leave attempted unknown usage unresolved. Preserve durable pilot budget and leases.
- [ ] Enforce per-kind pilot caps and incomplete human quality gates; make queue failures/failed-work recovery explicit and fix executable schedule guidance.
- [ ] Run affected offline API/runner/pilot/operations suites; no real paid request.

## Task3: complete portable restore (C5)

**Files:** `src/utils/minio_client.py`, manifest fields in `src/ingestion.py` (coordinated with Task4), restore tests.

- [ ] Add failing tests for actual live manifests, raw retrieval after source archive becomes unavailable, complete canonical/evidence/cursor restore and metadata disk allocation.
- [ ] Validate all typed payloads first, preserve origin/batch metadata, rebind restored raw references, reserve all allocations and avoid success on partial restore.
- [ ] Run new and existing object-storage/recovery tests including test-origin positive controls.

## Task4: source grounding, metadata and acquisition (C3/C4)

**Files:** `src/main.py`, `src/scrapers/statistics.py`, `src/ingestion.py`, source tests; narrow supporting schemas/prompts only when required.

- [ ] Add failing complete-run tests for passport inspection/CBAM formatting; correct unsupported mechanisms/scope to review without special-case phrase patches.
- [ ] Add failing Eurostat named dimensions and ECB MIR/EXR metadata tests; preserve target natural keys, intervals, flags and complete release provenance.
- [ ] Test provider-specific bounded request/auth/envelope/window contracts and article/feed provenance with offline responses. Keep fixtures out of production acquisition and expansion blocked where evidence/rights are absent.
- [ ] Include actual manifest origin/metadata required by Task3, then run source/runner/pilot suites.

## Task5: eligible dataset, baselines and temporal GNN (C7)

**Files:** `src/models/gnn_dataset.py`, `src/quant/forecasting.py`, `src/gnn/graph_exporter.py`, `src/gnn/temporal_gnn.py`, `src/gnn/trainer.py`, forecast/GNN acceptance tests.

- [ ] Add failing tests for staged/retracted labels, missing vintages, wrong quarter ends, denominator release, future lag targets and Cartesian context.
- [ ] Use eligible committed revision/release selection with complete period/base/unit identity; chronological release-aware baselines/splits and train-only transformations.
- [ ] Restrict graph scope/messages to supported valid edges, align recurrent stable IDs and masks across variable node sets, and independently fit/evaluate ablations.
- [ ] Persist reproducible dataset/model/prediction manifests through existing schemas; keep actual PyG execution unavailable if dependency is absent, without shims or unverified readiness claims.

## Task6: honest outlook and final integration (C8)

**Files:** `src/storage/dashboard_data.py`, prediction artifact ownership in existing storage/model layers, outlook tests and completion docs.

- [ ] Add failing tests for fake daily calibration, retracted unexpired context, staged-evidence fallback and missing/future/mismatched frozen artifacts.
- [ ] Read eligible latest facts/evidence and a validated frozen prediction; otherwise show explicit forecast gaps. Preserve CPI rejection, quarterly horizon and observed context.
- [ ] Run focused RED/GREEN checks, fresh independent code review, then one guarded broad suite after edits settle. Recheck independent counterexamples and migration/restore compatibility.
- [ ] Update readiness/memory with exact verified behavior and genuine live/manual/dependency gates; do not claim real data quality/model accuracy or seven-day operation without performing it.
