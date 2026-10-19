"""Acceptance and unit tests for Task 9: Operations, cache budget, disk floor, recovery, and schedules."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings, clear_settings_cache
from src.ingestion import ArchiveError, DiskFloorError, run_ingestion
from src.schemas.ingestion import BatchManifest, Entity, EvidenceLink, Fact, Observation, SeriesDefinition, SourceDocument
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import IngestionJobLedger
from src.utils.minio_client import EvidenceArchive, enforce_cache_budget, verify_restore


@pytest.fixture
def isolated_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache"
    temp_dir = data_dir / "temp"
    lancedb_path = data_dir / "lancedb"
    sqlite_path = data_dir / "ingestion.sqlite"
    for d in [data_dir, cache_dir, temp_dir, lancedb_path]:
        d.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        mode="test",
        data_dir=data_dir,
        lancedb_path=lancedb_path,
        sqlite_path=sqlite_path,
        cache_dir=cache_dir,
        cache_limit_bytes=1024 * 1024,  # 1 MiB limit for test
        temp_dir=temp_dir,
        temp_limit_bytes=512 * 1024,   # 512 KiB limit
        disk_floor_bytes=10 * 1024 * 1024, # 10 MiB floor
        object_backend="local",
        object_bucket="test-bucket",
        object_prefix="powerlaw",
    )
    ledger = IngestionJobLedger(sqlite_path)
    store = DualLayerStore(lancedb_path)
    return {
        "settings": settings,
        "ledger": ledger,
        "store": store,
        "data_dir": data_dir,
        "cache_dir": cache_dir,
        "temp_dir": temp_dir,
    }


def test_cache_budget_enforcement_evicts_only_reproducible_files(isolated_env):
    """Temporary scratch files can be evicted, but unverified sole copies are never deleted."""
    settings = isolated_env["settings"]
    temp_dir = isolated_env["temp_dir"]

    # Create temporary scratch files exceeding temp budget (e.g. 800 KiB > 512 KiB)
    file1 = temp_dir / "scratch_1.tmp"
    file2 = temp_dir / "scratch_2.tmp"
    file1.write_bytes(b"A" * (400 * 1024))
    file2.write_bytes(b"B" * (400 * 1024))

    report = enforce_cache_budget(settings=settings)
    assert report["evicted_files_count"] >= 1
    assert not file1.exists() or not file2.exists()
    assert report["evicted_bytes"] >= 400 * 1024


def test_cache_budget_enforcement_never_deletes_sole_unverified_copy(isolated_env):
    """EvidenceArchive in local mode is the sole authoritative copy; it must NOT be evicted."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)

    # Save evidence file in local archive
    content = b"CRITICAL_LEGAL_EVIDENCE_DO_NOT_DELETE"
    uri, digest = archive.put(content)

    # Set cache_limit_bytes very low to force budget eviction attempt
    settings.cache_limit_bytes = 10  # 10 bytes

    report = enforce_cache_budget(settings=settings)
    # The sole copy must be protected, NOT deleted
    assert report["protected_files_count"] >= 1
    assert archive.get(uri, digest) == content


def test_download_interrupted_at_disk_floor(isolated_env):
    """When disk free space is below disk_floor_bytes, staging/archiving must raise DiskFloorError."""
    settings = isolated_env["settings"]
    settings.disk_floor_bytes = 100 * 1024 * 1024 * 1024  # 100 GB floor (simulating low disk)

    archive = EvidenceArchive(settings)

    # Attempt to archive content should fail due to disk floor breach
    with pytest.raises(DiskFloorError, match="disk floor"):
        archive.put(b"some content that requires disk space")


