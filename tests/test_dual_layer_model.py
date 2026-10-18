"""
Unit tests for Dual-Layer Data Models.
Tests GraphNode, GraphEdge, NodeStateRecord schemas and field validations.
"""

import pytest
from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)


def test_graph_node_creation():
    node = GraphNode(
        id="LAW_CBAM_001",
        label="EU CBAM",
        node_type=NodeType.LAW,
        description="Carbon Border Adjustment Mechanism",
        jurisdiction="EU",
        activation_date="2023-05-10",
        metadata={"celex_id": "32023R0956"}
    )
    assert node.id == "LAW_CBAM_001"
    assert node.node_type == NodeType.LAW
    d = node.to_dict()
    assert d["id"] == "LAW_CBAM_001"
    assert d["node_type"] == "LAW"
    assert "32023R0956" in d["metadata_json"]


def test_graph_edge_creation():
    edge = GraphEdge(
        source_id="LAW_CBAM_001",
        target_id="ACT_REPORTING_001",
        relation_type=EdgeRelation.MANDATES,
        weight=1.0,
        timestamp="2023-10-01",
        properties={"strictness": "HARD"}
    )
    assert edge.source_id == "LAW_CBAM_001"
    assert edge.target_id == "ACT_REPORTING_001"
    assert edge.relation_type == EdgeRelation.MANDATES
    d = edge.to_dict()
    assert d["relation_type"] == "MANDATES"
    assert d["weight"] == 1.0


def test_node_state_record_creation_and_timestamp():
    record = NodeStateRecord(
        node_id="CO2_ALLOWANCE",
        node_type=NodeType.COMMODITY,
        timestamp="2024-03-15",
        state_value="ACTIVE_TRADING",
        metric_name="eua_price",
        metric_value=62.50,
        delta=-1.20,
        unit="EUR/tCO2",
        source_id="EEX_SPOT",
        source_type="MARKET_INDEX",
        confidence=0.98
    )
    assert record.node_id == "CO2_ALLOWANCE"
    assert record.metric_value == 62.50
    assert record.timestamp_utc > 0  # Timestamp UTC auto-parsed
    d = record.to_dict()
    assert d["metric_value"] == 62.50
    assert d["delta"] == -1.20
