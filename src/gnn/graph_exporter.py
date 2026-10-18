"""
Graph Neural Network (GNN) Dataset Preparation & Exporter for PowerLaw.

Pulls from the dual-layer storage engine (topological graph + tabular history)
and compiles:
1. Node feature matrix X:
   - Text embeddings (semantic representations from metadata, external models, or deterministic hashing)
   - Topological centrality (in-degree, out-degree, betweenness, closeness, PageRank, clustering, harmonic)
   - Aggregated tabular metrics (latest value, mean, std, min, max, delta, record count, trend slope, volatility)
   - Categorical encodings (node type, jurisdiction, dynamic state)
2. Edge index tensor E and adjacency matrix A (dense, normalized, or sparse).
3. Chronological temporal graph sequence snapshots:
   - Slices topological graph and tabular history across time windows (t_0, t_1, ..., t_k)
   - Supports cumulative and sliding windows
   - Formatted for Temporal GNNs (TGNN / T-GCN / EvolveGCN / TGAT / PyG-Temporal)
4. Export formats:
   - PyTorch Geometric (PyG) compatible tensors and dictionaries
   - JSON graph formats (node-link and temporal snapshot schema)
   - NumPy compressed archive (.npz) for static and temporal sequence datasets
"""

import json
import math
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime, date, timezone
from typing import List, Dict, Any, Optional, Tuple, Union, Callable
from enum import Enum

import numpy as np
import pandas as pd
import networkx as nx
from scipy import sparse

try:
    import torch
    HAS_TORCH = True
except ImportError:
    torch = None
    HAS_TORCH = False

try:
    import torch_geometric
    from torch_geometric.data import Data as PyGData
    HAS_PYG = True
except ImportError:
    torch_geometric = None
    PyGData = None
    HAS_PYG = False

from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)

logger = logging.getLogger(__name__)


def _normalize_dt(val: Any) -> Optional[pd.Timestamp]:
    """
    Safely convert any scalar date/timestamp representation into a tz-naive UTC pd.Timestamp.
    Handles ISO strings, unix timestamps, dates, tz-aware and tz-naive datetimes uniformly,
    preventing TypeError: Cannot compare tz-naive and tz-aware timestamps.
    """
    if val is None or pd.isna(val) or val == "":
        return None
    try:
        ts = pd.to_datetime(val, utc=True)
        if ts is pd.NaT:
            return None
        return ts.tz_localize(None) if ts.tzinfo is None else ts.tz_convert(None)
    except Exception:
        return None


def _to_datetime_series(series: pd.Series) -> pd.Series:
    """
    Safely convert a pandas Series of mixed datetime representations into tz-naive UTC timestamps.
    Handles mixed date formats and offsets across records without turning valid dates into NaT.
    """
    if series is None or series.empty:
        return pd.Series(dtype="datetime64[us]")
    try:
        converted = pd.to_datetime(series, format="mixed", utc=True, errors="coerce")
    except (TypeError, ValueError):
        converted = pd.to_datetime(series, utc=True, errors="coerce")
    if hasattr(converted, "dt") and hasattr(converted.dt, "tz_convert"):
        try:
            return converted.dt.tz_convert(None)
        except TypeError:
            return converted
    return converted


class GNNJsonEncoder(json.JSONEncoder):
    """Custom JSON encoder handling NumPy types, sets, datetimes, and Pydantic models."""
    def default(self, obj: Any) -> Any:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
            if math.isnan(obj) or math.isinf(obj):
                return 0.0
            return float(obj)
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        if isinstance(obj, (datetime, date, pd.Timestamp)):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, set):
            return list(obj)
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            return obj.model_dump()
        return super().default(obj)


