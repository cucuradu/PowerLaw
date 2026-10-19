"""
Downstream GNN Interface & Dataset Generator for PowerLaw.
Prepares topological graph structures and behind-the-graph temporal tabular data
for Graph Neural Network (GNN) architectures (e.g., GCN, GAT, TGNN, STGCN).
"""

from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
from datetime import datetime

from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import NodeType, EdgeRelation


class DualLayerGNNDataset:
    """
    Constructs feature matrices and edge indices for downstream GNN training.
    Integrates topological node/edge properties with behind-the-graph temporal tabular data.
    """

    def __init__(self, graph_store: DualLayerGraphStore):
        self.store = graph_store

    def build_snapshot(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Builds a static or temporal graph snapshot up to `as_of_date`.
        Nodes and edges active up to the timestamp are retained, and tabular states
        are filtered to reflect only information known at `as_of_date`.
        """
        all_nodes = self.store.get_nodes()
        all_edges = self.store.get_edges()
        history_df = self.store.get_history_df()

        if as_of_date:
            as_of_dt = pd.to_datetime(as_of_date)
            # Filter history up to date
            if not history_df.empty and "timestamp_dt" in history_df.columns:
                history_df = history_df[history_df["timestamp_dt"] <= as_of_dt]

            # Filter nodes by activation_date if specified
            active_nodes = []
            for n in all_nodes:
                if n.activation_date:
                    try:
                        if pd.to_datetime(n.activation_date) <= as_of_dt:
                            active_nodes.append(n)
                    except Exception:
                        active_nodes.append(n)
                else:
                    active_nodes.append(n)
        else:
            active_nodes = all_nodes

        node_ids = [n.id for n in active_nodes]
        node2idx = {nid: i for i, nid in enumerate(node_ids)}
        N = len(node_ids)

        if N == 0:
            return {
                "as_of_date": as_of_date,
                "num_nodes": 0,
                "num_edges": 0,
                "X": np.empty((0, 11), dtype=np.float32),
                "edge_index": np.empty((2, 0), dtype=np.int64)
            }

        # Filter edges whose endpoints are both in active_nodes
        active_edges = []
        for e in all_edges:
            if e.source_id in node2idx and e.target_id in node2idx:
                if as_of_date and e.timestamp:
                    try:
                        if pd.to_datetime(e.timestamp) <= pd.to_datetime(as_of_date):
                            active_edges.append(e)
                    except Exception:
                        active_edges.append(e)
                else:
                    active_edges.append(e)

        # Feature matrix construction
        node_types = [NodeType.LAW, NodeType.ACTION, NodeType.REACTION, NodeType.SECTOR, NodeType.COMMODITY, NodeType.ECONOMIC_INDEX]
        type2idx = {t: i for i, t in enumerate(node_types)}

        # Degree calculations
        in_degrees = {nid: 0 for nid in node_ids}
        out_degrees = {nid: 0 for nid in node_ids}
        for e in active_edges:
            out_degrees[e.source_id] = out_degrees.get(e.source_id, 0) + 1
            in_degrees[e.target_id] = in_degrees.get(e.target_id, 0) + 1

        max_in = max(in_degrees.values()) if in_degrees and max(in_degrees.values()) > 0 else 1
        max_out = max(out_degrees.values()) if out_degrees and max(out_degrees.values()) > 0 else 1

        feature_matrix = []
        for nid in node_ids:
            node = self.store.get_node(nid)
            # One-hot node type (6)
            oh = [0.0] * len(node_types)
            if node and node.node_type in type2idx:
                oh[type2idx[node.node_type]] = 1.0

            # Degree features (2)
            deg_feats = [
                float(in_degrees[nid]) / max_in,
                float(out_degrees[nid]) / max_out
            ]

            # Tabular features (3)
            latest_val, mean_val, count_rec = 0.0, 0.0, 0.0
            if not history_df.empty and "node_id" in history_df.columns:
                sub = history_df[history_df["node_id"] == nid]
                if not sub.empty:
                    latest_val = float(sub.iloc[-1]["metric_value"])
                    mean_val = float(sub["metric_value"].mean())
                    count_rec = float(len(sub))

            feature_matrix.append(oh + deg_feats + [latest_val, mean_val, count_rec])

        X = np.array(feature_matrix, dtype=np.float32)

        # Edge Index
        edge_sources = [node2idx[e.source_id] for e in active_edges]
        edge_targets = [node2idx[e.target_id] for e in active_edges]
        edge_weights = [float(e.weight) for e in active_edges]
        edge_index = np.array([edge_sources, edge_targets], dtype=np.int64)

        return {
            "as_of_date": as_of_date or "latest",
            "num_nodes": N,
            "num_edges": len(active_edges),
            "feature_dim": X.shape[1],
            "feature_names": [
                "is_law", "is_action", "is_reaction", "is_sector", "is_commodity", "is_economic_index",
                "in_degree_norm", "out_degree_norm", "latest_metric_value", "mean_metric_value", "history_records_count"
            ],
            "node_ids": node_ids,
            "node_mapping": node2idx,
            "X": X,
            "edge_index": edge_index,
            "edge_weights": np.array(edge_weights, dtype=np.float32)
        }

    def generate_temporal_sequence(self, date_list: List[str]) -> List[Dict[str, Any]]:
        """
        Generate a time-series of graph snapshots (dynamic graph) for Temporal GNNs.
        """
        snapshots = []
        for d in sorted(date_list):
            snapshots.append(self.build_snapshot(as_of_date=d))
        return snapshots


# =========================================================================
# Release-Aware Real Forecasting Dataset Generator
# =========================================================================

from datetime import date, timezone


def _quarter_bounds(year: int, quarter: int) -> Tuple[date, date, str]:
    start_month = 3 * (quarter - 1) + 1
    start_d = date(year, start_month, 1)
    if quarter == 4:
        end_d = date(year + 1, 1, 1)
    else:
        end_d = date(year, start_month + 3, 1)
    label = f"{year}-Q{quarter}"
    return start_d, end_d, label


def _dt_to_quarter(dt: datetime) -> Tuple[int, int]:
    month = dt.month
    quarter = (month - 1) // 3 + 1
    return dt.year, quarter


def _add_quarters(year: int, quarter: int, delta: int) -> Tuple[int, int]:
    total_q = year * 4 + (quarter - 1) + delta
    new_year = total_q // 4
    new_quarter = (total_q % 4) + 1
    return new_year, new_quarter


def build_forecast_dataset(
    target_series_id: str, origins: List[datetime], horizon_periods: int = 1,
    mode: str = "public_as_of", store: Any = None, window_size: int = 8,
    label_cutoff: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Freeze quarterly inputs and evidenced first-release growth labels separately."""
    import hashlib
    import json
    import math
    from src.gnn.graph_exporter import export_as_of, to_heterodata, CANONICAL_NODE_TYPES, HAS_PYG
    from src.storage.dual_layer_store import DualLayerStore

    if horizon_periods != 1 or window_size < 1:
        raise ValueError("The registered target requires a one-quarter horizon and positive window")
    store = store or DualLayerStore()
    label_cutoff = store._utc(label_cutoff or datetime.now(timezone.utc)) if hasattr(store, "_utc") else (label_cutoff or datetime.now(timezone.utc))
    origins = sorted(store._utc(o) for o in origins)
    if len(set(origins)) != len(origins):
        raise ValueError("Duplicate forecast origins")
    definitions = {d.series_id: d for d in store.query_series(label_cutoff, mode)}
    definition = definitions.get(target_series_id)
    if definition is None or definition.frequency.upper() not in {"Q", "QUARTERLY"}:
        raise ValueError("A committed quarterly target definition is required")
    from src.config import load_data_sources_registry
    target_table = load_data_sources_registry()["primary_target"]["table_id"]
    if definition.dataset_id != target_table or definition.geography_id not in {"DE", "geo:DE"}:
        raise ValueError(f"The primary target is German residential construction PPI {target_table}")
    if definition.measurement_kind.upper() not in {"PRODUCER_PRICE_INDEX", "PRODUCER_PRICE"}:
        raise ValueError("Input costs, material prices and consumer prices cannot replace construction PPI")

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

    revisions = [o for o in store.query_observation_revisions(label_cutoff, mode)
                 if o.series_id == target_series_id and o.vintage_id and o.unit == definition.unit]
    # A current download of old periods cannot establish their historical release dates.
    verified = []
    for obs in revisions:
        docs = [d for d in store.documents_for(obs, label_cutoff, mode)
                if d.availability_basis in {"provider_vintage", "archived_release"}]
        if docs:
            links = [e for e in store.evidence_for(obs, label_cutoff, mode)
                     if e.document_version_id in {d.version_id for d in docs}]
            release = min(max(store._available(obs), store._available(e),
                              store._available(next(d for d in docs if d.version_id == e.document_version_id))) for e in links)
            verified.append((obs, release))
    samples, snapshot_hashes, label_lineage = [], [], []
    for origin in origins:
        if origin > label_cutoff:
            raise ValueError("Forecast origin is later than the dataset label cutoff")
        year, quarter = _dt_to_quarter(origin)
        denom_start, denom_end, denom_label = _quarter_bounds(year, quarter)
        ty, tq = _add_quarters(year, quarter, horizon_periods)
        target_start, target_end, target_label = _quarter_bounds(ty, tq)
        snapshots, hashes = [], []
        for step in range(window_size):
            delta = step - window_size + 1
            sy, sq = _add_quarters(year, quarter, delta)
            _, end, _ = _quarter_bounds(sy, sq)
            cutoff = origin if delta == 0 else datetime.combine(end, datetime.min.time(), timezone.utc) - pd.Timedelta(microseconds=1)
            raw = export_as_of(cutoff, mode=mode, store=store)
            # Global generation changes do not change the historical feature identity.
            hashes.append(digest({k: raw.get(k) for k in ("cutoff", "mode", "nodes", "edges", "observations")}))
            snapshots.append(to_heterodata(raw) if HAS_PYG else raw)
        snapshot_hashes.append(hashes)
        last = snapshots[-1]
        ids = list(last["series"].node_id) if HAS_PYG else sorted(n["id"] for n in last.get("nodes", []) if n.get("node_type") in ("SERIES", "ECONOMIC_INDEX") or n["id"].startswith("series:"))
        index = ids.index(target_series_id) if target_series_id in ids else -1
        candidates = []
        for denominator, da in verified:
            if (denominator.period_start, denominator.period_end_exclusive) != (str(denom_start), str(denom_end)):
                continue
            for numerator, na in verified:
                if (numerator.period_start, numerator.period_end_exclusive) != (str(target_start), str(target_end)):
                    continue
                if numerator.vintage_id != denominator.vintage_id or not numerator.source_unit or numerator.source_unit != denominator.source_unit:
                    continue
                if denominator.value == 0:
                    continue
                release = max(da, na)
                if release <= label_cutoff:
                    candidates.append((release, numerator.vintage_id, denominator.revision_id,
                                       numerator.revision_id, denominator, numerator))
        chosen = min(candidates, key=lambda c: c[:4]) if candidates else None
        value = 100 * (chosen[5].value / chosen[4].value - 1) if chosen else math.nan
        valid = chosen is not None and math.isfinite(value)
        sample = {"origin": origin, "target_series_id": target_series_id,
                  "target_period_start": str(target_start), "target_period_end_exclusive": str(target_end),
                  "target_period_label": target_label, "denominator_period_label": denom_label,
                  "snapshots": snapshots, "cutoff": origin, "target_series_index": np.array([index], dtype=np.int64),
                  "target_series_present": index >= 0, "y": np.array([[value]], dtype=np.float32),
                  "y_mask": np.array([[valid]], dtype=bool), "vintage_id": chosen[1] if valid else None,
                  "first_release_available_at": chosen[0] if valid else None,
                  "label_revision_ids": list(chosen[2:4]) if valid else [],
                  "label_missing_reason": None if valid else "no_eligible_comparable_verified_release"}
        samples.append(sample)
        label_lineage.append({k: sample[k] for k in ("origin", "vintage_id", "first_release_available_at", "label_revision_ids")})
    feature_profile = {"feature_dim": 8, "node_types": CANONICAL_NODE_TYPES, "edge_dim": 12, "window_size": window_size}
    manifest = {"schema_version": 1, "num_samples": len(samples), "target_series_id": target_series_id,
                "mode": mode, "horizon_periods": horizon_periods, "window_size": window_size,
                "origins": [o.isoformat() for o in origins], "label_cutoff": label_cutoff.isoformat(),
                "committed_generation": store.generation, "input_table_versions": store._state.get("tables", {}),
                "target_spec_id": digest(definition.model_dump(mode="json")), "feature_profile_id": digest(feature_profile),
                "snapshot_hashes": snapshot_hashes, "labels": label_lineage}
    dataset_id = "dataset:" + digest(manifest)
    return {"dataset_id": dataset_id, "target_series_id": target_series_id, "mode": mode,
            "horizon_periods": horizon_periods, "samples": samples, "feature_profile": feature_profile,
            "manifest": {**manifest, "dataset_id": dataset_id}}
