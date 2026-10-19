"""Bounded pilot, failed queue work and restart regressions."""
import json
from types import SimpleNamespace

import pytest

from scripts.ops import ingest
from src.config import Settings
from src.ingestion import FetchError, run_ingestion
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger


@pytest.fixture
def operation(tmp_path):
    settings = Settings(mode="test", data_dir=tmp_path, lancedb_path=tmp_path / "db", sqlite_path=tmp_path / "jobs.sqlite", cache_dir=tmp_path / "cache", temp_dir=tmp_path / "temp", disk_floor_bytes=0)
    return settings, IngestionJobLedger(settings.sqlite_path), DualLayerStore(settings.lancedb_path)


def test_pilot_caps_legal_news_and_total_statistical_series(operation, monkeypatch):
    settings, ledger, store = operation
    limits = {}
    def collect(source_id, **kwargs):
        limits[source_id] = kwargs["limit"]
        return {"status": "committed"}
    monkeypatch.setattr(ingest, "run_ingestion", collect)
    monkeypatch.setattr("src.config.load_data_sources_registry", lambda: {
        "sources": {source: {"enabled": True} for source in ingest.VERIFIED_PILOT_SOURCES}})
    report = ingest.run_pilot(limit=1000, settings=settings, ledger=ledger, store=store)
    assert limits["EURLEX_CELLAR"] <= 20
    assert limits["OFFICIAL_NEWS"] <= 300
    assert sum(limits[key] for key in ("DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "BUNDESBANK_SDMX", "SMARD_DE")) <= 40
    assert report["expansion_allowed"] is False
    assert report["quality_gates"]["held_out_human_review"]["status"] == "pending"


def test_pilot_collects_only_enabled_supported_sources(operation, monkeypatch):
    settings, ledger, store = operation
    called = []
    monkeypatch.setattr("src.config.load_data_sources_registry", lambda: {"sources": {
        "DESTATIS_GENESIS": {"enabled": True}, "EURLEX_CELLAR": {"enabled": False},
        "UNSUPPORTED": {"enabled": True}}})
    def collect(source, **kwargs):
        called.append(source)
        return {"status": "committed"}
    monkeypatch.setattr(ingest, "run_ingestion", collect)
    report = ingest.run_pilot(settings=settings, ledger=ledger, store=store)
    assert called == ["DESTATIS_GENESIS"]
    assert set(report["sources"]) == {"DESTATIS_GENESIS"}
    assert report["status"] == "committed"
    assert report["expansion_allowed"] is False


@pytest.mark.parametrize("dry_run", [True, False])
def test_pilot_without_enabled_supported_sources_is_blocked(operation, monkeypatch, dry_run):
    settings, ledger, store = operation
    monkeypatch.setattr("src.config.load_data_sources_registry", lambda: {"sources": {
        "EURLEX_CELLAR": {"enabled": False}, "UNSUPPORTED": {"enabled": True}}})
    monkeypatch.setattr(ingest, "run_ingestion", lambda *a, **kw: pytest.fail("disabled source executed"))
    report = ingest.run_pilot(dry_run=dry_run, settings=settings, ledger=ledger, store=store)
    assert report["status"] == "blocked"
    assert report["sources"] == {}
    assert report["total_committed"] == 0
    assert report["expansion_allowed"] is False


def test_pilot_failed_spend_stays_in_shared_budget(operation, monkeypatch):
    settings, ledger, store = operation
    settings.extraction_budget_usd = 1
    remaining = []
    def collect(source_id, **kwargs):
        auth = kwargs["budget_run_id"]
        remaining.append(1 - ledger.spent_cost(auth))
        amount = .6
        ledger.ensure_run(auth, source_id, kwargs["settings"].extraction_budget_usd)
        ledger.reserve_cost(auth, source_id, amount, 1, price_known=True)
        ledger.settle_cost(auth, source_id, amount)
        raise FetchError("failure after billed extraction")
    monkeypatch.setattr(ingest, "run_ingestion", collect)
    report = ingest.run_pilot(settings=settings, ledger=ledger, store=store)
    assert report["total_cost_usd"] == pytest.approx(.6)
    assert remaining[0] == 1
    assert all(value == pytest.approx(.4) for value in remaining[1:])


def test_cli_status_includes_persistent_ai_spending(operation, monkeypatch, capsys):
    settings, ledger, store = operation
    ledger.configure_spending_limit(3)
    ledger.ensure_run("billed-run", "EURLEX_CELLAR", 1)
    ledger.reserve_cost("billed-run", "request-1", .5, 1, price_known=True)
    ledger.settle_cost("billed-run", "request-1", .4)
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "get_production_overview", lambda: {"committed_batches": 0})
    assert ingest.main(["--status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ai_spending"] == ledger.spending_summary()


@pytest.mark.parametrize("failure", ["exception", "failed", "committed_with_errors"])
def test_queue_failure_is_durable_and_not_silently_requeued(operation, monkeypatch, failure):
    settings, ledger, store = operation
    job_id = ledger.submit_job("EURLEX_CELLAR", "queued", {"limit": 1})
    def collect(*args, **kwargs):
        if failure == "exception":
            raise FetchError("bad source")
        return {"status": failure}
    monkeypatch.setattr(ingest, "run_ingestion", collect)
    report = ingest.consume_queue(settings=settings, ledger=ledger, store=store)
    assert report["failed_count"] == 1
    assert report["status"] == "partial_failure"
    assert ledger.get_job(job_id)["status"] == "failed"
    assert ledger.pending_jobs() == []


def test_failed_batch_retries_only_failed_work_then_commits_once(operation):
    settings, ledger, store = operation
    fetched, parsed = [], []
    items = [{"item_id": key, "url": "https://example.com/" + key, "cursor": key} for key in ("a", "b")]
    def discover(*args):
        return items
    def fetch(url, **kwargs):
        fetched.append(url)
        return b"evidence", url, "text/plain", 200
    def parse(item, content, document):
        parsed.append(item["item_id"])
        if item["item_id"] == "b" and parsed.count("b") == 1:
            raise ValueError("failed extraction")
        return {"status": "accepted"}
    kwargs = dict(settings=settings, ledger=ledger, store=store, discover=discover, fetch=fetch, parse=parse, run_id="retry", limit=2)
    first = run_ingestion("TEST", **kwargs)
    assert first["status"] == "failed"
    assert not [batch for batch in store._canonical_batches.values() if batch.status == "committed"]
    assert ledger.cursor("TEST") is None
    kwargs["ledger"] = IngestionJobLedger(ledger.path)
    result = run_ingestion("TEST", **kwargs)
    assert result["status"] == "committed"
    assert ledger.cursor("TEST") == "b"
    assert len(fetched) == 2
    assert parsed == ["a", "b", "b"]
    assert len([batch for batch in store._canonical_batches.values() if batch.status == "committed"]) == 1


def test_run_resume_cannot_change_budget_authorization(operation):
    settings, ledger, store = operation
    settings.extraction_budget_usd = 1
    kwargs = dict(settings=settings, ledger=ledger, store=store, discover=lambda *args: [], parse=lambda *args: {}, run_id="child", limit=1)
    run_ingestion("TEST", budget_run_id="pilot", **kwargs)
    with pytest.raises(BudgetError):
        run_ingestion("TEST", budget_run_id="new-pool", **kwargs)
    # Omitting the shared ID on restart must preserve the existing authorization.
    run_ingestion("TEST", **kwargs)
    assert ledger.bind_budget_run("child", "TEST", 1) == "pilot"


def test_single_statistics_payload_cannot_bypass_series_cap(operation):
    settings, ledger, store = operation
    series = [dict(series_id=str(index), source_id="TEST", dataset_id="stats", provider_series_key=str(index), metric_name="rate", unit="PERCENT") for index in range(9)]
    report = run_ingestion("TEST", limit=1, settings=settings, ledger=ledger, store=store,
        discover=lambda *args: [{"url": "https://example.com/stats"}],
        fetch=lambda url, **kwargs: (b"stats", url, "text/plain", 200),
        parse=lambda *args: {"status": "accepted", "series": series},
        max_series=8)
    assert report["status"] == "failed"
    assert report["source_errors"][0]["code"] == "PILOT_SERIES_CAP"
    assert not [batch for batch in store._canonical_batches.values() if batch.status == "committed"]
