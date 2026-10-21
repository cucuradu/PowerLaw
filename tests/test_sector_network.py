"""The sector network view is built from committed, evidenced facts only; projections use quarterly levels."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.ingestion import _gii_discover, _naio_discover, _stats_discover, run_ingestion
from src.storage.dashboard_data import get_sector_network
from src.storage.dual_layer_store import DualLayerStore
from tests.test_release_dated_target import _settings

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"


def _replay(settings, store, source, items, files, content_type):
    raw = {i["url"]: (FIXTURES / files[i["upstream_id"]]).read_bytes() for i in items}
    report = run_ingestion(source, limit=10, settings=settings, store=store, discover=lambda *a: items,
                           fetch=lambda url, **kw: (raw[url], url, content_type, 200))
    assert report["status"] == "committed", report["source_errors"]


def test_network_joins_suppliers_materials_prices_and_laws(tmp_path):
    settings = _settings(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    for item in _naio_discover("EUROSTAT_NAIO", None, None, 5):  # both tables share one upstream ID, so one at a time
        _replay(settings, store, "EUROSTAT_NAIO", [item],
                {item["upstream_id"]: f"eurostat_naio_10_cp1700_de_{item['table'].split('_')[1]}_official.json"}, "application/json")
    prices = [i for i in _stats_discover("DESTATIS_GENESIS", "2025-01", "2025-12", 20) if i["upstream_id"] == "61241-0006"]
    _replay(settings, store, "DESTATIS_GENESIS", prices, {"61241-0006": "destatis_61241_0006_2025_official.zip"}, "application/zip")
    laws = [i for i in _gii_discover("GESETZE_IM_INTERNET", None, None, 20) if i["upstream_id"] == "GEG"]
    _replay(settings, store, "GESETZE_IM_INTERNET", laws, {"GEG": "gii_geg_20260928_official.zip"}, "application/zip")

    network = get_sector_network(store, cutoff=datetime.now(timezone.utc), top=10)
    groups = {n["id"]: n["group"] for n in network["nodes"]}
    assert network["year"] == 2022 and groups["sec_F"] == "construction"
    assert groups["sec_C23"] == "supplier" and groups["sec_D1"] == "cost" and groups["sec_P51G"] == "final demand"
    assert groups["commodity_cement"] == "material"
    edges = {(e["source"], e["target"], e["kind"]) for e in network["edges"]}
    assert ("commodity_cement", "sec_C23", "part of") in edges
    assert ("law_de_geg", "sec_F", "regulates") in edges and ("law_de_geg", "law_32010L0031", "implements") in edges
    # Supported world-price chain links appear; unsupported ones do not.
    chain = {"chain_links": [{"from": "commodity:crude_oil", "to": "commodity:bitumen_products", "verdict": "supported", "pass_through": 0.17, "p": 0.002},
                             {"from": "commodity:copper", "to": "sector:F_BPNB530", "verdict": "not supported", "pass_through": 0.0, "p": 0.2}]}
    linked = {(e["source"], e["target"]) for e in get_sector_network(store, cutoff=datetime.now(timezone.utc), top=10, links=chain)["edges"]
              if e["kind"] == "feeds"}
    assert ("commodity_crude_oil", "commodity_bitumen_products") in linked and ("commodity_copper", "sec_F_BPNB530") not in linked
    # Nothing is visible before the data was fetched.
    assert get_sector_network(store, cutoff=datetime(2020, 1, 1, tzinfo=timezone.utc))["nodes"] == []


def test_monthly_prices_become_complete_quarterly_means():
    from scripts.ops.project_series import quarterly_levels
    when = lambda d: datetime(2026, d, 20, tzinfo=timezone.utc)
    rows = [SimpleNamespace(series_id="s", value=v, is_retracted=False, period_start=f"2026-0{m}-01",
                            period_end_exclusive=f"2026-0{m + 1}-01", available_at=when(m + 1))
            for m, v in ((1, 100.0), (2, 102.0), (3, 104.0), (4, 110.0))]
    store = SimpleNamespace(_available=lambda o: o.available_at)
    levels = quarterly_levels(rows, "s", None, store)
    assert levels == [{"period_start": "2026-01-01", "value": 102.0, "available_at": when(4)}]  # Q2 is incomplete