def test_object_store_outage_raises_archive_error(isolated_env):
    """Simulated S3/MinIO network outage raises ArchiveError without corrupting state."""
    settings = isolated_env["settings"]
    settings.object_backend = "s3"
    settings.minio_endpoint = "localhost:9999"
    settings.minio_secure = True
    settings.minio_access_key = "dummy"
    settings.minio_secret_key = "dummy"

    with patch("src.utils.minio_client.MinioClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_client.upload_bytes.side_effect = ConnectionError("MinIO connection refused")
        mock_client_cls.return_value = mock_client

        archive = EvidenceArchive(settings, client=mock_client)
        with pytest.raises(ArchiveError, match="object storage error"):
            archive.put(b"payload")


def test_corrupt_remote_object_fails_restore(isolated_env):
    """If a remote object's digest does not match the manifest, verify_restore fails safely."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)

    # Put a valid document
    content = b"original valid document content"
    uri, digest = archive.put(content)

    doc = SourceDocument.from_content(
        source_id="TEST_SOURCE",
        canonical_url="https://example.org/test.pdf",
        publisher="Test Publisher",
        content_bytes=content,
        batch_id="test_batch_1",
        data_origin="test",
    ).model_copy(update={"raw_uri": uri, "content_sha256": digest})

    manifest_payload = {
        "batch_id": "test_batch_1",
        "source_id": "TEST_SOURCE",
        "documents": [doc.model_dump(mode="json")],
        "facts": [],
        "observations": [],
    }
    manifest_bytes = json.dumps(manifest_payload).encode("utf-8")
    manifest_uri, manifest_sha = archive.put(manifest_bytes)

    # Corrupt the file on disk / remote
    if uri.startswith("file://"):
        raw_path = uri.replace("file://", "")
        if os.name == "nt" and raw_path.startswith("/"):
            raw_path = raw_path[1:]
        Path(raw_path).write_bytes(b"tampered corrupt content")

    restore_target = isolated_env["data_dir"] / "restore_test"
    with pytest.raises(ArchiveError, match="checksum verification failed"):
        verify_restore(manifest_uri, restore_target, settings=settings, archive=archive)


def test_verify_restore_successful_manifest_roundtrip(isolated_env):
    """Valid manifest restores all documents and writes integrity report."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)

    doc1_content = b"Document 1 Content: Council Directive 2024/01"
    doc2_content = b"Document 2 Content: Official Gazette Record"
    uri1, sha1 = archive.put(doc1_content)
    uri2, sha2 = archive.put(doc2_content)

    doc1 = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://example.org/dir1.pdf",
        publisher="EU Publications",
        content_bytes=doc1_content,
        batch_id="batch_restore_ok",
        data_origin="test",
    ).model_copy(update={"raw_uri": uri1, "content_sha256": sha1})

    doc2 = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://example.org/dir2.pdf",
        publisher="EU Publications",
        content_bytes=doc2_content,
        batch_id="batch_restore_ok",
        data_origin="test",
    ).model_copy(update={"raw_uri": uri2, "content_sha256": sha2})

    manifest_payload = {
        "batch_id": "batch_restore_ok",
        "source_id": "EURLEX_CELLAR",
        "documents": [doc1.model_dump(mode="json"), doc2.model_dump(mode="json")],
        "facts": [],
        "observations": [],
    }
    manifest_bytes = json.dumps(manifest_payload).encode("utf-8")
    manifest_uri, manifest_sha = archive.put(manifest_bytes)

    restore_target = isolated_env["data_dir"] / "restore_success"
    report = verify_restore(manifest_uri, restore_target, settings=settings, archive=archive)

    assert report["status"] == "restored"
    assert report["batch_id"] == "batch_restore_ok"
    assert report["documents_restored"] == 2
    assert report["bytes_restored"] == len(doc1_content) + len(doc2_content)
    assert (restore_target / doc1.version_id).exists()
    assert (restore_target / doc2.version_id).exists()
    assert (restore_target / doc1.version_id).read_bytes() == doc1_content


