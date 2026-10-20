"""Target availability comes from evidenced release dates (Bundesbank real-time vintages), not fetch time."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.config import primary_target_series_id
from src.ingestion import run_ingestion
from src.models.gnn_dataset import build_forecast_dataset
from src.schemas.ingestion import Observation, SeriesDefinition
from src.storage.dual_layer_store import DualLayerStore
from tests.acceptance_fixtures import stage_evidenced

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"
TARGET = primary_target_series_id()
FETCHED = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
BERLIN = ZoneInfo("Europe/Berlin")
VINTAGES = {"2006-01-06": "bbkrt_pp300_vintage_20060106_official.csv",
            "2025-04-10": "bbkrt_pp300_vintage_20250410_official.csv"}


def _settings(path):
    return SimpleNamespace(mode="test", sqlite_path=path / "jobs.sqlite", lancedb_path=path / "db",
        cache_dir=path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket",
        minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True,
        extraction_budget_usd=0.0)


def _end_of_day(day):
    return datetime.fromisoformat(f"{day}T23:59:59").replace(tzinfo=BERLIN).astimezone(timezone.utc)


def _store_with_fetched_target(tmp_path):
    """Destatis target values as they are today: committed, but only known since the fetch."""
    settings = _settings(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    values = {"2005-07-01": 100.0, "2005-10-01": 101.0, "2024-10-01": 130.0, "2025-01-01": 132.6}
    ends = {"2005-07-01": "2005-10-01", "2005-10-01": "2006-01-01", "2024-10-01": "2025-01-01", "2025-01-01": "2025-04-01"}
    obs = [Observation(series_id=TARGET, period_start=p, period_end_exclusive=ends[p], value=v, unit="2021=100",
                       source_unit="2021=100", available_at=FETCHED, batch_id="destatis", data_origin="live")
           for p, v in values.items()]
    series = [SeriesDefinition(series_id=TARGET, source_id="DESTATIS_GENESIS", dataset_id="61261-0002",
        provider_series_key=TARGET, metric_name="producer_price_index", unit="2021=100", frequency="QUARTERLY",
        geography_id="DE", measurement_kind="PRODUCER_PRICE_INDEX", available_at=FETCHED,
        batch_id="destatis")]
    stage_evidenced(store, "destatis", observations=obs, series=series, origin="live", available=FETCHED,
                    source_id="DESTATIS_GENESIS")
    return settings, store


def _ingest_vintages(settings, store):
    items = [{"item_id": f"rtd_{day}", "upstream_id": f"PP300@{day}", "series_key": "PP300", "release_date": day,
              "url": f"https://api.statistiken.bundesbank.de/rest/data/BBKRT/Q.DE....PP300...{day.replace('-', '.')}?format=csv&lang=en",
              "canonical_url": f"https://api.statistiken.bundesbank.de/rest/data/BBKRT/Q.DE....PP300...{day.replace('-', '.')}",
              "publisher": "Deutsche Bundesbank", "content_type": "text/csv", "availability_basis": "archived_release",
              "published_at": _end_of_day(day).isoformat()} for day in VINTAGES]
    payloads = {item["url"]: (FIXTURES / VINTAGES[item["release_date"]]).read_bytes() for item in items}
    report = run_ingestion("BUNDESBANK_RTD", limit=10, settings=settings, store=store,
                           discover=lambda *a: items, fetch=lambda url, **kw: (payloads[url], url, "text/csv", 200))
    assert report["status"] == "committed", report["source_errors"]
    return DualLayerStore(settings.lancedb_path)


def _visible(store, cutoff):
    rows = store.query_observations(cutoff=cutoff, mode="public_as_of").to_pylist()
    return {r["period_start"]: r["value"] for r in rows if r["series_id"] == TARGET}


def test_target_quarters_become_visible_at_their_evidenced_release(tmp_path):
    settings, store = _store_with_fetched_target(tmp_path)
    store = _ingest_vintages(settings, store)
    assert _visible(store, datetime(2006, 1, 5, tzinfo=timezone.utc)) == {}
    early = _visible(store, datetime(2006, 1, 7, tzinfo=timezone.utc))
    assert early == {"2005-07-01": 100.0, "2005-10-01": 101.0}  # released in the 2006-01-06 vintage
    # Only these two vintages are fixtures, so 2024-Q4's earliest evidence is also the 2025-04-10 release.
    assert _visible(store, datetime(2025, 4, 9, tzinfo=timezone.utc)) == early
    after = _visible(store, datetime(2025, 4, 11, tzinfo=timezone.utc))
    assert after["2024-10-01"] == 130.0 and after["2025-01-01"] == 132.6


def test_forecast_label_uses_release_dated_levels(tmp_path):
    settings, store = _store_with_fetched_target(tmp_path)
    store = _ingest_vintages(settings, store)
    dataset = build_forecast_dataset(TARGET, [datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc)], store=store,
                                     window_size=1, label_cutoff=FETCHED)
    (sample,) = dataset["samples"]
    assert sample["y_mask"][0, 0]
    assert abs(float(sample["y"][0, 0]) - 2.0) < 1e-4  # 100 * (132.6 / 130 - 1)
    assert sample["first_release_available_at"] == _end_of_day("2025-04-10")


def test_unreadable_vintage_fails_instead_of_committing_nothing():
    import pytest
    from src.ingestion import _rtd_parse
    with pytest.raises(ValueError, match="no parseable"):
        _rtd_parse({"_store": None}, b"Zeitreihe;Wert\r\n2025-Q1;133,8\r\n", None)


def test_discovery_requests_each_vintage_in_the_verified_english_csv_layout(monkeypatch):
    import src.ingestion as ingestion
    header = b'\xef\xbb\xbf"",10.04.2025,06.01.2006\n"",\n'
    monkeypatch.setattr(ingestion, "bounded_fetch", lambda url, **kw: (header, url, "text/csv", 200))
    items = list(ingestion._rtd_discover("BUNDESBANK_RTD", None, None, 10))
    assert [item["release_date"] for item in items] == ["2006-01-06", "2025-04-10"]
    assert items[1]["url"].endswith("/BBKRT/Q.DE....PP300...2025.04.10?format=csv&lang=en")
    assert items[0]["availability_basis"] == "archived_release"
    assert items[0]["published_at"] == _end_of_day("2006-01-06").isoformat()


def test_definition_is_public_from_its_earliest_release_evidence_only(tmp_path):
    settings, store = _store_with_fetched_target(tmp_path)
    before = DualLayerStore(settings.lancedb_path)
    assert TARGET not in {d.series_id for d in before.query_series(datetime(2010, 1, 1, tzinfo=timezone.utc))}
    store = _ingest_vintages(settings, store)
    assert TARGET not in {d.series_id for d in store.query_series(datetime(2006, 1, 5, tzinfo=timezone.utc))}
    assert TARGET in {d.series_id for d in store.query_series(datetime(2006, 1, 7, tzinfo=timezone.utc))}
    # System-as-of still reflects when we actually learned it.
    assert TARGET not in {d.series_id for d in store.query_series(datetime(2006, 1, 7, tzinfo=timezone.utc), "system_as_of")}
