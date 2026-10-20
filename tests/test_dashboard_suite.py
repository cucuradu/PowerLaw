from tests.sample_data import get_fixture_graph_store
"""
Automated Verification & Test Suite for PowerLaw Dashboard
Tests all pages in dashboard.py and pages/ for clean rendering and execution.
"""

import os
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from src.storage.dashboard_data import (
    get_graph_store,
    get_system_kpis,
    get_all_laws_data,
    get_all_news_data,
    _CACHED_GRAPH_STORES
)
import src.config

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def setup_demo_environment(tmp_path, monkeypatch):
    """Isolate dashboard tests to explicit temporary demo storage with demo seeds."""
    demo_db = tmp_path / "demo_lancedb"
    demo_db.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POWERLAW_MODE", "demo")
    monkeypatch.setenv("LANCEDB_PATH", str(demo_db))
    monkeypatch.setenv("POWERLAW_DATA_DIR", str(tmp_path))
    src.config.clear_settings_cache()
    _CACHED_GRAPH_STORES.clear()
    get_fixture_graph_store(storage_dir=str(demo_db))
    yield
    _CACHED_GRAPH_STORES.clear()
    src.config.clear_settings_cache()


def test_dashboard_data_provider():
    """Verify the dashboard data provider loads valid KPIs, laws, and news."""
    store = get_graph_store()
    assert store is not None
    assert len(store.get_nodes()) > 0
    assert len(store.get_edges()) > 0
    assert len(store.get_history()) > 0

    kpis = get_system_kpis()
    assert "total_laws" in kpis
    assert "total_actions" in kpis
    assert "total_reactions" in kpis
    assert "total_edges" in kpis
    assert "total_temporal_entries" in kpis
    assert "gamma" in kpis
    assert kpis["total_laws"] > 0
    assert kpis["total_edges"] > 0
    assert kpis["total_temporal_entries"] > 0

    laws = get_all_laws_data()
    assert len(laws) > 0
    for l in laws:
        assert "law_id" in l
        assert "summary" in l
        assert "rule" in l

    news = get_all_news_data()
    assert len(news) > 0
    for n in news:
        assert "title" in n
        assert "receptor_signal" in n
        assert "target_asset" in n


def test_render_dashboard_main():
    """Test dashboard.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "dashboard.py")).run(timeout=15)
    assert not at.exception, f"dashboard.py raised exception: {at.exception}"
    assert len(at.title) > 0 or len(at.markdown) > 0


def test_render_page_1_law_registry():
    """Test pages/1_📜_Law_Registry.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "1_📜_Law_Registry.py")).run(timeout=15)
    assert not at.exception, f"pages/1_📜_Law_Registry.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_render_page_2_news_feed():
    """Test pages/2_📰_News_Feed.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "2_📰_News_Feed.py")).run(timeout=15)
    assert not at.exception, f"pages/2_📰_News_Feed.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_render_page_3_graph_explorer():
    """Test pages/3_🕸️_Graph_Explorer.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "3_🕸️_Graph_Explorer.py")).run(timeout=15)
    assert not at.exception, f"pages/3_🕸️_Graph_Explorer.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_render_page_4_temporal_table():
    """Test pages/4_⏱️_Temporal_Table.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "4_⏱️_Temporal_Table.py")).run(timeout=15)
    assert not at.exception, f"pages/4_⏱️_Temporal_Table.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_render_page_5_gnn_modeling_lab():
    """Test pages/5_🤖_GNN_Modeling_Lab.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "5_🤖_GNN_Modeling_Lab.py")).run(timeout=25)
    assert not at.exception, f"pages/5_🤖_GNN_Modeling_Lab.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_render_page_6_ingestion_console():
    """Test pages/6_⚙️_Ingestion_Console.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "6_⚙️_Ingestion_Console.py")).run(timeout=15)
    assert not at.exception, f"pages/6_⚙️_Ingestion_Console.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_graph_power_law_metrics():
    """Test scale-free power law degree analytics computation."""
    store = get_graph_store()
    metrics = store.compute_power_law_metrics()
    assert metrics["num_nodes"] > 0
    assert metrics["num_edges"] > 0
    assert metrics["gamma"] > 0.0
    assert "degree_distribution" in metrics
    assert "top_in_degree" in metrics
    assert "top_out_degree" in metrics


def test_gnn_tensor_export():
    """Test export of GNN feature matrix and edge index."""
    store = get_graph_store()
    gnn_data = store.export_gnn_dataset()
    assert gnn_data["num_nodes"] > 0
    assert gnn_data["num_edges"] > 0
    assert gnn_data["feature_dim"] == 11
    assert len(gnn_data["X"]) == gnn_data["num_nodes"]
    assert len(gnn_data["edge_index"]) == 2
    assert len(gnn_data["edge_index"][0]) == gnn_data["num_edges"]


def test_edge_case_law_registry_search_nonexistent():
    """Test Law Registry behavior when search matches zero laws."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "1_📜_Law_Registry.py"))
    at.run()
    if at.sidebar.text_input:
        at.sidebar.text_input[0].input("NONEXISTENT_REGULATION_QUERY_12345").run()
    assert not at.exception, f"Law registry threw on nonexistent search: {at.exception}"


