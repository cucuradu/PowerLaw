"""
Unit tests for DualLayerGraphStore.
Tests node and edge CRUD, tabular history append, NetworkX conversion,
and Power-Law metrics computation.
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)
from src.storage.graph_store import DualLayerGraphStore


@pytest.fixture
def temp_store():
    tmp_dir = tempfile.mkdtemp()
    store = DualLayerGraphStore(storage_dir=tmp_dir)
    yield store
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_graph_store_crud_and_persistence(temp_store):
    node1 = GraphNode(id="LAW_A", label="Law A", node_type=NodeType.LAW, activation_date="2023-01-01")
    node2 = GraphNode(id="ACT_B", label="Action B", node_type=NodeType.ACTION, activation_date="2023-02-01")
    edge = GraphEdge(source_id="LAW_A", target_id="ACT_B", relation_type=EdgeRelation.MANDATES)

    temp_store.add_node(node1)
    temp_store.add_node(node2)
    temp_store.add_edge(edge)

    state1 = NodeStateRecord(
        node_id="ACT_B", node_type=NodeType.ACTION, timestamp="2023-02-01",
        state_value="ENFORCED", metric_name="compliance_pct", metric_value=100.0
    )
    temp_store.add_state_record(state1)
    temp_store.save()

    # Create new instance to test reload from disk
    reload_store = DualLayerGraphStore(storage_dir=str(temp_store.storage_dir))
    assert len(reload_store.get_nodes()) == 2
    assert len(reload_store.get_edges()) == 1
    assert len(reload_store.get_history()) == 1
    assert reload_store.get_node("LAW_A") is not None
    assert reload_store.get_node("ACT_B") is not None


def test_power_law_degree_metrics(temp_store):
    # Construct a scale-free star-hub topology
    hub = GraphNode(id="HUB_CBAM", label="Hub CBAM", node_type=NodeType.LAW)
    temp_store.add_node(hub)

    for i in range(10):
        leaf = GraphNode(id=f"LEAF_{i}", label=f"Leaf {i}", node_type=NodeType.ACTION)
        temp_store.add_node(leaf)
        temp_store.add_edge(GraphEdge(source_id="HUB_CBAM", target_id=f"LEAF_{i}", relation_type=EdgeRelation.MANDATES))

    metrics = temp_store.compute_power_law_metrics()
    assert metrics["num_nodes"] == 11
    assert metrics["num_edges"] == 10
    assert metrics["gamma"] > 0
    assert len(metrics["top_out_degree"]) > 0
    assert metrics["top_out_degree"][0]["node_id"] == "HUB_CBAM"
