from pathlib import Path
from types import SimpleNamespace
import json

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ingestion import ArchiveError, FetchError, bounded_fetch, resume_batch, run_ingestion
from src.schemas.ingestion import BatchManifest, SourceDocument, Entity, Fact, EvidenceLink
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger, JobLeaseError
from src.utils.minio_client import EvidenceArchive


class FakeResponse:
    def __init__(self, status=200, content=b"body", headers=None, location=None):
        self.status_code = status
        self._content = content
        self.headers = headers or {"Content-Type": "text/html"}
        if location:
            self.headers["Location"] = location

    def iter_content(self, chunk_size=0):
        yield self._content

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, *args, **kwargs):
        return next(self.responses)


class TimeoutSession:
    def get(self, *args, **kwargs):
        raise requests.Timeout("timed out")


def test_transient_retry_after_and_oversize(monkeypatch):
    delays = []
    monkeypatch.setattr("src.ingestion.time.sleep", delays.append)
    response = FakeResponse(503, headers={"Retry-After": "3", "Content-Type": "text/html"})
    final = FakeResponse(200, b"ok")
    content, _, _, _ = bounded_fetch("https://example.com/feed", session=FakeSession([response, final]), retries=1)
    assert content == b"ok"
    assert delays == [3.0]

    oversized = FakeResponse(200, b"12345", headers={"Content-Length": "5", "Content-Type": "text/plain"})
    with pytest.raises(FetchError, match="maximum size"):
        bounded_fetch("https://example.com/feed", session=FakeSession([oversized]), max_bytes=4)

    with pytest.raises(FetchError, match="bounded retries"):
        bounded_fetch("https://example.com/feed", session=TimeoutSession(), retries=1)


def test_private_redirect_and_html_disguised_as_pdf():
    redirect = FakeResponse(302, location="http://127.0.0.1/private")
    with pytest.raises(FetchError, match="private or local"):
        bounded_fetch("https://example.com/file.pdf", session=FakeSession([redirect]))

    html_pdf = FakeResponse(200, b"<!doctype html><title>error</title>", headers={"Content-Type": "application/pdf"})
    with pytest.raises(FetchError, match="disguised as PDF"):
        bounded_fetch("https://example.com/file.pdf", session=FakeSession([html_pdf]))


def test_blocked_stream_obeys_total_timeout_and_closes_response(monkeypatch):
    class BlockingResponse(FakeResponse):
        def __init__(self):
            super().__init__(200, b"", headers={"Content-Type": "text/plain"})
            self.closed = False

        def iter_content(self, chunk_size=0):
            import time
            time.sleep(0.2)
            yield b"late"

        def close(self):
            self.closed = True

    response = BlockingResponse()
    with pytest.raises(FetchError, match="total acquisition timeout"):
        bounded_fetch("https://example.com/feed", session=FakeSession([response]), total_timeout=0.02)
    assert response.closed


