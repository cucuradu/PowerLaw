"""Offline regressions for source claims, provider contracts and provenance."""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ingestion import _make_stats_parser, _stats_discover, _news_parse, bounded_fetch, FetchError, run_ingestion
from src.schemas.ingestion import SourceDocument
from src.storage.dual_layer_store import DualLayerStore


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", "test")
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: pytest.fail("external network disabled"))
    monkeypatch.setattr("src.main.get_completion_with_json", lambda **kw: '{"rules": []}')


def settings_at(path):
    return SimpleNamespace(mode="test", sqlite_path=path / "jobs.sqlite", lancedb_path=path / "db",
        cache_dir=path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket",
        minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True,
        extraction_budget_usd=0.0)


@pytest.mark.parametrize("text", [
    "Operators shall ensure passport inspection procedures are followed.",
    "Operators shall establish inspection procedures for the digital passport.",
    "CBAM document formatting requirements shall apply from 2028-06-01.",
    "CBAM document presentation requirements shall apply from 2028-06-01.",
])
def test_complete_law_run_does_not_invent_operative_obligation(tmp_path, text):
    raw = ('<?xml version="1.0"?><ACT><DOCUMENT.REF>32024R9999</DOCUMENT.REF>'
           '<ARTICLE id="art_1"><TI.ART>Article 1</TI.ART><PARAGRAPH>' + text +
           '</PARAGRAPH></ARTICLE></ACT>').encode()
    settings = settings_at(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    item = {"url": "https://eur-lex.europa.eu/?uri=CELEX:32024R9999", "upstream_id": "32024R9999",
            "publisher": "Offline official fixture", "content_type": "application/xml"}
    report = run_ingestion("EURLEX_CELLAR", limit=1, settings=settings, store=store,
        discover=lambda *a: [item], fetch=lambda url, **kw: (raw, url, "application/xml", 200))
    assert report["status"] == "committed"
    for fact in store._canonical_facts.values():
        if fact.predicate != "MANDATES" or fact.quality_state != "accepted":
            continue
        rule = json.loads(fact.payload_json)
        obligation = rule["mechanism"]["non_numeric_obligation"].lower()
        assert "establish digital construction passport" not in obligation
        assert "certificates" not in obligation
        assert not rule["scope"]["affected_commodities"]


def parse(source, key, content):
    document = SourceDocument.from_content(source, "https://example.test/data", "Official fixture", content,
        "source-test", data_origin="test", available_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    return _make_stats_parser(source)({"series_key": key}, content, document)


def test_named_eurostat_dimensions_and_release_metadata():
    raw = (Path(__file__).parent / "fixtures/ingestion/eurostat_sts_copi_q_de_official.json").read_bytes()
    result = parse("EUROSTAT_STS", "sts_copi_q", raw)
    for definition in result["series"]:
        dims = json.loads(definition.dimensions_json)
        assert dims["geo"] == definition.geography_id == "DE"
        assert dims["freq"] == "Q"
        assert dims["s_adj"] == definition.adjustment == "NSA"
        assert definition.dataset_id == "sts_copi_q"
        assert dims["cpa2_1"] == "CPA_F41001_X_410014"
    assert all(o.source_updated_at is not None for o in result["observations"])


def test_ecb_interest_rates_keep_monthly_definition_and_release():
    raw = (b"KEY,FREQ,REF_AREA,UNIT,TIME_PERIOD,OBS_VALUE,OBS_STATUS,LAST_UPDATE\n"
           b"MIR.M.DE.B.A2C.A.R.A.2250.EUR.N,M,DE,PCPA,2026-08,3.7,P,2026-10-01T10:00:00Z\n")
    result = parse("ECB_SDMX", "MIR.M.DE.B.A2C.A.R.A.2250.EUR.N", raw)
    definition = result["series"][0]
    assert definition.frequency == "MONTHLY"
    assert definition.measurement_kind == "INTEREST_RATE"
    assert definition.geography_id == "DE"
    assert json.loads(definition.dimensions_json)["REF_AREA"] == "DE"
    observation = result["observations"][0]
    assert observation.unit == "PERCENT"
    assert observation.vintage_id == "2026-10-01T10:00:00Z"
    assert observation.source_updated_at == datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("source,flow", [("ECB_SDMX", "EXR"), ("BUNDESBANK_SDMX", "BBSIS")])
def test_sdmx_requests_separate_flow_from_key_and_bound_window(source, flow):
    item = next(iter(_stats_discover(source, "2024-01-01", "2024-12-31", 1)))
    assert f"/{flow}/{flow}." not in item["url"]
    assert "startPeriod=2024-01-01" in item["url"]
    assert "endPeriod=2024-12-31" in item["url"]


def test_destatis_request_is_post_with_private_header_auth_and_bounded_form(monkeypatch):
    item = next(iter(_stats_discover("DESTATIS_GENESIS", "2024-01-01", "2025-12-31", 1)))
    assert item["request_method"] == "POST"
    assert item["request_data"]["startyear"] == "2024"
    assert item["request_data"]["endyear"] == "2025"
    assert item["request_data"]["job"] == "false"
    assert "username" not in json.dumps(item)
    calls = []
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/csv"}
        def iter_content(self, **kw):
            yield b"body"
        def close(self):
            pass
    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
    monkeypatch.setattr("src.ingestion._host_is_public", lambda h: True)
    monkeypatch.setenv("GENESIS_API_TOKEN", "offline-token")
    bounded_fetch(item["url"], session=Session(), request_method=item["request_method"],
                  request_data=item["request_data"], auth_source="DESTATIS_GENESIS")
    assert calls[0][1]["headers"]["username"] == "offline-token"
    assert "offline-token" not in calls[0][0]


def test_smard_download_center_is_not_an_api(monkeypatch):
    from src.config import load_data_sources_registry
    registry = load_data_sources_registry()
    smard = dict(registry["sources"]["SMARD_DE"], endpoints={"download_center": "https://www.smard.de/en/downloadcenter"})
    monkeypatch.setattr("src.config.load_data_sources_registry", lambda: {
        **registry, "sources": {**registry["sources"], "SMARD_DE": smard}
    })
    with pytest.raises(FetchError, match="export"):
        list(_stats_discover("SMARD_DE", "2024-01-01", "2024-12-31", 1))


def test_news_feed_retains_entry_time_without_backdating_archive():
    content = (b'<rss><channel><item><title>ArcelorMittal cuts steel output by 20%.</title>'
        b'<link>https://example.test/article</link><guid>release-1</guid>'
        b'<pubDate>Wed, 15 May 2024 10:35:00 +0200</pubDate></item></channel></rss>')
    document = SourceDocument.from_content("OFFICIAL_NEWS", "https://example.test/feed", "Official", content,
        "feed-batch", data_origin="test", available_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    result = _news_parse({}, content, document)
    assert result["facts"]
    for fact in result["facts"]:
        payload = json.loads(fact.payload_json)
        assert fact.available_at == document.available_at
        assert payload["published_at"] == "2024-05-15T08:35:00+00:00"
        assert payload["evidence_scope"] == "feed_summary"
        assert payload["article_document_version_id"] is None
        assert payload["feed_document_version_id"] == document.version_id
    assert all(link.selector_type == "RSS_ENTRY" for link in result["evidence_links"])


def test_archived_runner_manifest_includes_origin_and_batch_metadata(tmp_path):
    from src.storage.ingestion_jobs import IngestionJobLedger
    from src.utils.minio_client import EvidenceArchive
    settings = settings_at(tmp_path)
    result = run_ingestion("TEST", limit=1, settings=settings,
        discover=lambda *a: [{"url": "https://example.test/data"}],
        parse=lambda *a: {"status": "accepted", "accepted_count": 1},
        fetch=lambda url, **kw: (b"raw", url, "text/plain", 200))
    record = IngestionJobLedger(settings.sqlite_path).batch(result["batch_id"])
    payload = json.loads(EvidenceArchive(settings).get(record["manifest_uri"], record["archive_sha256"]))
    assert payload["data_origin"] == "test"
    assert payload["manifest_version"] == "2"
    assert payload["batch_metadata"]["fetched_count"] == 1
    assert payload["batch_metadata"]["accepted_count"] == 1


def test_destatis_official_zip_flatfile_and_error_envelope():
    import io
    import zipfile
    from src.scrapers.statistics import normalize_series_response
    csv_bytes = (Path(__file__).parent / "fixtures/ingestion/destatis_61261_0002_2025_official.csv").read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("61261-0002.csv", csv_bytes)
    observed = normalize_series_response("DESTATIS_GENESIS", "61261-0002", buffer.getvalue(), datetime(2026, 10, 19, tzinfo=timezone.utc))
    assert len(observed) > 100
    error = json.dumps({"Status": {"Code": 104, "Content": "no matching objects"}}).encode()
    with pytest.raises(ValueError, match="provider"):
        normalize_series_response("DESTATIS_GENESIS", "61261-0002", error, datetime.now(timezone.utc))


def test_destatis_definition_preserves_full_target_key_and_distinct_work():
    from src.config import load_data_sources_registry
    registry = load_data_sources_registry()
    key = registry["primary_target"]["provider_series_key"]
    raw = (Path(__file__).parent / "fixtures/ingestion/destatis_61261_0002_2025_official.csv").read_bytes()
    result = parse("DESTATIS_GENESIS", "61261-0002", raw)
    target = next(definition for definition in result["series"] if definition.series_id == key)
    assert target.provider_series_key == key
    assert target.base_period == "2021=100"
    assert target.frequency == "QUARTERLY"
    table = (Path(__file__).parent / "fixtures/ingestion/destatis_61261_0002_table_official.json").read_bytes()
    definitions = parse("DESTATIS_GENESIS", "61261-0002", table)["series"]
    assert any(definition.series_id == key for definition in definitions)
    assert len({definition.series_id for definition in definitions}) > 20


def test_news_correction_removal_survives_fresh_runner_store(tmp_path):
    settings = settings_at(tmp_path)
    feed_url = "https://example.test/feed"
    item = {"url": feed_url, "publisher": "Official", "content_type": "application/rss+xml"}
    def feed(title):
        return (f'<rss><channel><item><guid>release-1</guid><link>https://example.test/article</link>'
            f'<pubDate>Wed, 15 May 2024 10:35:00 +0200</pubDate><title>{title}</title></item></channel></rss>').encode()
    original = feed("ArcelorMittal cuts steel output by 20%.")
    corrected = feed("ArcelorMittal cuts steel output by 15%.")
    removed = feed("Release withdrawn; no economic announcement remains.")
    for raw in (original, corrected, removed):
        result = run_ingestion("OFFICIAL_NEWS", limit=1, settings=settings,
            store=DualLayerStore(settings.lancedb_path), discover=lambda *a: [item],
            fetch=lambda url, **kw: (raw, url, "application/rss+xml", 200))
        assert result["status"] == "committed"
    store = DualLayerStore(settings.lancedb_path)
    latest = {}
    for fact in store._canonical_facts.values():
        if fact.fact_id not in latest or fact.available_at > latest[fact.fact_id].available_at:
            latest[fact.fact_id] = fact
    assert latest and all(fact.is_retracted for fact in latest.values())
    assert all(fact.supersedes_version_id for fact in latest.values())


def test_sdmx_currency_flags_and_unknown_publication_stay_source_native():
    raw = (b"KEY,FREQ,CURRENCY,CURRENCY_DENOM,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_CONF,UNIT_MULT\n"
        b"EXR.M.GBP.EUR.SP00.A,M,GBP,EUR,2024-05,0.85,P,F,0\n")
    result = parse("ECB_SDMX", "EXR.M.GBP.EUR.SP00.A", raw)
    observation = result["observations"][0]
    assert observation.unit == "GBP_PER_EUR"
    assert observation.provider_status == "P"
    assert json.loads(observation.conversion_lineage_json)["source_attributes"]["OBS_CONF"] == "F"
    assert observation.published_at is None and observation.source_updated_at is None
    assert result["series"][0].currency == "GBP"
    assert result["series"][0].frequency == "MONTHLY"


@pytest.mark.parametrize("source,raw", [
    ("ECB_SDMX", b"<html>HTTP 200 error</html>"),
    ("EUROSTAT_STS", b'{"error": {"label": "dataset removed"}}'),
])
def test_statistics_schema_errors_fail_instead_of_advancing_empty(source, raw):
    with pytest.raises(ValueError, match="provider"):
        parse(source, "flow", raw)


@pytest.mark.parametrize("text", [
    "Operators shall establish digital controls. Construction passport inspection is optional.",
    "Operators may establish a digital construction passport. Authorities shall inspect documentation.",
])
def test_action_object_cannot_be_assembled_from_unrelated_clauses(tmp_path, monkeypatch, text):
    from src.main import LawIngestionPipeline
    pipeline = LawIngestionPipeline(storage_dir=tmp_path / "law")
    template = pipeline.extract_rules_from_text("", "32024R9999", provisions=[{
        "article_id": "ART_1", "title": "Article 1",
        "text": "Operators shall establish a digital construction passport."}])[0]
    monkeypatch.setattr(pipeline, "extract_rules_from_text", lambda *a, **kw: [template])
    raw = ('<ACT><ARTICLE id="art_1"><TI.ART>Article 1</TI.ART><PARAGRAPH>' + text + '</PARAGRAPH></ARTICLE></ACT>').encode()
    document = SourceDocument.from_content("EURLEX_CELLAR", "https://eur-lex.europa.eu/?uri=CELEX:32024R9999",
        "Official fixture", raw, "clauses", data_origin="test").model_copy(update={"content_type": "application/xml"})
    mandates = [fact for fact in pipeline.process_document(document, raw) if fact.predicate == "MANDATES"]
    assert mandates and all(fact.quality_state == "needs_review" for fact in mandates)
