"""Missing source data must remain missing in every application mode."""

import pandas as pd
import pytest
import json

from src.config import clear_settings_cache
from src.quant.fragility import compute_systemic_fragility
from src.quant.macro_fred import MacroFREDEngine
from src.quant.regime_hmm import ConstructionRegimeHMM
from src.quant.volatility_garch import GARCHVolatilityEngine
from src.scrapers.news_scraper import EconomicNewsScraper
from src.storage.dashboard_data import get_graph_store, get_system_kpis, get_all_news_data


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LANCEDB_PATH", str(tmp_path / "lancedb"))
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.parametrize("mode", ["production", "demo", "test"])
def test_empty_dashboard_never_creates_sample_records(mode, monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", mode)
    store = get_graph_store()
    assert store.get_nodes() == []
    assert store.get_history() == []
    kpis = get_system_kpis()
    assert kpis["min_date"] is None
    assert kpis["max_date"] is None


@pytest.mark.parametrize("mode", ["production", "demo", "test"])
def test_missing_macro_inputs_never_become_baseline_values(mode, monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", mode)
    engine = MacroFREDEngine()
    dates = pd.date_range("2025-01-01", periods=60)
    series = pd.Series(range(60), index=dates, dtype=float)
    monkeypatch.setattr(engine, "fetch_fred_series", lambda code: None if code == "NFCI" else series)
    result = engine.compute_macro_risk()
    assert result["status"] == "unavailable"
    assert result["macro_risk"] is None
    assert result["nfci_index"] is None
    store = get_graph_store()
    assert engine.ingest_to_store(store)["status"] == "unavailable"
    assert store.get_nodes() == []


def test_empty_history_has_no_invented_quant_forecasts():
    store = get_graph_store()
    fragility = compute_systemic_fragility(store)
    assert fragility["status"] == "unavailable"
    assert fragility["absorption_ratio"] is None
    regime = ConstructionRegimeHMM().fit_predict(pd.DataFrame())
    assert regime["current_regime"] is None
    assert regime["confidence"] is None
    assert regime["probabilities"] == {}
    volatility = GARCHVolatilityEngine().forecast_asset_volatility(store)
    assert volatility["garch_1d_pct"] is None
    assert volatility["garch_5d_pct"] is None


@pytest.mark.parametrize("mode", ["production", "demo", "test"])
def test_offline_news_returns_no_seed_events(mode, tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", mode)
    scraper = EconomicNewsScraper(data_dir=str(tmp_path / "news"), offline=True)
    assert scraper.scrape_all_feeds(save_to_db=True) == []
    assert not (tmp_path / "news" / "scraped_news.json").exists()


def test_missing_embedding_does_not_store_random_vector(tmp_path):
    scraper = EconomicNewsScraper(data_dir=str(tmp_path / "news"), offline=True)
    event = scraper.extract_structured_event(
        "Steel output fell 5%", "", "https://example.com/report", "Fixture", "2025-01-01"
    )
    assert scraper.save_to_lancedb([event]) == 0
    assert scraper.load_from_lancedb() == []


def test_legacy_trainer_rejects_missing_chronology():
    from src.gnn.trainer import TGNNTrainer
    from src.models.dual_layer_model import GraphNode, NodeType

    store = get_graph_store()
    store.add_node(GraphNode(id="fixture", label="Fixture", node_type=NodeType.SECTOR))
    with pytest.raises(ValueError, match="history|chronolog|dates"):
        TGNNTrainer(store).prepare_dataset()


def test_missing_news_measurements_remain_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", "test")
    folder = tmp_path / "raw/news"
    folder.mkdir(parents=True)
    (folder / "scraped_news.json").write_text(json.dumps([
        {"news_id": "fixture", "title": "General update", "receptor_signal": "POLICY_UPDATE"}
    ]), encoding="utf-8")
    row = get_all_news_data()[0]
    assert row["actions"] == []
    assert row["reactions"] == []
    assert row["direction"] is None
    assert row["delta_value"] is None
    assert row["timeframe"] is None


def test_graph_export_reads_configured_storage_without_writing(tmp_path):
    from scripts.export_graph_viz import export_graph_visualization
    from src.models.dual_layer_model import GraphNode, NodeType

    store = get_graph_store()
    store.add_node(GraphNode(id="only-current-data", label="Current record", node_type=NodeType.SECTOR))
    payload, html = export_graph_visualization(persist=False)
    assert [row["id"] for row in payload["nodes"]] == ["only-current-data"]
    assert payload["nodes"][0]["activation_date"] is None
    assert "only-current-data" in html
    assert not (tmp_path / "visualizations").exists()


def test_two_prices_cannot_produce_a_volatility_ranking():
    from src.models.dual_layer_model import GraphNode, NodeStateRecord, NodeType
    from src.quant.ranking import ConstructionFactorRanker

    store = get_graph_store()
    store.add_node(GraphNode(id="short", label="Short history", node_type=NodeType.COMMODITY))
    store.add_state_records_batch([NodeStateRecord(
        node_id="short", node_type=NodeType.COMMODITY, timestamp=date,
        state_value="OBSERVED", metric_name="price", metric_value=value,
    ) for date, value in [("2025-01-01", 100), ("2025-01-02", 101)]])
    assert ConstructionFactorRanker(store).compute_factor_rankings().empty


def test_old_seed_batch_name_cannot_bypass_evidence_checks(tmp_path):
    from datetime import datetime, timezone
    from src.schemas.ingestion import BatchManifest, Fact
    from src.storage.dual_layer_store import DualLayerStore

    store = DualLayerStore(tmp_path / "seed-regression")
    now = datetime.now(timezone.utc)
    # Reproduce a persisted legacy record without importing it as trusted evidence.
    batch = BatchManifest(batch_id="legacy_seed", source_id="fixture", status="committed", committed_at=now)
    fact = Fact(subject_id="s", predicate="AFFECTS", object_id="o", source_id="fixture",
                batch_id=batch.batch_id, available_at=now, recorded_at=now)
    store._canonical_batches[batch.batch_id] = batch
    store._canonical_facts[fact.fact_version_id] = fact
    assert store.query_facts(now) == []
