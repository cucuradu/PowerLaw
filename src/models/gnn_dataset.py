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