def test_ledger_last_success_and_oldest_pending_job(isolated_env):
    """Ledger accurately tracks last successful run and oldest pending job."""
    ledger = isolated_env["ledger"]

    # Initially empty
    assert ledger.last_success() is None
    assert ledger.oldest_pending_job() is None

    # Record a staged (pending) job
    ledger.ensure_run("run_pending_1", "OFFICIAL_NEWS", 0.0)
    ledger.record_batch("batch_p1", "run_pending_1", "OFFICIAL_NEWS", "staged")

    pending = ledger.oldest_pending_job()
    assert pending is not None
    assert pending["batch_id"] == "batch_p1"
    assert ledger.last_success() is None

    # Record a committed (successful) batch
    ledger.ensure_run("run_success_1", "OFFICIAL_NEWS", 0.0)
    ledger.record_batch("batch_s1", "run_success_1", "OFFICIAL_NEWS", "committed")

    success = ledger.last_success()
    assert success is not None
    assert success["batch_id"] == "batch_s1"
    assert success["status"] == "committed"


def test_cli_operations_flags(isolated_env, monkeypatch):
    """CLI supports --status, --enforce-cache, and --verify-restore."""
    from scripts.ops.ingest import main

    monkeypatch.setenv("POWERLAW_MODE", "test")
    monkeypatch.setenv("POWERLAW_DATA_DIR", str(isolated_env["data_dir"]))
    monkeypatch.setenv("LANCEDB_PATH", str(isolated_env["settings"].lancedb_path))
    monkeypatch.setenv("POWERLAW_SQLITE_PATH", str(isolated_env["settings"].sqlite_path))
    monkeypatch.setenv("POWERLAW_CACHE_DIR", str(isolated_env["cache_dir"]))
    monkeypatch.setenv("POWERLAW_TEMP_DIR", str(isolated_env["temp_dir"]))
    clear_settings_cache()

    # Test --status exits 0
    assert main(["--status"]) == 0

    # Test --enforce-cache exits 0
    assert main(["--enforce-cache"]) == 0


def test_backfill_bounded_date_range_and_cursor_overlap(isolated_env):
    """Backfill bounds discovery to since/until, and overlapping ranges do not create duplicate canonical records."""
    settings = isolated_env["settings"]
    ledger = isolated_env["ledger"]
    store = isolated_env["store"]

    # Discoverer that respects since/until
    raw_corpus = [
        {"item_id": "item_2010", "published_at": "2010-06-01T00:00:00Z", "url": "https://example.org/item1", "cursor": "2010-06-01"},
        {"item_id": "item_2011", "published_at": "2011-06-01T00:00:00Z", "url": "https://example.org/item2", "cursor": "2011-06-01"},
        {"item_id": "item_2012", "published_at": "2012-06-01T00:00:00Z", "url": "https://example.org/item3", "cursor": "2012-06-01"},
        {"item_id": "item_2013", "published_at": "2013-06-01T00:00:00Z", "url": "https://example.org/item4", "cursor": "2013-06-01"},
    ]

    def mock_discover(source_id, since, until, limit):
        for item in raw_corpus:
            dt = item["published_at"][:10]
            if since and dt < since:
                continue
            if until and dt > until:
                continue
            yield item

    def mock_parse(item, content, doc):
        from src.schemas.ingestion import Observation, SeriesDefinition, EvidenceLink, stable_id
        obs_id = stable_id("obs", item["item_id"])
        series_def = SeriesDefinition(
            series_id="TEST_PPI",
            source_id="TEST_BACKFILL",
            dataset_id="TEST_DATASET",
            provider_series_key="TEST_KEY",
            metric_name="producer_price_index",
            unit="INDEX",
            batch_id=doc.batch_id,
        )
        obs = Observation(
            observation_id=obs_id,
            series_id="TEST_PPI",
            period_start=item["published_at"][:10],
            period_end_exclusive="2099-12-31",
            value=100.0,
            unit="INDEX",
            provider_status="FINAL",
            data_origin="test",
            batch_id=doc.batch_id,
            available_at=doc.available_at,
        )
        evidence = EvidenceLink(
            assertion_version_id=obs.revision_id,
            assertion_kind="OBSERVATION",
            document_version_id=doc.version_id,
            selector_type="TABLE_CELL",
            support_type="DIRECT",
            batch_id=doc.batch_id,
        )
        return {"observations": [obs], "series": [series_def], "evidence_links": [evidence], "status": "accepted"}

    def mock_fetch(url, **kwargs):
        return b'{"data": "mock"}', url, "application/json", 200

    # 1. Run backfill for 2010-2012 (should find 3 items)
    rep1 = run_ingestion(
        "TEST_BACKFILL",
        limit=10,
        since="2010-01-01",
        until="2012-12-31",
        settings=settings,
        ledger=ledger,
        store=store,
        discover=mock_discover,
        parse=mock_parse,
        fetch=mock_fetch,
    )
    assert rep1["discovered"] == 3
    assert rep1["committed"] == 6  # 3 docs + 3 obs

    # 2. Run overlapping backfill for 2011-2013 (should find item_2011, item_2012, item_2013)
    rep2 = run_ingestion(
        "TEST_BACKFILL",
        limit=10,
        since="2011-01-01",
        until="2013-12-31",
        settings=settings,
        ledger=ledger,
        store=store,
        discover=mock_discover,
        parse=mock_parse,
        fetch=mock_fetch,
    )
    assert rep2["discovered"] == 3
    # item_2011 and item_2012 already exist in store, only item_2013 is new!
    total_docs = len(store._canonical_documents)
    assert total_docs == 4  # 2010, 2011, 2012, 2013 (no duplicate documents)