class GNNGraphExporter:
    """
    Compiles dual-layer graph and tabular history into static and temporal
    tensors ready for Graph Neural Network training and inference.
    """

    NODE_TYPES: List[NodeType] = [
        NodeType.LAW,
        NodeType.ACTION,
        NodeType.REACTION,
        NodeType.SECTOR,
        NodeType.COMMODITY,
        NodeType.ECONOMIC_INDEX
    ]

    EDGE_RELATIONS: List[EdgeRelation] = list(EdgeRelation)

    COMMON_STATES: List[str] = [
        "ACTIVE",
        "ENFORCED",
        "PROPOSED",
        "PEAK",
        "CRITICAL",
        "INCREASE",
        "DECREASE",
        "STABLE"
    ]

    COMMON_JURISDICTIONS: List[str] = ["EU", "DE", "FR", "GLOBAL", "OTHER"]

    def __init__(
        self,
        store: Optional[DualLayerGraphStore] = None,
        storage_dir: Optional[Union[str, Path]] = "data/lancedb",
        embedding_dim: int = 128,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
        custom_embeddings: Optional[Dict[str, List[float]]] = None,
        include_text_embeddings: bool = True,
        include_centrality: bool = True,
        include_tabular_metrics: bool = True,
        include_categorical: bool = True,
        metric_filter: Optional[str] = None,
    ):
        """
        Initialize the GNN Exporter.

        Args:
            store: Optional existing DualLayerGraphStore instance.
            storage_dir: Directory path to load the store from if store is not provided.
            embedding_dim: Dimension for node text embeddings (default 128).
            embedding_fn: Optional callable to generate embeddings from text strings.
            custom_embeddings: Optional precomputed dictionary {node_id: list_of_floats}.
            include_text_embeddings: Whether to incorporate text embeddings in X.
            include_centrality: Whether to incorporate topological centrality in X.
            include_tabular_metrics: Whether to incorporate historical tabular aggregates in X.
            include_categorical: Whether to incorporate one-hot node types & jurisdictions in X.
            metric_filter: Optional filter to restrict tabular aggregations to a specific metric_name.
        """
        if store is not None:
            self.store = store
        else:
            self.store = DualLayerGraphStore(str(storage_dir))

        self.embedding_dim = max(0, int(embedding_dim))
        self.embedding_fn = embedding_fn
        self.custom_embeddings = custom_embeddings or {}
        self.include_text_embeddings = include_text_embeddings
        self.include_centrality = include_centrality
        self.include_tabular_metrics = include_tabular_metrics
        self.include_categorical = include_categorical
        self.metric_filter = metric_filter

        # Internal embedding cache
        self._embedding_cache: Dict[str, np.ndarray] = {}

    # -------------------------------------------------------------------------
    # Text Embedding Compilation
    # -------------------------------------------------------------------------
    def _deterministic_text_embedding(self, text: str, dim: int) -> np.ndarray:
        """
        Deterministic, unit-norm pseudo-embedding generated via hashing and n-gram projection.
        Ensures consistent, reproducible feature vectors even without network/API access.
        """
        if dim <= 0:
            return np.empty(0, dtype=np.float32)
        if not text:
            return np.zeros(dim, dtype=np.float32)

        v = np.zeros(dim, dtype=np.float32)
        clean = text.lower().strip()
        words = re.findall(r"\w+", clean)

        # Collect word tokens and subword 3-grams
        tokens = list(words)
        for w in words:
            if len(w) >= 3:
                for i in range(len(w) - 2):
                    tokens.append(w[i:i + 3])

        if not tokens:
            return np.zeros(dim, dtype=np.float32)

        for token in tokens:
            # Deterministic bucket index via SHA256
            h_idx = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dim
            # Deterministic sign via MD5
            sign = 1.0 if (int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % 2 == 0) else -1.0
            v[h_idx] += sign

        norm = np.linalg.norm(v)
        if norm > 1e-9:
            v = v / norm
        return v.astype(np.float32)

    def get_node_embedding(self, node: GraphNode) -> np.ndarray:
        """
        Retrieve or compute text embedding for a node.
        Hierarchy:
        1. Node metadata 'embedding' or 'vector'
        2. custom_embeddings dict
        3. embedding_fn (if provided)
        4. Deterministic semantic hash projection
        """
        if node.id in self._embedding_cache:
            return self._embedding_cache[node.id]

        if self.embedding_dim <= 0:
            return np.empty(0, dtype=np.float32)

        emb = None

        # 1. Metadata check
        if isinstance(node.metadata, dict):
            for k in ("embedding", "vector"):
                if k in node.metadata:
                    raw = node.metadata[k]
                    if isinstance(raw, (list, tuple, np.ndarray)) and len(raw) > 0:
                        emb = np.array(raw, dtype=np.float32)
                        break
                    elif isinstance(raw, str) and raw.strip().startswith("["):
                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, list) and len(parsed) > 0:
                                emb = np.array(parsed, dtype=np.float32)
                                break
                        except Exception:
                            pass

        # 2. Custom dictionary check
        if emb is None and node.id in self.custom_embeddings:
            emb = np.array(self.custom_embeddings[node.id], dtype=np.float32)

        # 3. External embedding callable
        text_content = f"{node.label}. {node.description}".strip()
        if emb is None and self.embedding_fn is not None and text_content:
            try:
                res = self.embedding_fn(text_content)
                if isinstance(res, (list, tuple, np.ndarray)):
                    emb = np.array(res, dtype=np.float32)
            except Exception as e:
                logger.warning(f"Error calling embedding_fn for node {node.id}: {e}")

        # 4. Fallback deterministic hash embedding
        if emb is None:
            emb = self._deterministic_text_embedding(text_content or node.id, self.embedding_dim)

        # Ensure exact matching embedding_dim
        if len(emb) != self.embedding_dim:
            if len(emb) > self.embedding_dim:
                emb = emb[:self.embedding_dim]
            else:
                padded = np.zeros(self.embedding_dim, dtype=np.float32)
                padded[:len(emb)] = emb
                emb = padded

        # Normalize
        norm = np.linalg.norm(emb)
        if norm > 1e-9:
            emb = emb / norm

        self._embedding_cache[node.id] = emb.astype(np.float32)
        return self._embedding_cache[node.id]

    # -------------------------------------------------------------------------
    # Topological Centrality Compilation
    # -------------------------------------------------------------------------
    def compute_topological_features(
        self,
        nodes: List[GraphNode],
        edges: List[GraphEdge]
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Compute graph topological centrality metrics for nodes using NetworkX.
        Returns:
            matrix of shape (N, 10), feature names list.
        """
        N = len(nodes)
        names = [
            "in_degree_norm",
            "out_degree_norm",
            "degree_norm",
            "log_in_degree",
            "log_out_degree",
            "betweenness_centrality",
            "closeness_centrality",
            "pagerank",
            "harmonic_centrality",
            "clustering_coeff"
        ]

        if N == 0:
            return np.empty((0, len(names)), dtype=np.float32), names

        G = nx.DiGraph()
        for n in nodes:
            G.add_node(n.id)

        node_set = {n.id for n in nodes}
        for e in edges:
            if e.source_id in node_set and e.target_id in node_set:
                w = float(e.weight) if e.weight is not None else 1.0
                G.add_edge(e.source_id, e.target_id, weight=w)

        norm_factor = max(1, N - 1)
        in_degrees = dict(G.in_degree())
        out_degrees = dict(G.out_degree())

        # Centrality metrics
        betweenness = nx.betweenness_centrality(G, weight=None)
        closeness = nx.closeness_centrality(G)

        try:
            pagerank = nx.pagerank(G, alpha=0.85, max_iter=200, tol=1e-6)
        except Exception:
            pagerank = {n.id: 1.0 / N for n in nodes}

        try:
            harmonic_raw = nx.harmonic_centrality(G)
            harmonic = {nid: val / norm_factor for nid, val in harmonic_raw.items()}
        except Exception:
            harmonic = {n.id: 0.0 for n in nodes}

        try:
            clustering = nx.clustering(G.to_undirected())
        except Exception:
            clustering = {n.id: 0.0 for n in nodes}

        rows = []
        for n in nodes:
            nid = n.id
            in_d = in_degrees.get(nid, 0)
            out_d = out_degrees.get(nid, 0)
            tot_d = in_d + out_d

            row = [
                float(in_d) / norm_factor,
                float(out_d) / norm_factor,
                float(tot_d) / max(1, 2 * norm_factor),
                float(np.log1p(in_d)),
                float(np.log1p(out_d)),
                float(betweenness.get(nid, 0.0)),
                float(closeness.get(nid, 0.0)),
                float(pagerank.get(nid, 0.0)),
                float(harmonic.get(nid, 0.0)),
                float(clustering.get(nid, 0.0))
            ]
            rows.append(row)

        arr = np.nan_to_num(np.array(rows, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        return arr, names

    # -------------------------------------------------------------------------
    # Tabular Metrics Aggregation
    # -------------------------------------------------------------------------
    def compute_tabular_features(
        self,
        nodes: List[GraphNode],
        history_df: pd.DataFrame,
        metric_filter: Optional[str] = None
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Aggregate behind-the-graph temporal tabular metrics for each node.
        Returns:
            matrix of shape (N, 20), feature names list.
        """
        N = len(nodes)
        stat_names = [
            "latest_metric_value",
            "mean_metric_value",
            "std_metric_value",
            "min_metric_value",
            "max_metric_value",
            "latest_delta",
            "mean_delta",
            "sum_delta",
            "records_count",
            "records_count_log",
            "volatility",
            "trend_slope"
        ]
        state_names = [f"state_{s.lower()}" for s in self.COMMON_STATES]
        names = stat_names + state_names

        if N == 0:
            return np.empty((0, len(names)), dtype=np.float32), names

        active_metric_filter = metric_filter or self.metric_filter

        # Group history by node_id if non-empty
        grouped: Dict[str, pd.DataFrame] = {}
        if not history_df.empty and "node_id" in history_df.columns:
            work_df = history_df.copy()
            # Ensure proper chronological sort across any timezone format
            if "timestamp" in work_df.columns:
                work_df["_sort_dt"] = _to_datetime_series(work_df["timestamp"])
                work_df = work_df.sort_values("_sort_dt", ascending=True).drop(columns=["_sort_dt"])
            for nid, grp in work_df.groupby("node_id"):
                grouped[str(nid)] = grp

        rows = []
        for n in nodes:
            sub = grouped.get(n.id)
            if active_metric_filter and sub is not None and not sub.empty and "metric_name" in sub.columns:
                sub = sub[sub["metric_name"].astype(str).str.lower() == active_metric_filter.lower()]

            if sub is not None and not sub.empty:
                vals = pd.to_numeric(sub["metric_value"], errors="coerce").dropna().to_numpy(dtype=np.float64)
                deltas = pd.to_numeric(sub["delta"], errors="coerce").dropna().to_numpy(dtype=np.float64) if "delta" in sub.columns else np.zeros_like(vals)
                if len(deltas) == 0 and len(vals) > 0:
                    deltas = np.zeros_like(vals)

                if len(vals) > 0:
                    latest_val = float(vals[-1])
                    mean_val = float(np.mean(vals))
                    std_val = float(np.std(vals)) if len(vals) > 1 else 0.0
                    min_val = float(np.min(vals))
                    max_val = float(np.max(vals))

                    latest_d = float(deltas[-1]) if len(deltas) > 0 else 0.0
                    mean_d = float(np.mean(deltas)) if len(deltas) > 0 else 0.0
                    sum_d = float(np.sum(deltas)) if len(deltas) > 0 else 0.0

                    count_rec = float(len(vals))
                    count_log = float(np.log1p(count_rec))
                    volatility = float(std_val / (abs(mean_val) + 1e-4))

                    # Trend slope calculation via linear regression over days
                    trend_slope = 0.0
                    if len(vals) >= 2 and "timestamp" in sub.columns:
                        try:
                            s_dt = _to_datetime_series(sub["timestamp"]).dropna()
                            if len(s_dt) >= 2:
                                dts = s_dt.to_numpy()
                                t_days = (dts - dts[0]) / np.timedelta64(1, "D")
                                t_diff = t_days - np.mean(t_days)
                                denom = float(np.sum(t_diff ** 2))
                                if denom > 1e-5:
                                    aligned_vals = vals[:len(t_days)]
                                    aligned_mean = float(np.mean(aligned_vals))
                                    trend_slope = float(np.sum(t_diff * (aligned_vals - aligned_mean)) / denom)
                        except Exception:
                            trend_slope = 0.0

                    # State one-hot
                    last_state = str(sub.iloc[-1].get("state_value", "")).strip().upper()
                    state_vec = [1.0 if s == last_state else 0.0 for s in self.COMMON_STATES]

                    row = [
                        latest_val, mean_val, std_val, min_val, max_val,
                        latest_d, mean_d, sum_d,
                        count_rec, count_log, volatility, trend_slope
                    ] + state_vec
                else:
                    row = [0.0] * len(stat_names) + [0.0] * len(state_names)
            else:
                # Node has no tabular history yet
                row = [0.0] * len(stat_names) + [0.0] * len(state_names)

            rows.append(row)

        arr = np.nan_to_num(np.array(rows, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        return arr, names

    # -------------------------------------------------------------------------
    # Categorical & Identity Features Compilation
    # -------------------------------------------------------------------------
    def compute_categorical_features(
        self,
        nodes: List[GraphNode]
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Encode node types and jurisdictions as one-hot vectors.
        Returns:
            matrix of shape (N, len(NODE_TYPES) + len(COMMON_JURISDICTIONS))
        """
        N = len(nodes)
        type_names = [f"type_{t.value.lower()}" for t in self.NODE_TYPES]
        jurisdiction_names = [f"jurisdiction_{j.lower()}" for j in self.COMMON_JURISDICTIONS]
        names = type_names + jurisdiction_names

        if N == 0:
            return np.empty((0, len(names)), dtype=np.float32), names

        type2idx = {t: i for i, t in enumerate(self.NODE_TYPES)}
        jur2idx = {j: i for i, j in enumerate(self.COMMON_JURISDICTIONS)}

        rows = []
        for n in nodes:
            # One-hot node type safely handling string variants
            t_vec = [0.0] * len(self.NODE_TYPES)
            curr_type = None
            try:
                if isinstance(n.node_type, NodeType):
                    curr_type = n.node_type
                else:
                    curr_type = NodeType(str(n.node_type).upper())
            except Exception:
                curr_type = None

            if curr_type in type2idx:
                t_vec[type2idx[curr_type]] = 1.0

            # One-hot jurisdiction
            j_vec = [0.0] * len(self.COMMON_JURISDICTIONS)
            jur_clean = str(n.jurisdiction or "EU").strip().upper()
            if jur_clean in jur2idx:
                j_vec[jur2idx[jur_clean]] = 1.0
            else:
                j_vec[jur2idx["OTHER"]] = 1.0

            rows.append(t_vec + j_vec)

        return np.array(rows, dtype=np.float32), names

    # -------------------------------------------------------------------------
    # Node Feature Matrix Compilation
    # -------------------------------------------------------------------------
    def compile_node_features(
        self,
        as_of_date: Optional[str] = None,
        nodes: Optional[List[GraphNode]] = None,
        edges: Optional[List[GraphEdge]] = None,
        history_df: Optional[pd.DataFrame] = None,
        metric_filter: Optional[str] = None
    ) -> Tuple[np.ndarray, List[str], Dict[str, Tuple[int, int]]]:
        """
        Compile full node feature matrix X combining text embeddings, topological
        centrality, tabular metrics, and categorical encodings.

        Returns:
            X: Matrix of shape (N, D)
            feature_names: List of all D feature names
            feature_slices: Dict mapping component name -> (start_idx, end_idx)
        """
        target_nodes = nodes if nodes is not None else self.store.get_nodes()
        target_edges = edges if edges is not None else self.store.get_edges()

        if history_df is None:
            raw_hist = self.store.get_history_df()
            if as_of_date and not raw_hist.empty and "timestamp" in raw_hist.columns:
                cutoff = _normalize_dt(as_of_date)
                if cutoff is not None:
                    raw_hist_copy = raw_hist.copy()
                    raw_hist_copy["_ts_dt"] = _to_datetime_series(raw_hist_copy["timestamp"])
                    target_hist = raw_hist_copy[raw_hist_copy["_ts_dt"] <= cutoff].drop(columns=["_ts_dt"])
                else:
                    target_hist = raw_hist
            else:
                target_hist = raw_hist
        else:
            target_hist = history_df

        N = len(target_nodes)
        components: List[np.ndarray] = []
        all_names: List[str] = []
        slices: Dict[str, Tuple[int, int]] = {}
        curr_idx = 0

        # 1. Text Embeddings
        if self.include_text_embeddings and self.embedding_dim > 0:
            emb_names = [f"text_emb_{i}" for i in range(self.embedding_dim)]
            if N > 0:
                emb_rows = [self.get_node_embedding(n) for n in target_nodes]
                emb_mat = np.array(emb_rows, dtype=np.float32)
            else:
                emb_mat = np.empty((0, self.embedding_dim), dtype=np.float32)
            components.append(emb_mat)
            all_names.extend(emb_names)
            slices["text_embedding"] = (curr_idx, curr_idx + self.embedding_dim)
            curr_idx += self.embedding_dim

        # 2. Topological Centrality
        if self.include_centrality:
            topo_mat, topo_names = self.compute_topological_features(target_nodes, target_edges)
            components.append(topo_mat)
            all_names.extend(topo_names)
            slices["topological_centrality"] = (curr_idx, curr_idx + len(topo_names))
            curr_idx += len(topo_names)

        # 3. Aggregated Tabular Metrics
        if self.include_tabular_metrics:
            tab_mat, tab_names = self.compute_tabular_features(target_nodes, target_hist, metric_filter=metric_filter)
            components.append(tab_mat)
            all_names.extend(tab_names)
            slices["tabular_metrics"] = (curr_idx, curr_idx + len(tab_names))
            curr_idx += len(tab_names)

        # 4. Categorical Encodings
        if self.include_categorical:
            cat_mat, cat_names = self.compute_categorical_features(target_nodes)
            components.append(cat_mat)
            all_names.extend(cat_names)
            slices["categorical"] = (curr_idx, curr_idx + len(cat_names))
            curr_idx += len(cat_names)

        if components and N > 0:
            X = np.hstack(components).astype(np.float32)
        else:
            X = np.empty((N, curr_idx), dtype=np.float32)

        return X, all_names, slices

    # -------------------------------------------------------------------------
    # Edge Index & Adjacency Matrix Compilation
    # -------------------------------------------------------------------------
    def compile_edge_index(
        self,
        nodes: Optional[List[GraphNode]] = None,
        edges: Optional[List[GraphEdge]] = None,
        node_mapping: Optional[Dict[str, int]] = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compile edge index tensor [2, E], edge attributes [E, D_edge], and edge weights [E].

        Returns:
            edge_index: (2, E) integer array
            edge_attr: (E, 8) float32 array (7 one-hot relation types + 1 numeric weight)
            edge_weight: (E,) float32 array
        """
        target_nodes = nodes if nodes is not None else self.store.get_nodes()
        target_edges = edges if edges is not None else self.store.get_edges()

        if node_mapping is None:
            node_mapping = {n.id: i for i, n in enumerate(target_nodes)}

        rel2idx = {rel: i for i, rel in enumerate(self.EDGE_RELATIONS)}

        src_list: List[int] = []
        dst_list: List[int] = []
        weight_list: List[float] = []
        attr_list: List[List[float]] = []

        for e in target_edges:
            if e.source_id in node_mapping and e.target_id in node_mapping:
                u = node_mapping[e.source_id]
                v = node_mapping[e.target_id]
                w = float(e.weight) if e.weight is not None else 1.0

                src_list.append(u)
                dst_list.append(v)
                weight_list.append(w)

                # Edge attributes: 7 one-hot relation types + weight
                rel_oh = [0.0] * len(self.EDGE_RELATIONS)
                rel_enum = None
                try:
                    if isinstance(e.relation_type, EdgeRelation):
                        rel_enum = e.relation_type
                    else:
                        rel_enum = EdgeRelation(str(e.relation_type).upper())
                except Exception:
                    rel_enum = None

                if rel_enum in rel2idx:
                    rel_oh[rel2idx[rel_enum]] = 1.0

                attr_list.append(rel_oh + [w])

        if src_list:
            edge_index = np.array([src_list, dst_list], dtype=np.int64)
            edge_attr = np.array(attr_list, dtype=np.float32)
            edge_weight = np.array(weight_list, dtype=np.float32)
        else:
            edge_index = np.empty((2, 0), dtype=np.int64)
            edge_attr = np.empty((0, len(self.EDGE_RELATIONS) + 1), dtype=np.float32)
            edge_weight = np.empty(0, dtype=np.float32)

        return edge_index, edge_attr, edge_weight

    def compile_adjacency_matrix(
        self,
        nodes: Optional[List[GraphNode]] = None,
        edges: Optional[List[GraphEdge]] = None,
        normalized: bool = False,
        self_loops: bool = True,
        symmetrize: bool = False
    ) -> np.ndarray:
        """
        Compile dense adjacency matrix A in R^(N x N).
        Args:
            nodes: Optional subset of nodes.
            edges: Optional subset of edges.
            normalized: If True, computes normalized adjacency:
                        - If symmetrize=True: Kipf & Welling GCN symmetric norm D^(-1/2) * A_tilde * D^(-1/2)
                        - If symmetrize=False: Directed norm D_out^(-1/2) * A_tilde * D_in^(-1/2)
                          preserving all directed edges without zeroing out edges to sink nodes.
            self_loops: If True, adds self-loops A + I_N before normalization.
            symmetrize: If True, symmetrizes the adjacency matrix: np.maximum(A, A.T).
        """
        target_nodes = nodes if nodes is not None else self.store.get_nodes()
        target_edges = edges if edges is not None else self.store.get_edges()
        N = len(target_nodes)

        if N == 0:
            return np.empty((0, 0), dtype=np.float32)

        node2idx = {n.id: i for i, n in enumerate(target_nodes)}
        A = np.zeros((N, N), dtype=np.float32)

        for e in target_edges:
            if e.source_id in node2idx and e.target_id in node2idx:
                u = node2idx[e.source_id]
                v = node2idx[e.target_id]
                w = float(e.weight) if e.weight is not None else 1.0
                A[u, v] += w

        if symmetrize:
            # Symmetrize adjacency matrix preserving max connection weight
            A = np.maximum(A, A.T)

        if self_loops:
            A = A + np.eye(N, dtype=np.float32)

        if normalized:
            if symmetrize:
                # Kipf & Welling GCN symmetric normalization for undirected graphs
                deg = np.sum(A, axis=1)
                deg_inv_sqrt = np.zeros_like(deg)
                pos_mask = deg > 0
                deg_inv_sqrt[pos_mask] = np.power(deg[pos_mask], -0.5)
                D_inv_sqrt = np.diag(deg_inv_sqrt)
                A = D_inv_sqrt @ A @ D_inv_sqrt
            else:
                # Directed normalization: D_out^(-1/2) * A * D_in^(-1/2)
                # Left-multiply by out-degree, right-multiply by in-degree
                # Ensures incoming edges to sink nodes and outgoing edges from source nodes are preserved
                deg_out = np.sum(A, axis=1)
                deg_in = np.sum(A, axis=0)

                d_out_inv = np.zeros_like(deg_out)
                d_in_inv = np.zeros_like(deg_in)

                pos_out = deg_out > 0
                pos_in = deg_in > 0

                d_out_inv[pos_out] = np.power(deg_out[pos_out], -0.5)
                d_in_inv[pos_in] = np.power(deg_in[pos_in], -0.5)

                A = np.diag(d_out_inv) @ A @ np.diag(d_in_inv)

        return A.astype(np.float32)

    # -------------------------------------------------------------------------
    # Static Graph Compilation
    # -------------------------------------------------------------------------
    def compile_static_graph(
        self,
        as_of_date: Optional[str] = None,
        symmetrize: bool = False,
        metric_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Compile full static dual-layer graph dataset with node features X,
        edge index, edge attributes, adjacency matrix, and metadata.
        """
        nodes = self.store.get_nodes()
        edges = self.store.get_edges()

        # If as_of_date provided, filter active nodes/edges safely with timezone normalization
        if as_of_date:
            cutoff = _normalize_dt(as_of_date)
            if cutoff is not None:
                filtered_nodes = []
                for n in nodes:
                    norm_act = _normalize_dt(n.activation_date)
                    if norm_act is not None:
                        if norm_act <= cutoff:
                            filtered_nodes.append(n)
                    else:
                        filtered_nodes.append(n)
                nodes = filtered_nodes

                active_node_ids = {n.id for n in nodes}
                filtered_edges = []
                for e in edges:
                    if e.source_id in active_node_ids and e.target_id in active_node_ids:
                        norm_e_ts = _normalize_dt(e.timestamp)
                        if norm_e_ts is not None:
                            if norm_e_ts <= cutoff:
                                filtered_edges.append(e)
                        else:
                            filtered_edges.append(e)
                edges = filtered_edges

        node_ids = [n.id for n in nodes]
        node2idx = {nid: i for i, nid in enumerate(node_ids)}

        X, feature_names, feature_slices = self.compile_node_features(
            as_of_date=as_of_date, nodes=nodes, edges=edges, metric_filter=metric_filter
        )
        edge_index, edge_attr, edge_weight = self.compile_edge_index(
            nodes=nodes, edges=edges, node_mapping=node2idx
        )
        adj = self.compile_adjacency_matrix(
            nodes=nodes, edges=edges, normalized=False, self_loops=False, symmetrize=symmetrize
        )
        adj_norm = self.compile_adjacency_matrix(
            nodes=nodes, edges=edges, normalized=True, self_loops=True, symmetrize=symmetrize
        )

        return {
            "num_nodes": len(node_ids),
            "num_edges": edge_index.shape[1],
            "feature_dim": X.shape[1] if X.ndim > 1 else 0,
            "edge_dim": edge_attr.shape[1] if edge_attr.ndim > 1 else 0,
            "node_ids": node_ids,
            "node_mapping": node2idx,
            "feature_names": feature_names,
            "feature_slices": feature_slices,
            "X": X,
            "edge_index": edge_index,
            "edge_attr": edge_attr,
            "edge_weight": edge_weight,
            "edge_weights": edge_weight,  # Field alias for UI compatibility
            "adjacency": adj,
            "normalized_adjacency": adj_norm,
            "as_of_date": as_of_date or "latest"
        }

    # -------------------------------------------------------------------------
    # Temporal Graph Sequence Snapshots (TGNN / T-GCN / EvolveGCN)
    # -------------------------------------------------------------------------
    def slice_temporal_snapshots(
        self,
        window_size: Optional[str] = "30D",
        num_snapshots: Optional[int] = None,
        time_windows: Optional[List[Tuple[str, str]]] = None,
        cumulative: bool = True,
        fixed_node_set: bool = True,
        target_metric: Optional[str] = None,
        symmetrize: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Slices the dual-layer graph and tabular state across chronological time windows (t_0, ..., t_k).
        Ready for Temporal Graph Neural Networks (TGNN / T-GCN / EvolveGCN / TGAT / PyG-Temporal).

        Args:
            window_size: Frequency string (e.g. '30D', '90D', '180D', '365D') if num_snapshots is None.
            num_snapshots: Explicit number of uniform chronological snapshots to produce.
            time_windows: List of explicit (start_date, end_date) ISO strings.
            cumulative: If True, snapshots aggregate all data known up to t_k.
                        If False, sliding window: data occurring strictly within (t_{k-1}, t_k].
            fixed_node_set: If True, maintains unified node index space across all snapshots
                            (standard for EvolveGCN / T-GCN) with a boolean node_mask.
                            If False, induces snapshot graphs containing only currently active nodes.
            target_metric: Optional metric name to extract next-step forecast targets (Y_t).
            symmetrize: Whether to symmetrize snapshot adjacency matrices.

        Returns:
            List of snapshot dictionaries.
        """
        all_nodes = self.store.get_nodes()
        all_edges = self.store.get_edges()
        history_df = self.store.get_history_df()

        if not all_nodes:
            return []

        global_node_ids = [n.id for n in all_nodes]
        global_node2idx = {nid: i for i, nid in enumerate(global_node_ids)}
        total_N = len(all_nodes)

        # 1. Determine temporal boundaries safely normalizing all dates
        dates: List[pd.Timestamp] = []
        for n in all_nodes:
            norm_d = _normalize_dt(n.activation_date)
            if norm_d is not None:
                dates.append(norm_d)
        for e in all_edges:
            norm_d = _normalize_dt(e.timestamp)
            if norm_d is not None:
                dates.append(norm_d)
        if not history_df.empty and "timestamp" in history_df.columns:
            ts_series = _to_datetime_series(history_df["timestamp"]).dropna()
            dates.extend(ts_series.tolist())

        # 2. Compute non-degenerate window ranges
        is_explicit = (time_windows is not None and len(time_windows) > 0)
        if not dates:
            # Fallback to single snapshot today if no dates found
            now_dt = _normalize_dt(datetime.now(timezone.utc))
            window_ranges = [(now_dt - pd.Timedelta(days=365), now_dt)]
        elif is_explicit:
            window_ranges = [
                (_normalize_dt(w[0]), _normalize_dt(w[1])) for w in time_windows
                if _normalize_dt(w[0]) is not None and _normalize_dt(w[1]) is not None
            ]
        elif num_snapshots is not None and num_snapshots > 0:
            min_date = min(dates)
            max_date = max(dates)
            if min_date >= max_date:
                max_date = min_date + pd.Timedelta(days=30)

            if num_snapshots == 1:
                window_ranges = [(min_date, max_date)]
            else:
                # Exactly num_snapshots non-zero intervals spanning min_date to max_date
                cutoffs = pd.date_range(start=min_date, end=max_date, periods=num_snapshots + 1)
                window_ranges = []
                for i in range(num_snapshots):
                    start_w = min_date if cumulative else cutoffs[i]
                    window_ranges.append((start_w, cutoffs[i + 1]))
        else:
            # Frequency window_size
            freq = window_size or "30D"
            min_date = min(dates)
            max_date = max(dates)
            if min_date >= max_date:
                max_date = min_date + pd.Timedelta(days=30)
            cutoffs = pd.date_range(start=min_date, end=max_date, freq=freq)
            if len(cutoffs) == 0:
                cutoffs = pd.DatetimeIndex([min_date, max_date])
            elif cutoffs[-1] < max_date:
                cutoffs = cutoffs.append(pd.DatetimeIndex([max_date]))
            elif len(cutoffs) == 1:
                cutoffs = cutoffs.append(pd.DatetimeIndex([max_date]))

            window_ranges = []
            for i in range(len(cutoffs) - 1):
                start_w = min_date if cumulative else cutoffs[i]
                window_ranges.append((start_w, cutoffs[i + 1]))

        # Prepare history with normalized timestamp column for fast, reliable slicing
        hist_prepared = history_df.copy() if not history_df.empty else pd.DataFrame()
        if not hist_prepared.empty and "timestamp" in hist_prepared.columns:
            hist_prepared["_dt"] = _to_datetime_series(hist_prepared["timestamp"])
            hist_prepared = hist_prepared.dropna(subset=["_dt"]).sort_values("_dt", ascending=True)

        snapshots: List[Dict[str, Any]] = []

        for snap_idx, (w_start, w_end) in enumerate(window_ranges):
            end_str = w_end.strftime("%Y-%m-%d")
            start_str = w_start.strftime("%Y-%m-%d")

            # Slice history
            if not hist_prepared.empty:
                if cumulative:
                    sub_hist = hist_prepared[hist_prepared["_dt"] <= w_end].drop(columns=["_dt"])
                else:
                    if snap_idx == 0 or is_explicit:
                        sub_hist = hist_prepared[(hist_prepared["_dt"] >= w_start) & (hist_prepared["_dt"] <= w_end)].drop(columns=["_dt"])
                    else:
                        sub_hist = hist_prepared[(hist_prepared["_dt"] > w_start) & (hist_prepared["_dt"] <= w_end)].drop(columns=["_dt"])
            else:
                sub_hist = pd.DataFrame()

            # Slice edges
            sub_edges: List[GraphEdge] = []
            for e in all_edges:
                norm_e_ts = _normalize_dt(e.timestamp)
                if norm_e_ts is not None:
                    if cumulative:
                        edge_active = (norm_e_ts <= w_end)
                    else:
                        if snap_idx == 0 or is_explicit:
                            edge_active = (w_start <= norm_e_ts <= w_end)
                        else:
                            edge_active = (w_start < norm_e_ts <= w_end)
                else:
                    edge_active = True

                if edge_active:
                    sub_edges.append(e)

            # Active nodes identification
            active_node_mask = np.zeros(total_N, dtype=bool)
            observed_node_ids = set()
            if not sub_hist.empty and "node_id" in sub_hist.columns:
                observed_node_ids = set(sub_hist["node_id"].dropna().unique())

            edge_endpoint_ids = set()
            for e in sub_edges:
                edge_endpoint_ids.add(e.source_id)
                edge_endpoint_ids.add(e.target_id)

            for i, n in enumerate(all_nodes):
                norm_act = _normalize_dt(n.activation_date)
                if norm_act is not None:
                    if cumulative:
                        is_active = (norm_act <= w_end)
                    else:
                        if snap_idx == 0 or is_explicit:
                            is_active = (w_start <= norm_act <= w_end)
                        else:
                            is_active = (w_start < norm_act <= w_end)
                else:
                    is_active = True  # Baseline entities active from t_0

                if is_active or (n.id in observed_node_ids) or (n.id in edge_endpoint_ids):
                    active_node_mask[i] = True

            if fixed_node_set:
                target_nodes = all_nodes
                node_mapping = global_node2idx
                current_node_ids = global_node_ids
            else:
                target_nodes = [n for i, n in enumerate(all_nodes) if active_node_mask[i]]
                current_node_ids = [n.id for n in target_nodes]
                node_mapping = {nid: i for i, nid in enumerate(current_node_ids)}
                # Filter sub_edges so endpoints belong to current target_nodes
                sub_edges = [e for e in sub_edges if e.source_id in node_mapping and e.target_id in node_mapping]

            # Compile X for this snapshot
            X_t, feat_names, feat_slices = self.compile_node_features(
                as_of_date=end_str,
                nodes=target_nodes,
                edges=sub_edges,
                history_df=sub_hist
            )

            # Compile edge index & adjacency for this snapshot
            edge_index_t, edge_attr_t, edge_weight_t = self.compile_edge_index(
                nodes=target_nodes,
                edges=sub_edges,
                node_mapping=node_mapping
            )
            adj_t = self.compile_adjacency_matrix(
                nodes=target_nodes,
                edges=sub_edges,
                normalized=False,
                self_loops=False,
                symmetrize=symmetrize
            )

            # Compute forecasting target Y_t if requested
            target_Y = None
            if target_metric:
                if snap_idx + 1 < len(window_ranges):
                    target_Y = np.zeros(len(target_nodes), dtype=np.float32)
                    if not hist_prepared.empty and "metric_name" in hist_prepared.columns:
                        next_start, next_end = window_ranges[snap_idx + 1]
                        future_hist = hist_prepared[
                            (hist_prepared["_dt"] > w_end) & (hist_prepared["_dt"] <= next_end)
                        ]
                        if not future_hist.empty:
                            filtered_fut = future_hist[
                                future_hist["metric_name"].astype(str).str.lower() == target_metric.lower()
                            ]
                            for nid, grp in filtered_fut.groupby("node_id"):
                                if nid in node_mapping:
                                    target_Y[node_mapping[nid]] = float(np.nan_to_num(grp.iloc[-1]["metric_value"], nan=0.0))

            snapshot = {
                "snapshot_index": snap_idx,
                "timestamp": end_str,
                "window_start": start_str,
                "window_end": end_str,
                "num_nodes": len(target_nodes),
                "num_edges": edge_index_t.shape[1],
                "feature_dim": X_t.shape[1] if X_t.ndim > 1 else 0,
                "edge_dim": edge_attr_t.shape[1] if edge_attr_t.ndim > 1 else 0,
                "active_nodes_count": int(np.sum(active_node_mask)),
                "node_mask": active_node_mask if fixed_node_set else np.ones(len(target_nodes), dtype=bool),
                "node_ids": current_node_ids,
                "node_mapping": node_mapping,
                "X": X_t,
                "edge_index": edge_index_t,
                "edge_attr": edge_attr_t,
                "edge_weight": edge_weight_t,
                "edge_weights": edge_weight_t,  # Field alias for UI compatibility
                "adjacency": adj_t,
                "y": target_Y,
                "feature_names": feat_names,
                "feature_slices": feat_slices
            }
            snapshots.append(snapshot)

        return snapshots

    # -------------------------------------------------------------------------
    # PyTorch Geometric (PyG) Compatible Export
    # -------------------------------------------------------------------------
    def to_pyg_data(
        self,
        graph_dict_or_snapshot: Optional[Dict[str, Any]] = None,
        to_torch: bool = False
    ) -> Union[Dict[str, Any], Any]:
        """
        Convert a compiled graph dictionary or snapshot into a PyTorch Geometric compatible dictionary
        or torch_geometric.data.Data object (if to_torch=True and PyTorch is available).
        """
        data_dict = graph_dict_or_snapshot if graph_dict_or_snapshot is not None else self.compile_static_graph()

        X = data_dict["X"]
        edge_index = data_dict["edge_index"]
        edge_attr = data_dict.get("edge_attr")
        edge_weight = data_dict.get("edge_weight") if data_dict.get("edge_weight") is not None else data_dict.get("edge_weights")
        node_mask = data_dict.get("node_mask")
        y = data_dict.get("y")

        if to_torch:
            if not HAS_TORCH:
                raise ImportError(
                    "PyTorch (torch) is not installed. Install PyTorch to use to_torch=True, "
                    "or set to_torch=False to export as a NumPy PyG-compatible dictionary."
                )

            t_x = torch.from_numpy(X).float()
            t_edge_index = torch.from_numpy(edge_index).long()
            t_edge_attr = torch.from_numpy(edge_attr).float() if edge_attr is not None else None
            t_edge_weight = torch.from_numpy(edge_weight).float() if edge_weight is not None else None
            t_node_mask = torch.from_numpy(node_mask).bool() if node_mask is not None else None
            t_y = torch.from_numpy(y).float() if y is not None else None

            if HAS_PYG:
                kwargs = {}
                if t_edge_attr is not None:
                    kwargs["edge_attr"] = t_edge_attr
                if t_edge_weight is not None:
                    kwargs["edge_weight"] = t_edge_weight
                if t_y is not None:
                    kwargs["y"] = t_y
                if t_node_mask is not None:
                    kwargs["node_mask"] = t_node_mask
                pyg_obj = PyGData(
                    x=t_x,
                    edge_index=t_edge_index,
                    **kwargs
                )
                pyg_obj.num_nodes = data_dict["num_nodes"]
                pyg_obj.node_ids = data_dict["node_ids"]
                return pyg_obj

            return {
                "x": t_x,
                "edge_index": t_edge_index,
                "edge_attr": t_edge_attr,
                "edge_weight": t_edge_weight,
                "edge_weights": t_edge_weight,
                "node_mask": t_node_mask,
                "y": t_y,
                "num_nodes": data_dict["num_nodes"],
                "num_edges": data_dict["num_edges"],
                "feature_dim": X.shape[1] if X.ndim > 1 else 0,
                "edge_dim": edge_attr.shape[1] if edge_attr is not None and edge_attr.ndim > 1 else 0,
                "node_ids": data_dict["node_ids"],
                "feature_names": data_dict.get("feature_names", []),
                "torch_available": True
            }

        # NumPy PyG-compatible dictionary
        return {
            "x": X,
            "edge_index": edge_index,
            "edge_attr": edge_attr,
            "edge_weight": edge_weight,
            "edge_weights": edge_weight,
            "node_mask": node_mask,
            "y": y,
            "num_nodes": data_dict["num_nodes"],
            "num_edges": data_dict["num_edges"],
            "feature_dim": X.shape[1] if X.ndim > 1 else 0,
            "edge_dim": edge_attr.shape[1] if edge_attr is not None and edge_attr.ndim > 1 else 0,
            "node_ids": data_dict["node_ids"],
            "feature_names": data_dict.get("feature_names", []),
            "feature_slices": data_dict.get("feature_slices", {}),
            "timestamp": data_dict.get("timestamp") or data_dict.get("as_of_date", "latest"),
            "torch_available": HAS_TORCH
        }

    def export_temporal_pyg(
        self,
        snapshots: Optional[List[Dict[str, Any]]] = None,
        to_torch: bool = False,
        **slice_kwargs
    ) -> Dict[str, Any]:
        """
        Export temporal graph sequence into a collection of PyG snapshots,
        compatible with PyTorch Geometric Temporal (DynamicGraphTemporalSignal / StaticGraphTemporalSignal).
        """
        if snapshots is None:
            snapshots = self.slice_temporal_snapshots(**slice_kwargs)

        pyg_snapshots = [self.to_pyg_data(s, to_torch=to_torch) for s in snapshots]
        snapshot_times = [s["timestamp"] for s in snapshots]

        return {
            "format": "pyg_temporal_sequence",
            "num_snapshots": len(snapshots),
            "snapshot_times": snapshot_times,
            "snapshots": pyg_snapshots,
            "feature_dim": snapshots[0]["X"].shape[1] if snapshots and snapshots[0]["X"].ndim > 1 else 0,
            "edge_dim": snapshots[0]["edge_attr"].shape[1] if snapshots and snapshots[0]["edge_attr"].ndim > 1 else 0,
            "feature_names": snapshots[0].get("feature_names", []) if snapshots else [],
            "feature_slices": snapshots[0].get("feature_slices", {}) if snapshots else {},
            "torch_available": HAS_TORCH and to_torch
        }

    # -------------------------------------------------------------------------
    # JSON Graph Export Formats
    # -------------------------------------------------------------------------
    def export_json(
        self,
        graph_dict: Optional[Dict[str, Any]] = None,
        filepath: Optional[Union[str, Path]] = None,
        indent: int = 2
    ) -> str:
        """
        Export static dual-layer graph into standard JSON graph format.
        Includes node-link graph representation and numerical tensor arrays.
        """
        data = graph_dict if graph_dict is not None else self.compile_static_graph()

        nodes_info = []
        for i, nid in enumerate(data["node_ids"]):
            n_obj = self.store.get_node(nid)
            nodes_info.append({
                "id": nid,
                "label": n_obj.label if n_obj else nid,
                "node_type": n_obj.node_type.value if n_obj and isinstance(n_obj.node_type, NodeType) else (n_obj.node_type if n_obj else "UNKNOWN"),
                "jurisdiction": n_obj.jurisdiction if n_obj else "EU",
                "activation_date": n_obj.activation_date if n_obj else "",
                "feature_index": i,
                "features": data["X"][i].tolist() if i < len(data["X"]) else []
            })

        links_info = []
        edge_index = data["edge_index"]
        edge_weight = data["edge_weight"]
        edge_attr = data["edge_attr"]
        for j in range(edge_index.shape[1]):
            u_idx = int(edge_index[0, j])
            v_idx = int(edge_index[1, j])
            links_info.append({
                "source": data["node_ids"][u_idx],
                "target": data["node_ids"][v_idx],
                "weight": float(edge_weight[j]) if j < len(edge_weight) else 1.0,
                "attributes": edge_attr[j].tolist() if j < len(edge_attr) else []
            })

        out = {
            "format": "powerlaw_gnn_static_v1",
            "as_of_date": data.get("as_of_date", "latest"),
            "num_nodes": data["num_nodes"],
            "num_edges": data["num_edges"],
            "feature_dim": data["feature_dim"],
            "edge_dim": data["edge_dim"],
            "feature_names": data["feature_names"],
            "feature_slices": data["feature_slices"],
            "node_mapping": data["node_mapping"],
            "nodes": nodes_info,
            "links": links_info,
            "tensors": {
                "edge_index": data["edge_index"].tolist(),
                "edge_attr": data["edge_attr"].tolist(),
                "edge_weight": data["edge_weight"].tolist(),
                "edge_weights": data["edge_weight"].tolist(),
                "X": data["X"].tolist()
            }
        }

        if "node_mask" in data and data["node_mask"] is not None:
            out["tensors"]["node_mask"] = (
                data["node_mask"].tolist()
                if isinstance(data["node_mask"], np.ndarray)
                else data["node_mask"]
            )
        if "y" in data and data["y"] is not None:
            out["tensors"]["y"] = (
                data["y"].tolist()
                if isinstance(data["y"], np.ndarray)
                else data["y"]
            )

        json_str = json.dumps(out, cls=GNNJsonEncoder, indent=indent)

        if filepath:
            p = Path(filepath)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(json_str)

        return json_str

    def export_temporal_json(
        self,
        snapshots: Optional[List[Dict[str, Any]]] = None,
        filepath: Optional[Union[str, Path]] = None,
        indent: int = 2,
        **slice_kwargs
    ) -> str:
        """
        Export temporal sequence snapshots into JSON graph format.
        """
        if snapshots is None:
            snapshots = self.slice_temporal_snapshots(**slice_kwargs)

        snap_list = []
        for s in snapshots:
            snap_list.append({
                "snapshot_index": s["snapshot_index"],
                "timestamp": s["timestamp"],
                "window_start": s["window_start"],
                "window_end": s["window_end"],
                "num_nodes": s["num_nodes"],
                "num_edges": s["num_edges"],
                "feature_dim": s.get("feature_dim", s["X"].shape[1] if s["X"].ndim > 1 else 0),
                "edge_dim": s.get("edge_dim", s["edge_attr"].shape[1] if s.get("edge_attr") is not None and s["edge_attr"].ndim > 1 else 0),
                "active_nodes_count": s["active_nodes_count"],
                "node_ids": s["node_ids"],
                "node_mask": s["node_mask"].tolist() if isinstance(s["node_mask"], np.ndarray) else s["node_mask"],
                "edge_index": s["edge_index"].tolist(),
                "edge_attr": s["edge_attr"].tolist() if "edge_attr" in s and isinstance(s["edge_attr"], np.ndarray) else [],
                "edge_weight": s["edge_weight"].tolist(),
                "edge_weights": s["edge_weight"].tolist(),
                "X": s["X"].tolist(),
                "y": s["y"].tolist() if s.get("y") is not None and isinstance(s["y"], np.ndarray) else s.get("y")
            })

        out = {
            "format": "powerlaw_gnn_temporal_v1",
            "num_snapshots": len(snapshots),
            "feature_dim": snapshots[0]["X"].shape[1] if snapshots and snapshots[0]["X"].ndim > 1 else 0,
            "edge_dim": snapshots[0]["edge_attr"].shape[1] if snapshots and "edge_attr" in snapshots[0] and snapshots[0]["edge_attr"].ndim > 1 else 0,
            "feature_names": snapshots[0].get("feature_names", []) if snapshots else [],
            "feature_slices": snapshots[0].get("feature_slices", {}) if snapshots else {},
            "snapshots": snap_list
        }

        json_str = json.dumps(out, cls=GNNJsonEncoder, indent=indent)

        if filepath:
            p = Path(filepath)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(json_str)

        return json_str

    # -------------------------------------------------------------------------
    # NumPy Archive Export (.npz)
    # -------------------------------------------------------------------------
    def export_npz(
        self,
        filepath: Union[str, Path],
        graph_dict: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Save compiled static tensors into a compressed NumPy .npz archive.
        """
        data = graph_dict if graph_dict is not None else self.compile_static_graph()
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)

        save_kwargs = {
            "X": data["X"],
            "edge_index": data["edge_index"],
            "edge_attr": data["edge_attr"],
            "edge_weight": data["edge_weight"],
            "adjacency": data["adjacency"],
            "normalized_adjacency": data["normalized_adjacency"],
            "node_ids": np.array(data["node_ids"], dtype=object),
            "feature_names": np.array(data["feature_names"], dtype=object)
        }
        if "node_mask" in data and data["node_mask"] is not None:
            save_kwargs["node_mask"] = data["node_mask"]
        if "y" in data and data["y"] is not None:
            save_kwargs["y"] = data["y"]

        np.savez_compressed(p, **save_kwargs)

    def export_temporal_npz(
        self,
        filepath: Union[str, Path],
        snapshots: Optional[List[Dict[str, Any]]] = None,
        **slice_kwargs
    ) -> None:
        """
        Save compiled temporal sequence snapshots into a compressed NumPy .npz archive.
        If node counts are uniform across snapshots (standard for fixed node set Temporal GNNs),
        saves a unified 3D sequence array X_sequence in R^(T x N x D) and node_mask_sequence in {0, 1}^(T x N).
        """
        if snapshots is None:
            snapshots = self.slice_temporal_snapshots(**slice_kwargs)

        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)

        if not snapshots:
            np.savez_compressed(p, num_snapshots=0)
            return

        save_dict: Dict[str, Any] = {
            "num_snapshots": len(snapshots),
            "snapshot_times": np.array([s["timestamp"] for s in snapshots], dtype=object),
            "window_starts": np.array([s["window_start"] for s in snapshots], dtype=object),
            "window_ends": np.array([s["window_end"] for s in snapshots], dtype=object),
            "feature_names": np.array(snapshots[0].get("feature_names", []), dtype=object),
        }

        # Check if node counts are uniform across all snapshots
        all_same_n = len({s["num_nodes"] for s in snapshots}) == 1
        if all_same_n and len(snapshots[0]["X"]) > 0:
            save_dict["X_sequence"] = np.stack([s["X"] for s in snapshots], axis=0)
            save_dict["node_mask_sequence"] = np.stack([s["node_mask"] for s in snapshots], axis=0)
            save_dict["node_ids"] = np.array(snapshots[0]["node_ids"], dtype=object)

        # Per snapshot arrays
        for idx, s in enumerate(snapshots):
            save_dict[f"X_{idx}"] = s["X"]
            save_dict[f"edge_index_{idx}"] = s["edge_index"]
            save_dict[f"edge_attr_{idx}"] = s["edge_attr"]
            save_dict[f"edge_weight_{idx}"] = s["edge_weight"]
            save_dict[f"node_mask_{idx}"] = s["node_mask"]
            if s.get("y") is not None:
                save_dict[f"y_{idx}"] = s["y"]

        np.savez_compressed(p, **save_dict)
