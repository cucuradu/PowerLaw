"""
Automated Test Suite for GNN Preparation & Dataset Exporter.

Verifies:
1. Static graph compilation (Node feature matrix X, edge index E, adjacency matrix A).
2. Text embedding integration (deterministic fallback, metadata vector, custom dict, callable).
3. Topological centrality compilation (in/out degree, betweenness, closeness, PageRank, clustering, harmonic).
4. Tabular metrics aggregation (latest, mean, std, min, max, delta, volatility, trend slope, state one-hot).
5. Temporal sequence slicing (chronological snapshots, cumulative vs sliding, fixed vs dynamic node set, forecasting targets).
6. Export formats (PyG-compatible dictionary/tensors, JSON graph serialization, NPZ archive).
7. Edge cases (empty graph, single node, disconnected graph, missing history, invalid dates, real data/lancedb).
"""

import os
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)
from src.storage.graph_store import DualLayerGraphStore
from src.gnn.graph_exporter import GNNGraphExporter, GNNJsonEncoder


class TestGNNGraphExporter(unittest.TestCase):
    """Comprehensive test suite for GNNGraphExporter."""

    def setUp(self):
        """Create a controlled in-memory dual-layer graph store for deterministic testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = DualLayerGraphStore(storage_dir=self.temp_dir.name)

        # 1. Create a set of diverse nodes
        self.node_law = GraphNode(
            id="LAW_CBAM_01",
            label="Carbon Border Adjustment Regulation",
            node_type=NodeType.LAW,
            description="EU regulation mandating carbon border reporting on imported metals and cement.",
            jurisdiction="EU",
            activation_date="2023-01-01",
            metadata={"strictness": "HARD", "celex_id": "32023R0956"}
        )
        self.node_act = GraphNode(
            id="ACT_REPORT_01",
            label="Mandatory Quarterly Carbon Reporting",
            node_type=NodeType.ACTION,
            description="Importers must submit emissions declarations every quarter.",
            jurisdiction="EU",
            activation_date="2023-06-01",
            metadata={"compliance_frequency": "QUARTERLY"}
        )
        self.node_react = GraphNode(
            id="REACT_COST_SURGE",
            label="Steel Compliance Cost Rise",
            node_type=NodeType.REACTION,
            description="Domestic and import compliance costs increase by 12 EUR per ton.",
            jurisdiction="EU",
            activation_date="2023-09-01",
            metadata={"sector": "STEEL"}
        )
        self.node_sector = GraphNode(
            id="SEC_BASIC_METALS",
            label="Basic Metals & Metallurgy",
            node_type=NodeType.SECTOR,
            description="NACE C24 manufacture of basic metals.",
            jurisdiction="EU",
            activation_date="2022-01-01",
            metadata={"nace_code": "C24"}
        )
        self.node_commodity = GraphNode(
            id="COM_CO2_EUA",
            label="EU Carbon Allowance (EUA)",
            node_type=NodeType.COMMODITY,
            description="European Union Allowance spot carbon permit price.",
            jurisdiction="GLOBAL",
            activation_date="2022-01-01",
            metadata={"unit": "EUR/tCO2"}
        )

        for n in [self.node_law, self.node_act, self.node_react, self.node_sector, self.node_commodity]:
            self.store.add_node(n)

        # 2. Create topological edges
        self.edge_1 = GraphEdge(
            source_id="LAW_CBAM_01",
            target_id="ACT_REPORT_01",
            relation_type=EdgeRelation.MANDATES,
            weight=1.0,
            timestamp="2023-01-15"
        )
        self.edge_2 = GraphEdge(
            source_id="ACT_REPORT_01",
            target_id="REACT_COST_SURGE",
            relation_type=EdgeRelation.TRIGGERS,
            weight=0.85,
            timestamp="2023-06-15"
        )
        self.edge_3 = GraphEdge(
            source_id="REACT_COST_SURGE",
            target_id="SEC_BASIC_METALS",
            relation_type=EdgeRelation.IMPACTS,
            weight=1.2,
            timestamp="2023-09-15"
        )
        self.edge_4 = GraphEdge(
            source_id="LAW_CBAM_01",
            target_id="COM_CO2_EUA",
            relation_type=EdgeRelation.REGULATES,
            weight=0.9,
            timestamp="2023-02-01"
        )

        for e in [self.edge_1, self.edge_2, self.edge_3, self.edge_4]:
            self.store.add_edge(e)

        # 3. Create historical tabular state records across chronological quarters
        records = [
            # Carbon Allowance history
            NodeStateRecord(
                node_id="COM_CO2_EUA",
                node_type=NodeType.COMMODITY,
                timestamp="2022-03-31",
                state_value="ACTIVE",
                metric_name="price",
                metric_value=78.5,
                delta=0.0
            ),
            NodeStateRecord(
                node_id="COM_CO2_EUA",
                node_type=NodeType.COMMODITY,
                timestamp="2022-09-30",
                state_value="ACTIVE",
                metric_name="price",
                metric_value=84.0,
                delta=5.5
            ),
            NodeStateRecord(
                node_id="COM_CO2_EUA",
                node_type=NodeType.COMMODITY,
                timestamp="2023-03-31",
                state_value="PEAK",
                metric_name="price",
                metric_value=96.2,
                delta=12.2
            ),
            NodeStateRecord(
                node_id="COM_CO2_EUA",
                node_type=NodeType.COMMODITY,
                timestamp="2023-09-30",
                state_value="CRITICAL",
                metric_name="price",
                metric_value=85.0,
                delta=-11.2
            ),
            # Reaction node history
            NodeStateRecord(
                node_id="REACT_COST_SURGE",
                node_type=NodeType.REACTION,
                timestamp="2023-09-30",
                state_value="INCREASE",
                metric_name="compliance_cost",
                metric_value=12.0,
                delta=12.0
            ),
            NodeStateRecord(
                node_id="REACT_COST_SURGE",
                node_type=NodeType.REACTION,
                timestamp="2023-12-31",
                state_value="STABLE",
                metric_name="compliance_cost",
                metric_value=14.5,
                delta=2.5
            )
        ]
        self.store.add_state_records_batch(records)
        self.store.save()

        # Instantiate exporter with default 64-dim embeddings for testing
        self.exporter = GNNGraphExporter(
            store=self.store,
            embedding_dim=64,
            include_text_embeddings=True,
            include_centrality=True,
            include_tabular_metrics=True,
            include_categorical=True
        )

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Static Graph Compilation Tests
    # -------------------------------------------------------------------------
    def test_compile_static_graph(self):
        """Test compiling the full static graph structure."""
        graph = self.exporter.compile_static_graph()

        self.assertEqual(graph["num_nodes"], 5)
        self.assertEqual(graph["num_edges"], 4)

        # X matrix assertions
        X = graph["X"]
        self.assertIsInstance(X, np.ndarray)
        self.assertEqual(X.dtype, np.float32)
        self.assertEqual(X.shape[0], 5)
        # Expected dimension = 64 (text) + 10 (centrality) + 20 (tabular) + 11 (categorical: 6 types + 5 jurisdictions) = 105
        expected_dim = 64 + 10 + 20 + 11
        self.assertEqual(X.shape[1], expected_dim)
        self.assertEqual(graph["feature_dim"], expected_dim)

        # Verify no NaN or Inf
        self.assertFalse(np.isnan(X).any(), "X contains NaN values")
        self.assertFalse(np.isinf(X).any(), "X contains Inf values")

        # Edge index assertions
        edge_index = graph["edge_index"]
        self.assertIsInstance(edge_index, np.ndarray)
        self.assertEqual(edge_index.dtype, np.int64)
        self.assertEqual(edge_index.shape, (2, 4))
        self.assertTrue(np.all(edge_index >= 0))
        self.assertTrue(np.all(edge_index < 5))

        # Edge attributes & weights assertions
        edge_attr = graph["edge_attr"]
        edge_weight = graph["edge_weight"]
        self.assertEqual(edge_attr.shape, (4, len(EdgeRelation) + 1))
        self.assertEqual(edge_weight.shape, (4,))
        self.assertEqual(edge_weight.dtype, np.float32)

        # Feature slices check
        slices = graph["feature_slices"]
        self.assertIn("text_embedding", slices)
        self.assertIn("topological_centrality", slices)
        self.assertIn("tabular_metrics", slices)
        self.assertIn("categorical", slices)
        self.assertEqual(slices["text_embedding"], (0, 64))
        self.assertEqual(slices["topological_centrality"], (64, 74))

    # -------------------------------------------------------------------------
    # 2. Adjacency Matrix Tests
    # -------------------------------------------------------------------------
    def test_adjacency_matrix_compilation(self):
        """Test dense and normalized adjacency matrices."""
        nodes = self.store.get_nodes()
        edges = self.store.get_edges()

        # Unnormalized adjacency without self-loops
        A = self.exporter.compile_adjacency_matrix(nodes=nodes, edges=edges, normalized=False, self_loops=False)
        self.assertEqual(A.shape, (5, 5))
        self.assertEqual(A.dtype, np.float32)
        # Exactly 4 off-diagonal entries should be non-zero
        self.assertEqual(int(np.count_nonzero(A)), 4)
        self.assertEqual(int(np.trace(A)), 0)

        # Normalized adjacency with self-loops
        A_norm = self.exporter.compile_adjacency_matrix(nodes=nodes, edges=edges, normalized=True, self_loops=True)
        self.assertEqual(A_norm.shape, (5, 5))
        self.assertEqual(A_norm.dtype, np.float32)
        self.assertFalse(np.isnan(A_norm).any())
        # Self loop implies diagonal is strictly positive
        self.assertTrue(np.all(np.diag(A_norm) > 0))

    # -------------------------------------------------------------------------
    # 3. Text Embeddings Integration Tests
    # -------------------------------------------------------------------------
    def test_deterministic_text_embedding(self):
        """Verify deterministic text embeddings are reproducible and unit-norm."""
        emb1 = self.exporter.get_node_embedding(self.node_law)
        emb2 = self.exporter.get_node_embedding(self.node_law)

        self.assertEqual(len(emb1), 64)
        np.testing.assert_array_equal(emb1, emb2)
        norm = np.linalg.norm(emb1)
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_custom_and_metadata_embeddings(self):
        """Verify priority hierarchy for precomputed and custom embeddings."""
        # Custom embeddings dictionary override
        custom_vec = np.ones(64, dtype=np.float32)
        custom_exporter = GNNGraphExporter(
            store=self.store,
            embedding_dim=64,
            custom_embeddings={"LAW_CBAM_01": custom_vec.tolist()}
        )
        emb = custom_exporter.get_node_embedding(self.node_law)
        # Should be unit-normalized custom_vec
        expected = custom_vec / np.linalg.norm(custom_vec)
        np.testing.assert_allclose(emb, expected, atol=1e-5)

        # Metadata vector override
        node_with_vec = GraphNode(
            id="NODE_WITH_VEC",
            label="Vector Node",
            node_type=NodeType.ACTION,
            metadata={"vector": [0.5] * 64}
        )
        emb_meta = self.exporter.get_node_embedding(node_with_vec)
        self.assertAlmostEqual(np.linalg.norm(emb_meta), 1.0, places=5)

    def test_callable_embedding_fn(self):
        """Verify external embedding callable function works and caches."""
        calls = []

        def mock_embedder(text: str) -> list:
            calls.append(text)
            return [0.1] * 64

        exp = GNNGraphExporter(store=self.store, embedding_dim=64, embedding_fn=mock_embedder)
        emb = exp.get_node_embedding(self.node_law)
        self.assertEqual(len(calls), 1)
        # Second call should hit internal cache
        emb_cached = exp.get_node_embedding(self.node_law)
        self.assertEqual(len(calls), 1)
        np.testing.assert_array_equal(emb, emb_cached)

    # -------------------------------------------------------------------------
    # 4. Topological Centrality Compilation Tests
    # -------------------------------------------------------------------------
    def test_topological_centrality(self):
        """Verify topological centrality metrics across directed nodes."""
        nodes = self.store.get_nodes()
        edges = self.store.get_edges()

        topo_mat, names = self.exporter.compute_topological_features(nodes, edges)
        self.assertEqual(topo_mat.shape, (5, 10))
        self.assertFalse(np.isnan(topo_mat).any())

        # LAW_CBAM_01 has out-degree 2 (to ACT_REPORT_01 and COM_CO2_EUA) and in-degree 0
        node2idx = {n.id: i for i, n in enumerate(nodes)}
        law_idx = node2idx["LAW_CBAM_01"]

        # in_degree_norm is column 0, out_degree_norm is column 1
        self.assertEqual(topo_mat[law_idx, 0], 0.0)
        self.assertGreater(topo_mat[law_idx, 1], 0.0)

    # -------------------------------------------------------------------------
    # 5. Tabular Metrics Aggregation Tests
    # -------------------------------------------------------------------------
    def test_tabular_metrics_aggregation(self):
        """Verify calculation of statistical aggregates from history records."""
        nodes = self.store.get_nodes()
        history_df = self.store.get_history_df()

        tab_mat, names = self.exporter.compute_tabular_features(nodes, history_df)
        self.assertEqual(tab_mat.shape, (5, 20))
        self.assertFalse(np.isnan(tab_mat).any())

        node2idx = {n.id: i for i, n in enumerate(nodes)}
        com_idx = node2idx["COM_CO2_EUA"]

        # COM_CO2_EUA had 4 records: 78.5, 84.0, 96.2, 85.0
        # latest_val = 85.0
        # mean_val = (78.5 + 84.0 + 96.2 + 85.0) / 4 = 85.925
        # min_val = 78.5, max_val = 96.2, count = 4
        latest_idx = names.index("latest_metric_value")
        mean_idx = names.index("mean_metric_value")
        min_idx = names.index("min_metric_value")
        max_idx = names.index("max_metric_value")
        count_idx = names.index("records_count")

        self.assertAlmostEqual(tab_mat[com_idx, latest_idx], 85.0, places=2)
        self.assertAlmostEqual(tab_mat[com_idx, mean_idx], 85.925, places=2)
        self.assertAlmostEqual(tab_mat[com_idx, min_idx], 78.5, places=2)
        self.assertAlmostEqual(tab_mat[com_idx, max_idx], 96.2, places=2)
        self.assertEqual(tab_mat[com_idx, count_idx], 4.0)

        # Node without history (e.g. LAW_CBAM_01) should have zero statistics
        law_idx = node2idx["LAW_CBAM_01"]
        self.assertEqual(tab_mat[law_idx, count_idx], 0.0)
        self.assertEqual(tab_mat[law_idx, latest_idx], 0.0)

    # -------------------------------------------------------------------------
    # 6. Temporal Sequence Slicing Tests
    # -------------------------------------------------------------------------
    def test_temporal_snapshots_slicing(self):
        """Verify chronological slicing for Temporal GNNs."""
        # 1. Slicing with explicit snapshot count
        snapshots = self.exporter.slice_temporal_snapshots(
            num_snapshots=4,
            cumulative=True,
            fixed_node_set=True
        )

        self.assertEqual(len(snapshots), 4)

        # Check chronological order
        timestamps = [s["timestamp"] for s in snapshots]
        self.assertEqual(timestamps, sorted(timestamps))

        # Check snapshot tensor format
        for s in snapshots:
            self.assertEqual(s["num_nodes"], 5)
            self.assertIn("X", s)
            self.assertIn("edge_index", s)
            self.assertIn("node_mask", s)
            self.assertEqual(s["X"].shape[0], 5)
            self.assertEqual(s["node_mask"].shape, (5,))

        # Cumulative property: active edges should be non-decreasing over time
        edge_counts = [s["num_edges"] for s in snapshots]
        self.assertEqual(edge_counts, sorted(edge_counts))

        # 2. Dynamic node set slicing (fixed_node_set=False)
        dyn_snapshots = self.exporter.slice_temporal_snapshots(
            num_snapshots=3,
            cumulative=False,
            fixed_node_set=False
        )
        self.assertGreater(len(dyn_snapshots), 0)
        for s in dyn_snapshots:
            self.assertEqual(len(s["node_ids"]), s["num_nodes"])
            self.assertEqual(s["X"].shape[0], s["num_nodes"])
            if s["num_edges"] > 0:
                self.assertTrue(np.all(s["edge_index"] < s["num_nodes"]))

        # 3. Forecasting target generation (target_metric)
        forecast_snapshots = self.exporter.slice_temporal_snapshots(
            num_snapshots=3,
            cumulative=True,
            fixed_node_set=True,
            target_metric="price"
        )
        # Snapshot 0 should have future target Y for commodity price
        s0_y = forecast_snapshots[0]["y"]
        self.assertIsNotNone(s0_y)
        self.assertEqual(s0_y.shape, (5,))

    # -------------------------------------------------------------------------
    # 7. PyTorch Geometric Export Tests
    # -------------------------------------------------------------------------
    def test_pyg_data_export(self):
        """Verify PyG-compatible dictionary structure and types."""
        pyg_data = self.exporter.to_pyg_data()

        required_keys = ["x", "edge_index", "edge_attr", "edge_weight", "num_nodes", "num_edges", "node_ids"]
        for k in required_keys:
            self.assertIn(k, pyg_data)

        self.assertEqual(pyg_data["num_nodes"], 5)
        self.assertEqual(pyg_data["num_edges"], 4)
        self.assertEqual(pyg_data["x"].shape, (5, self.exporter.embedding_dim + 10 + 20 + 11))
        self.assertEqual(pyg_data["edge_index"].shape, (2, 4))
        self.assertEqual(pyg_data["edge_index"].dtype, np.int64)

        # Temporal PyG collection
        temporal_pyg = self.exporter.export_temporal_pyg(num_snapshots=3)
        self.assertEqual(temporal_pyg["format"], "pyg_temporal_sequence")
        self.assertEqual(temporal_pyg["num_snapshots"], 3)
        self.assertEqual(len(temporal_pyg["snapshots"]), 3)
        self.assertEqual(len(temporal_pyg["snapshot_times"]), 3)

    # -------------------------------------------------------------------------
    # 8. JSON Export Tests
    # -------------------------------------------------------------------------
    def test_json_export(self):
        """Verify JSON graph serialization for static and temporal graphs."""
        # Static graph JSON
        json_str = self.exporter.export_json(indent=2)
        parsed = json.loads(json_str)

        self.assertEqual(parsed["format"], "powerlaw_gnn_static_v1")
        self.assertEqual(parsed["num_nodes"], 5)
        self.assertEqual(parsed["num_edges"], 4)
        self.assertEqual(len(parsed["nodes"]), 5)
        self.assertEqual(len(parsed["links"]), 4)
        self.assertIn("tensors", parsed)
        self.assertEqual(len(parsed["tensors"]["X"]), 5)

        # Save to disk test
        out_path = Path(self.temp_dir.name) / "gnn_export.json"
        self.exporter.export_json(filepath=out_path)
        self.assertTrue(out_path.exists())
        with open(out_path, "r", encoding="utf-8") as f:
            disk_parsed = json.load(f)
        self.assertEqual(disk_parsed["num_nodes"], 5)

        # Temporal graph JSON
        t_json_str = self.exporter.export_temporal_json(num_snapshots=3)
        t_parsed = json.loads(t_json_str)
        self.assertEqual(t_parsed["format"], "powerlaw_gnn_temporal_v1")
        self.assertEqual(t_parsed["num_snapshots"], 3)
        self.assertEqual(len(t_parsed["snapshots"]), 3)

    # -------------------------------------------------------------------------
    # 9. NPZ Archive Export Tests
    # -------------------------------------------------------------------------
    def test_npz_export(self):
        """Verify exporting and reloading from NumPy compressed archive."""
        npz_path = Path(self.temp_dir.name) / "gnn_dataset.npz"
        self.exporter.export_npz(filepath=npz_path)

        self.assertTrue(npz_path.exists())
        loaded = np.load(npz_path, allow_pickle=True)
        self.assertIn("X", loaded)
        self.assertIn("edge_index", loaded)
        self.assertIn("adjacency", loaded)
        self.assertEqual(loaded["X"].shape[0], 5)
        self.assertEqual(loaded["edge_index"].shape, (2, 4))

    # -------------------------------------------------------------------------
    # 10. Edge Cases & Real Data Tests
    # -------------------------------------------------------------------------
    def test_empty_graph(self):
        """Verify edge case: completely empty graph store."""
        empty_dir = tempfile.TemporaryDirectory()
        empty_store = DualLayerGraphStore(storage_dir=empty_dir.name)
        empty_exporter = GNNGraphExporter(store=empty_store)

        g = empty_exporter.compile_static_graph()
        self.assertEqual(g["num_nodes"], 0)
        self.assertEqual(g["num_edges"], 0)
        self.assertEqual(g["X"].shape[0], 0)
        self.assertEqual(g["edge_index"].shape[1], 0)

        # Temporal snapshots on empty graph
        snaps = empty_exporter.slice_temporal_snapshots()
        self.assertEqual(len(snaps), 0)

        # JSON export on empty graph
        js = empty_exporter.export_json()
        parsed = json.loads(js)
        self.assertEqual(parsed["num_nodes"], 0)

        empty_dir.cleanup()

    def test_single_node_graph(self):
        """Verify graph with a single isolated node."""
        single_dir = tempfile.TemporaryDirectory()
        single_store = DualLayerGraphStore(storage_dir=single_dir.name)
        single_store.add_node(self.node_law)
        exp = GNNGraphExporter(store=single_store)

        g = exp.compile_static_graph()
        self.assertEqual(g["num_nodes"], 1)
        self.assertEqual(g["num_edges"], 0)
        self.assertEqual(g["X"].shape[0], 1)
        self.assertFalse(np.isnan(g["X"]).any())

        adj = exp.compile_adjacency_matrix(normalized=True, self_loops=True)
        self.assertEqual(adj.shape, (1, 1))
        self.assertAlmostEqual(float(adj[0, 0]), 1.0, places=5)

        single_dir.cleanup()

    def test_real_lancedb_data(self):
        """Verify GNNGraphExporter against real repository data in data/lancedb if present."""
        real_lancedb_path = Path("data/lancedb")
        if real_lancedb_path.exists() and (real_lancedb_path / "graph_nodes.parquet").exists():
            nodes_df = pd.read_parquet(real_lancedb_path / "graph_nodes.parquet")
            edges_df = pd.read_parquet(real_lancedb_path / "graph_edges.parquet") if (real_lancedb_path / "graph_edges.parquet").exists() else pd.DataFrame()
            expected_nodes = len(nodes_df)
            expected_edges = len(edges_df)

            real_exp = GNNGraphExporter(storage_dir=real_lancedb_path, embedding_dim=128)
            g = real_exp.compile_static_graph()

            self.assertEqual(g["num_nodes"], expected_nodes)
            self.assertEqual(g["num_edges"], expected_edges)
            self.assertEqual(g["X"].shape[0], expected_nodes)
            self.assertFalse(np.isnan(g["X"]).any(), "Real data X contains NaNs")

            # Slice 4 temporal snapshots
            snaps = real_exp.slice_temporal_snapshots(num_snapshots=4)
            self.assertEqual(len(snaps), 4)
            for s in snaps:
                self.assertEqual(s["num_nodes"], expected_nodes)
                self.assertFalse(np.isnan(s["X"]).any())

            # Export JSON
            js = real_exp.export_json()
            parsed = json.loads(js)
            self.assertEqual(parsed["num_nodes"], expected_nodes)
            self.assertEqual(parsed["num_edges"], expected_edges)

    def test_feature_slices_partition(self):
        """Verify feature_slices cleanly and contiguously partitions feature_names and X."""
        g = self.exporter.compile_static_graph()
        slices = g["feature_slices"]
        total_dim = g["feature_dim"]

        # Ensure contiguous slices from 0 to total_dim
        prev_end = 0
        for comp_name in ["text_embedding", "topological_centrality", "tabular_metrics", "categorical"]:
            if comp_name in slices:
                start, end = slices[comp_name]
                self.assertEqual(start, prev_end, f"Slice {comp_name} starts at {start}, expected {prev_end}")
                self.assertGreater(end, start, f"Slice {comp_name} length must be > 0")
                prev_end = end
        self.assertEqual(prev_end, total_dim, f"Slices end at {prev_end}, expected total dim {total_dim}")
        self.assertEqual(len(g["feature_names"]), total_dim)

    def test_to_torch_flag(self):
        """Verify to_torch parameter raises ImportError if PyTorch is absent, or returns tensors if present."""
        try:
            import torch
            has_torch = True
        except ImportError:
            has_torch = False

        if not has_torch:
            with self.assertRaises(ImportError):
                self.exporter.to_pyg_data(to_torch=True)
        else:
            pyg = self.exporter.to_pyg_data(to_torch=True)
            self.assertIsNotNone(pyg)

    def test_temporal_frequency_and_explicit_windows(self):
        """Verify frequency windowing and explicit time_windows."""
        # Frequency windowing
        freq_snaps = self.exporter.slice_temporal_snapshots(window_size="180D", cumulative=True)
        self.assertGreater(len(freq_snaps), 1)

        # Explicit time windows
        custom_windows = [
            ("2022-01-01", "2022-12-31"),
            ("2023-01-01", "2023-12-31")
        ]
        explicit_snaps = self.exporter.slice_temporal_snapshots(time_windows=custom_windows, cumulative=False)
        self.assertEqual(len(explicit_snaps), 2)
        self.assertEqual(explicit_snaps[0]["window_end"], "2022-12-31")
        self.assertEqual(explicit_snaps[1]["window_end"], "2023-12-31")

    def test_disconnected_graph_metrics(self):
        """Verify topological metrics work on disconnected components without errors."""
        disc_dir = tempfile.TemporaryDirectory()
        disc_store = DualLayerGraphStore(storage_dir=disc_dir.name)

        # Disconnected pair 1: N1 -> N2
        n1 = GraphNode(id="D1", label="Disc 1", node_type=NodeType.ACTION)
        n2 = GraphNode(id="D2", label="Disc 2", node_type=NodeType.ACTION)
        e1 = GraphEdge(source_id="D1", target_id="D2", relation_type=EdgeRelation.TRIGGERS)

        # Disconnected pair 2: N3 -> N4
        n3 = GraphNode(id="D3", label="Disc 3", node_type=NodeType.REACTION)
        n4 = GraphNode(id="D4", label="Disc 4", node_type=NodeType.REACTION)
        e2 = GraphEdge(source_id="D3", target_id="D4", relation_type=EdgeRelation.IMPACTS)

        # Isolated node N5
        n5 = GraphNode(id="D5", label="Disc 5", node_type=NodeType.COMMODITY)

        for n in [n1, n2, n3, n4, n5]:
            disc_store.add_node(n)
        for e in [e1, e2]:
            disc_store.add_edge(e)

        exp = GNNGraphExporter(store=disc_store)
        g = exp.compile_static_graph()
        self.assertEqual(g["num_nodes"], 5)
        self.assertEqual(g["num_edges"], 2)
        self.assertFalse(np.isnan(g["X"]).any())
        self.assertFalse(np.isnan(g["adjacency"]).any())
        self.assertFalse(np.isnan(g["normalized_adjacency"]).any())

        disc_dir.cleanup()

    def test_timezone_aware_and_mixed_formats(self):
        """Verify robust handling of mixed tz-naive, UTC, and offset timestamps."""
        tz_dir = tempfile.TemporaryDirectory()
        tz_store = DualLayerGraphStore(storage_dir=tz_dir.name)

        n1 = GraphNode(id="TZ_1", label="TZ Node 1", node_type=NodeType.LAW, activation_date="2023-01-01T00:00:00Z")
        n2 = GraphNode(id="TZ_2", label="TZ Node 2", node_type=NodeType.ACTION, activation_date="2023-06-01 12:00:00+02:00")
        tz_store.add_node(n1)
        tz_store.add_node(n2)

        e1 = GraphEdge(source_id="TZ_1", target_id="TZ_2", relation_type=EdgeRelation.MANDATES, timestamp="2023-03-01T15:30:00Z")
        tz_store.add_edge(e1)

        # Tabular records with steady linear increase: 10 -> 20 -> 30 across 3 days
        recs = [
            NodeStateRecord(
                node_id="TZ_1",
                node_type=NodeType.LAW,
                timestamp="2023-01-01T00:00:00Z",
                state_value="ACTIVE",
                metric_name="score",
                metric_value=10.0
            ),
            NodeStateRecord(
                node_id="TZ_1",
                node_type=NodeType.LAW,
                timestamp="2023-01-02 02:00:00+02:00",
                state_value="ACTIVE",
                metric_name="score",
                metric_value=20.0
            ),
            NodeStateRecord(
                node_id="TZ_1",
                node_type=NodeType.LAW,
                timestamp="2023-01-03T00:00:00Z",
                state_value="ACTIVE",
                metric_name="score",
                metric_value=30.0
            )
        ]
        tz_store.add_state_records_batch(recs)
        tz_store.save()

        exp = GNNGraphExporter(store=tz_store, embedding_dim=16)

        # 1. Compile static graph with naive cutoff date
        g_naive = exp.compile_static_graph(as_of_date="2023-04-01")
        self.assertEqual(g_naive["num_nodes"], 1)  # Only TZ_1 activated by April
        self.assertEqual(g_naive["num_edges"], 0)  # TZ_2 not active yet, so edge TZ_1 -> TZ_2 cannot exist

        # 2. Compile static graph with timezone-aware cutoff date
        g_aware = exp.compile_static_graph(as_of_date="2023-07-01T00:00:00Z")
        self.assertEqual(g_aware["num_nodes"], 2)  # Both TZ_1 and TZ_2 activated by July
        self.assertEqual(g_aware["num_edges"], 1)  # Edge active now that both endpoints exist

        # 3. Verify trend slope is properly calculated (10 units per day) and not zero
        X, feat_names, slices = exp.compile_node_features(nodes=[n1], history_df=tz_store.get_history_df())
        slope_idx = feat_names.index("trend_slope")
        self.assertAlmostEqual(float(X[0, slope_idx]), 10.0, places=1)

        tz_dir.cleanup()

    def test_sliding_window_coverage_and_no_zero_duration(self):
        """Verify sliding window snapshots have positive duration and include min_date items."""
        win_dir = tempfile.TemporaryDirectory()
        win_store = DualLayerGraphStore(storage_dir=win_dir.name)

        n1 = GraphNode(id="W1", label="W1", node_type=NodeType.LAW, activation_date="2022-01-01")
        n2 = GraphNode(id="W2", label="W2", node_type=NodeType.ACTION, activation_date="2022-06-01")
        n3 = GraphNode(id="W3", label="W3", node_type=NodeType.REACTION, activation_date="2022-12-01")
        for n in [n1, n2, n3]:
            win_store.add_node(n)

        e1 = GraphEdge(source_id="W1", target_id="W2", relation_type=EdgeRelation.MANDATES, timestamp="2022-01-01")
        win_store.add_edge(e1)
        win_store.save()

        exp = GNNGraphExporter(store=win_store, embedding_dim=16)

        # 3 uniform sliding snapshots
        snaps = exp.slice_temporal_snapshots(num_snapshots=3, cumulative=False, fixed_node_set=True)
        self.assertEqual(len(snaps), 3)

        # Every snapshot must have strictly positive window duration
        for s in snaps:
            start_dt = pd.to_datetime(s["window_start"])
            end_dt = pd.to_datetime(s["window_end"])
            self.assertLess(start_dt, end_dt, f"Snapshot window has 0 duration: {s['window_start']} to {s['window_end']}")

        # W1 and e1 activated on 2022-01-01 must be active in snapshot 0
        s0 = snaps[0]
        self.assertGreater(s0["active_nodes_count"], 0)
        self.assertTrue(s0["node_mask"][0])  # W1 is active in snapshot 0
        self.assertEqual(s0["num_edges"], 1)  # e1 is active in snapshot 0

        win_dir.cleanup()

    def test_dynamic_node_sets_endpoint_preservation(self):
        """Verify dynamic node set (fixed_node_set=False) preserves endpoints of active edges."""
        dyn_dir = tempfile.TemporaryDirectory()
        dyn_store = DualLayerGraphStore(storage_dir=dyn_dir.name)

        # N1 activated in 2021, N2 activated in 2022. Edge occurs in 2022.
        n1 = GraphNode(id="OLD_LAW", label="Old Law", node_type=NodeType.LAW, activation_date="2021-01-01")
        n2 = GraphNode(id="NEW_ACT", label="New Action", node_type=NodeType.ACTION, activation_date="2022-03-01")
        dyn_store.add_node(n1)
        dyn_store.add_node(n2)

        e1 = GraphEdge(source_id="OLD_LAW", target_id="NEW_ACT", relation_type=EdgeRelation.MANDATES, timestamp="2022-03-15")
        dyn_store.add_edge(e1)
        dyn_store.save()

        exp = GNNGraphExporter(store=dyn_store, embedding_dim=16)

        # Sliding window in 2022
        custom_windows = [("2022-01-01", "2022-12-31")]
        snaps = exp.slice_temporal_snapshots(time_windows=custom_windows, cumulative=False, fixed_node_set=False)
        self.assertEqual(len(snaps), 1)

        s = snaps[0]
        # Both endpoints must be present so that the edge is not orphaned or dropped
        self.assertIn("OLD_LAW", s["node_ids"])
        self.assertIn("NEW_ACT", s["node_ids"])
        self.assertEqual(s["num_edges"], 1)
        self.assertEqual(s["edge_index"].shape, (2, 1))
        # Edge index values must be strictly valid node indices (< num_nodes)
        self.assertTrue(np.all(s["edge_index"] < s["num_nodes"]))

        dyn_dir.cleanup()

    def test_directed_adjacency_preserves_sink_edges(self):
        """Verify directed normalized adjacency does not erase edges ending at sink nodes."""
        chain_dir = tempfile.TemporaryDirectory()
        chain_store = DualLayerGraphStore(storage_dir=chain_dir.name)

        # Chain graph: A -> B -> C (C has out-degree 0)
        na = GraphNode(id="NODE_A", label="A", node_type=NodeType.ACTION)
        nb = GraphNode(id="NODE_B", label="B", node_type=NodeType.ACTION)
        nc = GraphNode(id="NODE_C", label="C", node_type=NodeType.ACTION)
        chain_store.add_node(na)
        chain_store.add_node(nb)
        chain_store.add_node(nc)

        chain_store.add_edge(GraphEdge(source_id="NODE_A", target_id="NODE_B", relation_type=EdgeRelation.TRIGGERS, weight=1.0))
        chain_store.add_edge(GraphEdge(source_id="NODE_B", target_id="NODE_C", relation_type=EdgeRelation.IMPACTS, weight=1.0))
        chain_store.save()

        exp = GNNGraphExporter(store=chain_store)

        # 1. Directed normalized without self-loops: both edges must be non-zero
        A_dir = exp.compile_adjacency_matrix(normalized=True, self_loops=False, symmetrize=False)
        self.assertGreater(A_dir[0, 1], 0.0, "Edge A->B was zeroed out")
        self.assertGreater(A_dir[1, 2], 0.0, "Edge B->C to sink node was incorrectly zeroed out!")

        # 2. Symmetrized normalized: must be exactly symmetric A == A.T
        A_sym = exp.compile_adjacency_matrix(normalized=True, self_loops=True, symmetrize=True)
        np.testing.assert_allclose(A_sym, A_sym.T, atol=1e-6)

        chain_dir.cleanup()

    def test_export_temporal_npz(self):
        """Verify exporting temporal sequences to .npz and loading 3D tensors."""
        npz_temporal_path = Path(self.temp_dir.name) / "gnn_temporal.npz"
        self.exporter.export_temporal_npz(filepath=npz_temporal_path, num_snapshots=3, cumulative=True)

        self.assertTrue(npz_temporal_path.exists())
        loaded = np.load(npz_temporal_path, allow_pickle=True)
        self.assertEqual(int(loaded["num_snapshots"]), 3)
        self.assertIn("X_sequence", loaded)
        self.assertEqual(loaded["X_sequence"].shape, (3, 5, 105))  # (T, N, D)
        self.assertIn("node_mask_sequence", loaded)
        self.assertEqual(loaded["node_mask_sequence"].shape, (3, 5))
        self.assertIn("snapshot_times", loaded)
        self.assertEqual(len(loaded["snapshot_times"]), 3)

    def test_metric_filter(self):
        """Verify metric_filter selectively aggregates target metric records."""
        multi_dir = tempfile.TemporaryDirectory()
        multi_store = DualLayerGraphStore(storage_dir=multi_dir.name)

        node = GraphNode(id="MULTI_1", label="Multi Node", node_type=NodeType.COMMODITY)
        multi_store.add_node(node)

        # Add price records and volume records
        multi_store.add_state_records_batch([
            NodeStateRecord(node_id="MULTI_1", node_type=NodeType.COMMODITY, timestamp="2023-01-01",
                            state_value="ACTIVE", metric_name="price", metric_value=100.0, delta=0.0),
            NodeStateRecord(node_id="MULTI_1", node_type=NodeType.COMMODITY, timestamp="2023-02-01",
                            state_value="ACTIVE", metric_name="price", metric_value=150.0, delta=50.0),
            NodeStateRecord(node_id="MULTI_1", node_type=NodeType.COMMODITY, timestamp="2023-03-01",
                            state_value="ACTIVE", metric_name="volume", metric_value=1000000.0, delta=0.0),
        ])
        multi_store.save()

        # Filter strictly to price
        exp_price = GNNGraphExporter(store=multi_store, metric_filter="price")
        g = exp_price.compile_static_graph()
        X = g["X"]
        feat_names = g["feature_names"]

        latest_idx = feat_names.index("latest_metric_value")
        mean_idx = feat_names.index("mean_metric_value")
        count_idx = feat_names.index("records_count")

        self.assertAlmostEqual(float(X[0, latest_idx]), 150.0)
        self.assertAlmostEqual(float(X[0, mean_idx]), 125.0)
        self.assertEqual(float(X[0, count_idx]), 2.0)  # Only the 2 price records

        multi_dir.cleanup()

    def test_case_insensitive_enums_and_unknown_types(self):
        """Verify graceful handling of lowercase/custom node types and relation strings."""
        cust_dir = tempfile.TemporaryDirectory()
        cust_store = DualLayerGraphStore(storage_dir=cust_dir.name)

        # Lowercase string node type and custom jurisdiction
        n1 = GraphNode(id="CUST_1", label="Cust 1", node_type=NodeType.LAW, jurisdiction="us_california")
        n2 = GraphNode(id="CUST_2", label="Cust 2", node_type=NodeType.ACTION, jurisdiction="DE")
        object.__setattr__(n1, "node_type", "law")
        object.__setattr__(n2, "node_type", "UNKNOWN_TYPE")
        cust_store.add_node(n1)
        cust_store.add_node(n2)

        # String relation type
        e1 = GraphEdge(source_id="CUST_1", target_id="CUST_2", relation_type=EdgeRelation.MANDATES, weight=1.5)
        object.__setattr__(e1, "relation_type", "mandates")
        cust_store.add_edge(e1)
        cust_store.save()

        exp = GNNGraphExporter(store=cust_store, embedding_dim=16)
        g = exp.compile_static_graph()

        self.assertEqual(g["num_nodes"], 2)
        self.assertEqual(g["num_edges"], 1)
        self.assertFalse(np.isnan(g["X"]).any())
        self.assertEqual(float(g["edge_weight"][0]), 1.5)

        # Check categorical encoding: law type should be 1.0, jurisdiction us_california should map to OTHER
        feat_names = g["feature_names"]
        law_idx = feat_names.index("type_law")
        other_jur_idx = feat_names.index("jurisdiction_other")
        de_jur_idx = feat_names.index("jurisdiction_de")

        self.assertEqual(float(g["X"][0, law_idx]), 1.0)
        self.assertEqual(float(g["X"][0, other_jur_idx]), 1.0)
        self.assertEqual(float(g["X"][1, de_jur_idx]), 1.0)

        cust_dir.cleanup()

    def test_to_torch_mocked(self):
        """Verify to_torch=True execution with mocked torch and torch_geometric modules."""
        from unittest.mock import MagicMock, patch
        import sys

        mock_torch = MagicMock()
        mock_torch.from_numpy.side_effect = lambda arr: MagicMock(shape=arr.shape)

        mock_pyg = MagicMock()
        mock_pyg_data = MagicMock()
        mock_pyg.data.Data = mock_pyg_data

        with patch.dict(sys.modules, {"torch": mock_torch, "torch_geometric": mock_pyg, "torch_geometric.data": mock_pyg.data}):
            with patch("src.gnn.graph_exporter.HAS_TORCH", True), \
                 patch("src.gnn.graph_exporter.HAS_PYG", True), \
                 patch("src.gnn.graph_exporter.torch", mock_torch), \
                 patch("src.gnn.graph_exporter.PyGData", mock_pyg_data):
                pyg = self.exporter.to_pyg_data(to_torch=True)
                mock_torch.from_numpy.assert_called()
                mock_pyg_data.assert_called_once()

            # Test PyTorch-only without PyG installed
            with patch("src.gnn.graph_exporter.HAS_TORCH", True), \
                 patch("src.gnn.graph_exporter.HAS_PYG", False), \
                 patch("src.gnn.graph_exporter.torch", mock_torch):
                t_dict = self.exporter.to_pyg_data(to_torch=True)
                self.assertIsInstance(t_dict, dict)
                self.assertTrue(t_dict["torch_available"])
                self.assertIn("x", t_dict)
                self.assertIn("edge_index", t_dict)


if __name__ == "__main__":
    unittest.main()