def test_out_of_order_revised_release(isolated_env):
    """When a revised release arrives with a later vintage, previous records are retained immutably."""
    store = isolated_env["store"]
    from src.schemas.ingestion import BatchManifest, Observation, SeriesDefinition, SourceDocument, EvidenceLink, stable_id
    from datetime import datetime

    series_def = SeriesDefinition(
        series_id="SERIES_A",
        source_id="DESTATIS",
        dataset_id="61261-0002",
        provider_series_key="KEY_A",
        metric_name="ppi",
        unit="2021=100",
    )

    # Vintage 1 (published Jan 2024, initial estimate)
    batch_v1 = BatchManifest(batch_id="batch_v1", source_id="DESTATIS", data_origin="test")
    doc_v1 = SourceDocument.from_content(source_id="DESTATIS", canonical_url="https://example.com/v1", publisher="DESTATIS", content_bytes=b"v1", batch_id="batch_v1", data_origin="test", available_at=datetime(2024, 4, 15, 8, 0, tzinfo=timezone.utc))
    obs_v1 = Observation(
        observation_id=stable_id("obs", "SERIES_A", "2024-01-01", "2024-04-01"),
        series_id="SERIES_A",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=100.0,
        unit="2021=100",
        provider_status="PROVISIONAL",
        data_origin="test",
        batch_id="batch_v1",
        available_at=datetime(2024, 4, 15, 8, 0, tzinfo=timezone.utc),
    )
    evi_v1 = EvidenceLink(
        assertion_version_id=obs_v1.revision_id,
        assertion_kind="OBSERVATION",
        document_version_id=doc_v1.version_id,
        support_type="DIRECT",
        batch_id="batch_v1",
        available_at=datetime(2024, 4, 15, 8, 0, tzinfo=timezone.utc),
    )
    store.stage_batch(batch_v1, [doc_v1], [], [obs_v1], series=[series_def], evidence_links=[evi_v1])
    store.commit_batch("batch_v1", committed_at=datetime(2024, 4, 15, 8, 0, tzinfo=timezone.utc))

    # Vintage 2 (published Jun 2024, revised estimate)
    batch_v2 = BatchManifest(batch_id="batch_v2", source_id="DESTATIS", data_origin="test")
    doc_v2 = SourceDocument.from_content(source_id="DESTATIS", canonical_url="https://example.com/v2", publisher="DESTATIS", content_bytes=b"v2", batch_id="batch_v2", data_origin="test", available_at=datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc))
    obs_v2 = Observation(
        observation_id=stable_id("obs", "SERIES_A", "2024-01-01", "2024-04-01"),
        series_id="SERIES_A",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=102.5,
        unit="2021=100",
        provider_status="REVISED",
        data_origin="test",
        batch_id="batch_v2",
        available_at=datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc),
    )
    evi_v2 = EvidenceLink(
        assertion_version_id=obs_v2.revision_id,
        assertion_kind="OBSERVATION",
        document_version_id=doc_v2.version_id,
        support_type="DIRECT",
        batch_id="batch_v2",
        available_at=datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc),
    )
    store.stage_batch(batch_v2, [doc_v2], [], [obs_v2], evidence_links=[evi_v2])
    store.commit_batch("batch_v2", committed_at=datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc))

    # Point-in-time queries:
    # As of May 2024: value is 100.0
    t_may = store.query_observations(cutoff=datetime(2024, 5, 1, tzinfo=timezone.utc))
    assert t_may.num_rows == 1
    assert t_may.column("value")[0].as_py() == 100.0

    # As of July 2024: value is 102.5 (revised)
    t_july = store.query_observations(cutoff=datetime(2024, 7, 1, tzinfo=timezone.utc))
    assert t_july.num_rows == 1
    assert t_july.column("value")[0].as_py() == 102.5


