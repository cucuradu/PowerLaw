"""Construction's supplier and customer links come from the official Eurostat input-output table."""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion import _naio_discover, _naio_parse, run_ingestion
from src.schemas.ingestion import SourceDocument
from src.storage.dual_layer_store import DualLayerStore
from tests.test_release_dated_target import _settings

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"
FETCHED = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
RAW = {"construction_inputs": "eurostat_naio_10_cp1700_de_inputs_official.json",
       "construction_uses": "eurostat_naio_10_cp1700_de_uses_official.json"}


def _parse(table):
    item = next(i for i in _naio_discover("EUROSTAT_NAIO", None, None, 5) if i["table"] == table)
    raw = (FIXTURES / RAW[table]).read_bytes()
    doc = SourceDocument.from_content("EUROSTAT_NAIO", item["canonical_url"], "Eurostat", raw, "naio", data_origin="test",
                                      available_at=FETCHED)
    return _naio_parse(item, raw, doc)


def test_inputs_become_cost_shares_flowing_into_construction():
    result = _parse("construction_inputs")
    by_year = {}
    for fact in result["facts"]:
        payload = json.loads(fact.payload_json)
        assert fact.predicate == "FLOWS_TO" and fact.object_id == "sec_F"
        by_year.setdefault(payload["episode"], {})[fact.subject_id] = payload
    assert sorted(by_year) == list(range(2010, 2023))
    minerals = by_year[2022]["sec_C23"]
    assert minerals["value_mio_eur"] > 20000 and 0 < minerals["import_share"] < 0.5
    # Products plus primary inputs (labour, profit, depreciation, taxes) add up to output.
    assert abs(sum(p["share"] for p in by_year[2022].values()) - 1) < 0.02
    assert "sec_D1" in by_year[2022] and by_year[2022]["sec_D1"]["share"] > 0.2
    link = result["evidence_links"][0]
    assert link.selector_type == "TABLE_CELL" and "million EUR" in link.quoted_text
    assert {e.entity_id for e in result["entities"]} >= {"sec_F", "sec_C23", "sec_D1"}


def test_uses_show_who_buys_construction():
    result = _parse("construction_uses")
    latest = {f.object_id: json.loads(f.payload_json) for f in result["facts"] if json.loads(f.payload_json)["episode"] == 2022}
    assert all(f.subject_id == "sec_F" for f in result["facts"])
    assert max(latest, key=lambda k: latest[k]["value_mio_eur"]) == "sec_P51G"  # investment


def test_refetching_the_same_table_adds_no_new_fact_versions(tmp_path):
    settings = _settings(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    raw = {i["url"]: (FIXTURES / RAW[i["table"]]).read_bytes() for i in _naio_discover("EUROSTAT_NAIO", None, None, 5)}
    fetch = lambda url, **kw: (raw[url], url, "application/json", 200)
    first = run_ingestion("EUROSTAT_NAIO", limit=5, settings=settings, store=store, fetch=fetch)
    assert first["status"] == "committed", first["source_errors"]
    facts = len(store._canonical_facts)
    assert facts > 1000
    second = run_ingestion("EUROSTAT_NAIO", limit=5, settings=settings, store=store, fetch=fetch)
    assert second["status"] == "committed", second["source_errors"]
    assert len(store._canonical_facts) == facts


def test_material_prices_link_to_their_commodity_and_supplier_sector(tmp_path):
    from src.ingestion import _stats_discover
    settings = _settings(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    item = next(i for i in _stats_discover("DESTATIS_GENESIS", "2025-01", "2025-12", 20) if i["upstream_id"] == "61241-0006")
    raw = (FIXTURES / "destatis_61241_0006_2025_official.zip").read_bytes()
    run = lambda: run_ingestion("DESTATIS_GENESIS", limit=1, settings=settings, store=store, discover=lambda *a: [item],
                                fetch=lambda url, **kw: (raw, url, "application/zip", 200))
    assert run()["status"] == "committed"
    facts = {(f.subject_id, f.predicate, f.object_id) for f in store._canonical_facts.values()}
    cement = "61241-0006|DINSG=DG|GP19M6=GP19-235112|PRE001|2021=100"
    assert (cement, "MEASURES", "commodity_cement") in facts
    assert ("commodity_cement", "BELONGS_TO", "sec_C23") in facts
    assert store._canonical_entities["commodity_cement"].canonical_label == "Cement"
    count = len(store._canonical_facts)
    assert run()["status"] == "committed" and len(store._canonical_facts) == count
