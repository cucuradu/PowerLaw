"""
Unit tests for DualLayerGNNDataset.
Tests feature matrix tensor generation, edge index alignment, and temporal slicing.
"""

import pytest
import tempfile
import shutil
import numpy as np

from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)
from src.storage.graph_store import DualLayerGraphStore
from src.models.gnn_dataset import DualLayerGNNDataset


@pytest.fixture
def populated_store():
    tmp_dir = tempfile.mkdtemp()
    store = DualLayerGraphStore(storage_dir=tmp_dir)

    n1 = GraphNode(id="LAW_1", label="Law 1", node_type=NodeType.LAW, activation_date="2023-01-01")
    n2 = GraphNode(id="ACT_1", label="Action 1", node_type=NodeType.ACTION, activation_date="2023-03-01")
    n3 = GraphNode(id="REACT_1", label="Reaction 1", node_type=NodeType.REACTION, activation_date="2023-06-01")

    store.add_node(n1)
    store.add_node(n2)
    store.add_node(n3)

    store.add_edge(GraphEdge(source_id="LAW_1", target_id="ACT_1", relation_type=EdgeRelation.MANDATES, timestamp="2023-01-15"))
    store.add_edge(GraphEdge(source_id="ACT_1", target_id="REACT_1", relation_type=EdgeRelation.TRIGGERS, timestamp="2023-03-15"))

    store.add_state_record(NodeStateRecord(
        node_id="REACT_1", node_type=NodeType.REACTION, timestamp="2023-06-15",
        state_value="PEAK", metric_name="price_delta", metric_value=25.0
    ))
    store.save()

    yield store
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_gnn_snapshot_construction(populated_store):
    gnn_ds = DualLayerGNNDataset(populated_store)
    snap = gnn_ds.build_snapshot()

    assert snap["num_nodes"] == 3
    assert snap["num_edges"] == 2
    assert snap["X"].shape == (3, 11)
    assert snap["edge_index"].shape == (2, 2)
    assert isinstance(snap["X"], np.ndarray)


def test_gnn_temporal_filtering(populated_store):
    gnn_ds = DualLayerGNNDataset(populated_store)

    # Before Reaction 1 was activated (2023-02-01: only Law 1 is active)
    snap_early = gnn_ds.build_snapshot(as_of_date="2023-02-01")
    assert snap_early["num_nodes"] == 1
    assert snap_early["num_edges"] == 0

    # After Action 1 active (2023-04-01: Law 1 and Action 1 active)
    snap_mid = gnn_ds.build_snapshot(as_of_date="2023-04-01")
    assert snap_mid["num_nodes"] == 2
    assert snap_mid["num_edges"] == 1