def test_edge_case_graph_explorer_filtering():
    """Test Graph Explorer with single node type filter."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "3_🕸️_Graph_Explorer.py"))
    at.run()
    if at.sidebar.multiselect:
        # Select only LAW nodes
        at.sidebar.multiselect[0].select("LAW").run()
    assert not at.exception, f"Graph explorer threw on filter: {at.exception}"


def test_edge_case_temporal_table_slider():
    """Test Temporal Table with earliest date slider selection."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "4_⏱️_Temporal_Table.py"))
    at.run()
    if at.select_slider:
        at.select_slider[0].set_value("2022-01-15").run()
    assert not at.exception, f"Temporal table threw on date change: {at.exception}"


def test_temporal_gnn_export_as_of():
    """Verify export_gnn_dataset properly applies as_of temporal filtering."""
    store = get_graph_store()
    full_data = store.export_gnn_dataset()
    early_data = store.export_gnn_dataset(as_of="2022-01-15")

    assert full_data["num_nodes"] == early_data["num_nodes"]
    # Feature 10 is history_records_count; earlier timestamp has fewer or equal history records
    full_counts = [row[10] for row in full_data["X"]]
    early_counts = [row[10] for row in early_data["X"]]
    assert sum(early_counts) <= sum(full_counts)


def test_dashboard_data_cache_isolation(tmp_path):
    """Verify get_graph_store isolates instances by storage directory."""
    dir1 = str(tmp_path / "db1")
    dir2 = str(tmp_path / "db2")

    store1 = get_graph_store(storage_dir=dir1)
    store2 = get_graph_store(storage_dir=dir2)

    assert store1 is not store2
    assert store1.storage_dir != store2.storage_dir


def test_news_feed_null_delta_resilience(monkeypatch):
    """Verify News Feed renders cleanly without TypeError when delta_value is None."""
    import src.storage.dashboard_data as dd
    
    mock_news = [
        {
            "news_id": "TEST_NULL_DELTA_1",
            "title": "EU Considers New Sanctions Package",
            "published_date": "2024-05-01",
            "source_name": "Reuters",
            "source_url": "https://example.com/news1",
            "summary": "EU officials discussed additional sanctions on industrial imports.",
            "receptor_signal": "SANCTIONS_EXPANSION",
            "target_asset": "COMMODITY_METALS",
            "metric": "COMPLIANCE_RESTRICTION",
            "direction": "INCREASE",
            "delta_value": None,
            "delta_unit": None,
            "timeframe": "IMMEDIATE",
            "actions": [],
            "reactions": []
        }
    ]
    monkeypatch.setattr(dd, "get_all_news_data", lambda: mock_news)
    
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "2_📰_News_Feed.py")).run()
    assert not at.exception, f"News Feed threw on null delta_value: {at.exception}"


def test_law_registry_null_rule_resilience(monkeypatch):
    """Verify Law Registry handles regulations with null rule and scope safely."""
    import src.storage.dashboard_data as dd

    mock_laws = [
        {
            "law_id": "99999R0000",
            "title": "Draft Law with Unparsed Rule",
            "chunk_id": "ART_1",
            "rule_type": "REGULATORY",
            "jurisdiction": "EU",
            "valid_from": "2024-01-01",
            "valid_to": "",
            "summary": "Law without parsed logic gates.",
            "recitals": "",
            "source_url": "",
            "rule": None
        }
    ]
    monkeypatch.setattr(dd, "get_all_laws_data", lambda: mock_laws)

    at = AppTest.from_file(str(REPO_ROOT / "pages" / "1_📜_Law_Registry.py")).run()
    assert not at.exception, f"Law Registry threw on null rule: {at.exception}"


def test_graph_explorer_ego_network_mode():
    """Verify Graph Explorer ego-network radio option runs without throwing."""
    at = AppTest.from_file(str(REPO_ROOT / "pages" / "3_🕸️_Graph_Explorer.py"))
    at.run()
    if at.sidebar.radio:
        # Select Ego-Network (k-Hop Neighborhood)
        at.sidebar.radio[0].set_value("Ego-Network (k-Hop Neighborhood)").run()
    assert not at.exception, f"Graph Explorer threw on ego-network mode: {at.exception}"

