"""Offline portable restore acceptance; every sink belongs to tmp_path."""
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Settings
from src.ingestion import ArchiveError, DiskFloorError
from src.schemas.ingestion import BatchManifest, Entity, EvidenceLink, Fact, Observation, SeriesDefinition, SourceDocument
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import IngestionJobLedger
from src.utils.minio_client import EvidenceArchive, verify_restore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("restore tests must remain offline")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def backup(tmp_path):
    cfg = Settings(mode="test", data_dir=tmp_path, lancedb_path=tmp_path / "db",
                   sqlite_path=tmp_path / "jobs.sqlite", cache_dir=tmp_path / "cache",
                   temp_dir=tmp_path / "temp", disk_floor_bytes=0, object_backend="local")
    archive = EvidenceArchive(cfg)
    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    raw = b"verified source evidence"
    raw_uri, _ = archive.put(raw)
    doc = SourceDocument.from_content("TEST", "https://example.invalid/source", "Test", raw,
                                      "restore-batch", data_origin="live", available_at=now)
    doc = doc.model_copy(update={"raw_uri": raw_uri})
    entities = [Entity(entity_id=key, entity_type=kind, canonical_label=key,
                       batch_id=doc.batch_id, data_origin="live", created_at=now)
                for key, kind in [("law:a", "LAW"), ("rule:a", "ACTION")]]
    fact = Fact(subject_id="law:a", predicate="MANDATES", object_id="rule:a", source_id="TEST",
                batch_id=doc.batch_id, data_origin="live", available_at=now, recorded_at=now)
    series = SeriesDefinition(series_id="series:a", source_id="TEST", dataset_id="D",
                              provider_series_key="A", metric_name="ppi", unit="INDEX",
                              batch_id=doc.batch_id, available_at=now)
    observations = [Observation(series_id=series.series_id, period_start="2019-10-01",
                                period_end_exclusive="2020-01-01", value=value, unit="INDEX",
                                vintage_id=str(year), available_at=datetime(year, 1, 1, tzinfo=timezone.utc),
                                batch_id=doc.batch_id, data_origin="live")
                    for year, value in [(2020, 100), (2021, 102)]]
    links = [EvidenceLink(assertion_version_id=identity, assertion_kind=kind,
                          document_version_id=doc.version_id, quoted_text=raw.decode(),
                          batch_id=doc.batch_id, available_at=now)
             for identity, kind in [(fact.fact_version_id, "FACT")] +
             [(obs.revision_id, "OBSERVATION") for obs in observations]]
    batch = BatchManifest(batch_id=doc.batch_id, source_id="TEST", data_origin="live",
                          discovered_count=1, fetched_count=1, accepted_count=3, created_at=now)
    payload = dict(manifest_version="2", batch_id=doc.batch_id, source_id="TEST", data_origin="live",
                   batch_metadata=batch.model_dump(mode="json"), cursor_key="releases", cursor_value="cursor-A",
                   documents=[doc.model_dump(mode="json")], facts=[fact.model_dump(mode="json")],
                   observations=[obs.model_dump(mode="json") for obs in observations],
                   entities=[e.model_dump(mode="json") for e in entities],
                   series=[series.model_dump(mode="json")], evidence_links=[e.model_dump(mode="json") for e in links])
    return cfg, archive, payload, raw


def save(archive, payload):
    return archive.put(json.dumps(payload).encode())[0]


def test_live_restore_is_portable_and_complete_after_archive_disappears(backup, tmp_path):
    cfg, archive, payload, raw = backup
    uri = save(archive, payload)
    original = json.loads(json.dumps(payload))
    target = tmp_path / "restored" / "evidence"
    store = DualLayerStore(tmp_path / "restored-db")
    ledger = IngestionJobLedger(tmp_path / "restored-jobs.sqlite")
    report = verify_restore(uri, target, settings=cfg, archive=archive, store=store, ledger=ledger)
    # A cold reader must use only the restored bytes, never the source archive.
    archive.root.rename(tmp_path / "source-archive-unavailable")
    cold = DualLayerStore(store.storage_dir)
    document = next(iter(cold._canonical_documents.values()))
    restored_archive = EvidenceArchive(cfg.model_copy(update={"cache_dir": target.parent}))
    assert restored_archive.get(document.raw_uri, document.content_sha256) == raw
    assert document.version_id == payload["documents"][0]["version_id"]
    assert document.raw_uri != payload["documents"][0]["raw_uri"]
    assert cold.query_observations(datetime(2030, 1, 1, tzinfo=timezone.utc)).to_pylist()[0]["value"] == 102
    assert len(cold._canonical_observations) == 2
    assert len(cold.project_graph(datetime(2030, 1, 1, tzinfo=timezone.utc))["edges"]) == 1
    assert len(cold._canonical_evidence) == 3
    assert cold._canonical_batches[payload["batch_id"]].accepted_count == 3
    assert ledger.cursor("TEST", "releases") == "cursor-A"
    assert report["status"] == "restored"
    assert payload == original


def test_restore_accounts_for_metadata_before_allocating(backup, tmp_path, monkeypatch):
    cfg, archive, payload, raw = backup
    uri = save(archive, payload)
    monkeypatch.setattr("src.utils.minio_client.shutil.disk_usage", lambda path: SimpleNamespace(free=100 + len(raw) + 1))
    target = tmp_path / "too-full"
    with pytest.raises(DiskFloorError):
        verify_restore(uri, target, settings=cfg.model_copy(update={"disk_floor_bytes": 100}), archive=archive)
    assert not target.exists() or not list(target.iterdir())


