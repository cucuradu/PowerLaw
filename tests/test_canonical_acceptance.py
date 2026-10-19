"""Publication and production-reader boundaries must reject noncanonical truth."""
import json

import lancedb
import pytest

from src.config import Settings
from src.schemas.ingestion import BatchManifest, Entity, EvidenceLink, Fact, Observation, SeriesDefinition
from src.storage.dual_layer_store import DualLayerStore
from src.storage import dashboard_data as dd
from tests.acceptance_fixtures import stage_evidenced, utc


def series(batch="live"):
    return SeriesDefinition(series_id="series:ppi", source_id="TEST", dataset_id="D", provider_series_key="K",
                            metric_name="construction_ppi", unit="INDEX", available_at=utc(2020),
                            recorded_at=utc(2020), batch_id=batch)


@pytest.mark.parametrize("kind", ["FACT", "OBSERVATION"])
def test_live_assertion_cannot_be_backed_by_demo_document(tmp_path, kind):
    store = DualLayerStore(tmp_path)
    doc = stage_evidenced(store, "demo", origin="demo")
    if kind == "OBSERVATION":
        row = Observation(series_id="series:ppi", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                          value=100, unit="INDEX", batch_id="live", data_origin="live", available_at=utc(2024))
        args = {"observations": [row], "facts": [], "series": [series()]}
        version = row.revision_id
    else:
        row = Fact(subject_id="law:a", predicate="MANDATES", object_id="rule:a", source_id="TEST",
                   batch_id="live", data_origin="live", available_at=utc(2024))
        args = {"observations": [], "facts": [row], "entities": [
            Entity(entity_id=k, entity_type=t, canonical_label=k, batch_id="live", data_origin="live")
            for k, t in [("law:a", "LAW"), ("rule:a", "ACTION")]]}
        version = row.fact_version_id
    link = EvidenceLink(assertion_version_id=version, assertion_kind=kind, document_version_id=doc.version_id,
                        batch_id="live", quoted_text="Fixture")
    with pytest.raises(ValueError, match="origin"):
        store.stage_batch(BatchManifest(batch_id="live", source_id="TEST", data_origin="live"), [],
                          evidence_links=[link], **args)


def test_evidence_kind_must_match_the_assertion(tmp_path):
    store = DualLayerStore(tmp_path)
    doc = stage_evidenced(store, "source")
    row = Observation(series_id="series:ppi", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                      value=100, unit="INDEX", batch_id="live", data_origin="test")
    link = EvidenceLink(assertion_version_id=row.revision_id, assertion_kind="FACT", document_version_id=doc.version_id,
                        batch_id="live")
    with pytest.raises(ValueError, match="kind"):
        store.stage_batch(BatchManifest(batch_id="live", source_id="TEST", data_origin="test"), [], [], [row],
                          series=[series()], evidence_links=[link])


def law(store, *, batch="b", origin="live"):
    fact = Fact(subject_id="law_32024R9999", predicate="MANDATES", object_id="rule:a", source_id="TEST",
                available_at=utc(2020), recorded_at=utc(2020), batch_id=batch, data_origin=origin,
                payload_json=json.dumps({"metadata": {"regulation_id": "32024R9999", "article_id": "ART_1"},
                                         "summary": "Source-backed requirement"}))
    entities = [Entity(entity_id=k, entity_type=t, canonical_label=k, created_at=utc(2020), batch_id=batch, data_origin=origin)
                for k, t in [(fact.subject_id, "LAW"), (fact.object_id, "ACTION")]]
    stage_evidenced(store, batch, facts=[fact], entities=entities, origin=origin)
    return fact


def test_public_fact_query_selects_latest_retractions(tmp_path):
    store = DualLayerStore(tmp_path)
    fact = law(store, origin="test")
    tomb = fact.model_copy(update={"fact_version_id": "tomb", "batch_id": "tomb", "is_retracted": True,
                                   "available_at": utc(2025), "recorded_at": utc(2025),
                                   "supersedes_version_id": fact.fact_version_id})
    stage_evidenced(store, "tomb", facts=[tomb])
    assert len(store.query_facts(utc(2024))) == 1
    assert store.query_facts(utc(2026)) == []


def test_production_law_reader_ignores_uncommitted_chunk_for_committed_law(tmp_path, monkeypatch):
    store = DualLayerStore(tmp_path)
    law(store)
    db = lancedb.connect(str(tmp_path))
    db.create_table("structured_rules", data=[{"law_id": "32024R9999", "chunk_id": "UNCOMMITTED_RULE",
                    "gnn_rule_json": "{}", "data_origin": "live", "summary": "Invented loose row"}], mode="overwrite")
    cfg = Settings(mode="production", data_dir=tmp_path / "data", lancedb_path=tmp_path)
    monkeypatch.setattr(dd, "get_settings", lambda: cfg)
    rows = dd.get_all_laws_data(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["summary"] == "Source-backed requirement"
    assert rows[0]["chunk_id"] != "UNCOMMITTED_RULE"


def test_production_news_reader_ignores_loose_live_json(tmp_path, monkeypatch):
    path = tmp_path / "raw/news/scraped_news.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"title": "LOOSE_NEWS", "data_origin": "live"}]))
    cfg = Settings(mode="production", data_dir=tmp_path, lancedb_path=tmp_path / "db")
    monkeypatch.setattr(dd, "get_settings", lambda: cfg)
    assert dd.get_all_news_data() == []


def test_production_news_reads_canonical_evidenced_action(tmp_path, monkeypatch):
    store = DualLayerStore(tmp_path / "db")
    fact = Fact(subject_id="EVENT_a", predicate="ABOUT", object_id="sector:steel", source_id="TEST",
                available_at=utc(2020), recorded_at=utc(2020), batch_id="news", data_origin="live",
                payload_json=json.dumps({"kind": "action", "article_id": "ARTICLE_a", "event_id": "EVENT_a",
                    "evidence_text": "Factory closes in Germany", "action": {"action_type": "FACILITY_SHUTDOWN",
                    "actor": "Factory", "target": "sector:steel", "description": "Factory closes", "magnitude": None}}))
    entities = [Entity(entity_id=k, entity_type=t, canonical_label=k, created_at=utc(2020), batch_id="news", data_origin="live")
                for k, t in [(fact.subject_id, "ACTION"), (fact.object_id, "SECTOR")]]
    stage_evidenced(store, "news", facts=[fact], entities=entities, origin="live")
    cfg = Settings(mode="production", data_dir=tmp_path, lancedb_path=tmp_path / "db")
    monkeypatch.setattr(dd, "get_settings", lambda: cfg)
    rows = dd.get_all_news_data()
    assert len(rows) == 1
    assert rows[0]["actions"][0]["description"] == "Factory closes"
    assert rows[0]["evidence_document_ids"]
