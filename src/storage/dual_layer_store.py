"""
Dual-Layer Graph & Temporal Tabular Storage Engine.
Backed natively by LanceDB with dense 768-dim vector embeddings and temporal time-series.

Layer 1: Graph Topology
- Nodes: LawNode, ActionNode, ReactionNode, SectorNode, CommodityNode, EconomicIndexNode, BaseGraphNode
- Edges: Directed edges (TRIGGERS, MANDATES, REACTS_TO, AFFECTS_COST_OF, AMENDS, etc.)
- NetworkX conversion, pathfinding, and scale-free Power-Law degree distribution metrics.

Layer 2: Temporal Tabular State
- TabularNodeHistory: Behind-the-graph timestamped state records for each node.
- Point-in-time (as-of) queries: evaluate historical state at any point in time $t$.
- Time-series window queries, transition tracking, and GNN multimodal tensor exports.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import networkx as nx

import lancedb

from src.schemas.graph_temporal import (
    BaseGraphNode,
    LawNode,
    ActionNode,
    ReactionNode,
    SectorNode,
    CommodityNode,
    EconomicIndexNode,
    NodeType,
    EdgeRelation,
    GraphEdge,
    TriggersEdge,
    MandatesEdge,
    ReactsToEdge,
    AffectsCostOfEdge,
    AmendsEdge,
    TabularNodeHistory,
    node_from_dict,
    edge_from_dict,
    parse_iso_or_date_to_utc,
    _is_nan,
)

logger = logging.getLogger(__name__)


def _sanitize_record_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Strip NaN values so Pydantic model defaults apply cleanly."""
    clean = {}
    for k, v in d.items():
        if _is_nan(v):
            continue
        clean[k] = v
    return clean


