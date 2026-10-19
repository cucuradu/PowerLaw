"""Re-fetching an unchanged value without a provider vintage must not create a new revision."""
from datetime import datetime, timezone

from src.ingestion import _reconfirm_unchanged_observations
from src.schemas.ingestion import BatchManifest, EvidenceLink, Observation, SourceDocument
from src.storage.dual_layer_store import DualLayerStore

SERIES = "61261-0002|test"


def _stage(store, tmp_path, batch_id, when, value, vintage=None):
    content = f"{batch_id}:{value}".encode()
    doc = SourceDocument.from_content("DESTATIS_GENESIS", "https://genesis.destatis.de/x", "Destatis", content,
                                      batch_id, data_origin="live", available_at=when)
    raw = tmp_path / doc.content_sha256
    raw.write_bytes(content)
    doc = doc.model_copy(update={"raw_uri": raw.as_uri()})
    obs = Observation(series_id=SERIES, period_start="2025-01-01", period_end_exclusive="2025-04-01", value=value,
                      unit="2021=100", source_unit="2021=100", vintage_id=vintage, available_at=when,
                      batch_id=batch_id, data_origin="live")
    link = EvidenceLink(assertion_version_id=obs.revision_id, assertion_kind="OBSERVATION",
                        document_version_id=doc.version_id, batch_id=batch_id)
    observations, evidence = _reconfirm_unchanged_observations(store, [obs], [link])
    from src.schemas.ingestion import SeriesDefinition
    series = [SeriesDefinition(series_id=SERIES, source_id="DESTATIS_GENESIS", dataset_id="61261-0002",
                               provider_series_key=SERIES, metric_name="ppi", unit="2021=100", frequency="QUARTERLY",
                               measurement_kind="PRODUCER_PRICE_INDEX", available_at=when, batch_id=batch_id)]
    store.stage_batch(BatchManifest(batch_id=batch_id, source_id="DESTATIS_GENESIS", data_origin="live"),
                      [doc], [], observations, evidence_links=evidence, entities=[], series=series)
    store.commit_batch(batch_id)
    return obs, observations, evidence


def _revisions(store):
    rows = store.query_observation_revisions(datetime(2030, 1, 1, tzinfo=timezone.utc), "system_as_of")
    return sorted((o.value, store._available(o)) for o in rows if o.series_id == SERIES)


def test_unchanged_refetch_reconfirms_existing_revision(tmp_path):
    store = DualLayerStore(storage_dir=str(tmp_path / "store"))
    first, _, _ = _stage(store, tmp_path, "b1", datetime(2026, 1, 10, tzinfo=timezone.utc), 132.6)
    _, kept, evidence = _stage(store, tmp_path, "b2", datetime(2026, 2, 10, tzinfo=timezone.utc), 132.6)
    assert kept == []
    assert [link.assertion_version_id for link in evidence] == [first.revision_id]
    # One revision, first available at the original fetch.
    assert _revisions(store) == [(132.6, datetime(2026, 1, 10, tzinfo=timezone.utc))]


def test_changed_value_is_a_new_revision(tmp_path):
    store = DualLayerStore(storage_dir=str(tmp_path / "store"))
    _stage(store, tmp_path, "b1", datetime(2026, 1, 10, tzinfo=timezone.utc), 132.6)
    _, kept, _ = _stage(store, tmp_path, "b2", datetime(2026, 2, 10, tzinfo=timezone.utc), 132.9)
    assert len(kept) == 1
    assert [value for value, _ in _revisions(store)] == [132.6, 132.9]


def test_provider_vintage_confirmation_stays_a_revision(tmp_path):
    store = DualLayerStore(storage_dir=str(tmp_path / "store"))
    _stage(store, tmp_path, "b1", datetime(2026, 1, 10, tzinfo=timezone.utc), 2.41, vintage="2026-01-01T10:00:00+01:00")
    _, kept, _ = _stage(store, tmp_path, "b2", datetime(2026, 2, 10, tzinfo=timezone.utc), 2.41, vintage="2026-02-01T10:00:00+01:00")
    assert len(kept) == 1