@pytest.mark.parametrize("mutation", ["byte_count", "negative_bytes", "bad_fact", "missing_evidence", "missing_entity", "mixed_origin", "wrong_kind", "duplicate_document", "unknown_version", "wrong_counts", "missing_metadata", "missing_identity"])
def test_invalid_restore_never_publishes_partial_target(backup, tmp_path, mutation):
    cfg, archive, payload, raw = backup
    if mutation == "byte_count":
        payload["documents"][0]["byte_count"] += 1
    elif mutation == "negative_bytes":
        payload["documents"][0]["byte_count"] = -1
    elif mutation == "bad_fact":
        payload["facts"][0].pop("subject_id")
    elif mutation == "missing_evidence":
        payload["evidence_links"] = []
    elif mutation == "missing_entity":
        payload["entities"] = []
    elif mutation == "mixed_origin":
        payload["documents"][0]["data_origin"] = "demo"
    elif mutation == "wrong_kind":
        payload["evidence_links"][0]["assertion_kind"] = "OBSERVATION"
    elif mutation == "duplicate_document":
        payload["documents"] *= 2
    elif mutation == "wrong_counts":
        payload["batch_metadata"]["fetched_count"] = 100
    elif mutation == "missing_metadata":
        payload.pop("batch_metadata")
    elif mutation == "missing_identity":
        payload["facts"][0].pop("fact_version_id")
    else:
        payload["manifest_version"] = "999"
    target = tmp_path / mutation
    with pytest.raises((ArchiveError, ValueError)):
        verify_restore(save(archive, payload), target, settings=cfg, archive=archive)
    assert not target.exists() or not list(target.iterdir())


def test_restore_refuses_nonempty_sink_before_writes(backup, tmp_path):
    cfg, archive, payload, raw = backup
    store = DualLayerStore(tmp_path / "occupied-db")
    store.stage_batch(BatchManifest(batch_id="existing", source_id="TEST", data_origin="test"), [], [], [])
    store.commit_batch("existing")
    target = tmp_path / "target"
    with pytest.raises((ArchiveError, ValueError), match="fresh|empty"):
        verify_restore(save(archive, payload), target, settings=cfg, archive=archive, store=store)
    assert not target.exists() or not list(target.iterdir())
    assert set(DualLayerStore(store.storage_dir)._canonical_batches) == {"existing"}


@pytest.mark.parametrize("origin", ["live", "test"])
def test_actual_runner_manifest_restores_without_defaulting_origin(backup, tmp_path, origin, monkeypatch):
    from src.ingestion import run_ingestion
    cfg, archive, payload, raw = backup
    # Only fetch/parse boundaries are injected; manifest construction is the real runner.
    cfg = cfg.model_copy(update={"mode": "production" if origin == "live" else "test"})
    url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/sts_copi_q"
    monkeypatch.setattr("src.ingestion.load_data_sources_registry", lambda: {"sources": {"EUROSTAT_STS": {
        "enabled": True, "rights_checked": True, "access_fee": 0, "requires_paid_subscription": False,
        "endpoints": {"statistics": url}, "cursor_key": "releases"}}})
    report = run_ingestion("EUROSTAT_STS", limit=1, settings=cfg, archive=archive,
                           discover=lambda *args: [{"url": url, "cursor": "release-1"}],
                           fetch=lambda *args, **kwargs: (raw, url, "application/json", 200),
                           parse=lambda *args: {"status": "needs_review"})
    assert report["status"] == "committed"
    ledger = IngestionJobLedger(cfg.sqlite_path)
    record = ledger.batch(report["batch_id"])
    manifest = json.loads(archive.get(record["manifest_uri"], record["archive_sha256"]))
    assert manifest["manifest_version"] == "2"
    assert manifest["data_origin"] == origin
    target = tmp_path / "actual-restore"
    verify_restore(record["manifest_uri"], target, settings=cfg, archive=archive)
    assert next(iter(DualLayerStore(target / "canonical")._canonical_documents.values())).data_origin == origin


def test_restore_rolls_back_all_fresh_sinks_when_last_promotion_fails(backup, tmp_path, monkeypatch):
    cfg, archive, payload, raw = backup
    store = DualLayerStore(tmp_path / "fresh-db")
    ledger = IngestionJobLedger(tmp_path / "fresh-jobs.sqlite")
    target = tmp_path / "final-target"
    replace = os.replace
    def fail_target(source, destination):
        if Path(destination) == target:
            raise OSError("injected final promotion failure")
        return replace(source, destination)
    monkeypatch.setattr("src.utils.minio_client.os.replace", fail_target)
    with pytest.raises(OSError, match="injected final"):
        verify_restore(save(archive, payload), target, settings=cfg, archive=archive, store=store, ledger=ledger)
    assert not target.exists()
    assert not store._canonical_batches
    assert not DualLayerStore(store.storage_dir)._canonical_batches
    assert ledger.cursor("TEST", "releases") is None
    assert ledger.batch(payload["batch_id"]) is None
    assert archive.get(payload["documents"][0]["raw_uri"], payload["documents"][0]["content_sha256"]) == raw
