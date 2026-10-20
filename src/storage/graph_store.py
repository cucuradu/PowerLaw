"""
Dual-Layer Graph & Tabular Storage Engine.
Backed by the shared canonical LanceDB store, with legacy Parquet read compatibility.
Provides:
1. Topological graph management (Nodes & Edges)
2. Behind-the-graph temporal tabular time-series storage (NodeStateRecord)
3. NetworkX graph analytics & Power-Law degree distribution calculation
4. GNN tensor extraction interface
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
import numpy as np
import pandas as pd
try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    nx = None
    HAS_NETWORKX = False

try:
    import lancedb
    HAS_LANCEDB = True
except ImportError:
    HAS_LANCEDB = False

from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)

logger = logging.getLogger(__name__)


def _safe_json_parse(raw_val: Any) -> Dict[str, Any]:
    """Safely parse JSON or dict properties without using unsafe eval()."""
    if pd.isna(raw_val) or raw_val is None:
        return {}
    if isinstance(raw_val, dict):
        return raw_val
    if isinstance(raw_val, str):
        val_str = raw_val.strip()
        if not val_str or val_str in ("{}", "None", "nan"):
            return {}
        try:
            return json.loads(val_str)
        except Exception:
            try:
                import ast
                res = ast.literal_eval(val_str)
                return res if isinstance(res, dict) else {}
            except Exception:
                return {}
    return {}


class DualLayerGraphStore:
    """
    Adapter preserving legacy interfaces and signatures while backed by the canonical DualLayerStore.
    Eliminates eval(), prevents unsafe overwrite persistence, and preserves multigraph multiplicity.
    """

    def __init__(self, storage_dir: Optional[Union[str, Path]] = None):
        if storage_dir is None:
            from src.config import get_settings
            storage_dir = get_settings().lancedb_path
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        from src.storage.dual_layer_store import DualLayerStore
        self.canonical_store = DualLayerStore(storage_dir=self.storage_dir)
        self.db = self.canonical_store.db

        # In-memory working cache preserving multigraph edge list
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: List[GraphEdge] = []
        self._history: List[NodeStateRecord] = []
        self.load(reload=False)  # the store constructor has just loaded everything

    @property
    def _generation(self) -> int:
        return self.canonical_store._generation

    def _get_disk_generation(self) -> int:
        return self.canonical_store._get_disk_generation()

    def add_node(self, node: GraphNode) -> None:
        self.canonical_store.add_node(node.model_dump(mode="json"))
        self._nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        data = edge.model_dump(mode="json")
        data["id"] = f"{edge.source_id}->{data['relation_type']}->{edge.target_id}_{len(self._edges)}"
        self.canonical_store.add_edge(data)
        self._edges.append(edge)

    def add_state_record(self, record: NodeStateRecord) -> None:
        self.canonical_store.add_history_record(record.model_dump(mode="json"))
        self._history.append(record)

    def add_state_records(self, records: List[NodeStateRecord]) -> None:
        for record in records:
            self.add_state_record(record)

    add_state_records_batch = add_state_records

    def save(self) -> None:
        """The canonical store owns persistence and propagates failures."""
        self.canonical_store.save()

    def load(self, reload: bool = True) -> None:
        """Adapt shared records without a second persistence format or default relations."""
        if reload:
            self.canonical_store.load()
        self._nodes.clear()
        self._edges.clear()
        self._history.clear()
        for node in self.canonical_store.get_nodes():
            metadata = node.metadata_dict
            self._nodes[node.id] = GraphNode(
                id=node.id, label=node.label, node_type=node.node_type,
                description=node.description, jurisdiction=node.jurisdiction,
                activation_date=metadata.get("activation_date"), metadata=metadata)
        for edge in self.canonical_store.get_edges():
            self._edges.append(GraphEdge(source_id=edge.source, target_id=edge.target,
                                          relation_type=edge.relationship_type, weight=edge.weight,
                                          timestamp=edge.timestamp or None, properties=edge.metadata_dict))
        for record in self.canonical_store.get_history():
            data = record.model_dump()
            data.update(record.metadata_dict)
            data["timestamp"] = record.effective_timestamp
            data.setdefault("node_type", NodeType.ACTION)
            data.setdefault("source_id", record.source_url)
            self._history.append(NodeStateRecord(**data))

    # Query APIs
    def get_nodes(self) -> List[GraphNode]:
        return list(self._nodes.values())

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self._nodes.get(node_id)

    def get_edges(self) -> List[GraphEdge]:
        return self._edges

    def get_history(self, node_id: Optional[str] = None) -> List[NodeStateRecord]:
        if node_id:
            return [h for h in self._history if h.node_id == node_id]
        return self._history

    def get_nodes_df(self) -> pd.DataFrame:
        data = [n.to_dict() for n in self._nodes.values()]
        return pd.DataFrame(data) if data else pd.DataFrame()

    def get_edges_df(self) -> pd.DataFrame:
        data = [e.to_dict() for e in self._edges]
        return pd.DataFrame(data) if data else pd.DataFrame()

    def get_history_df(self, node_id: Optional[str] = None) -> pd.DataFrame:
        history = self.get_history(node_id)
        data = [h.to_dict() for h in history]
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        if "timestamp" in df.columns:
            try:
                df["timestamp_dt"] = pd.to_datetime(df["timestamp"], format="mixed", errors="coerce")
            except (TypeError, ValueError):
                df["timestamp_dt"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df = df.sort_values("timestamp_dt", ascending=True)
        return df

    def to_networkx(self) -> nx.MultiDiGraph:
        """Build NetworkX directed multigraph preserving multiple edges between endpoints."""
        G = nx.MultiDiGraph()
        for node in self._nodes.values():
            G.add_node(
                node.id,
                label=node.label,
                node_type=node.node_type.value if isinstance(node.node_type, NodeType) else node.node_type,
                description=node.description,
                jurisdiction=node.jurisdiction,
                activation_date=node.activation_date or ""
            )

        for edge in self._edges:
            G.add_edge(
                edge.source_id,
                edge.target_id,
                relation_type=edge.relation_type.value if isinstance(edge.relation_type, EdgeRelation) else edge.relation_type,
                weight=float(edge.weight),
                timestamp=edge.timestamp or ""
            )
        return G


    def compute_power_law_metrics(self) -> Dict[str, Any]:
        """
        Compute scale-free degree distribution and power-law properties:
        P(k) ~ k^(-gamma)
        """
        G = self.to_networkx()
        if len(G) == 0:
            return {
                "num_nodes": 0,
                "num_edges": 0,
                "gamma": 0.0,
                "degree_distribution": {},
                "top_in_degree": [],
                "top_out_degree": [],
                "top_betweenness": []
            }

        degrees = [d for _, d in G.degree()]
        in_degrees = dict(G.in_degree())
        out_degrees = dict(G.out_degree())
        
        # Simple estimate of power-law exponent gamma via discrete MLE or log-log slope
        non_zero_degrees = [d for d in degrees if d > 0]
        if len(non_zero_degrees) > 5:
            d_min = min(non_zero_degrees)
            # Clauset-Shalizi-Newman continuous approximation for discrete power law
            gamma = 1.0 + len(non_zero_degrees) / sum(np.log(d / (d_min - 0.5)) for d in non_zero_degrees)
        else:
            gamma = 1.0

        betweenness = nx.betweenness_centrality(G)

        top_in = sorted(in_degrees.items(), key=lambda x: x[1], reverse=True)[:10]
        top_out = sorted(out_degrees.items(), key=lambda x: x[1], reverse=True)[:10]
        top_bet = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:10]

        degree_counts = pd.Series(degrees).value_counts().sort_index().to_dict()

        return {
            "num_nodes": len(G.nodes()),
            "num_edges": len(G.edges()),
            "gamma": round(float(gamma), 3),
            "density": round(float(nx.density(G)), 4),
            "degree_distribution": {int(k): int(v) for k, v in degree_counts.items()},
            "top_in_degree": [{"node_id": k, "label": self._nodes.get(k).label if k in self._nodes else k, "in_degree": v} for k, v in top_in],
            "top_out_degree": [{"node_id": k, "label": self._nodes.get(k).label if k in self._nodes else k, "out_degree": v} for k, v in top_out],
            "top_betweenness": [{"node_id": k, "label": self._nodes.get(k).label if k in self._nodes else k, "centrality": round(v, 4)} for k, v in top_bet]
        }

    def export_gnn_dataset(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        """
        Export dual-layer data structure into numeric matrices ready for downstream GNNs:
        - Node feature matrix X (N x D)
        - Edge index E (2 x M)
        - Edge attributes
        - Node ID to index mapping
        - Temporal feature tensors (evaluated as of timestamp if provided)
        """
        node_ids = list(self._nodes.keys())
        node2idx = {nid: i for i, nid in enumerate(node_ids)}
        N = len(node_ids)

        if N == 0:
            return {"num_nodes": 0, "num_edges": 0, "X": [], "edge_index": [[], []]}

        # Build feature matrix
        # Columns: [One-hot node type (6), in_degree_norm, out_degree_norm, latest_metric_val, history_count]
        G = self.to_networkx()
        in_deg = dict(G.in_degree())
        out_deg = dict(G.out_degree())
        max_in = max(in_deg.values()) if in_deg else 1
        max_out = max(out_deg.values()) if out_deg else 1

        node_types = [NodeType.LAW, NodeType.ACTION, NodeType.REACTION, NodeType.SECTOR, NodeType.COMMODITY, NodeType.ECONOMIC_INDEX]
        type2idx = {t: i for i, t in enumerate(node_types)}

        history_df = self.get_history_df()
        if as_of is not None and not history_df.empty and "timestamp" in history_df.columns:
            history_df = history_df[history_df["timestamp"].astype(str) <= str(as_of)]
        
        feature_rows = []
        for nid in node_ids:
            node = self._nodes[nid]
            # One-hot type (6)
            oh = [0.0] * len(node_types)
            if node.node_type in type2idx:
                oh[type2idx[node.node_type]] = 1.0

            # Degree topological features (2)
            deg_feats = [
                float(in_deg.get(nid, 0)) / max(max_in, 1),
                float(out_deg.get(nid, 0)) / max(max_out, 1)
            ]

            # Tabular temporal aggregations (3)
            if not history_df.empty and "node_id" in history_df.columns:
                nh = history_df[history_df["node_id"] == nid]
                if not nh.empty:
                    latest_val = float(nh.iloc[-1]["metric_value"])
                    mean_val = float(nh["metric_value"].mean())
                    count_rec = float(len(nh))
                else:
                    latest_val, mean_val, count_rec = 0.0, 0.0, 0.0
            else:
                latest_val, mean_val, count_rec = 0.0, 0.0, 0.0

            row = oh + deg_feats + [latest_val, mean_val, count_rec]
            feature_rows.append(row)

        X = np.array(feature_rows, dtype=np.float32)

        # Edge index
        edge_sources = []
        edge_targets = []
        edge_weights = []
        edge_types = []
        
        rel2idx = {rel: i for i, rel in enumerate(EdgeRelation)}

        for edge in self._edges:
            if edge.source_id in node2idx and edge.target_id in node2idx:
                edge_sources.append(node2idx[edge.source_id])
                edge_targets.append(node2idx[edge.target_id])
                edge_weights.append(float(edge.weight))
                edge_types.append(rel2idx.get(edge.relation_type, 0))

        edge_index = np.array([edge_sources, edge_targets], dtype=np.int64)

        return {
            "num_nodes": N,
            "num_edges": len(edge_sources),
            "feature_dim": X.shape[1],
            "feature_names": [
                "is_law", "is_action", "is_reaction", "is_sector", "is_commodity", "is_economic_index",
                "norm_in_degree", "norm_out_degree", "latest_metric_value", "mean_metric_value", "history_records_count"
            ],
            "node_mapping": node2idx,
            "node_ids": node_ids,
            "X": X.tolist(),
            "edge_index": edge_index.tolist(),
            "edge_weights": edge_weights,
            "edge_types": edge_types
        }
