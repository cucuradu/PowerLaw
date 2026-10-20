"""German federal laws link to sectors and materials only where a quote is found in the official text."""
import json
from pathlib import Path

from src.ingestion import _gii_discover, run_ingestion
from src.storage.dual_layer_store import DualLayerStore
from tests.test_release_dated_target import _settings

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"
RAW = {"GEG": "gii_geg_20260928_official.zip", "BEHG": "gii_behg_20260928_official.zip"}


def _ingest(tmp_path, monkeypatch=None, quote_override=None):
    settings = _settings(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    items = [i for i in _gii_discover("GESETZE_IM_INTERNET", None, None, 20) if i["upstream_id"] in RAW]
    raw = {i["url"]: (FIXTURES / RAW[i["upstream_id"]]).read_bytes() for i in items}
    report = run_ingestion("GESETZE_IM_INTERNET", limit=5, settings=settings, store=store, discover=lambda *a: items,
                           fetch=lambda url, **kw: (raw[url], url, "application/zip", 200))
    assert report["status"] == "committed", report["source_errors"]
    return store


def test_quoted_links_are_accepted_and_eu_directives_are_linked(tmp_path):
    store = _ingest(tmp_path)
    facts = {(f.subject_id, f.predicate, f.object_id): f for f in store._canonical_facts.values()}
    assert facts[("law_de_geg", "REGULATES", "sec_F")].quality_state == "accepted"
    assert facts[("law_de_geg", "REGULATES", "commodity_heat_pumps")].quality_state == "accepted"
    assert ("law_de_geg", "IMPLEMENTS", "law_32010L0031") in facts  # EPBD 2010
    assert facts[("law_de_behg", "REGULATES", "commodity_natural_gas")].quality_state == "accepted"
    assert store._canonical_entities["law_de_geg"].jurisdiction == "DE"
    quotes = {e.quoted_text for e in store._canonical_evidence.values()}
    assert "eine elektrisch angetriebene Wärmepumpe" in quotes


def test_a_quote_missing_from_the_text_is_kept_for_review(tmp_path, monkeypatch):
    import src.ingestion as ingestion
    registry = ingestion.load_data_sources_registry()
    registry["sources"]["GESETZE_IM_INTERNET"]["laws"]["GEG"]["links"][0]["quote"] = "Dieser Satz steht nicht im Gesetz"
    monkeypatch.setattr(ingestion, "load_data_sources_registry", lambda: registry)
    store = _ingest(tmp_path)
    fact = next(f for f in store._canonical_facts.values() if f.object_id == "sec_F" and f.subject_id == "law_de_geg")
    assert fact.quality_state == "needs_review"
    link = next(e for e in store._canonical_evidence.values() if e.assertion_version_id == fact.fact_version_id)
    assert link.support_type == "UNVERIFIED"
