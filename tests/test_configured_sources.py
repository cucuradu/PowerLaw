"""Replay public provider bytes retrieved 2026-10-19; never contact providers.

The configured_* fixtures are the unchanged public HTTP bodies from the readiness
review. URLs are checked against configured discovery below; no request headers,
credentials or echoed private request metadata are retained.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from google.auth.exceptions import DefaultCredentialsError
from google.genai.errors import ClientError

from src.config import load_data_sources_registry
from src.ingestion import _make_stats_parser, _news_discover, _news_parse, _stats_discover, run_ingestion
from src.schemas.ingestion import SourceDocument
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger
from src.utils.minio_client import EvidenceArchive


FIXTURES = Path(__file__).parent / "fixtures/ingestion"
FETCHED_AT = datetime(2026, 10, 19, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", "test")
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: pytest.fail("external network disabled"))


def settings_at(path):
    return SimpleNamespace(mode="test", sqlite_path=path / "jobs.sqlite", lancedb_path=path / "db",
        cache_dir=path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket",
        minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True,
        extraction_budget_usd=0.0)


def test_retained_public_response_checksums():
    provenance = json.loads((FIXTURES / "configured_sources_provenance.json").read_text())
    for filename, metadata in provenance["responses"].items():
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == metadata["sha256"]


def test_only_verified_sources_and_requests_are_active():
    sources = load_data_sources_registry()["sources"]
    assert {key for key, value in sources.items() if value["enabled"]} == {
        "DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "OFFICIAL_NEWS",
        "BUNDESBANK_SDMX", "BUNDESBANK_RTD", "EURLEX_CELLAR"}
    assert list(sources["DESTATIS_GENESIS"]["series_keys"].values()) == [
        "61261-0002", "61261-0014", "61241-0004", "44111-0009", "31111-0020", "62221-0002"]
    assert list(sources["EUROSTAT_STS"]["series_keys"].values()) == ["sts_copi_q", "ei_bsbu_m_r2"]
    assert list(sources["ECB_SDMX"]["series_keys"].values()) == ["MIR.M.DE.B.A2C.A.R.A.2250.EUR.N"]
    news = list(_news_discover("OFFICIAL_NEWS", None, None, 10))
    assert [item["url"] for item in news] == ["https://ec.europa.eu/commission/presscorner/api/rss?language=en"]
    raw = (FIXTURES / "configured_official_news.rss").read_bytes()
    document = SourceDocument.from_content("OFFICIAL_NEWS", news[0]["url"], "European Commission",
        raw, "news-replay", data_origin="test", available_at=FETCHED_AT)
    result = _news_parse(news[0], raw, document)
    assert result["status"] in {"accepted", "needs_review", "out_of_scope"}
    assert all(fact.available_at == FETCHED_AT for fact in result["facts"])


@pytest.mark.parametrize("source,fixture", [
    ("DESTATIS_GENESIS", "configured_destatis_genesis.zip"),
    ("EUROSTAT_STS", "configured_eurostat_sts.json"),
    ("ECB_SDMX", "configured_ecb_mir_de_housing_loans.csv"),
])
def test_configured_real_response_commits_exact_selection_with_pilot_cap(tmp_path, source, fixture):
    item = next(iter(_stats_discover(source, "2025-01-01", "2025-12-31", 1)))
    raw = (FIXTURES / fixture).read_bytes()
    settings = settings_at(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    report = run_ingestion(source, limit=1, settings=settings, store=store, max_series=8,
        discover=lambda *a: [item], fetch=lambda url, **kw: (raw, url, item["content_type"], 200))
    assert report["status"] == "committed", report["source_errors"]
    assert len(store._canonical_series) == 1
    expected = item["required_series_keys"][0]
    assert set(store._canonical_series) == {expected}
    assert {obs.series_id for obs in store._canonical_observations.values()} == {expected}
    document = next(iter(store._canonical_documents.values()))
    assert document.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert all(obs.available_at == document.available_at and obs.data_origin == "test"
        for obs in store._canonical_observations.values())
    if source == "DESTATIS_GENESIS":
        assert expected == load_data_sources_registry()["primary_target"]["provider_series_key"]
        assert {obs.period_start: obs.value for obs in store._canonical_observations.values()} == {
            "2025-01-01": 132.6, "2025-04-01": 133.6, "2025-07-01": 134.3, "2025-10-01": 135.0}
    elif source == "EUROSTAT_STS":
        params = parse_qs(urlparse(item["url"]).query)
        dimensions = load_data_sources_registry()["primary_target"]["eurostat_crosscheck"]["dimensions"]
        assert all(params[key] == [value] for key, value in dimensions.items())
        assert "indic_bt=PRC_PRR" in expected and "COST" not in expected


def test_each_context_table_fixture_yields_exactly_its_registered_keys():
    """Every registered context selection is produced by the production parser from its official response."""
    selections = load_data_sources_registry()["sources"]["DESTATIS_GENESIS"]["context_series"].values()
    by_table = {}
    for entry in selections:
        by_table.setdefault(entry["table_id"], []).append(entry)
    assert len(by_table["61241-0004"]) == 6  # five material groups and energy
    for table, entries in by_table.items():
        expected = {entry["provider_series_key"] for entry in entries}
        raw = (Path(__file__).parent.parent / entries[0]["fixture_path"]).read_bytes()
        assert {hashlib.sha256(raw).hexdigest()} == {entry["fixture_sha256"] for entry in entries}
        doc = SourceDocument.from_content("DESTATIS_GENESIS", "https://genesis.destatis.de/x", "Destatis", raw,
            "context-replay", data_origin="test", available_at=FETCHED_AT)
        item = {"series_key": table, "required_series_keys": sorted(expected)}
        result = _make_stats_parser("DESTATIS_GENESIS")(item, raw, doc)
        assert {definition.series_id for definition in result["series"]} == expected, table
        kinds = {entry["provider_series_key"]: entry["measurement_kind"] for entry in entries}
        assert {d.series_id: d.measurement_kind for d in result["series"]} == kinds, table


def test_missing_configured_target_fails_without_empty_commit_or_cursor(tmp_path):
    item = next(iter(_stats_discover("DESTATIS_GENESIS", "2025", "2025", 1)))
    item["cursor"] = "2025-12-31"
    raw = (FIXTURES / "destatis_61241_0004_2025_official.csv").read_bytes()
    settings = settings_at(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    report = run_ingestion("DESTATIS_GENESIS", limit=1, settings=settings, store=store,
        discover=lambda *a: [item], fetch=lambda url, **kw: (raw, url, "text/csv", 200))
    assert report["status"] == "failed"
    assert report["committed"] == 0
    assert report["source_errors"]
    assert not store._canonical_batches
    assert IngestionJobLedger(settings.sqlite_path).cursor("DESTATIS_GENESIS") is None


@pytest.mark.parametrize("stage", ["draft", "review"])
@pytest.mark.parametrize("error", [BudgetError("budget disabled"), DefaultCredentialsError("ADC unavailable"),
    ClientError(401, {"error": {"message": "unauthenticated", "status": "UNAUTHENTICATED"}}),
    ValueError("truncated response"), "invalid_rules"])
def test_law_technical_failure_archives_and_same_run_retry_extracts(tmp_path, monkeypatch, stage, error):
    from src.schemas.rule_schema import GNNRuleList
    settings = settings_at(tmp_path)
    store = DualLayerStore(settings.lancedb_path)
    ledger = IngestionJobLedger(settings.sqlite_path)
    archive = EvidenceArchive(settings)
    raw = (b'<ACT><DOCUMENT.REF>32024R9999</DOCUMENT.REF><ARTICLE><TI.ART>Article 99</TI.ART>'
        b'<PARAG><TXT>Operators shall keep a product provenance register.</TXT></PARAG></ARTICLE></ACT>')
    item = {"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R9999",
        "item_id": "32024R9999", "upstream_id": "32024R9999", "content_type": "application/xml",
        "cursor": "2026-10-19"}
    draft = GNNRuleList.model_validate({"rules": [{
        "metadata": {"regulation_id": "32024R9999", "article_id": "Article 99", "law_type": "REGULATION", "parse_confidence": 1.0},
        "scope": {"regions": ["EU"], "industrial_sectors": ["F41"], "node_types": ["PRODUCER"]},
        "timeline": {}, "mechanism": {"target_variable": "compliance_status", "action_type": "MANDATE",
            "operator": "REQUIRES", "non_numeric_obligation": "keep a product provenance register",
            "evidence_anchor": "Article 99"},
        "enforcement": {"strictness": "SOFT", "penalty_factor": 0.2},
        "summary": "Operators shall keep a product provenance register."}]}).model_dump_json()
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        if (stage == "draft" and not kwargs["use_pro"]) or (stage == "review" and kwargs["use_pro"]):
            if isinstance(error, Exception):
                raise error
            return '{"rules": [{"metadata": {}}]}'
        return draft
    monkeypatch.setattr("src.main.get_completion_with_json", completion)
    fetches = []
    def fetch(url, **kwargs):
        fetches.append(url)
        return raw, url, "application/xml", 200
    report = run_ingestion("EURLEX_CELLAR", limit=1, settings=settings, ledger=ledger, store=store,
        archive=archive, discover=lambda *a: [item], fetch=fetch)
    assert report["status"] == "failed", report
    assert report["committed"] == 0 and report["source_errors"]
    assert ledger.cursor("EURLEX_CELLAR") is None
    assert not store._canonical_batches
    work = ledger.work_items(report["run_id"])[0]
    assert work["status"] == "failed"
    document = SourceDocument.model_validate_json(work["document_json"])
    assert archive.get(document.raw_uri, document.content_sha256) == raw
    monkeypatch.setattr("src.main.get_completion_with_json", lambda **kw: draft)
    retry = run_ingestion("EURLEX_CELLAR", limit=1, settings=settings, ledger=ledger, store=store,
        archive=archive, run_id=report["run_id"], fetch=fetch)
    assert retry["status"] == "committed", retry
    assert store._canonical_facts
    assert ledger.cursor("EURLEX_CELLAR") == item["cursor"]
    assert len(fetches) == 1


def test_explicit_successful_empty_law_extraction_is_allowed(tmp_path, monkeypatch):
    from src.main import LawIngestionPipeline
    monkeypatch.setattr("src.main.get_completion_with_json", lambda **kw: '{"rules": []}')
    pipeline = LawIngestionPipeline(store=DualLayerStore(tmp_path / "db"))
    assert pipeline.extract_rules_from_full_text("A provision with no economic rules.", "32024R9999") == []


def test_eurostat_survey_selections_request_and_parse_exactly_the_registered_series():
    """Builder price expectations and material shortages come from one survey request (official 2025 response)."""
    registry = load_data_sources_registry()["sources"]["EUROSTAT_STS"]
    items = {item["upstream_id"]: item for item in _stats_discover("EUROSTAT_STS", "2025-01", "2025-12", 10)}
    survey = items["ei_bsbu_m_r2"]
    assert "indic=BS-CPE-BAL" in survey["url"] and "indic=BS-FLBA5-PC" in survey["url"]
    expected = {s["provider_series_key"]: s["measurement_kind"] for s in registry["context_series"].values()}
    assert set(survey["required_series_keys"]) == set(expected)
    raw = (FIXTURES / "eurostat_ei_bsbu_m_r2_de_2025_official.json").read_bytes()
    doc = SourceDocument.from_content("EUROSTAT_STS", survey["canonical_url"], "Eurostat", raw, "survey-replay",
                                      data_origin="test", available_at=FETCHED_AT)
    result = _make_stats_parser("EUROSTAT_STS")(survey, raw, doc)
    assert {d.series_id: d.measurement_kind for d in result["series"]} == expected
    assert len([o for o in result["observations"] if o.value is not None]) == 24