class DualLayerStore:
    """
    LanceDB-backed Dual-Layer Storage Engine managing:
    1. Topological graph layer (Laws, Actions, Reactions, Sectors, with 768-dim vector embeddings).
    2. Behind-the-graph tabular temporal layer (timestamped metrics, states, audit logs).
    """

    def __init__(
        self,
        storage_dir: Union[str, Path] = "data/lancedb",
        nodes_table_name: str = "nodes",
        edges_table_name: str = "edges",
        history_table_name: str = "tabular_history",
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.nodes_table_name = nodes_table_name
        self.edges_table_name = edges_table_name
        self.history_table_name = history_table_name

        # In-memory working caches for rapid topological queries
        self._nodes: Dict[str, BaseGraphNode] = {}
        self._edges: Dict[str, GraphEdge] = {}
        self._history: List[TabularNodeHistory] = []

        # Initialize LanceDB connection and tables
        self.db = lancedb.connect(str(self.storage_dir))
        self._init_tables()
        self._load_from_storage()

    def _init_tables(self) -> None:
        """Initialize LanceDB tables with pydantic schemas safely without concurrency races."""
        def _get_or_create(table_name: str, schema: Any):
            try:
                if table_name in self.db:
                    return self.db.open_table(table_name)
            except Exception:
                pass
            try:
                return self.db.create_table(table_name, schema=schema, exist_ok=True)
            except ValueError:
                return self.db.open_table(table_name)

        self._nodes_tbl = _get_or_create(self.nodes_table_name, BaseGraphNode)
        self._edges_tbl = _get_or_create(self.edges_table_name, GraphEdge)
        self._history_tbl = _get_or_create(self.history_table_name, TabularNodeHistory)

    def _load_from_storage(self) -> None:
        """Load persisted entities from LanceDB tables into the in-memory working cache."""
        try:
            # Load Nodes
            if len(self._nodes_tbl) > 0:
                nodes_df = self._nodes_tbl.to_pandas()
                records = nodes_df.to_dict(orient="records")
                for r in records:
                    clean_r = _sanitize_record_dict(r)
                    node = node_from_dict(clean_r)
                    self._nodes[node.id] = node

            # Load Edges
            if len(self._edges_tbl) > 0:
                edges_df = self._edges_tbl.to_pandas()
                records = edges_df.to_dict(orient="records")
                for r in records:
                    clean_r = _sanitize_record_dict(r)
                    edge = edge_from_dict(clean_r)
                    self._edges[edge.id] = edge

            # Load Tabular History
            if len(self._history_tbl) > 0:
                history_df = self._history_tbl.to_pandas()
                records = history_df.to_dict(orient="records")
                for r in records:
                    clean_r = _sanitize_record_dict(r)
                    rec = TabularNodeHistory(**clean_r)
                    self._history.append(rec)
        except Exception as e:
            logger.warning(f"Error loading records from LanceDB: {e}")

    # =========================================================================
    # 1. NODE OPERATIONS (Layer 1 - Graph)
    # =========================================================================

    def add_node(self, node: Union[BaseGraphNode, Dict[str, Any]]) -> BaseGraphNode:
        """
        Add or update a graph node in LanceDB and in-memory cache.
        Supports LawNode, ActionNode, ReactionNode, SectorNode or dictionaries.
        """
        if isinstance(node, dict):
            node_obj = node_from_dict(node)
        elif isinstance(node, BaseGraphNode):
            node_obj = node
        else:
            raise TypeError(f"Expected BaseGraphNode or dict, got {type(node)}")

        # Persistent upsert in LanceDB
        self._nodes_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute([node_obj])

        # Cache update
        self._nodes[node_obj.id] = node_obj
        return node_obj

    def add_nodes(self, nodes: List[Union[BaseGraphNode, Dict[str, Any]]]) -> List[BaseGraphNode]:
        """Batch add or update multiple graph nodes with deduplication."""
        node_objs = [
            node_from_dict(n) if isinstance(n, dict) else n
            for n in nodes
        ]
        if not node_objs:
            return []

        # Deduplicate within batch preserving last occurrence for each ID
        unique_nodes: Dict[str, BaseGraphNode] = {}
        for n in node_objs:
            unique_nodes[n.id] = n
        batch = list(unique_nodes.values())

        self._nodes_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(batch)

        for n in batch:
            self._nodes[n.id] = n

        return batch

    def get_node(self, node_id: str) -> Optional[BaseGraphNode]:
        """Fetch a node by unique identifier, checking memory cache with LanceDB fallback."""
        if node_id in self._nodes:
            return self._nodes[node_id]

        # Fallback query directly against LanceDB
        try:
            escaped_id = node_id.replace("'", "''")
            res = self._nodes_tbl.search().where(f"id == '{escaped_id}'").limit(1).to_pandas()
            if not res.empty:
                clean_r = _sanitize_record_dict(res.iloc[0].to_dict())
                node = node_from_dict(clean_r)
                self._nodes[node.id] = node
                return node
        except Exception as e:
            logger.debug(f"Direct query for node {node_id} failed: {e}")
        return None

    def get_nodes(self, node_type: Optional[str] = None) -> List[BaseGraphNode]:
        """Retrieve all nodes, optionally filtered by node_type (e.g. 'LAW', 'ACTION')."""
        if len(self._nodes) == 0 and len(self._nodes_tbl) > 0:
            self._load_from_storage()

        nodes = list(self._nodes.values())
        if node_type:
            target_type = node_type.upper()
            return [n for n in nodes if n.node_type.upper() == target_type]
        return nodes

    def delete_node(
        self,
        node_id: str,
        cascade_edges: bool = True,
        cascade_history: bool = False
    ) -> bool:
        """Delete a node and optionally its incident edges and tabular history."""
        if node_id not in self._nodes and len(self._nodes_tbl) == 0:
            return False

        escaped_id = node_id.replace("'", "''")
        try:
            self._nodes_tbl.delete(f"id == '{escaped_id}'")
        except Exception as e:
            logger.warning(f"Failed deleting node {node_id} from LanceDB: {e}")

        self._nodes.pop(node_id, None)

        if cascade_edges:
            edges_to_delete = [
                e for e in list(self._edges.values())
                if e.source == node_id or e.target == node_id
            ]
            for e in edges_to_delete:
                self.delete_edge(e.source, e.target, e.relationship_type)

        if cascade_history:
            self.delete_history(node_id)

        return True

    def count_nodes(self) -> int:
        """Return total number of nodes in the graph."""
        if len(self._nodes) == 0 and len(self._nodes_tbl) > 0:
            return self._nodes_tbl.count_rows()
        return len(self._nodes)

    def search_nodes_by_vector(
        self,
        query_vector: Union[List[float], np.ndarray],
        limit: int = 10,
        node_type: Optional[str] = None
    ) -> List[Tuple[BaseGraphNode, float]]:
        """
        Perform vector nearest-neighbor similarity search over the 768-dim node embeddings.
        Returns a list of (node, distance) tuples.
        """
        if isinstance(query_vector, np.ndarray):
            query_vector = query_vector.tolist()

        if len(query_vector) != 768:
            raise ValueError(f"query_vector must have 768 dimensions, got {len(query_vector)}")

        if len(self._nodes_tbl) == 0:
            return []

        search_query = self._nodes_tbl.search(query_vector).limit(limit)
        if node_type:
            search_query = search_query.where(f"node_type == '{node_type.upper()}'")

        df = search_query.to_pandas()
        results = []
        for _, row in df.iterrows():
            row_dict = _sanitize_record_dict(row.to_dict())
            dist = float(row_dict.pop("_distance", 0.0))
            node = node_from_dict(row_dict)
            results.append((node, dist))
        return results

    # =========================================================================
    # 2. EDGE OPERATIONS (Layer 1 - Graph)
    # =========================================================================

    def add_edge(self, edge: Union[GraphEdge, Dict[str, Any]]) -> GraphEdge:
        """Add or update a directional edge in LanceDB and in-memory cache."""
        if isinstance(edge, dict):
            edge_obj = edge_from_dict(edge)
        elif isinstance(edge, GraphEdge):
            edge_obj = edge
        else:
            raise TypeError(f"Expected GraphEdge or dict, got {type(edge)}")

        # Persistent upsert in LanceDB
        self._edges_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute([edge_obj])

        self._edges[edge_obj.id] = edge_obj
        return edge_obj

    def add_edges(self, edges: List[Union[GraphEdge, Dict[str, Any]]]) -> List[GraphEdge]:
        """Batch add or update directional edges with deduplication."""
        edge_objs = [
            edge_from_dict(e) if isinstance(e, dict) else e
            for e in edges
        ]
        if not edge_objs:
            return []

        # Deduplicate within batch preserving last occurrence
        unique_edges: Dict[str, GraphEdge] = {}
        for e in edge_objs:
            unique_edges[e.id] = e
        batch = list(unique_edges.values())

        self._edges_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(batch)

        for e in batch:
            self._edges[e.id] = e

        return batch

    def get_edge(
        self,
        source: str,
        target: str,
        relationship_type: Optional[str] = None
    ) -> Optional[GraphEdge]:
        """Fetch an edge by source, target, and optional relationship type, with LanceDB fallback."""
        for edge in self._edges.values():
            if edge.source == source and edge.target == target:
                if relationship_type is None or edge.relationship_type == relationship_type:
                    return edge

        # Fallback query directly against LanceDB
        try:
            esc_s = source.replace("'", "''")
            esc_t = target.replace("'", "''")
            where_clause = f"source == '{esc_s}' AND target == '{esc_t}'"
            if relationship_type:
                esc_r = relationship_type.replace("'", "''")
                where_clause += f" AND relationship_type == '{esc_r}'"
            res = self._edges_tbl.search().where(where_clause).limit(1).to_pandas()
            if not res.empty:
                clean_r = _sanitize_record_dict(res.iloc[0].to_dict())
                edge = edge_from_dict(clean_r)
                self._edges[edge.id] = edge
                return edge
        except Exception as e:
            logger.debug(f"Direct query for edge {source}->{target} failed: {e}")

        return None

    def get_edges(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        relationship_type: Optional[str] = None
    ) -> List[GraphEdge]:
        """Retrieve edges matching source, target, and/or relationship_type filters."""
        if len(self._edges) == 0 and len(self._edges_tbl) > 0:
            self._load_from_storage()

        matched = list(self._edges.values())
        if source:
            matched = [e for e in matched if e.source == source]
        if target:
            matched = [e for e in matched if e.target == target]
        if relationship_type:
            matched = [e for e in matched if e.relationship_type == relationship_type]
        return matched

    def delete_edge(
        self,
        source: str,
        target: str,
        relationship_type: Optional[str] = None
    ) -> bool:
        """Delete matching edge(s) from LanceDB and in-memory cache."""
        to_delete = [
            e for e in list(self._edges.values())
            if e.source == source and e.target == target and (
                relationship_type is None or e.relationship_type == relationship_type
            )
        ]
        if not to_delete:
            return False

        for edge in to_delete:
            escaped_id = edge.id.replace("'", "''")
            try:
                self._edges_tbl.delete(f"id == '{escaped_id}'")
            except Exception as e:
                logger.warning(f"Error deleting edge {edge.id}: {e}")
            self._edges.pop(edge.id, None)

        return True

    def count_edges(self) -> int:
        """Return total number of edges in the graph."""
        if len(self._edges) == 0 and len(self._edges_tbl) > 0:
            return self._edges_tbl.count_rows()
        return len(self._edges)

    # =========================================================================
    # 3. GRAPH TOPOLOGY QUERIES (Layer 1 - Graph)
    # =========================================================================

    def get_in_edges(self, node_id: str) -> List[GraphEdge]:
        """Return all edges where target == node_id."""
        return [e for e in self._edges.values() if e.target == node_id]

    def get_out_edges(self, node_id: str) -> List[GraphEdge]:
        """Return all edges where source == node_id."""
        return [e for e in self._edges.values() if e.source == node_id]

    def get_neighbors(self, node_id: str, direction: str = "out") -> List[str]:
        """
        Get neighbor node IDs connected to node_id.
        Direction: 'out' (successors), 'in' (predecessors), 'both', or 'all'.
        """
        dir_clean = str(direction).lower().strip()
        neighbors = set()
        if dir_clean in ("out", "both", "all"):
            for e in self.get_out_edges(node_id):
                neighbors.add(e.target)
        if dir_clean in ("in", "both", "all"):
            for e in self.get_in_edges(node_id):
                neighbors.add(e.source)
        return sorted(list(neighbors))

    def get_subgraph(self, node_id: str, depth: int = 1) -> Dict[str, Any]:
        """
        Extract k-hop subgraph around node_id using breadth-first traversal.
        Returns dictionary with 'nodes', 'edges', and 'node_ids'.
        """
        root_node = self.get_node(node_id)
        if root_node is None:
            return {
                "root_id": node_id,
                "depth": depth,
                "node_ids": [],
                "nodes": [],
                "edges": [],
            }

        visited_nodes = {node_id}
        frontier = {node_id}
        collected_edges: Dict[str, GraphEdge] = {}

        for _ in range(max(1, depth)):
            next_frontier = set()
            for nid in frontier:
                incident = self.get_out_edges(nid) + self.get_in_edges(nid)
                for e in incident:
                    collected_edges[e.id] = e
                    other = e.target if e.source == nid else e.source
                    if other not in visited_nodes:
                        visited_nodes.add(other)
                        next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        subgraph_nodes = [
            self.get_node(nid) for nid in visited_nodes if self.get_node(nid) is not None
        ]

        return {
            "root_id": node_id,
            "depth": depth,
            "node_ids": sorted(list(visited_nodes)),
            "nodes": subgraph_nodes,
            "edges": list(collected_edges.values()),
        }

    def to_networkx(self) -> nx.DiGraph:
        """Build and return a NetworkX directed graph from current nodes and edges."""
        G = nx.DiGraph()
        for node in self._nodes.values():
            vec = list(node.vector) if hasattr(node.vector, "__iter__") else node.vector
            G.add_node(
                node.id,
                label=node.label,
                node_type=node.node_type,
                description=node.description,
                jurisdiction=node.jurisdiction,
                metadata=node.metadata_dict,
                vector=vec,
            )

        for edge in self._edges.values():
            G.add_edge(
                edge.source,
                edge.target,
                id=edge.id,
                relationship_type=edge.relationship_type,
                relation_type=edge.relationship_type,
                weight=float(edge.weight),
                weights=float(edge.weight),
                timestamp=edge.timestamp,
                metadata=edge.metadata_dict,
            )
        return G

    def find_paths(self, source_id: str, target_id: str, max_depth: int = 5) -> List[List[str]]:
        """Find all simple directed paths between source_id and target_id up to max_depth."""
        G = self.to_networkx()
        if source_id not in G or target_id not in G:
            return []
        try:
            return list(nx.all_simple_paths(G, source=source_id, target=target_id, cutoff=max_depth))
        except Exception as e:
            logger.warning(f"Path search error between {source_id} and {target_id}: {e}")
            return []

    def compute_topology_metrics(self) -> Dict[str, Any]:
        """
        Compute scale-free degree distribution and power-law properties:
        P(k) ~ k^(-gamma)
        Estimates power-law exponent gamma, graph density, and centrality rankings.
        """
        G = self.to_networkx()
        N = len(G.nodes())
        M = len(G.edges())

        if N == 0:
            return {
                "num_nodes": 0,
                "num_edges": 0,
                "density": 0.0,
                "gamma": 0.0,
                "degree_distribution": {},
                "top_in_degree": [],
                "top_out_degree": [],
                "top_betweenness": [],
            }

        degrees = [d for _, d in G.degree()]
        in_degrees = dict(G.in_degree())
        out_degrees = dict(G.out_degree())

        # Power-law exponent estimation via discrete MLE (Clauset et al.)
        non_zero_degrees = [d for d in degrees if d > 0]
        if len(non_zero_degrees) >= 3:
            d_min = min(non_zero_degrees)
            denom = sum(np.log(d / (d_min - 0.5)) for d in non_zero_degrees)
            gamma = 1.0 + len(non_zero_degrees) / denom if denom > 0 else 1.0
        else:
            gamma = 1.0

        betweenness = nx.betweenness_centrality(G) if N > 1 else {nid: 0.0 for nid in G.nodes()}

        top_in = sorted(in_degrees.items(), key=lambda x: x[1], reverse=True)[:10]
        top_out = sorted(out_degrees.items(), key=lambda x: x[1], reverse=True)[:10]
        top_bet = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:10]

        degree_counts = pd.Series(degrees).value_counts().sort_index().to_dict()

        return {
            "num_nodes": N,
            "num_edges": M,
            "density": round(float(nx.density(G)), 4),
            "gamma": round(float(gamma), 3),
            "degree_distribution": {int(k): int(v) for k, v in degree_counts.items()},
            "top_in_degree": [
                {"node_id": k, "label": self._nodes[k].label if k in self._nodes else k, "in_degree": v}
                for k, v in top_in
            ],
            "top_out_degree": [
                {"node_id": k, "label": self._nodes[k].label if k in self._nodes else k, "out_degree": v}
                for k, v in top_out
            ],
            "top_betweenness": [
                {"node_id": k, "label": self._nodes[k].label if k in self._nodes else k, "centrality": round(v, 4)}
                for k, v in top_bet
            ],
        }

    # =========================================================================
    # 4. TEMPORAL TABULAR OPERATIONS & POINT-IN-TIME (AS-OF) QUERIES (Layer 2)
    # =========================================================================

    def add_history_record(
        self,
        record: Union[TabularNodeHistory, Dict[str, Any]]
    ) -> TabularNodeHistory:
        """Append a timestamped state and metric record to tabular history."""
        if isinstance(record, dict):
            rec_obj = TabularNodeHistory(**record)
        elif isinstance(record, TabularNodeHistory):
            rec_obj = record
        else:
            raise TypeError(f"Expected TabularNodeHistory or dict, got {type(record)}")

        self._history_tbl.add([rec_obj])
        self._history.append(rec_obj)
        return rec_obj

    def add_history_records(
        self,
        records: List[Union[TabularNodeHistory, Dict[str, Any]]]
    ) -> List[TabularNodeHistory]:
        """Batch append timestamped state records to tabular history."""
        rec_objs = [
            TabularNodeHistory(**r) if isinstance(r, dict) else r
            for r in records
        ]
        if not rec_objs:
            return []

        self._history_tbl.add(rec_objs)
        self._history.extend(rec_objs)
        return rec_objs

    def delete_history(self, node_id: str) -> int:
        """Delete historical records for a specific node from LanceDB and cache."""
        initial_count = len([r for r in self._history if r.node_id == node_id])
        escaped_id = node_id.replace("'", "''")
        try:
            self._history_tbl.delete(f"node_id == '{escaped_id}'")
        except Exception as e:
            logger.warning(f"Error deleting history for node {node_id}: {e}")
        self._history = [r for r in self._history if r.node_id != node_id]
        return initial_count

    def count_history(self) -> int:
        """Return total number of records in tabular history."""
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            return self._history_tbl.count_rows()
        return len(self._history)

    def query_state_at(
        self,
        timestamp: Union[str, datetime, int, float],
        node_id: Optional[str] = None,
        metric_name: Optional[str] = None
    ) -> Union[Optional[TabularNodeHistory], Dict[str, TabularNodeHistory]]:
        """
        Query historical tabular state at any point in time $t$.
        Computes point-in-time / as-of state where effective_timestamp <= t.

        - If node_id is provided: returns the single latest TabularNodeHistory for that node
          at or before timestamp $t$ (or None if no state existed yet).
        - If node_id is None: returns a dictionary mapping {node_id: TabularNodeHistory}
          representing the state of all nodes as of timestamp $t$.
        """
        # Sync if memory cache is empty but table has data
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            self._load_from_storage()

        t_utc = parse_iso_or_date_to_utc(timestamp)

        # Filter all records whose effective timestamp is <= t_utc
        valid_records = [
            r for r in self._history
            if r.timestamp_utc <= t_utc and (metric_name is None or r.metric_name == metric_name)
        ]

        if node_id:
            node_records = [r for r in valid_records if r.node_id == node_id]
            if not node_records:
                return None
            # Sort ascending and pick last record so tie-breaking exactly matches the all-nodes loop
            sorted_node = sorted(node_records, key=lambda r: r.timestamp_utc)
            return sorted_node[-1]

        # For all nodes: find the latest record for each node_id
        latest_by_node: Dict[str, TabularNodeHistory] = {}
        # Sort chronologically so subsequent records overwrite earlier ones
        sorted_records = sorted(valid_records, key=lambda r: r.timestamp_utc)
        for r in sorted_records:
            latest_by_node[r.node_id] = r

        return latest_by_node

    def query_history_range(
        self,
        node_id: Optional[str] = None,
        start_time: Optional[Union[str, datetime, int, float]] = None,
        end_time: Optional[Union[str, datetime, int, float]] = None,
        metric_name: Optional[str] = None
    ) -> List[TabularNodeHistory]:
        """
        Query historical tabular records within a time range [start_time, end_time],
        optionally filtered by node_id and metric_name.
        Returns records sorted chronologically ascending.
        """
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            self._load_from_storage()

        records = list(self._history)

        if node_id:
            records = [r for r in records if r.node_id == node_id]
        if metric_name:
            records = [r for r in records if r.metric_name == metric_name]

        if start_time is not None:
            start_utc = parse_iso_or_date_to_utc(start_time)
            records = [r for r in records if r.timestamp_utc >= start_utc]

        if end_time is not None:
            end_utc = parse_iso_or_date_to_utc(end_time)
            records = [r for r in records if r.timestamp_utc <= end_utc]

        return sorted(records, key=lambda r: r.timestamp_utc)

    def query_latest_state(
        self,
        node_id: Optional[str] = None,
        metric_name: Optional[str] = None
    ) -> Union[Optional[TabularNodeHistory], Dict[str, TabularNodeHistory]]:
        """Retrieve the absolute latest state record for a node or for all nodes."""
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            self._load_from_storage()

        records = [
            r for r in self._history
            if metric_name is None or r.metric_name == metric_name
        ]

        if node_id:
            node_records = [r for r in records if r.node_id == node_id]
            if not node_records:
                return None
            sorted_node = sorted(node_records, key=lambda r: r.timestamp_utc)
            return sorted_node[-1]

        latest_by_node: Dict[str, TabularNodeHistory] = {}
        for r in sorted(records, key=lambda x: x.timestamp_utc):
            latest_by_node[r.node_id] = r
        return latest_by_node

    def query_state_transitions(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve state transitions for a node ordered by time,
        recording whenever the categorical state_value changed.
        """
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            self._load_from_storage()

        node_records = sorted(
            [r for r in self._history if r.node_id == node_id],
            key=lambda r: r.timestamp_utc
        )
        transitions = []
        last_state = None

        for rec in node_records:
            if rec.state_value != last_state:
                transitions.append({
                    "effective_timestamp": rec.effective_timestamp,
                    "timestamp_utc": rec.timestamp_utc,
                    "from_state": last_state,
                    "to_state": rec.state_value,
                    "metric_name": rec.metric_name,
                    "metric_value": rec.metric_value,
                    "unit": rec.unit,
                    "source_url": rec.source_url,
                    "event_log": rec.event_log_parsed,
                })
                last_state = rec.state_value

        return transitions

    # =========================================================================
    # 5. INTEGRATED DUAL-LAYER SNAPSHOTS & GNN DATASET EXPORT
    # =========================================================================

    def get_node_snapshot(
        self,
        node_id: str,
        as_of: Optional[Union[str, datetime, int, float]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a complete dual-layer snapshot of a node:
        - Layer 1: Topological identity, label, in/out edges, and neighbors.
        - Layer 2: Point-in-time tabular state and metric reading as of time $t$.
        """
        node = self.get_node(node_id)
        if node is None:
            return None

        # Point-in-time state
        state_record = self.query_state_at(as_of, node_id=node_id) if as_of is not None else self.query_latest_state(node_id=node_id)

        return {
            "node": node.to_dict(),
            "in_edges": [e.to_dict() for e in self.get_in_edges(node_id)],
            "out_edges": [e.to_dict() for e in self.get_out_edges(node_id)],
            "neighbors_out": self.get_neighbors(node_id, direction="out"),
            "neighbors_in": self.get_neighbors(node_id, direction="in"),
            "as_of_timestamp": as_of,
            "temporal_state": state_record.to_dict() if state_record is not None else None,
        }

    def export_gnn_dataset(
        self,
        as_of: Optional[Union[str, datetime, int, float]] = None,
        vector_dim: Optional[int] = 16
    ) -> Dict[str, Any]:
        """
        Export dual-layer data structure into numeric matrices ready for downstream GNNs:
        - Node feature matrix X (N x D) combining embeddings, topological degrees, and temporal metrics as of time $t$.
        - Edge index tensor (2 x M)
        - Edge weights and types
        - Node ID to index mapping
        """
        node_ids = list(self._nodes.keys())
        node2idx = {nid: i for i, nid in enumerate(node_ids)}
        N = len(node_ids)

        if N == 0:
            return {"num_nodes": 0, "num_edges": 0, "X": [], "edge_index": [[], []]}

        G = self.to_networkx()
        in_deg = dict(G.in_degree())
        out_deg = dict(G.out_degree())
        max_in = max(in_deg.values()) if in_deg else 1
        max_out = max(out_deg.values()) if out_deg else 1

        node_types = ["LAW", "ACTION", "REACTION", "SECTOR", "COMMODITY", "ECONOMIC_INDEX"]
        type2idx = {t: i for i, t in enumerate(node_types)}

        # Snapshot of states as of time t
        states_as_of: Dict[str, TabularNodeHistory] = {}
        if as_of is not None:
            result = self.query_state_at(as_of)
            if isinstance(result, dict):
                states_as_of = result
        else:
            result = self.query_latest_state()
            if isinstance(result, dict):
                states_as_of = result

        feature_rows = []
        for nid in node_ids:
            node = self._nodes[nid]

            # One-hot node type encoding (6)
            oh = [0.0] * len(node_types)
            nt = node.node_type.upper()
            if nt in type2idx:
                oh[type2idx[nt]] = 1.0

            # Degree topological features (2)
            deg_feats = [
                float(in_deg.get(nid, 0)) / max(max_in, 1),
                float(out_deg.get(nid, 0)) / max(max_out, 1),
            ]

            # Point-in-time tabular metric reading (1)
            metric_val = 0.0
            if nid in states_as_of and states_as_of[nid] is not None:
                metric_val = float(states_as_of[nid].metric_value)

            # Vector embedding slice or full 768 representation
            if vector_dim is None:
                emb_slice = list(node.vector) if hasattr(node.vector, "__iter__") else [0.0] * 768
            else:
                emb_slice = list(node.vector[:vector_dim]) if hasattr(node.vector, "__iter__") else [0.0] * vector_dim

            row = oh + deg_feats + [metric_val] + emb_slice
            feature_rows.append(row)

        X = np.array(feature_rows, dtype=np.float32)

        # Directed edge index
        edge_sources = []
        edge_targets = []
        edge_weights = []
        edge_types = []

        all_relations = [
            EdgeRelation.TRIGGERS.value,
            EdgeRelation.MANDATES.value,
            EdgeRelation.REACTS_TO.value,
            EdgeRelation.AFFECTS_COST_OF.value,
            EdgeRelation.AMENDS.value,
            "REGULATES",
            "IMPACTS",
        ]
        rel2idx = {rel: i for i, rel in enumerate(all_relations)}

        for edge in self._edges.values():
            if edge.source in node2idx and edge.target in node2idx:
                edge_sources.append(node2idx[edge.source])
                edge_targets.append(node2idx[edge.target])
                edge_weights.append(float(edge.weight))
                edge_types.append(rel2idx.get(edge.relationship_type, 0))

        edge_index = np.array([edge_sources, edge_targets], dtype=np.int64)

        return {
            "num_nodes": N,
            "num_edges": len(edge_sources),
            "feature_dim": X.shape[1],
            "node_mapping": node2idx,
            "node_ids": node_ids,
            "X": X.tolist(),
            "edge_index": edge_index.tolist(),
            "edge_weights": edge_weights,
            "edge_types": edge_types,
        }

    # =========================================================================
    # 6. DATAFRAME & PERSISTENCE HELPERS
    # =========================================================================

    def get_nodes_df(self) -> pd.DataFrame:
        """Return nodes as a pandas DataFrame."""
        data = [n.to_dict() for n in self._nodes.values()]
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["id", "label", "node_type", "metadata"])

    def get_edges_df(self) -> pd.DataFrame:
        """Return edges as a pandas DataFrame."""
        data = [e.to_dict() for e in self._edges.values()]
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["source", "target", "relationship_type", "weight"])

    def get_history_df(self, node_id: Optional[str] = None) -> pd.DataFrame:
        """Return tabular history as a chronologically sorted pandas DataFrame."""
        records = [r for r in self._history if r.node_id == node_id] if node_id else list(self._history)
        data = [r.to_dict() for r in records]
        if not data:
            return pd.DataFrame(columns=["record_id", "node_id", "effective_timestamp", "state_value", "metric_value"])
        df = pd.DataFrame(data)
        if "timestamp_utc" in df.columns:
            df = df.sort_values("timestamp_utc", ascending=True)
        return df

    def save(self) -> None:
        """Explicitly persist working state and export Parquet backups safely."""
        nodes_df = self.get_nodes_df().copy()
        edges_df = self.get_edges_df().copy()
        history_df = self.get_history_df().copy()

        # Sanitize dict/list columns to JSON strings so PyArrow can write cleanly to Parquet
        for col in ["metadata", "properties"]:
            if col in nodes_df.columns:
                nodes_df[col] = nodes_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else ("{}" if pd.isna(x) else str(x)))
            if col in edges_df.columns:
                edges_df[col] = edges_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else ("{}" if pd.isna(x) else str(x)))
        for col in ["metadata", "event_log"]:
            if col in history_df.columns:
                history_df[col] = history_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else ("{}" if pd.isna(x) else str(x)))

        # Parquet backup
        nodes_df.to_parquet(self.storage_dir / "graph_nodes.parquet", index=False)
        edges_df.to_parquet(self.storage_dir / "graph_edges.parquet", index=False)
        history_df.to_parquet(self.storage_dir / "node_tabular_history.parquet", index=False)

    def load(self) -> None:
        """Reload state from storage."""
        self._nodes.clear()
        self._edges.clear()
        self._history.clear()
        self._load_from_storage()


# Class Aliases for seamless drop-in interoperability
DualLayerStorage = DualLayerStore
DualLayerGraphStore = DualLayerStore
DualLayerStorageEngine = DualLayerStore
