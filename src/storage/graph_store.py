"""
Dual-Layer Graph & Tabular Storage Engine.
Backed by LanceDB with fallback to Arrow/Pandas persistence.
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
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
import networkx as nx

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


class DualLayerGraphStore:
    """
    Manages both:
    1. The topological Graph (Laws, Actions, Reactions, Sectors, Commodities)
    2. The behind-the-graph Tabular History (Timestamped metrics and state changes)
    """

    def __init__(self, storage_dir: str = "data/lancedb"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db = None
        
        # In-memory working cache
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: List[GraphEdge] = []
        self._history: List[NodeStateRecord] = []
        
        self._init_backend()

    def _init_backend(self):
        """Initialize LanceDB or file-based tables."""
        if HAS_LANCEDB:
            try:
                self.db = lancedb.connect(str(self.storage_dir))
            except Exception as e:
                logger.warning(f"Could not connect to LanceDB at {self.storage_dir}: {e}")
                self.db = None

        # Load existing if available
        self.load()

    def add_node(self, node: GraphNode) -> None:
        """Add or update a graph node."""
        self._nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge between nodes."""
        self._edges.append(edge)

    def add_state_record(self, record: NodeStateRecord) -> None:
        """Add a timestamped tabular record for a node."""
        self._history.append(record)

    def add_state_records_batch(self, records: List[NodeStateRecord]) -> None:
        """Batch append timestamped tabular records."""
        self._history.extend(records)

    def save(self) -> None:
        """Persist graph and tabular data to storage."""
        nodes_data = [n.to_dict() for n in self._nodes.values()]
        edges_data = [e.to_dict() for e in self._edges]
        history_data = [h.to_dict() for h in self._history]

        nodes_df = pd.DataFrame(nodes_data) if nodes_data else pd.DataFrame(columns=[
            "id", "label", "node_type", "description", "jurisdiction", "activation_date", "metadata_json"
        ])
        edges_df = pd.DataFrame(edges_data) if edges_data else pd.DataFrame(columns=[
            "source_id", "target_id", "relation_type", "weight", "timestamp", "properties_json"
        ])
        history_df = pd.DataFrame(history_data) if history_data else pd.DataFrame(columns=[
            "record_id", "timestamp", "timestamp_utc", "node_id", "node_type", "state_value",
            "metric_name", "metric_value", "delta", "unit", "source_id", "source_type",
            "confidence", "notes"
        ])

        # Parquet / Arrow local backup
        nodes_df.to_parquet(self.storage_dir / "graph_nodes.parquet", index=False)
        edges_df.to_parquet(self.storage_dir / "graph_edges.parquet", index=False)
        history_df.to_parquet(self.storage_dir / "node_tabular_history.parquet", index=False)

        # LanceDB storage if connected
        if self.db is not None:
            try:
                if len(nodes_df) > 0:
                    self.db.create_table("graph_nodes", data=nodes_df, mode="overwrite")
                if len(edges_df) > 0:
                    self.db.create_table("graph_edges", data=edges_df, mode="overwrite")
                if len(history_df) > 0:
                    self.db.create_table("node_tabular_history", data=history_df, mode="overwrite")
            except Exception as e:
                logger.warning(f"Error persisting to LanceDB: {e}")

    def load(self) -> None:
        """Load graph and tabular data from storage."""
        nodes_path = self.storage_dir / "graph_nodes.parquet"
        edges_path = self.storage_dir / "graph_edges.parquet"
        history_path = self.storage_dir / "node_tabular_history.parquet"

        if nodes_path.exists():
            try:
                ndf = pd.read_parquet(nodes_path)
                for _, row in ndf.iterrows():
                    meta = {}
                    if pd.notna(row.get("metadata_json")):
                        try:
                            meta = eval(row["metadata_json"]) if isinstance(row["metadata_json"], str) else {}
                        except Exception:
                            meta = {}
                    node = GraphNode(
                        id=str(row["id"]),
                        label=str(row["label"]),
                        node_type=NodeType(row["node_type"]) if row["node_type"] in NodeType._value2member_map_ else NodeType.ACTION,
                        description=str(row.get("description", "")),
                        jurisdiction=str(row.get("jurisdiction", "EU")),
                        activation_date=str(row.get("activation_date", "")),
                        metadata=meta
                    )
                    self._nodes[node.id] = node
            except Exception as e:
                logger.warning(f"Could not load nodes: {e}")

        if edges_path.exists():
            try:
                edf = pd.read_parquet(edges_path)
                self._edges = []
                for _, row in edf.iterrows():
                    props = {}
                    if pd.notna(row.get("properties_json")):
                        try:
                            props = eval(row["properties_json"]) if isinstance(row["properties_json"], str) else {}
                        except Exception:
                            props = {}
                    edge = GraphEdge(
                        source_id=str(row["source_id"]),
                        target_id=str(row["target_id"]),
                        relation_type=EdgeRelation(row["relation_type"]) if row["relation_type"] in EdgeRelation._value2member_map_ else EdgeRelation.TRIGGERS,
                        weight=float(row.get("weight", 1.0)),
                        timestamp=str(row.get("timestamp", "")),
                        properties=props
                    )
                    self._edges.append(edge)
            except Exception as e:
                logger.warning(f"Could not load edges: {e}")

        if history_path.exists():
            try:
                hdf = pd.read_parquet(history_path)
                self._history = []
                for _, row in hdf.iterrows():
                    rec = NodeStateRecord(
                        record_id=str(row["record_id"]),
                        timestamp=str(row["timestamp"]),
                        timestamp_utc=int(row.get("timestamp_utc", 0)),
                        node_id=str(row["node_id"]),
                        node_type=NodeType(row["node_type"]) if row["node_type"] in NodeType._value2member_map_ else NodeType.ACTION,
                        state_value=str(row["state_value"]),
                        metric_name=str(row["metric_name"]),
                        metric_value=float(row["metric_value"]),
                        delta=float(row.get("delta", 0.0)),
                        unit=str(row.get("unit", "INDEX")),
                        source_id=str(row.get("source_id", "")),
                        source_type=str(row.get("source_type", "EUR_LEX")),
                        confidence=float(row.get("confidence", 1.0)),
                        notes=str(row.get("notes", ""))
                    )
                    self._history.append(rec)
            except Exception as e:
                logger.warning(f"Could not load history: {e}")

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

    def to_networkx(self) -> nx.DiGraph:
        """Build NetworkX directed graph."""
        G = nx.DiGraph()
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
