from tests.sample_data import get_fixture_graph_store
"""
Automated Verification & Test Suite for PowerLaw Dashboard
Tests all pages in dashboard.py and pages/ for clean rendering and execution.
"""

import os
import ast
from pathlib import Path
from types import SimpleNamespace
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
    """The app entry (navigation) and the overview page render cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "dashboard.py")).run(timeout=15)
    assert not at.exception, f"dashboard.py raised exception: {at.exception}"
    at = AppTest.from_file(str(REPO_ROOT / "overview.py")).run(timeout=15)
    assert not at.exception, f"overview.py raised exception: {at.exception}"
    assert len(at.title) > 0


def test_overview_reads_reports_through_a_cache():
    """The overview reloads report data only when a report file changes, and never opens the store."""
    source = (REPO_ROOT / "overview.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_reports")
    assert any(ast.unparse(d).startswith("st.cache_data") for d in loader.decorator_list)
    assert "get_graph_store" not in source and "DualLayerStore" not in source














def test_render_ingestion_page():
    """Test views/ingestion.py renders cleanly without exceptions."""
    at = AppTest.from_file(str(REPO_ROOT / "views" / "ingestion.py")).run(timeout=15)
    assert not at.exception, f"views/ingestion.py raised exception: {at.exception}"
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


def test_production_readers_reuse_graph_store(monkeypatch, tmp_path):
    """Production law/news reads must not construct separate canonical stores."""
    import src.storage.dashboard_data as dd

    canonical_store = object()
    graph_store = SimpleNamespace(canonical_store=canonical_store)
    settings = SimpleNamespace(mode="production", lancedb_path=tmp_path, data_dir=tmp_path)
    seen = []

    monkeypatch.setattr(dd, "get_settings", lambda: settings)
    monkeypatch.setattr(dd, "get_graph_store", lambda storage_dir=None: graph_store)
    monkeypatch.setattr(dd, "_canonical_law_rows", lambda store, cutoff: seen.append(store) or [])
    monkeypatch.setattr(dd, "_canonical_news_rows", lambda store, cutoff: seen.append(store) or [])

    assert dd.get_all_laws_data() == []
    assert dd.get_all_news_data() == []
    assert seen == [canonical_store, canonical_store]











def test_laws_and_graph_pages_render_with_and_without_a_snapshot(tmp_path):
    """Without a snapshot the graph page explains how to make one; with one, both pages render a law and the map."""
    import json
    for page in ("graph.py", "laws.py"):
        at = AppTest.from_file(str(REPO_ROOT / "views" / page)).run(timeout=30)
        assert not at.exception, f"{page} raised {at.exception}"
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    snapshot = {"generated_at": "2026-10-23", "years": [2022], "chains": [{"id": "c", "label": "Chain", "why": "why"}],
                "inventory": {"nodes": 3, "relations": 2, "documents": 1, "series": 0, "cross_border_flows_not_drawn": 0},
                "zones": {z: {"label": z, "color": "#2563EB", "about": z} for z in ("laws", "construction", "economy", "materials", "data")},
                "edge_types": {t: {"label": t, "color": "#16A34A", "dashes": False, "about": t} for t in
                               ("contains", "implements", "amends", "regulates", "supplies", "buys", "part_of", "classified_in",
                                "sets", "measures", "tested_link", "chain")},
                "nodes": [{"id": "law_de_geg", "zone": "laws", "kind": "law", "label": "GEG", "description": "d", "facts": {}, "degree": 1},
                          {"id": "sec_F", "zone": "construction", "kind": "sector", "label": "Construction", "description": "", "facts": {}, "degree": 2},
                          {"id": "sec_C23", "zone": "economy", "kind": "sector", "label": "Minerals", "description": "", "facts": {}, "degree": 1}],
                "edges": [{"id": "e0", "source": "law_de_geg", "target": "sec_F", "type": "regulates"},
                          {"id": "e1", "source": "sec_C23", "target": "sec_F", "type": "supplies", "weight": 0.07, "year": 2022}]}
    (reports / "graph-view-latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    graph = AppTest.from_file(str(REPO_ROOT / "views" / "graph.py")).run(timeout=30)
    assert not graph.exception, f"graph.py raised {graph.exception}"
    laws = AppTest.from_file(str(REPO_ROOT / "views" / "laws.py"))
    laws.query_params["law"] = "law_de_geg"
    laws.run(timeout=30)
    assert not laws.exception, f"laws.py raised {laws.exception}"
    assert any("GEG" in h.value for h in laws.subheader)
