"""
Unit and Integration Tests for DualLayerStore and graph_temporal schemas.
Verifies LanceDB persistence, graph topology querying, point-in-time historical tabular querying,
vector search, and edge cases.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

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
    parse_iso_or_date_to_utc,
    node_from_dict,
    edge_from_dict,
)
from src.storage.dual_layer_store import DualLayerStore


@pytest.fixture
def temp_store():
    """Provides a fresh DualLayerStore in a temporary directory."""
    tmp_dir = tempfile.mkdtemp()
    store = DualLayerStore(storage_dir=tmp_dir)
    yield store
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================================
# 1. SCHEMA TESTS
# ============================================================================

def test_node_models_initialization():
    # 768-dim embedding defaults to zero vector
    law = LawNode(id="LAW_CBAM", label="CBAM Regulation", celex_id="32023R0956")
    assert law.id == "LAW_CBAM"
    assert law.node_type == "LAW"
    assert len(law.vector) == 768
    assert law.metadata_dict.get("celex_id") == "32023R0956"

    act = ActionNode(id="ACT_BUY", label="Purchase Allowances", deadline="2025-06-30")
    assert act.id == "ACT_BUY"
    assert act.node_type == "ACTION"
    assert len(act.vector) == 768
    assert act.metadata_dict.get("deadline") == "2025-06-30"

    react = ReactionNode(id="REACT_SUBST", label="Material Substitution", elasticity=-0.45)
    assert react.id == "REACT_SUBST"
    assert react.node_type == "REACTION"
    assert react.metadata_dict.get("elasticity") == -0.45

    sector = SectorNode(id="SEC_STEEL", label="Steel Industry", nace="C24.10")
    assert sector.id == "SEC_STEEL"
    assert sector.node_type == "SECTOR"
    assert sector.metadata_dict.get("nace") == "C24.10"


def test_edge_models_initialization():
    e1 = TriggersEdge(source="ACT_BUY", target="REACT_SUBST", weight=0.75)
    assert e1.relationship_type == EdgeRelation.TRIGGERS.value
    assert e1.id == "ACT_BUY->TRIGGERS->REACT_SUBST"
    assert e1.weight == 0.75

    e2 = MandatesEdge(source_id="LAW_CBAM", target_id="ACT_BUY", weight=1.0)
    assert e2.relationship_type == EdgeRelation.MANDATES.value
    assert e2.source == "LAW_CBAM"
    assert e2.target == "ACT_BUY"

    e3 = ReactsToEdge(source="REACT_SUBST", target="ACT_BUY")
    assert e3.relationship_type == EdgeRelation.REACTS_TO.value

    e4 = AffectsCostOfEdge(source="ACT_BUY", target="SEC_STEEL", weight=1.5)
    assert e4.relationship_type == EdgeRelation.AFFECTS_COST_OF.value

    e5 = AmendsEdge(source="LAW_CBAM_V2", target="LAW_CBAM")
    assert e5.relationship_type == EdgeRelation.AMENDS.value


def test_tabular_node_history_timestamp_parsing():
    rec = TabularNodeHistory(
        node_id="ACT_BUY",
        effective_timestamp="2024-03-01T12:00:00Z",
        state_value="ENFORCED",
        metric_name="compliance_cost",
        metric_value=85.5,
        unit="EUR/t",
        source_url="https://eur-lex.europa.eu",
        event_log=[{"event": "enforcement_start", "phase": 1}],
    )
    assert rec.node_id == "ACT_BUY"
    assert rec.timestamp_utc == 1709294400
    assert rec.state_value == "ENFORCED"
    assert rec.metric_value == 85.5
    assert rec.unit == "EUR/t"
    assert rec.source_url == "https://eur-lex.europa.eu"
    assert isinstance(rec.event_log_parsed, list)
    assert rec.event_log_parsed[0]["event"] == "enforcement_start"


# ============================================================================
# 2. PERSISTENCE & CRUD TESTS
# ============================================================================

def test_lancedb_node_and_edge_persistence(temp_store):
    law = LawNode(id="LAW_1", label="EU ETS II", jurisdiction="EU")
    act = ActionNode(id="ACT_1", label="Surrender Allowances")
    edge = MandatesEdge(source="LAW_1", target="ACT_1", weight=1.0)

    temp_store.add_node(law)
    temp_store.add_node(act)
    temp_store.add_edge(edge)

    assert temp_store.count_nodes() == 2
    assert temp_store.count_edges() == 1

    # Reconnect new store instance to the same LanceDB directory
    reloaded = DualLayerStore(storage_dir=temp_store.storage_dir)
    assert reloaded.count_nodes() == 2
    assert reloaded.count_edges() == 1

    loaded_law = reloaded.get_node("LAW_1")
    assert loaded_law is not None
    assert isinstance(loaded_law, LawNode)
    assert loaded_law.label == "EU ETS II"

    loaded_edge = reloaded.get_edge("LAW_1", "ACT_1", "MANDATES")
    assert loaded_edge is not None
    assert loaded_edge.weight == 1.0


def test_node_upsert_and_deletion(temp_store):
    node = SectorNode(id="SEC_CEMENT", label="Cement Sector", emission_cap=100.0)
    temp_store.add_node(node)

    # Update with new attributes
    updated_node = SectorNode(id="SEC_CEMENT", label="Cement Sector Updated", emission_cap=80.0)
    temp_store.add_node(updated_node)

    retrieved = temp_store.get_node("SEC_CEMENT")
    assert retrieved.label == "Cement Sector Updated"
    assert retrieved.metadata_dict.get("emission_cap") == 80.0
    assert temp_store.count_nodes() == 1

    # Incident edge
    edge = AffectsCostOfEdge(source="ACT_1", target="SEC_CEMENT")
    temp_store.add_edge(edge)
    assert temp_store.count_edges() == 1

    # Cascade delete
    temp_store.delete_node("SEC_CEMENT", cascade_edges=True)
    assert temp_store.get_node("SEC_CEMENT") is None
    assert temp_store.count_nodes() == 0
    assert temp_store.count_edges() == 0


# ============================================================================
# 3. GRAPH TOPOLOGY & POWER-LAW METRICS TESTS
# ============================================================================

def test_topology_neighbors_and_subgraph(temp_store):
    law = LawNode(id="L1", label="Directive")
    a1 = ActionNode(id="A1", label="Action 1")
    a2 = ActionNode(id="A2", label="Action 2")
    r1 = ReactionNode(id="R1", label="Reaction 1")

    temp_store.add_nodes([law, a1, a2, r1])
    temp_store.add_edges([
        MandatesEdge(source="L1", target="A1"),
        MandatesEdge(source="L1", target="A2"),
        TriggersEdge(source="A1", target="R1"),
    ])

    # Neighbors
    out_l1 = temp_store.get_neighbors("L1", direction="out")
    assert sorted(out_l1) == ["A1", "A2"]

    in_r1 = temp_store.get_neighbors("R1", direction="in")
    assert in_r1 == ["A1"]

    # Subgraph
    subgraph = temp_store.get_subgraph("L1", depth=2)
    assert set(subgraph["node_ids"]) == {"L1", "A1", "A2", "R1"}
    assert len(subgraph["edges"]) == 3

    # Paths
    paths = temp_store.find_paths("L1", "R1")
    assert paths == [["L1", "A1", "R1"]]


def test_topology_power_law_metrics(temp_store):
    # Construct a star hub topology
    hub = LawNode(id="HUB_OMNIBUS", label="Omnibus Regulation")
    temp_store.add_node(hub)

    for i in range(12):
        spoke = ActionNode(id=f"SPOKE_{i}", label=f"Spoke {i}")
        temp_store.add_node(spoke)
        temp_store.add_edge(MandatesEdge(source="HUB_OMNIBUS", target=f"SPOKE_{i}"))

    metrics = temp_store.compute_topology_metrics()
    assert metrics["num_nodes"] == 13
    assert metrics["num_edges"] == 12
    assert metrics["gamma"] > 0
    assert metrics["top_out_degree"][0]["node_id"] == "HUB_OMNIBUS"
    assert metrics["top_out_degree"][0]["out_degree"] == 12


# ============================================================================
# 4. TEMPORAL TABULAR POINT-IN-TIME (AS-OF) QUERY TESTS
# ============================================================================

def test_query_state_at_point_in_time(temp_store):
    node_id = "COMMODITY_CARBON"

    # Insert timeline:
    # 2024-01-01: PROPOSED, cost = 50.0
    # 2024-06-01: ADOPTED, cost = 75.0
    # 2025-01-01: ENFORCED, cost = 95.0
    temp_store.add_history_records([
        TabularNodeHistory(
            node_id=node_id,
            effective_timestamp="2024-01-01T00:00:00Z",
            state_value="PROPOSED",
            metric_name="eua_price",
            metric_value=50.0,
            unit="EUR/t",
            source_url="https://eex.com",
            event_log="Initial proposal"
        ),
        TabularNodeHistory(
            node_id=node_id,
            effective_timestamp="2024-06-01T00:00:00Z",
            state_value="ADOPTED",
            metric_name="eua_price",
            metric_value=75.0,
            unit="EUR/t",
            source_url="https://eex.com",
            event_log="Parliament vote"
        ),
        TabularNodeHistory(
            node_id=node_id,
            effective_timestamp="2025-01-01T00:00:00Z",
            state_value="ENFORCED",
            metric_name="eua_price",
            metric_value=95.0,
            unit="EUR/t",
            source_url="https://eex.com",
            event_log="Full compliance mandatory"
        ),
    ])

    # 1. Query before first recorded timestamp -> None
    s_prior = temp_store.query_state_at(timestamp="2023-12-01T00:00:00Z", node_id=node_id)
    assert s_prior is None

    # 2. Query between Jan 1 and June 1 (e.g. 2024-03-15) -> PROPOSED (50.0)
    s_spring = temp_store.query_state_at(timestamp="2024-03-15", node_id=node_id)
    assert s_spring is not None
    assert s_spring.state_value == "PROPOSED"
    assert s_spring.metric_value == 50.0

    # 3. Query between June 1 and Jan 1, 2025 (e.g. 2024-08-01) -> ADOPTED (75.0)
    s_summer = temp_store.query_state_at(timestamp="2024-08-01T12:00:00Z", node_id=node_id)
    assert s_summer is not None
    assert s_summer.state_value == "ADOPTED"
    assert s_summer.metric_value == 75.0

    # 4. Query after Jan 1, 2025 (e.g. 2025-06-01) -> ENFORCED (95.0)
    s_future = temp_store.query_state_at(timestamp="2025-06-01", node_id=node_id)
    assert s_future is not None
    assert s_future.state_value == "ENFORCED"
    assert s_future.metric_value == 95.0


def test_query_state_at_all_nodes(temp_store):
    # Multiple nodes with different timestamps
    temp_store.add_history_records([
        TabularNodeHistory(
            node_id="N1", effective_timestamp="2024-01-01", state_value="S1_A",
            metric_name="m1", metric_value=10.0, unit="INDEX", source_url="u1"
        ),
        TabularNodeHistory(
            node_id="N1", effective_timestamp="2024-07-01", state_value="S1_B",
            metric_name="m1", metric_value=20.0, unit="INDEX", source_url="u1"
        ),
        TabularNodeHistory(
            node_id="N2", effective_timestamp="2024-04-01", state_value="S2_A",
            metric_name="m2", metric_value=100.0, unit="EUR", source_url="u2"
        ),
    ])

    # As of 2024-05-01:
    # N1 should be S1_A (from 2024-01-01)
    # N2 should be S2_A (from 2024-04-01)
    as_of_may = temp_store.query_state_at(timestamp="2024-05-01")
    assert isinstance(as_of_may, dict)
    assert "N1" in as_of_may
    assert "N2" in as_of_may
    assert as_of_may["N1"].state_value == "S1_A"
    assert as_of_may["N2"].state_value == "S2_A"

    # As of 2024-08-01:
    # N1 should now be S1_B
    as_of_aug = temp_store.query_state_at(timestamp="2024-08-01")
    assert as_of_aug["N1"].state_value == "S1_B"
    assert as_of_aug["N2"].state_value == "S2_A"


def test_query_history_range_and_transitions(temp_store):
    node_id = "TRANS_NODE"
    temp_store.add_history_records([
        TabularNodeHistory(node_id=node_id, effective_timestamp="2024-01-01", state_value="STATE_1", metric_name="cost", metric_value=10.0, unit="EUR"),
        TabularNodeHistory(node_id=node_id, effective_timestamp="2024-02-01", state_value="STATE_1", metric_name="cost", metric_value=15.0, unit="EUR"),
        TabularNodeHistory(node_id=node_id, effective_timestamp="2024-03-01", state_value="STATE_2", metric_name="cost", metric_value=30.0, unit="EUR"),
        TabularNodeHistory(node_id=node_id, effective_timestamp="2024-04-01", state_value="STATE_3", metric_name="cost", metric_value=60.0, unit="EUR"),
    ])

    # Range query
    range_records = temp_store.query_history_range(
        node_id=node_id,
        start_time="2024-01-15",
        end_time="2024-03-15"
    )
    assert len(range_records) == 2
    assert [r.effective_timestamp for r in range_records] == ["2024-02-01", "2024-03-01"]

    # State transitions
    transitions = temp_store.query_state_transitions(node_id=node_id)
    assert len(transitions) == 3
    assert transitions[0]["to_state"] == "STATE_1"
    assert transitions[1]["to_state"] == "STATE_2"
    assert transitions[2]["to_state"] == "STATE_3"


# ============================================================================
# 5. VECTOR SIMILARITY SEARCH TESTS
# ============================================================================

def test_vector_search(temp_store):
    v1 = [1.0] + [0.0] * 767
    v2 = [0.0, 1.0] + [0.0] * 766
    v3 = [0.0, 0.0, 1.0] + [0.0] * 765

    n1 = LawNode(id="LAW_EMBED_1", label="First Law", vector=v1)
    n2 = ActionNode(id="ACT_EMBED_2", label="Second Action", vector=v2)
    n3 = SectorNode(id="SEC_EMBED_3", label="Third Sector", vector=v3)

    temp_store.add_nodes([n1, n2, n3])

    # Search closest to v1
    results = temp_store.search_nodes_by_vector(query_vector=v1, limit=1)
    assert len(results) == 1
    top_node, dist = results[0]
    assert top_node.id == "LAW_EMBED_1"
    assert dist >= 0.0

    # Search with node_type filter
    filtered = temp_store.search_nodes_by_vector(query_vector=v1, limit=5, node_type="ACTION")
    assert len(filtered) == 1
    assert filtered[0][0].id == "ACT_EMBED_2"


# ============================================================================
# 6. INTEGRATED DUAL-LAYER SNAPSHOTS & GNN EXPORT
# ============================================================================

def test_node_snapshot_and_gnn_export(temp_store):
    law = LawNode(id="LAW_EU", label="Renewable Directive")
    act = ActionNode(id="ACT_CAPEX", label="Capex Deployment")
    edge = MandatesEdge(source="LAW_EU", target="ACT_CAPEX", weight=2.5)

    temp_store.add_nodes([law, act])
    temp_store.add_edge(edge)

    temp_store.add_history_record(
        TabularNodeHistory(
            node_id="ACT_CAPEX",
            effective_timestamp="2024-05-01",
            state_value="FUNDED",
            metric_name="budget_allocated",
            metric_value=50000000.0,
            unit="EUR",
        )
    )

    # Snapshot
    snap = temp_store.get_node_snapshot("ACT_CAPEX", as_of="2024-06-01")
    assert snap is not None
    assert snap["node"]["id"] == "ACT_CAPEX"
    assert snap["temporal_state"]["state_value"] == "FUNDED"
    assert len(snap["in_edges"]) == 1
    assert snap["in_edges"][0]["source"] == "LAW_EU"

    # GNN Export
    gnn_data = temp_store.export_gnn_dataset(as_of="2024-06-01")
    assert gnn_data["num_nodes"] == 2
    assert gnn_data["num_edges"] == 1
    assert len(gnn_data["X"]) == 2
    assert len(gnn_data["edge_index"]) == 2
    assert len(gnn_data["edge_weights"]) == 1
    assert gnn_data["edge_weights"][0] == 2.5


# ============================================================================
# 7. EMPTY STORE EDGE CASES
# ============================================================================

def test_empty_store_edge_cases(temp_store):
    assert temp_store.count_nodes() == 0
    assert temp_store.count_edges() == 0
    assert temp_store.count_history() == 0

    assert temp_store.get_node("NON_EXISTENT") is None
    assert temp_store.get_edge("A", "B") is None
    assert temp_store.get_neighbors("NON_EXISTENT") == []
    assert temp_store.query_state_at("2025-01-01", node_id="NON_EXISTENT") is None
    assert temp_store.query_state_at("2025-01-01") == {}
    assert temp_store.search_nodes_by_vector([0.0] * 768) == []

    metrics = temp_store.compute_topology_metrics()
    assert metrics["num_nodes"] == 0
    assert metrics["num_edges"] == 0

    gnn_empty = temp_store.export_gnn_dataset()
    assert gnn_empty["num_nodes"] == 0
    assert gnn_empty["num_edges"] == 0


def test_cyclic_graph_and_multiple_timestamps(temp_store):
    # Cyclic graph: A -> B -> C -> A
    a = ActionNode(id="CYC_A", label="Node A")
    b = ActionNode(id="CYC_B", label="Node B")
    c = ActionNode(id="CYC_C", label="Node C")
    temp_store.add_nodes([a, b, c])
    temp_store.add_edges([
        TriggersEdge(source="CYC_A", target="CYC_B"),
        TriggersEdge(source="CYC_B", target="CYC_C"),
        TriggersEdge(source="CYC_C", target="CYC_A"),
    ])

    sub = temp_store.get_subgraph("CYC_A", depth=4)
    assert set(sub["node_ids"]) == {"CYC_A", "CYC_B", "CYC_C"}
    assert len(sub["edges"]) == 3

    # Multiple records for same node at exact same timestamp
    temp_store.add_history_records([
        TabularNodeHistory(node_id="CYC_A", effective_timestamp="2024-01-01T00:00:00Z", state_value="INIT", metric_name="m", metric_value=1.0, unit="u"),
        TabularNodeHistory(node_id="CYC_A", effective_timestamp="2024-01-01T00:00:00Z", state_value="FINAL", metric_name="m", metric_value=2.0, unit="u"),
    ])
    # Both single-node and all-nodes queries should return the latest record (2.0) consistently
    st_single = temp_store.query_state_at("2024-01-01T00:00:00Z", node_id="CYC_A")
    st_all = temp_store.query_state_at("2024-01-01T00:00:00Z")
    assert st_single is not None
    assert st_single.metric_value == 2.0
    assert st_all["CYC_A"].metric_value == 2.0


# ============================================================================
# 8. REGRESSION & ROBUSTNESS TESTS (BUGS FIXED)
# ============================================================================

def test_schema_serialization_roundtrip_no_pollution():
    """Verify that serialization to dict and deserialization does not pollute metadata."""
    # Node roundtrip
    node = LawNode(id="LAW_RT", label="Roundtrip Law", metadata={"custom_key": 42})
    d_node = node.to_dict()
    assert "metadata_json" in d_node
    reloaded_node = node_from_dict(d_node)
    assert reloaded_node.metadata_dict == {"custom_key": 42}
    d_node2 = reloaded_node.to_dict()
    reloaded_node2 = node_from_dict(d_node2)
    assert reloaded_node2.metadata_dict == {"custom_key": 42}

    # Edge roundtrip
    edge = MandatesEdge(source="LAW_RT", target="ACT_1", weight=1.5, metadata={"strictness": "HIGH"})
    d_edge = edge.to_dict()
    reloaded_edge = edge_from_dict(d_edge)
    assert reloaded_edge.metadata_dict == {"strictness": "HIGH"}
    assert reloaded_edge.weight == 1.5
    assert reloaded_edge.weights == 1.5

    # History roundtrip
    hist = TabularNodeHistory(
        node_id="LAW_RT",
        effective_timestamp="2024-01-01",
        state_value="ENFORCED",
        metric_name="compliance",
        metric_value=99.0,
        metadata={"audited": True}
    )
    d_hist = hist.to_dict()
    reloaded_hist = TabularNodeHistory(**d_hist)
    assert reloaded_hist.metadata_dict == {"audited": True}


def test_robust_date_parsing_and_fallback():
    """Verify parse_iso_or_date_to_utc handles non-standard date formats and invalid strings."""
    # Slashes format
    assert parse_iso_or_date_to_utc("2024/01/01") == 1704067200
    # ISO formats
    assert parse_iso_or_date_to_utc("2024-01-01T00:00:00Z") == 1704067200
    assert parse_iso_or_date_to_utc("2024-01-01") == 1704067200
    # European / custom formats via fallback
    assert parse_iso_or_date_to_utc("01-01-2024") > 0
    # Malformed strings should safely return 0 without raising ValueError
    assert parse_iso_or_date_to_utc("invalid-date-string") == 0
    assert parse_iso_or_date_to_utc("") == 0
    assert parse_iso_or_date_to_utc(None) == 0

    # Model instantiation with invalid date should not crash
    h = TabularNodeHistory(node_id="N_BAD_DATE", effective_timestamp="not-a-date")
    assert h.timestamp_utc == 0


def test_null_and_nan_sanitization():
    """Verify that None and NaN inputs from DataFrames are sanitized cleanly."""
    import numpy as np
    # None and NaN description/jurisdiction
    n1 = LawNode(id="L_NULL", label="Null Label", description=None, jurisdiction=None)
    assert n1.description == ""
    assert n1.jurisdiction == "EU"

    n2 = ActionNode(id="A_NAN", label="NaN Label", description=np.nan)
    assert n2.description == ""

    # None and NaN in edge
    e = TriggersEdge(source="L_NULL", target="A_NAN", weight=None, timestamp=None)
    assert e.weight == 1.0
    assert e.timestamp == ""

    # None and NaN in tabular history
    h = TabularNodeHistory(
        node_id="L_NULL",
        effective_timestamp="2024-01-01",
        metric_value=np.nan,
        unit=None,
        source_url=None
    )
    assert h.metric_value == 0.0
    assert h.unit == "INDEX"
    assert h.source_url == ""


def test_parquet_save_and_reload(temp_store):
    """Verify store.save() writes clean Parquet files without Arrow struct errors."""
    temp_store.add_node(LawNode(id="L_PARQUET", label="Parquet Law"))
    temp_store.add_node(ActionNode(id="A_PARQUET", label="Parquet Action"))
    temp_store.add_edge(MandatesEdge(source="L_PARQUET", target="A_PARQUET"))
    temp_store.add_history_record(
        TabularNodeHistory(
            node_id="L_PARQUET",
            effective_timestamp="2024-01-01",
            state_value="ACTIVE",
            metric_name="compliance",
            metric_value=10.0,
            event_log=[{"event": "init"}]
        )
    )

    # Calling save() must not crash with ArrowNotImplementedError
    temp_store.save()

    nodes_pq = temp_store.storage_dir / "graph_nodes.parquet"
    edges_pq = temp_store.storage_dir / "graph_edges.parquet"
    hist_pq = temp_store.storage_dir / "node_tabular_history.parquet"

    assert nodes_pq.exists()
    assert edges_pq.exists()
    assert hist_pq.exists()

    # Verify reloading from storage
    temp_store.load()
    assert temp_store.count_nodes() == 2
    assert temp_store.count_edges() == 1
    assert temp_store.count_history() == 1


def test_batch_insert_deduplication(temp_store):
    """Verify add_nodes and add_edges deduplicate within batch to prevent LanceDB duplicate rows."""
    batch_nodes = [
        LawNode(id="DUP_1", label="Version 1"),
        LawNode(id="DUP_1", label="Version 2"),
    ]
    temp_store.add_nodes(batch_nodes)
    assert temp_store.count_nodes() == 1
    assert temp_store._nodes_tbl.count_rows() == 1
    assert temp_store.get_node("DUP_1").label == "Version 2"

    batch_edges = [
        TriggersEdge(source="DUP_1", target="T1", weight=1.0),
        TriggersEdge(source="DUP_1", target="T1", weight=2.0),
    ]
    temp_store.add_edges(batch_edges)
    assert temp_store.count_edges() == 1
    assert temp_store._edges_tbl.count_rows() == 1
    assert temp_store.get_edge("DUP_1", "T1").weight == 2.0


def test_lancedb_fallback_when_cache_cleared(temp_store):
    """Verify get_node, get_edge, and query_state_at fallback to LanceDB if memory cache is purged."""
    temp_store.add_node(LawNode(id="PERSIST_L", label="Persisted Law"))
    edge = temp_store.add_edge(MandatesEdge(source="PERSIST_L", target="PERSIST_A"))
    temp_store.add_history_record(
        TabularNodeHistory(
            node_id="PERSIST_L",
            effective_timestamp="2024-01-01",
            state_value="ACTIVE",
            metric_name="cost",
            metric_value=50.0
        )
    )

    # Clear in-memory caches to simulate fresh connection or separate process
    temp_store._nodes.clear()
    temp_store._edges.clear()
    temp_store._history.clear()

    # get_node fallback
    node = temp_store.get_node("PERSIST_L")
    assert node is not None
    assert node.label == "Persisted Law"

    # get_edge fallback
    found_edge = temp_store.get_edge("PERSIST_L", "PERSIST_A")
    assert found_edge is not None
    assert found_edge.relationship_type == "MANDATES"

    # query_state_at fallback
    state = temp_store.query_state_at("2024-06-01", node_id="PERSIST_L")
    assert state is not None
    assert state.metric_value == 50.0


def test_delete_history_and_cascade(temp_store):
    """Verify delete_history and delete_node cascade_history option."""
    temp_store.add_node(LawNode(id="DEL_NODE", label="To Delete"))
    temp_store.add_history_records([
        TabularNodeHistory(node_id="DEL_NODE", effective_timestamp="2024-01-01", state_value="S1", metric_name="m"),
        TabularNodeHistory(node_id="DEL_NODE", effective_timestamp="2024-02-01", state_value="S2", metric_name="m"),
        TabularNodeHistory(node_id="OTHER_NODE", effective_timestamp="2024-01-01", state_value="S1", metric_name="m"),
    ])
    assert temp_store.count_history() == 3

    # Delete only DEL_NODE history
    deleted_count = temp_store.delete_history("DEL_NODE")
    assert deleted_count == 2
    assert temp_store.count_history() == 1
    assert temp_store.query_state_at("2024-03-01", node_id="DEL_NODE") is None

    # Test cascade_history in delete_node
    temp_store.add_node(LawNode(id="CASCADE_NODE", label="Cascade"))
    temp_store.add_history_record(TabularNodeHistory(node_id="CASCADE_NODE", effective_timestamp="2024-01-01", state_value="S", metric_name="m"))
    temp_store.delete_node("CASCADE_NODE", cascade_history=True)
    assert temp_store.get_node("CASCADE_NODE") is None
    assert temp_store.query_state_at("2024-03-01", node_id="CASCADE_NODE") is None


def test_commodity_and_economic_index_nodes(temp_store):
    """Verify CommodityNode and EconomicIndexNode types and properties."""
    com = CommodityNode(id="COM_EUA", label="Carbon EUA Permit")
    assert com.node_type == "COMMODITY"
    assert len(com.vector) == 768

    idx = EconomicIndexNode(id="IDX_CPI", label="Consumer Price Index")
    assert idx.node_type == "ECONOMIC_INDEX"
    assert len(idx.vector) == 768

    temp_store.add_nodes([com, idx])
    assert temp_store.count_nodes() == 2
    assert len(temp_store.get_nodes("COMMODITY")) == 1
    assert len(temp_store.get_nodes("ECONOMIC_INDEX")) == 1


def test_vector_dimension_validation(temp_store):
    """Verify search_nodes_by_vector validates vector length."""
    with pytest.raises(ValueError, match="768 dimensions"):
        temp_store.search_nodes_by_vector([1.0, 2.0, 3.0])


def test_concurrent_store_initialization():
    """Verify concurrent initializations against the same directory do not race on table creation."""
    import concurrent.futures
    tmp_dir = tempfile.mkdtemp()
    try:
        def init_store(_):
            s = DualLayerStore(storage_dir=tmp_dir)
            return s.count_nodes()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            counts = list(ex.map(init_store, range(4)))

        assert counts == [0, 0, 0, 0]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


