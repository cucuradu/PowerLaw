"""GDELT news coverage: monthly shares from daily counts, and requests paced to the provider's limit."""
import json

import pytest

from src.ingestion import FetchError, _gdelt_discover, _gdelt_parse, run_ingestion
from src.schemas.ingestion import SourceDocument
from src.storage.dual_layer_store import DualLayerStore
from tests.test_configured_sources import settings_at


def response(days):
    return json.dumps({"timeline": [{"series": "Article Count", "data": [
        {"date": d + "T000000Z", "value": v, "norm": n} for d, v, n in days]}]}).encode()


def test_daily_counts_become_complete_monthly_shares():
    item = next(_gdelt_discover("GDELT_DOC", None, None, 1))
    assert "sourcelang%3Agerman" in item["url"] and "mode=timelinevolraw" in item["url"] and item["retries"] == 1
    raw = response([("20220301", 3, 1_000_000), ("20220302", 1, 1_000_000), ("20220401", 0, 500_000), ("20990101", 9, 1)])
    doc = SourceDocument.from_content("GDELT_DOC", item["url"], "GDELT", raw, "g", data_origin="test")
    result = _gdelt_parse(item, raw, doc)
    assert {o.period_start: o.value for o in result["observations"]} == {"2022-03-01": 2.0, "2022-04-01": 0.0}  # 2099 is "current"
    assert result["evidence_links"][0].quoted_text == "4 matching of 2000000 monitored articles"
    with pytest.raises(FetchError):
        _gdelt_parse(item, b"Please limit requests to one every 5 seconds", doc)


def test_requests_wait_for_the_provider_interval(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr("src.ingestion.time.sleep", lambda s: waits.append(s))
    items = list(_gdelt_discover("GDELT_DOC", None, None, 3))
    raw = response([("20220301", 1, 1000)])
    report = run_ingestion("GDELT_DOC", limit=3, settings=settings_at(tmp_path), store=DualLayerStore(settings_at(tmp_path).lancedb_path),
                           discover=lambda *a: items, fetch=lambda url, **kw: (raw, url, "application/json", 200))
    assert report["status"] == "committed", report["source_errors"]
    assert len([w for w in waits if w > 5]) >= 2  # second and third request wait about 10 s each