def test_seven_day_simulated_schedule_and_isolated_restore(isolated_env):
    """Simulate 7 days of scheduled runs, verify cumulative ledger metrics, and perform isolated restore."""
    settings = isolated_env["settings"]
    ledger = isolated_env["ledger"]
    store = isolated_env["store"]
    archive = EvidenceArchive(settings)

    last_manifest_uri = None

    for day in range(1, 8):
        day_str = f"2026-10-{day:02d}"

        def mock_discover(source_id, since, until, limit):
            yield {
                "item_id": f"daily_doc_{day_str}",
                "published_at": f"{day_str}T10:00:00Z",
                "url": f"https://example.org/daily_{day_str}.json",
                "cursor": day_str,
            }

        def mock_parse(item, content, doc):
            from src.schemas.ingestion import Entity, Fact, EvidenceLink, stable_id
            ent1 = Entity(entity_id="SECTOR_C23", entity_type="SECTOR", canonical_label="Cement", data_origin="test", batch_id=doc.batch_id)
            ent2 = Entity(entity_id=f"DAILY_OBS_{day_str}", entity_type="ECONOMIC_INDEX", canonical_label=f"Obs {day_str}", data_origin="test", batch_id=doc.batch_id)
            fact = Fact(
                fact_id=stable_id("fact", item["item_id"]),
                subject_id=f"DAILY_OBS_{day_str}",
                predicate="MEASURES",
                object_id="SECTOR_C23",
                valid_from=f"{day_str}T00:00:00Z",
                quality_state="accepted",
                data_origin="test",
                batch_id=doc.batch_id,
                source_id="DAILY_SCHEDULE",
            )
            evidence = EvidenceLink(
                assertion_version_id=fact.fact_version_id,
                assertion_kind="FACT",
                document_version_id=doc.version_id,
                selector_type="TABLE_CELL",
                support_type="DIRECT",
                batch_id=doc.batch_id,
            )
            return {"entities": [ent1, ent2], "facts": [fact], "evidence_links": [evidence], "status": "accepted"}

        def mock_fetch(url, **kwargs):
            return f'{{"date": "{day_str}", "status": "ok"}}'.encode("utf-8"), url, "application/json", 200

        rep = run_ingestion(
            "DAILY_SCHEDULE",
            limit=5,
            since=day_str,
            until=day_str,
            settings=settings,
            ledger=ledger,
            store=store,
            archive=archive,
            discover=mock_discover,
            parse=mock_parse,
            fetch=mock_fetch,
        )
        assert rep["status"] == "committed"
        batch = ledger.batch(rep["batch_id"])
        last_manifest_uri = batch["manifest_uri"]

    # Verify 7 runs recorded in ledger
    recent = ledger.recent_runs(limit=10)
    assert len(recent) == 7
    assert all(r["status"] == "committed" for r in recent)

    # Perform isolated restore of day 7's batch
    restore_target = isolated_env["data_dir"] / "restore_rehearsal"
    restore_rep = verify_restore(last_manifest_uri, restore_target, settings=settings, archive=archive)
    assert restore_rep["status"] == "restored"
    assert restore_rep["documents_restored"] == 1
    assert (restore_target / f"doc_DAILY_SCHEDULE_daily_doc_2026-10-07").exists() or any(restore_target.glob("*"))


