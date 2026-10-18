"""
Edge case and boundary tests for PowerLaw.
Tests:
- Empty graphs and stores
- Invalid timestamps and malformed date strings
- Isolated nodes with zero degree
- Extreme values (negative prices, zero delta)
- Empty temporal snapshots (date prior to universe)
"""

import pytest
import tempfile
import shutil
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
from src.scrapers.news_scraper import EconomicNewsScraper
from src.models.gnn_dataset import DualLayerGNNDataset


@pytest.fixture
def empty_store():
    tmp_dir = tempfile.mkdtemp()
    store = DualLayerGraphStore(storage_dir=tmp_dir)
    yield store
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_empty_store_queries(empty_store):
    assert len(empty_store.get_nodes()) == 0
    assert len(empty_store.get_edges()) == 0
    assert len(empty_store.get_history()) == 0

    # Ensure to_networkx handles empty graph
    G = empty_store.to_networkx()
    assert len(G.nodes()) == 0

    # Ensure compute_power_law_metrics handles empty graph without ZeroDivisionError
    metrics = empty_store.compute_power_law_metrics()
    assert metrics["num_nodes"] == 0
    assert metrics["gamma"] == 0.0

    # Ensure get_history_df handles empty store
    df = empty_store.get_history_df()
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_isolated_nodes_power_law(empty_store):
    # Multiple isolated nodes (zero degrees)
    for i in range(5):
        empty_store.add_node(GraphNode(id=f"ISO_{i}", label=f"Isolated {i}", node_type=NodeType.SECTOR))

    metrics = empty_store.compute_power_law_metrics()
    assert metrics["num_nodes"] == 5
    assert metrics["num_edges"] == 0
    assert metrics["density"] == 0.0


def test_invalid_date_and_timestamps():
    # Malformed date strings should not crash NodeStateRecord
    rec = NodeStateRecord(
        node_id="TEST_NODE",
        node_type=NodeType.ACTION,
        timestamp="invalid-date-string",
        state_value="ACTIVE",
        metric_name="metric",
        metric_value=0.0
    )
    assert rec.timestamp_utc == 0

    scraper = EconomicNewsScraper()
    parsed = scraper._parse_date("not-a-valid-date-at-all")
    assert len(parsed) == 10  # Fallback to today YYYY-MM-DD


def test_extreme_and_negative_metric_values():
    rec = NodeStateRecord(
        node_id="NEGATIVE_GAS",
        node_type=NodeType.COMMODITY,
        timestamp="2020-04-20",
        state_value="CRASH",
        metric_name="oil_settle",
        metric_value=-37.63,
        delta=-50.0,
        unit="USD/bbl"
    )
    assert rec.metric_value == -37.63
    assert rec.delta == -50.0


def test_gnn_dataset_empty_and_prehistoric_date(empty_store):
    gnn_ds = DualLayerGNNDataset(empty_store)
    snap = gnn_ds.build_snapshot(as_of_date="1900-01-01")
    assert snap["num_nodes"] == 0
    assert snap["num_edges"] == 0
    assert snap["X"].shape == (0, 11)
    assert snap["edge_index"].shape == (2, 0)