def test_ledger_lease_and_budget_are_persistent(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    token = ledger.acquire_lease("source", "run-1")
    with pytest.raises(JobLeaseError):
        ledger.acquire_lease("source", "run-2")
    ledger.reserve_cost("run-1", "request-1", 0.4, 1.0, price_known=True)
    with pytest.raises(BudgetError):
        ledger.reserve_cost("run-1", "request-2", 0.7, 1.0, price_known=True)
    with pytest.raises(BudgetError):
        ledger.reserve_cost("run-1", "request-3", 0.01, 1.0, price_known=False)
    ledger.release_lease("source", "run-1", token)
    reopened = IngestionJobLedger(tmp_path / "jobs.sqlite")
    assert reopened.spent_cost("run-1") == 0.4


def test_runner_archives_commits_replays_and_advances_cursor_after_success(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True)
    items = [{"url": "https://example.com/a", "cursor": "a", "publisher": "Example"}]

    def discover(source_id, since, until, limit):
        return items

    def fetch(url, **kwargs):
        return b"source", url, "text/plain", 200

    def parse(item, content, document):
        return {"status": "accepted", "accepted_count": 1}

    first = run_ingestion("TEST", limit=1, settings=settings, discover=discover, parse=parse, fetch=fetch, archive=EvidenceArchive(settings))
    assert first["status"] == "committed"
    assert IngestionJobLedger(settings.sqlite_path).cursor("TEST") == "a"

    second = run_ingestion("TEST", limit=1, settings=settings, discover=discover, parse=parse, fetch=fetch, archive=EvidenceArchive(settings))
    assert second["status"] == "committed"
    assert second["committed"] == first["committed"]


def test_resume_commits_staged_batch(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True)
    ledger = IngestionJobLedger(settings.sqlite_path)
    store = DualLayerStore(settings.lancedb_path)
    content = b"staged"
    document = SourceDocument.from_content("TEST", "https://example.com/staged", "Example", content, "batch-1", data_origin="test")
    archive = EvidenceArchive(settings)
    from src.ingestion import archive_document
    document = archive_document(document, content, archive=archive)
    batch = BatchManifest(batch_id="batch-1", source_id="TEST", data_origin="test")
    store.stage_batch(batch, [document], [], [])
    ledger.record_batch("batch-1", "run-1", "TEST", "staged")

    with pytest.raises(Exception, match="durable manifest"):
        resume_batch("batch-1", settings=settings, ledger=ledger, store=store)
    assert store._canonical_batches["batch-1"].status == "staged"


def test_new_polls_get_new_run_ids_and_unchanged_documents_deduplicate(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    item = {"url": "https://example.com/a", "cursor": "a", "publisher": "Example"}
    parse = lambda item, content, document: {"status": "accepted", "accepted_count": 1}
    first = run_ingestion("TEST", limit=1, settings=settings, discover=lambda *args: [item], parse=parse, fetch=lambda *args, **kwargs: (b"same", args[0], "text/plain", 200))
    second = run_ingestion("TEST", limit=1, settings=settings, discover=lambda *args: [item], parse=parse, fetch=lambda *args, **kwargs: (b"same", args[0], "text/plain", 200))
    assert first["run_id"] != second["run_id"]
    assert len(DualLayerStore(settings.lancedb_path)._canonical_documents) == 1


def test_resume_verifies_manifest_and_raw_object(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    store = DualLayerStore(settings.lancedb_path)
    archive = EvidenceArchive(settings)
    parse = lambda item, content, document: {"status": "accepted", "accepted_count": 1}
    original = run_ingestion("TEST", limit=1, settings=settings, discover=lambda *args: [{"url": "https://example.com/a", "cursor": "a"}], parse=parse, fetch=lambda *args, **kwargs: (b"same", args[0], "text/plain", 200), store=store, archive=archive)
    record = IngestionJobLedger(settings.sqlite_path).batch(original["batch_id"])
    assert record["archive_sha256"]
    payload = archive.get(record["manifest_uri"], record["archive_sha256"])
    assert b"raw_uri" in payload and b"content_sha256" in payload


def test_production_rejects_unregistered_source_before_fetch(tmp_path):
    settings = SimpleNamespace(mode="production", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    called = []
    with pytest.raises(FetchError, match="not registered"):
        run_ingestion("NOT_REGISTERED", limit=1, settings=settings, discover=lambda *args: called.append(True), parse=lambda *args: {"status": "accepted"})
    assert not called


def test_error_redaction_and_lazy_discovery(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    consumed = []
    def discover(*args):
        for index in range(1000):
            consumed.append(index)
            yield {"url": f"https://example.com/{index}", "cursor": str(index)}
    report = run_ingestion("TEST", limit=1, settings=settings, discover=discover, parse=lambda *args: {"status": "accepted"}, fetch=lambda *args, **kwargs: (_ for _ in ()).throw(FetchError("https://example.com/x?key=secret-token")))
    assert len(consumed) == 1
    assert "secret-token" not in json.dumps(report)
    assert "[REDACTED]" in json.dumps(report)


def test_empty_parser_result_fails_without_publication(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    report = run_ingestion("TEST", limit=1, settings=settings, discover=lambda *args: [{"url": "https://example.com/a"}], parse=lambda *args: {}, fetch=lambda *args, **kwargs: (b"body", args[0], "text/plain", 200))
    assert report["status"] == "failed"
    assert report["accepted"] == 0
    assert not Path(settings.lancedb_path, "canonical_batches.parquet").exists()


def test_parser_state_is_reused_after_commit_failure(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    class CrashStore(DualLayerStore):
        def commit_batch(self, batch_id):
            raise RuntimeError("injected commit crash")
    calls = []
    discover = lambda *args: [{"url": "https://example.com/a", "cursor": "a"}]
    parse = lambda *args: {"status": "accepted", "accepted_count": 1}
    fetch = lambda *args, **kwargs: (calls.append(True) or (b"body", args[0], "text/plain", 200))
    run_id = "run-recover"
    with pytest.raises(RuntimeError):
        run_ingestion("TEST", limit=1, settings=settings, run_id=run_id, discover=discover, parse=parse, fetch=fetch, store=CrashStore(settings.lancedb_path))
    result = run_ingestion("TEST", limit=1, settings=settings, run_id=run_id, discover=lambda *args: (_ for _ in ()).throw(AssertionError("rediscovered")), parse=parse, fetch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("refetched")), store=DualLayerStore(settings.lancedb_path))
    assert result["status"] == "committed"
    assert len(calls) == 1


def test_streaming_oversize_fails_before_full_consumption():
    response = MagicMock(status_code=200, headers={"Content-Type": "text/plain"})
    consumed = []
    def chunks(**kwargs):
        for i in range(20):
            consumed.append(i)
            yield b"x" * 10
    response.iter_content.side_effect = chunks
    session = MagicMock()
    session.get.return_value = response
    with patch("src.ingestion._host_is_public", return_value=True):
        with pytest.raises(FetchError, match="maximum size"):
            bounded_fetch("https://example.org/a", session=session, max_bytes=15)
    assert len(consumed) == 2
    assert response.close.called


def test_first_publication_corruption_aborts_and_fails_resume(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    ledger = IngestionJobLedger(settings.sqlite_path)
    store = DualLayerStore(settings.lancedb_path)
    archive = EvidenceArchive(settings)

    def parser(item, content, doc):
        from urllib.parse import urlparse, unquote
        import os
        raw_p = unquote(urlparse(doc.raw_uri).path)
        if os.name == "nt" and len(raw_p) >= 3 and raw_p[0] == "/" and raw_p[2] == ":":
            raw_p = raw_p[1:]
        Path(raw_p).write_bytes(b"corrupted raw content")
        ent_a = Entity(entity_id="SECTOR_A", entity_type="SECTOR", canonical_label="A", data_origin="test", batch_id=doc.batch_id)
        ent_b = Entity(entity_id="SECTOR_B", entity_type="SECTOR", canonical_label="B", data_origin="test", batch_id=doc.batch_id)
        fact = Fact(subject_id="SECTOR_A", predicate="ABOUT", object_id="SECTOR_B", source_id="TEST", available_at=doc.available_at, data_origin="test", batch_id=doc.batch_id)
        ev = EvidenceLink(assertion_version_id=fact.fact_version_id, document_version_id=doc.version_id, quoted_text="good", batch_id=doc.batch_id)
        return {"status": "accepted", "accepted_count": 1, "entities": [ent_a, ent_b], "facts": [fact], "evidence_links": [ev]}

    report = run_ingestion("TEST", limit=1, settings=settings, ledger=ledger, store=store, archive=archive,
                           discover=lambda *a: [{"url": "https://example.org/doc", "cursor": "A"}],
                           parse=parser, fetch=lambda url, **k: (b"good", url, "text/plain", 200))
    assert report["status"] == "failed"
    assert len(store.get_edges()) == 0

    with pytest.raises(ArchiveError):
        resume_batch(report["batch_id"], settings=settings, ledger=ledger, store=store)

    with ledger._connection() as db:
        job_status = db.execute("SELECT status FROM jobs WHERE run_id=?", (report["run_id"],)).fetchone()[0]
    assert job_status == "interrupted"


def test_fetch_metadata_crash_preserves_document_and_prevents_refetch(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    ledger = IngestionJobLedger(settings.sqlite_path)
    fetches = []
    dates = []

    def fetch(url, **kwargs):
        fetches.append(url)
        return b"raw content", url, "text/plain", 200

    def parser(item, content, doc):
        dates.append(doc.available_at.isoformat())
        if len(dates) == 1:
            raise RuntimeError("crash after raw archive before parse completed")
        return {"status": "accepted", "accepted_count": 1}

    args = dict(source_id="TEST", limit=1, settings=settings, ledger=ledger, run_id="run_crash_meta",
                discover=lambda *a: [{"url": "https://example.org/doc"}], parse=parser, fetch=fetch)

    with pytest.raises(RuntimeError, match="crash after raw archive"):
        run_ingestion(**args)

    work_item = ledger.work_items("run_crash_meta")[0]
    assert work_item["document_json"] is not None
    assert work_item["status"] == "fetched"

    result = run_ingestion(**args)
    assert result["status"] == "committed"
    assert len(fetches) == 1
    assert dates[0] == dates[1]


def test_partial_resume_with_failed_items_does_not_advance_cursor(tmp_path):
    settings = SimpleNamespace(mode="test", sqlite_path=tmp_path / "jobs.sqlite", lancedb_path=tmp_path / "lancedb", cache_dir=tmp_path / "cache", object_backend="local", object_prefix="evidence", object_bucket="bucket", minio_endpoint=None, minio_access_key=None, minio_secret_key=None, minio_secure=True, extraction_budget_usd=0.0)
    ledger = IngestionJobLedger(settings.sqlite_path)
    store = DualLayerStore(settings.lancedb_path)
    items = [
        {"item_id": "failed", "url": "https://example.org/failed", "cursor": "A"},
        {"item_id": "ok", "url": "https://example.org/ok", "cursor": "B"},
    ]

    def fetch(url, **kwargs):
        if url.endswith("failed"):
            raise FetchError("injected failure")
        return b"ok", url, "text/plain", 200

    report = run_ingestion("TEST", limit=2, settings=settings, ledger=ledger, store=store,
                           discover=lambda *a: items, parse=lambda *a: {"status": "accepted", "accepted_count": 1}, fetch=fetch)
    assert report["status"] == "failed"
    assert ledger.cursor("TEST") is None
    assert not [batch for batch in store._canonical_batches.values() if batch.status == "committed"]
    assert ledger.batch(report["batch_id"]) is None
    assert ledger.cursor("TEST") is None
    assert len(ledger.work_items(report["run_id"], ("failed",))) == 1


def test_single_writer_across_sources_enforced(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    token_a = ledger.acquire_lease("SOURCE_A", "runA")
    assert token_a is not None
    with pytest.raises(JobLeaseError, match="operational worker lease already held"):
        ledger.acquire_lease("SOURCE_B", "runB")
    ledger.release_lease("SOURCE_A", "runA", token_a)
    token_b = ledger.acquire_lease("SOURCE_B", "runB")
    assert token_b is not None