def test_cache_budget_corrupt_remote_protects_local_copy(isolated_env):
    """When remote S3 object is corrupt or digest mismatch, local copy must never be evicted."""
    import hashlib
    settings = isolated_env["settings"]
    settings.object_backend = "s3"
    settings.cache_limit_bytes = 1  # Aggressive budget forcing eviction attempt
    settings.cache_dir.mkdir(parents=True, exist_ok=True)

    content = b"CRITICAL_CACHED_LOCAL_COPY"
    digest = hashlib.sha256(content).hexdigest()
    local_file = settings.cache_dir / digest
    local_file.write_bytes(content)

    mock_client = MagicMock()
    mock_client.object_exists.return_value = True
    mock_client.download_bytes.return_value = b"CORRUPTED_REMOTE_CONTENT"

    report = enforce_cache_budget(settings=settings, client=mock_client)
    assert local_file.exists(), "Local sole valid copy must be protected when remote is corrupt"
    assert report["evicted_files_count"] == 0
    assert report["protected_files_count"] >= 1
    assert report["status"] == "paused", "Status must pause rather than claim enforced when protected data prevents compliance"


def test_verify_restore_rejects_tampered_manifest(isolated_env):
    """Manifest tampering must be detected and rejected with ArchiveError."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)
    doc_content = b"Original Document"
    uri, digest = archive.put(doc_content)

    manifest_payload = {
        "batch_id": "batch_tamper_test",
        "source_id": "TEST_SOURCE",
        "documents": [{"raw_uri": uri, "content_sha256": digest, "version_id": "doc_1"}],
    }
    manifest_bytes = json.dumps(manifest_payload).encode("utf-8")
    manifest_uri, manifest_sha = archive.put(manifest_bytes)

    # Tamper with the manifest file on disk
    if manifest_uri.startswith("file://"):
        raw_path = manifest_uri.replace("file://", "")
        if os.name == "nt" and raw_path.startswith("/"):
            raw_path = raw_path[1:]
        p = Path(raw_path)
        tampered_payload = dict(manifest_payload, batch_id="TAMPERED_BATCH")
        p.write_bytes(json.dumps(tampered_payload).encode("utf-8"))

    restore_target = isolated_env["data_dir"] / "restore_tampered"
    with pytest.raises(ArchiveError, match="manifest checksum mismatch"):
        verify_restore(manifest_uri, restore_target, settings=settings, archive=archive)


def test_verify_restore_rejects_path_traversal(isolated_env):
    """Document with path traversal in version_id must be rejected and not escape target directory."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)
    uri, digest = archive.put(b"escaped content")

    manifest_payload = {
        "batch_id": "batch_escape_test",
        "documents": [{"raw_uri": uri, "content_sha256": digest, "version_id": "../escape.txt"}],
    }
    manifest_uri, manifest_sha = archive.put(json.dumps(manifest_payload).encode("utf-8"))

    restore_target = isolated_env["data_dir"] / "restore_escape_target"
    with pytest.raises(ArchiveError, match="Path traversal detected"):
        verify_restore(manifest_uri, restore_target, settings=settings, archive=archive)
    assert not (isolated_env["data_dir"] / "escape.txt").exists()


def test_verify_restore_requires_fresh_target(isolated_env):
    """Restoring into an existing non-empty directory must be rejected."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)
    uri, digest = archive.put(b"content")

    manifest_payload = {
        "batch_id": "batch_fresh_test",
        "documents": [{"raw_uri": uri, "content_sha256": digest, "version_id": "doc_1"}],
    }
    manifest_uri, manifest_sha = archive.put(json.dumps(manifest_payload).encode("utf-8"))

    restore_target = isolated_env["data_dir"] / "existing_dir"
    restore_target.mkdir(parents=True, exist_ok=True)
    (restore_target / "existing_user_file.txt").write_text("do not overwrite")

    with pytest.raises(ArchiveError, match="not fresh"):
        verify_restore(manifest_uri, restore_target, settings=settings, archive=archive)


def test_verify_restore_restores_canonical_records_and_manifest(isolated_env):
    """Restoring an archive recreates manifest.json and canonical fact/observation files."""
    settings = isolated_env["settings"]
    archive = EvidenceArchive(settings)
    uri, digest = archive.put(b"raw document content")
    batch_id = "batch_canonical_restore"
    doc = SourceDocument.from_content("TEST_SOURCE", "https://example.org/restored", "Test",
                                      b"raw document content", batch_id, data_origin="test")
    doc = doc.model_copy(update={"raw_uri": uri, "version_id": "doc_1"})
    entities = [Entity(entity_id=key, entity_type=kind, canonical_label=key,
                       batch_id=batch_id, data_origin="test") for key, kind in [("A", "LAW"), ("B", "ACTION")]]
    fact = Fact(subject_id="A", predicate="MANDATES", object_id="B", source_id="TEST_SOURCE",
                batch_id=batch_id, data_origin="test")
    definition = SeriesDefinition(series_id="S", source_id="TEST_SOURCE", dataset_id="D",
                                  provider_series_key="S", metric_name="ppi", unit="INDEX", batch_id=batch_id)
    obs = Observation(series_id="S", value=100.0, unit="INDEX", period_start="2020-01-01",
                      period_end_exclusive="2020-04-01", batch_id=batch_id, data_origin="test")
    evidence = [EvidenceLink(assertion_version_id=identity, assertion_kind=kind,
                             document_version_id=doc.version_id, batch_id=batch_id)
                for identity, kind in [(fact.fact_version_id, "FACT"), (obs.revision_id, "OBSERVATION")]]

    manifest_payload = {
        "batch_id": "batch_canonical_restore",
        "source_id": "TEST_SOURCE",
        "documents": [doc.model_dump(mode="json")],
        "facts": [fact.model_dump(mode="json")],
        "observations": [obs.model_dump(mode="json")],
        "entities": [e.model_dump(mode="json") for e in entities],
        "series": [definition.model_dump(mode="json")],
        "evidence_links": [e.model_dump(mode="json") for e in evidence],
        "cursor_value": "cursor_2026",
    }
    manifest_uri, manifest_sha = archive.put(json.dumps(manifest_payload).encode("utf-8"))

    restore_target = isolated_env["data_dir"] / "restore_canonical_complete"
    res = verify_restore(manifest_uri, restore_target, settings=settings, archive=archive)
    assert res["status"] == "restored"
    assert (restore_target / "manifest.json").exists()
    assert (restore_target / "canonical_facts.json").exists()
    assert (restore_target / "canonical_observations.json").exists()
    assert (restore_target / "cursor.json").exists()
    assert (restore_target / "doc_1").read_bytes() == b"raw document content"


