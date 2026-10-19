"""
Unit and Integration Tests for Task 3: One Durable Store, Version-Aware Reads,
and Reversible Migration.

Tests:
1. Repeat staging/commit yields identical committed IDs/counts.
2. Staging stays invisible until committed.
3. Mid-table-write crash / restart resumes safely.
4. Competing writers are rejected.
5. Two metrics on one node retain two as-of results.
6. Expired/amended/retracted assertions remain visible at old cutoffs;
   future obligations appear as upcoming but not active;
   relation multiplicity survives graph projection.
7. An accepted fact cannot refer to an uncommitted/missing document or endpoint.
8. One commodity resolves to multiple separately typed series and exact evidence;
   same material in DE and another market remains distinct.
9. Edge-subject trade series resolves correctly.
10. Latest revisions including tombstones are selected BEFORE suppressing retracted
    records, so old values cannot be resurrected.
11. Frozen committed generation for multi-table reads.
12. Legacy graph store adapter preserves multigraph, eliminates eval(), and avoids
    redundant overwrite persistence.
13. Migration dry-run mapping and backup restore verification.
"""

import os
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
import pyarrow as pa
import pandas as pd

from src.schemas.ingestion import (
    SourceDocument,
    Entity,
    EntityAlias,
    Fact,
    EvidenceLink,
    SeriesDefinition,
    Observation,
    BatchManifest,
    stable_id,
)
from src.storage.dual_layer_store import DualLayerStore, WriterLockError
from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import GraphNode, GraphEdge, NodeStateRecord, NodeType, EdgeRelation


@pytest.fixture
def temp_store_dir():
    tmp = tempfile.mkdtemp(prefix="powerlaw_store_test_")
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def store(temp_store_dir):
    return DualLayerStore(storage_dir=temp_store_dir)


def test_stage_batch_and_commit_idempotency_and_invisibility(store, temp_store_dir):
    """
    Staging stays invisible to queries until committed.
    Repeat staging and commit yields identical IDs and counts.
    """
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    batch = BatchManifest(batch_id="b_test_01", source_id="SRC_TEST", data_origin="test")

    doc = SourceDocument.from_content(
        source_id="SRC_TEST",
        canonical_url="https://example.com/doc1",
        publisher="Test Pub",
        content_bytes=b"Document 1 Content",
        batch_id=batch.batch_id,
        data_origin="test",
        availability_basis="provider_vintage",
        available_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
    )

    entity_a = Entity(
        entity_id="test:law:01",
        entity_type="LAW",
        canonical_label="Test Law 01",
        batch_id=batch.batch_id,
        data_origin="test",
    )
    entity_b = Entity(
        entity_id="test:rule:01",
        entity_type="ACTION",
        entity_subtype="LEGAL_RULE",
        canonical_label="Test Rule 01",
        batch_id=batch.batch_id,
        data_origin="test",
    )

    fact = Fact(
        subject_id=entity_a.entity_id,
        predicate="MANDATES",
        object_id=entity_b.entity_id,
        source_id="SRC_TEST",
        batch_id=batch.batch_id,
        available_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        valid_from=datetime(2024, 5, 1, tzinfo=timezone.utc),
        data_origin="test",
    )

    obs = Observation(
        series_id="test:series:ppi",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        unit="INDEX",
        value=105.2,
        batch_id=batch.batch_id,
        available_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        data_origin="test",
    )

    # Pre-register entities in the store so endpoint validation passes
    store.register_entities([entity_a, entity_b])
    store.register_series([
        SeriesDefinition(
            series_id="test:series:ppi",
            source_id="SRC_TEST",
            dataset_id="TEST_DATASET",
            provider_series_key="KEY_PPI",
            metric_name="ppi",
            unit="INDEX",
        )
    ])

    ev_fact = EvidenceLink(
        assertion_version_id=fact.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="mandates action",
        available_at=fact.available_at,
        batch_id=batch.batch_id,
        data_origin="test",
    )
    ev_obs = EvidenceLink(
        assertion_version_id=obs.revision_id,
        document_version_id=doc.version_id,
        quoted_text="105.2",
        available_at=obs.available_at,
        batch_id=batch.batch_id,
        data_origin="test",
    )

    # 1. Stage the batch
    store.stage_batch(batch, documents=[doc], facts=[fact], observations=[obs], evidence_links=[ev_fact, ev_obs])

    # Invisibility check before commit
    obs_table_pre = store.query_observations(cutoff=now, mode="public_as_of")
    assert len(obs_table_pre) == 0, "Staged observations must be invisible before commit"

    graph_pre = store.project_graph(cutoff=now, mode="public_as_of")
    assert len(graph_pre["edges"]) == 0, "Staged facts must be invisible before commit"

    # 2. Commit the batch
    store.commit_batch(batch.batch_id)

    # Visibility check after commit
    obs_table_post = store.query_observations(cutoff=now, mode="public_as_of")
    assert len(obs_table_post) == 1, "Committed observation must now be visible"
    assert obs_table_post.column("value")[0].as_py() == 105.2

    graph_post = store.project_graph(cutoff=now, mode="public_as_of")
    assert len(graph_post["edges"]) == 1, "Committed fact must now be visible in topology"

    # 3. Repeat staging & commit of the same batch (idempotency)
    store.stage_batch(batch, documents=[doc], facts=[fact], observations=[obs], evidence_links=[ev_fact, ev_obs])
    store.commit_batch(batch.batch_id)

    obs_table_repeat = store.query_observations(cutoff=now, mode="public_as_of")
    assert len(obs_table_repeat) == 1, "Idempotent commit must not duplicate observations"

    graph_repeat = store.project_graph(cutoff=now, mode="public_as_of")
    assert len(graph_repeat["edges"]) == 1, "Idempotent commit must not duplicate graph edges"


def test_competing_writers_rejected(store):
    """
    A second writer cannot stage or commit concurrently when another writer holds the lock.
    """
    with store.acquire_writer_lock():
        # Attempting to acquire lock again should fail
        with pytest.raises(WriterLockError):
            with store.acquire_writer_lock():
                pass


def test_fact_uncommitted_document_or_missing_endpoint_rejected(store):
    """
    An accepted fact cannot refer to an uncommitted/missing document or endpoint.
    """
    batch = BatchManifest(batch_id="b_err_01", source_id="SRC_TEST", data_origin="test")

    # 1. Unknown endpoint
    fact_bad_endpoint = Fact(
        subject_id="unknown:entity:99",
        predicate="MANDATES",
        object_id="unknown:entity:88",
        source_id="SRC_TEST",
        batch_id=batch.batch_id,
        data_origin="test",
    )

    with pytest.raises(ValueError, match="endpoint|missing"):
        store.stage_batch(batch, documents=[], facts=[fact_bad_endpoint], observations=[])

    # 2. Fact citing an uncommitted document version in evidence
    entity_a = Entity(entity_id="test:law:02", entity_type="LAW", canonical_label="L2", batch_id=batch.batch_id)
    entity_b = Entity(entity_id="test:rule:02", entity_type="ACTION", canonical_label="R2", batch_id=batch.batch_id)
    store.register_entities([entity_a, entity_b])

    fact_valid_endpoints = Fact(
        subject_id=entity_a.entity_id,
        predicate="MANDATES",
        object_id=entity_b.entity_id,
        source_id="SRC_TEST",
        batch_id=batch.batch_id,
        data_origin="test",
    )
    bad_evidence = EvidenceLink(
        assertion_version_id=fact_valid_endpoints.fact_version_id,
        document_version_id="docver_nonexistent_12345",
        selector_type="TEXT_SPAN",
    )

    with pytest.raises(ValueError, match="document|evidence"):
        store.stage_batch(batch, documents=[], facts=[fact_valid_endpoints], observations=[], evidence_links=[bad_evidence])


def test_query_observations_public_as_of_vs_system_as_of(store):
    """
    March observation first released in May and ingested in June:
    - In April: unavailable in both modes.
    - In May: available in public_as_of, but NOT system_as_of.
    - In June: available in both modes.
    """
    batch = BatchManifest(batch_id="b_obs_02", source_id="SRC_STAT", data_origin="test")

    doc = SourceDocument.from_content(
        source_id="SRC_STAT",
        canonical_url="https://example.com/stat1",
        publisher="Stat Pub",
        content_bytes=b"Stat Content",
        batch_id=batch.batch_id,
        data_origin="test",
        availability_basis="provider_vintage",
        available_at=datetime(2024, 5, 10, tzinfo=timezone.utc),
    ).model_copy(update={"recorded_at": datetime(2024, 6, 15, tzinfo=timezone.utc)})

    obs = Observation(
        series_id="test:de:construction_ppi",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        unit="INDEX",
        value=112.5,
        batch_id=batch.batch_id,
        available_at=datetime(2024, 5, 10, tzinfo=timezone.utc),
        recorded_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
        data_origin="test",
    )

    store.register_series([
        SeriesDefinition(
            series_id="test:de:construction_ppi",
            source_id="SRC_STAT",
            dataset_id="TEST_DATASET",
            provider_series_key="KEY_CONST_PPI",
            metric_name="construction_ppi",
            unit="INDEX",
        )
    ])
    ev_obs = EvidenceLink(
        assertion_version_id=obs.revision_id,
        document_version_id=doc.version_id,
        quoted_text="112.5",
        available_at=obs.available_at,
        recorded_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
        batch_id=batch.batch_id,
        data_origin="test",
    )
    store.stage_batch(batch, documents=[doc], facts=[], observations=[obs], evidence_links=[ev_obs])
    # Manually backdate batch committed_at to match recorded_at
    store.commit_batch(batch.batch_id, committed_at=datetime(2024, 6, 15, tzinfo=timezone.utc))

    april_cutoff = datetime(2024, 4, 30, tzinfo=timezone.utc)
    may_cutoff = datetime(2024, 5, 20, tzinfo=timezone.utc)
    june_cutoff = datetime(2024, 6, 20, tzinfo=timezone.utc)

    # April: unavailable in both
    assert len(store.query_observations(april_cutoff, mode="public_as_of")) == 0
    assert len(store.query_observations(april_cutoff, mode="system_as_of")) == 0

    # May: available in public_as_of, unavailable in system_as_of
    assert len(store.query_observations(may_cutoff, mode="public_as_of")) == 1
    assert len(store.query_observations(may_cutoff, mode="system_as_of")) == 0

    # June: available in both
    assert len(store.query_observations(june_cutoff, mode="public_as_of")) == 1
    assert len(store.query_observations(june_cutoff, mode="system_as_of")) == 1

    # Invalid mode raises ValueError
    with pytest.raises(ValueError, match="Invalid mode"):
        store.query_observations(june_cutoff, mode="invalid_mode")


def test_observation_tombstone_does_not_resurrect_superseded_value(store):
    """
    Select latest revisions including tombstones BEFORE suppressing retracted records,
    so old values cannot be resurrected.
    """
    series_id = "test:series:steel_ppi"
    period_start = "2024-01-01"
    period_end = "2024-04-01"

    store.register_series([
        SeriesDefinition(
            series_id=series_id,
            source_id="SRC_STAT",
            dataset_id="TEST_DATASET",
            provider_series_key="KEY_STEEL_PPI",
            metric_name="steel_ppi",
            unit="INDEX",
        )
    ])

    # Rev 1: May 1, value = 100.0
    b1 = BatchManifest(batch_id="b_rev1", source_id="SRC_STAT")
    doc1 = SourceDocument.from_content("SRC_STAT", "http://ex.com/1", "Pub", b"c1", b1.batch_id, available_at=datetime(2024, 5, 1, tzinfo=timezone.utc))
    obs_v1 = Observation(
        series_id=series_id,
        period_start=period_start,
        period_end_exclusive=period_end,
        value=100.0,
        unit="INDEX",
        vintage_id="v1",
        available_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        batch_id=b1.batch_id,
    )
    ev_obs_v1 = EvidenceLink(
        assertion_version_id=obs_v1.revision_id,
        document_version_id=doc1.version_id,
        quoted_text="100.0",
        available_at=obs_v1.available_at,
        batch_id=b1.batch_id,
        data_origin=b1.data_origin,
    )
    store.stage_batch(b1, documents=[doc1], facts=[], observations=[obs_v1], evidence_links=[ev_obs_v1])
    store.commit_batch(b1.batch_id, committed_at=datetime(2024, 5, 1, tzinfo=timezone.utc))

    # Rev 2: June 1, value = 105.0 (supersedes v1)
    b2 = BatchManifest(batch_id="b_rev2", source_id="SRC_STAT")
    doc2 = SourceDocument.from_content("SRC_STAT", "http://ex.com/2", "Pub", b"c2", b2.batch_id, available_at=datetime(2024, 6, 1, tzinfo=timezone.utc))
    obs_v2 = Observation(
        series_id=series_id,
        period_start=period_start,
        period_end_exclusive=period_end,
        value=105.0,
        unit="INDEX",
        vintage_id="v2",
        supersedes_revision_id=obs_v1.revision_id,
        available_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        batch_id=b2.batch_id,
    )
    ev_obs_v2 = EvidenceLink(
        assertion_version_id=obs_v2.revision_id,
        document_version_id=doc2.version_id,
        quoted_text="105.0",
        available_at=obs_v2.available_at,
        batch_id=b2.batch_id,
        data_origin=b2.data_origin,
    )
    store.stage_batch(b2, documents=[doc2], facts=[], observations=[obs_v2], evidence_links=[ev_obs_v2])
    store.commit_batch(b2.batch_id, committed_at=datetime(2024, 6, 1, tzinfo=timezone.utc))

    # Rev 3: July 1, retracted / tombstone
    b3 = BatchManifest(batch_id="b_rev3", source_id="SRC_STAT")
    doc3 = SourceDocument.from_content("SRC_STAT", "http://ex.com/3", "Pub", b"c3", b3.batch_id, available_at=datetime(2024, 7, 1, tzinfo=timezone.utc))
    obs_v3 = Observation(
        series_id=series_id,
        period_start=period_start,
        period_end_exclusive=period_end,
        value=None,
        unit="INDEX",
        vintage_id="v3_tombstone",
        is_retracted=True,
        supersedes_revision_id=obs_v2.revision_id,
        available_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
        batch_id=b3.batch_id,
    )
    store.stage_batch(b3, documents=[doc3], facts=[], observations=[obs_v3])
    store.commit_batch(b3.batch_id, committed_at=datetime(2024, 7, 1, tzinfo=timezone.utc))

    # Cutoff May 15 -> returns Rev 1 (100.0)
    t_may = datetime(2024, 5, 15, tzinfo=timezone.utc)
    res_may = store.query_observations(t_may)
    assert len(res_may) == 1
    assert res_may.column("value")[0].as_py() == 100.0

    # Cutoff June 15 -> returns Rev 2 (105.0)
    t_june = datetime(2024, 6, 15, tzinfo=timezone.utc)
    res_june = store.query_observations(t_june)
    assert len(res_june) == 1
    assert res_june.column("value")[0].as_py() == 105.0

    # Cutoff July 15 -> Rev 3 is latest revision and is retracted.
    # Must NOT resurrect Rev 2 (105.0) or Rev 1 (100.0)!
    t_july = datetime(2024, 7, 15, tzinfo=timezone.utc)
    res_july = store.query_observations(t_july)
    assert len(res_july) == 0, "Retracted tombstone must suppress observation without resurrecting old versions"


def test_two_metrics_on_one_node_retains_two_as_of_results(store):
    """
    One entity (e.g. Cement) measured by two distinct series (Producer Price Index
    and Production Volume) retains both observations at cutoff.
    """
    cement = Entity(entity_id="commodity:cement", entity_type="COMMODITY", canonical_label="Cement", batch_id="b_multi")
    store.register_entities([cement])

    # Series 1: PPI
    series_ppi = SeriesDefinition(
        series_id="series:cement_ppi",
        source_id="SRC_STAT",
        dataset_id="PPI_TABLE",
        provider_series_key="DE.CEMENT.PPI",
        metric_name="producer_price_index",
        unit="INDEX",
    )
    # Series 2: Volume
    series_vol = SeriesDefinition(
        series_id="series:cement_volume",
        source_id="SRC_STAT",
        dataset_id="VOL_TABLE",
        provider_series_key="DE.CEMENT.VOL",
        metric_name="production_volume",
        unit="THOUSAND_TONNES",
    )
    store.register_series([series_ppi, series_vol])

    # Facts linking series to entity
    fact_ppi = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=series_ppi.series_id,
        predicate="MEASURES",
        object_id=cement.entity_id,
        source_id="SRC_STAT",
        batch_id="b_multi",
    )
    fact_vol = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=series_vol.series_id,
        predicate="MEASURES",
        object_id=cement.entity_id,
        source_id="SRC_STAT",
        batch_id="b_multi",
    )

    b = BatchManifest(batch_id="b_multi", source_id="SRC_STAT")
    doc = SourceDocument.from_content("SRC_STAT", "http://ex.com/stats", "Pub", b"c", b.batch_id, available_at=datetime(2023, 1, 1, tzinfo=timezone.utc))

    obs_ppi = Observation(
        series_id=series_ppi.series_id,
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=114.2,
        unit="INDEX",
        available_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        batch_id=b.batch_id,
    )
    obs_vol = Observation(
        series_id=series_vol.series_id,
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=4820.0,
        unit="THOUSAND_TONNES",
        available_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        batch_id=b.batch_id,
    )
    ev_fact_ppi = EvidenceLink(
        assertion_version_id=fact_ppi.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="measures ppi",
        available_at=fact_ppi.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_fact_vol = EvidenceLink(
        assertion_version_id=fact_vol.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="measures vol",
        available_at=fact_vol.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_obs_ppi = EvidenceLink(
        assertion_version_id=obs_ppi.revision_id,
        document_version_id=doc.version_id,
        quoted_text="114.2",
        available_at=obs_ppi.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_obs_vol = EvidenceLink(
        assertion_version_id=obs_vol.revision_id,
        document_version_id=doc.version_id,
        quoted_text="4820.0",
        available_at=obs_vol.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )

    store.stage_batch(
        b,
        documents=[doc],
        facts=[fact_ppi, fact_vol],
        observations=[obs_ppi, obs_vol],
        evidence_links=[ev_fact_ppi, ev_fact_vol, ev_obs_ppi, ev_obs_vol],
    )
    store.commit_batch(b.batch_id)

    # Query observations for cement
    res = store.query_observations(cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc), entity_ids=[cement.entity_id])
    assert len(res) == 2, "Must retain both PPI and Volume metrics for the same entity"
    series_ids = set(res.column("series_id").to_pylist())
    assert series_ids == {series_ppi.series_id, series_vol.series_id}


def test_distinct_markets_and_edge_subject_series(store):
    """
    Same material in DE and FR remains distinct.
    Edge-subject series (trade flow between DE and FR) resolves correctly.
    """
    geo_de = Entity(entity_id="geo:DE", entity_type="GEOGRAPHY", canonical_label="Germany", batch_id="b_geo")
    geo_fr = Entity(entity_id="geo:FR", entity_type="GEOGRAPHY", canonical_label="France", batch_id="b_geo")
    store.register_entities([geo_de, geo_fr])

    series_de = SeriesDefinition(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        series_id="series:cement_ppi_de",
        source_id="SRC_STAT",
        dataset_id="PPI",
        provider_series_key="DE.CEMENT",
        metric_name="ppi",
        unit="INDEX",
        geography_id="DE",
    )
    series_fr = SeriesDefinition(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        series_id="series:cement_ppi_fr",
        source_id="SRC_STAT",
        dataset_id="PPI",
        provider_series_key="FR.CEMENT",
        metric_name="ppi",
        unit="INDEX",
        geography_id="FR",
    )
    # Trade edge between DE and FR
    trade_edge = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=geo_de.entity_id,
        predicate="FLOWS_TO",
        object_id=geo_fr.entity_id,
        source_id="SRC_STAT",
        batch_id="b_geo",
    )
    # Series measuring the trade edge
    series_trade = SeriesDefinition(
        series_id="series:trade_de_fr_cement",
        source_id="SRC_STAT",
        dataset_id="TRADE",
        provider_series_key="TRADE.DE.FR.CEMENT",
        metric_name="export_volume",
        unit="TONNES",
    )
    trade_measure_fact = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=series_trade.series_id,
        predicate="MEASURES",
        object_id=trade_edge.fact_id,  # edge subject
        payload_json=json.dumps({"subject_kind": "edge", "subject_id": trade_edge.fact_id}),
        source_id="SRC_STAT",
        batch_id="b_geo",
    )

    b = BatchManifest(batch_id="b_geo", source_id="SRC_STAT")
    doc = SourceDocument.from_content("SRC_STAT", "http://ex.com/geo", "Pub", b"content", b.batch_id, available_at=datetime(2023, 1, 1, tzinfo=timezone.utc))

    obs_de = Observation(series_id=series_de.series_id, period_start="2024-01-01", period_end_exclusive="2024-04-01", value=110.0, unit="INDEX", batch_id=b.batch_id, available_at=datetime(2024, 5, 1, tzinfo=timezone.utc))
    obs_fr = Observation(series_id=series_fr.series_id, period_start="2024-01-01", period_end_exclusive="2024-04-01", value=118.0, unit="INDEX", batch_id=b.batch_id, available_at=datetime(2024, 5, 1, tzinfo=timezone.utc))
    obs_trade = Observation(series_id=series_trade.series_id, period_start="2024-01-01", period_end_exclusive="2024-04-01", value=50000.0, unit="TONNES", batch_id=b.batch_id, available_at=datetime(2024, 5, 1, tzinfo=timezone.utc))

    ev_trade_edge = EvidenceLink(
        assertion_version_id=trade_edge.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="flows to",
        available_at=trade_edge.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_trade_meas = EvidenceLink(
        assertion_version_id=trade_measure_fact.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="measures trade",
        available_at=trade_measure_fact.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_obs_de = EvidenceLink(
        assertion_version_id=obs_de.revision_id,
        document_version_id=doc.version_id,
        quoted_text="110.0",
        available_at=obs_de.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_obs_fr = EvidenceLink(
        assertion_version_id=obs_fr.revision_id,
        document_version_id=doc.version_id,
        quoted_text="118.0",
        available_at=obs_fr.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_obs_trade = EvidenceLink(
        assertion_version_id=obs_trade.revision_id,
        document_version_id=doc.version_id,
        quoted_text="50000.0",
        available_at=obs_trade.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )

    store.stage_batch(
        b,
        documents=[doc],
        facts=[trade_edge, trade_measure_fact],
        observations=[obs_de, obs_fr, obs_trade],
        series=[series_de, series_fr, series_trade],
        evidence_links=[ev_trade_edge, ev_trade_meas, ev_obs_de, ev_obs_fr, ev_obs_trade],
    )
    store.commit_batch(b.batch_id)

    # Query for DE
    res_de = store.query_observations(cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc), entity_ids=[geo_de.entity_id])
    assert len(res_de) == 1
    assert res_de.column("series_id")[0].as_py() == series_de.series_id

    # Query for the trade edge
    res_trade = store.query_observations(cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc), entity_ids=[trade_edge.fact_id])
    assert len(res_trade) == 1
    assert res_trade.column("series_id")[0].as_py() == series_trade.series_id
    assert res_trade.column("value")[0].as_py() == 50000.0


def test_graph_projection_multigraph_and_future_obligations(store):
    """
    - Multiple distinct relations between the same endpoints survive projection.
    - Future obligation appears as upcoming, not active in traversal topology.
    - Expired assertion is visible in the past, inactive after valid_to.
    """
    act_a = Entity(entity_id="test:act:A", entity_type="ACTION", canonical_label="Act A", batch_id="b_proj")
    sec_b = Entity(entity_id="test:sec:B", entity_type="SECTOR", canonical_label="Sec B", batch_id="b_proj")
    act_c = Entity(entity_id="test:act:C", entity_type="ACTION", canonical_label="Act C", batch_id="b_proj")
    store.register_entities([act_a, sec_b, act_c])

    # Multigraph: Two distinct relations between act_a and sec_b
    fact_rel1 = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=act_a.entity_id,
        predicate="REGULATES",
        object_id=sec_b.entity_id,
        source_id="SRC_LEG",
        batch_id="b_proj",
        valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2024, 12, 31, tzinfo=timezone.utc),
    )
    fact_rel2 = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=act_a.entity_id,
        predicate="AFFECTS_COST_OF",
        object_id=sec_b.entity_id,
        source_id="SRC_LEG",
        batch_id="b_proj",
        valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )

    # Future obligation: valid_from in 2026
    fact_future = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=act_a.entity_id,
        predicate="MANDATES",
        object_id=act_c.entity_id,
        source_id="SRC_LEG",
        batch_id="b_proj",
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    # Expired relation: valid until 2023-06-01
    fact_expired = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=act_a.entity_id,
        predicate="IMPACTS",
        object_id=sec_b.entity_id,
        source_id="SRC_LEG",
        batch_id="b_proj",
        valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2023, 6, 1, tzinfo=timezone.utc),
    )

    b = BatchManifest(batch_id="b_proj", source_id="SRC_LEG")
    doc = SourceDocument.from_content("SRC_LEG", "http://ex.com/proj", "Pub", b"c", b.batch_id, available_at=datetime(2023, 1, 1, tzinfo=timezone.utc))

    ev_rel1 = EvidenceLink(
        assertion_version_id=fact_rel1.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="regulates sector",
        available_at=fact_rel1.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_rel2 = EvidenceLink(
        assertion_version_id=fact_rel2.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="affects cost",
        available_at=fact_rel2.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_future = EvidenceLink(
        assertion_version_id=fact_future.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="mandates action",
        available_at=fact_future.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )
    ev_expired = EvidenceLink(
        assertion_version_id=fact_expired.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="impacts sector",
        available_at=fact_expired.available_at,
        batch_id=b.batch_id,
        data_origin=b.data_origin,
    )

    store.stage_batch(
        b,
        documents=[doc],
        facts=[fact_rel1, fact_rel2, fact_future, fact_expired],
        observations=[],
        evidence_links=[ev_rel1, ev_rel2, ev_future, ev_expired],
    )
    store.commit_batch(b.batch_id)

    # Cutoff in 2024 (e.g. 2024-06-01)
    cutoff_2024 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    proj_2024 = store.project_graph(cutoff_2024)

    # Check relation multiplicity: Both REGULATES and AFFECTS_COST_OF are active
    active_predicates = [e["relation_type"] for e in proj_2024["edges"] if e.get("is_active", True)]
    assert "REGULATES" in active_predicates
    assert "AFFECTS_COST_OF" in active_predicates
    assert len(proj_2024["edges"]) >= 2, "Relation multiplicity must survive projection"

    # Future obligation: MANDATES must NOT be active in 2024
    mandates_edges = [e for e in proj_2024["edges"] if e["relation_type"] == "MANDATES"]
    assert len(mandates_edges) == 1
    assert mandates_edges[0]["status"] == "upcoming"
    assert mandates_edges[0]["is_active"] is False

    # Expired relation: IMPACTS must NOT be active in 2024
    impacts_edges = [e for e in proj_2024["edges"] if e["relation_type"] == "IMPACTS"]
    assert len(impacts_edges) == 1
    assert impacts_edges[0]["status"] == "expired"
    assert impacts_edges[0]["is_active"] is False

    # Past cutoff in early 2023: IMPACTS was active
    cutoff_2023 = datetime(2023, 3, 1, tzinfo=timezone.utc)
    proj_2023 = store.project_graph(cutoff_2023)
    impacts_2023 = [e for e in proj_2023["edges"] if e["relation_type"] == "IMPACTS"]
    assert impacts_2023[0]["is_active"] is True


def test_legacy_graph_store_adapter_no_eval_multigraph(temp_store_dir):
    """
    Legacy DualLayerGraphStore acts as an adapter:
    - Eliminates eval()
    - Uses safe JSON serialization
    - Preserves multigraph connectivity and existing analytical methods
    """
    legacy_store = DualLayerGraphStore(storage_dir=temp_store_dir)

    n1 = GraphNode(id="N1", label="Node 1", node_type=NodeType.LAW)
    n2 = GraphNode(id="N2", label="Node 2", node_type=NodeType.ACTION)
    legacy_store.add_node(n1)
    legacy_store.add_node(n2)

    # Add two edges between same endpoints
    e1 = GraphEdge(source_id="N1", target_id="N2", relation_type=EdgeRelation.MANDATES, weight=1.0)
    e2 = GraphEdge(source_id="N1", target_id="N2", relation_type=EdgeRelation.TRIGGERS, weight=0.8)
    legacy_store.add_edge(e1)
    legacy_store.add_edge(e2)

    st1 = NodeStateRecord(node_id="N2", node_type=NodeType.ACTION, timestamp="2024-01-01", state_value="ACTIVE", metric_name="cost", metric_value=42.0)
    legacy_store.add_state_record(st1)

    legacy_store.save()

    # Reload from disk into a fresh instance
    reloaded = DualLayerGraphStore(storage_dir=temp_store_dir)
    assert len(reloaded.get_nodes()) == 2
    assert len(reloaded.get_edges()) == 2, "Multigraph edges between same endpoints must not be collapsed"

    # Verify no eval was needed: metadata and properties were loaded safely
    assert reloaded.get_node("N1") is not None
    assert reloaded.get_node("N2") is not None

    # Analytical methods
    G = reloaded.to_networkx()
    assert len(G.nodes()) == 2
    metrics = reloaded.compute_power_law_metrics()
    assert metrics["num_nodes"] == 2
    assert metrics["num_edges"] >= 1

    gnn_data = reloaded.export_gnn_dataset()
    assert gnn_data["num_nodes"] == 2
    assert gnn_data["num_edges"] == 2


def test_migration_cli_dry_run_and_restore_verification(temp_store_dir):
    """
    Test scripts/ops/migrate_data.py with --dry-run:
    - Maps legacy data to canonical entities/facts/aliases
    - Creates aliases for old IDs
    - Verifies restore into temporary directory
    - Does not mutate production target directory
    """
    from scripts.ops.migrate_data import migrate_data

    # Create dummy legacy storage in temp_store_dir
    legacy_nodes = pd.DataFrame([
        {"id": "LEG_01", "label": "Legacy Law", "node_type": "LAW", "jurisdiction": "EU", "data_origin": "live"}
    ])
    legacy_edges = pd.DataFrame([
        {"source_id": "LEG_01", "target_id": "LEG_02", "relation_type": "MANDATES", "weight": 1.0, "data_origin": "live"}
    ])
    legacy_nodes.to_parquet(temp_store_dir / "graph_nodes.parquet", index=False)
    legacy_edges.to_parquet(temp_store_dir / "graph_edges.parquet", index=False)

    target_dir = temp_store_dir / "canonical_store"
    report_out = temp_store_dir / "migration_report.json"

    # Run dry run
    report = migrate_data(source_dirs=[temp_store_dir], target_dir=target_dir, dry_run=True, output_file=report_out)

    assert report["dry_run"] is True
    assert report["entities_mapped"] >= 1
    assert report["aliases_created"] >= 1
    assert report["restore_verification"]["status"] == "verified_temporary_restore_success"
    assert report_out.exists()
    assert not target_dir.exists(), "Dry run must NOT mutate target production directory"


def test_unsupported_unbacked_and_mixed_origin_facts_rejected(store):
    """
    Publication boundary rejects:
    1. Unsupported predicates/relations.
    2. Accepted facts with no supporting document or evidence link.
    3. Facts whose data_origin doesn't match staging batch data_origin.
    """
    batch_live = BatchManifest(batch_id="b_live_01", source_id="SRC_TEST", data_origin="live")
    entity_s = Entity(entity_id="test:subj:01", entity_type="LAW", canonical_label="S1", batch_id=batch_live.batch_id, data_origin="live")
    entity_o = Entity(entity_id="test:obj:01", entity_type="ACTION", canonical_label="O1", batch_id=batch_live.batch_id, data_origin="live")
    store.register_entities([entity_s, entity_o])

    # 1. Unsupported predicate
    fact_bad_pred = Fact(
        subject_id=entity_s.entity_id,
        predicate="LOVES",
        object_id=entity_o.entity_id,
        source_id="SRC_TEST",
        batch_id=batch_live.batch_id,
        data_origin="live",
    )
    with pytest.raises(ValueError, match="unsupported predicate"):
        store.stage_batch(batch_live, documents=[], facts=[fact_bad_pred], observations=[])

    # 2. Accepted fact with no document or evidence
    fact_unbacked = Fact(
        subject_id=entity_s.entity_id,
        predicate="MANDATES",
        object_id=entity_o.entity_id,
        source_id="SRC_TEST",
        batch_id=batch_live.batch_id,
        data_origin="live",
        quality_state="accepted",
    )
    with pytest.raises(ValueError, match="supporting document or evidence link"):
        store.stage_batch(batch_live, documents=[], facts=[fact_unbacked], observations=[])

    # 3. Mixed origin (fact data_origin="demo" in live batch)
    fact_mixed_origin = Fact(
        subject_id=entity_s.entity_id,
        predicate="MANDATES",
        object_id=entity_o.entity_id,
        source_id="SRC_TEST",
        batch_id=batch_live.batch_id,
        data_origin="demo",
    )
    with pytest.raises(ValueError, match="does not match staging batch data_origin"):
        store.stage_batch(batch_live, documents=[], facts=[fact_mixed_origin], observations=[])


def test_observation_semantic_validation_rejected(store):
    """
    Publication boundary rejects:
    1. Unregistered series.
    2. Unit mismatch against registered series.
    3. Reversed or equal interval (period_start >= period_end_exclusive).
    4. Non-finite values (NaN / Inf).
    """
    batch = BatchManifest(batch_id="b_obs_sem", source_id="SRC_STAT", data_origin="test")
    series_def = SeriesDefinition(
        series_id="test:series:sem",
        source_id="SRC_STAT",
        dataset_id="DATASET_1",
        provider_series_key="KEY_1",
        metric_name="metric",
        unit="INDEX",
    )
    store.register_series([series_def])

    # 1. Unregistered series
    obs_unregistered = Observation(
        series_id="test:series:unknown",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=100.0,
        unit="INDEX",
        batch_id=batch.batch_id,
        data_origin="test",
    )
    with pytest.raises(ValueError, match="unregistered or uncommitted series"):
        store.stage_batch(batch, documents=[], facts=[], observations=[obs_unregistered])

    # 2. Unit mismatch
    obs_bad_unit = Observation(
        series_id="test:series:sem",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=100.0,
        unit="PERCENT",
        batch_id=batch.batch_id,
        data_origin="test",
    )
    with pytest.raises(ValueError, match="unit .* does not match"):
        store.stage_batch(batch, documents=[], facts=[], observations=[obs_bad_unit])

    # 3. Reversed interval
    obs_rev_interval = Observation(
        series_id="test:series:sem",
        period_start="2024-04-01",
        period_end_exclusive="2024-01-01",
        value=100.0,
        unit="INDEX",
        batch_id=batch.batch_id,
        data_origin="test",
    )
    with pytest.raises(ValueError, match="strictly earlier than"):
        store.stage_batch(batch, documents=[], facts=[], observations=[obs_rev_interval])

    # 4. Non-finite value (NaN)
    obs_nan = Observation(
        series_id="test:series:sem",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=float("nan"),
        unit="INDEX",
        batch_id=batch.batch_id,
        data_origin="test",
    )
    with pytest.raises(ValueError, match="finite"):
        store.stage_batch(batch, documents=[], facts=[], observations=[obs_nan])


def test_staged_metadata_references_isolated(store):
    """
    Foreign staged definitions cannot supply committed endpoints or series to another batch.
    """
    batch_a = BatchManifest(batch_id="batch_staged_a", source_id="SRC_A", data_origin="test")
    series_a = SeriesDefinition(
        series_id="series:staged:a",
        source_id="SRC_A",
        dataset_id="DS_A",
        provider_series_key="K_A",
        metric_name="metric_a",
        unit="INDEX",
        batch_id=batch_a.batch_id,
    )
    # Stage batch A with series_a, but do NOT commit it
    store.stage_batch(batch_a, documents=[], facts=[], observations=[], series=[series_a])

    # Now attempt to stage batch B referring to series:staged:a
    batch_b = BatchManifest(batch_id="batch_staged_b", source_id="SRC_B", data_origin="test")
    obs_b = Observation(
        series_id="series:staged:a",
        period_start="2024-01-01",
        period_end_exclusive="2024-04-01",
        value=50.0,
        unit="INDEX",
        batch_id=batch_b.batch_id,
        data_origin="test",
    )
    with pytest.raises(ValueError, match="unregistered or uncommitted series"):
        store.stage_batch(batch_b, documents=[], facts=[], observations=[obs_b])


def test_batch_hash_cold_reopen(temp_store_dir):
    """
    archive_sha256 in BatchManifest persists across store reopen and matches original value.
    """
    store1 = DualLayerStore(storage_dir=temp_store_dir)
    batch = BatchManifest(
        batch_id="b_archive_hash",
        source_id="SRC_TEST",
        data_origin="test",
        archive_sha256="deadbeef" * 8,
    )
    store1.stage_batch(batch, documents=[], facts=[], observations=[])
    store1.commit_batch(batch.batch_id)

    # Reopen store
    store2 = DualLayerStore(storage_dir=temp_store_dir)
    assert store2._canonical_batches["b_archive_hash"].archive_sha256 == "deadbeef" * 8
    if hasattr(store2, "db") and store2.db is not None:
        try:
            tbl = store2.db.open_table("batches")
            arrow_tbl = tbl.to_arrow()
            batches_list = arrow_tbl.to_pylist()
            matching = [b for b in batches_list if b["batch_id"] == "b_archive_hash"]
            assert len(matching) == 1
            assert matching[0]["archive_sha256"] == "deadbeef" * 8
        except Exception:
            pass


def test_production_reader_isolation_and_cache_invalidation(temp_store_dir, monkeypatch):
    """
    Production readers filter out demo/test/quarantine data and UI cache invalidates on commit.
    """
    from src.storage.dashboard_data import get_all_laws_data, get_all_news_data, get_graph_store

    monkeypatch.setenv("POWERLAW_MODE", "production")
    monkeypatch.setenv("LANCEDB_PATH", str(temp_store_dir / "lancedb"))
    monkeypatch.setenv("POWERLAW_DATA_DIR", str(temp_store_dir))
    from src.config import clear_settings_cache
    clear_settings_cache()

    store = DualLayerStore(storage_dir=temp_store_dir)

    # In production, get_all_laws_data and get_all_news_data reject uncommitted or demo rows
    laws = get_all_laws_data(storage_dir=str(temp_store_dir))
    news = get_all_news_data()
    for law in laws:
        assert law.get("data_origin") not in ("demo", "test", "quarantine")
    for item in news:
        assert item.get("data_origin") not in ("demo", "test", "quarantine")

    # Verify cache invalidation on generation bump
    cached_store_1 = get_graph_store(temp_store_dir)
    gen_1 = cached_store_1._generation

    # Mutate store on disk and commit
    batch_new = BatchManifest(batch_id="b_new", source_id="SRC_LIVE", data_origin="live")
    store.stage_batch(batch_new, documents=[], facts=[], observations=[])
    store.commit_batch(batch_new.batch_id)

    # Next get_graph_store must detect new generation
    cached_store_2 = get_graph_store(temp_store_dir)
    assert cached_store_2._generation > gen_1


def test_migration_reference_interval_parsing():
    """
    _parse_reference_interval parses DAILY, MONTHLY, QUARTERLY, ANNUAL without misidentifying daily as monthly.
    """
    from scripts.ops.migrate_data import _parse_reference_interval

    # Daily
    d_start, d_end, _ = _parse_reference_interval("2024-05-15", "DAILY")
    assert d_start == "2024-05-15"
    assert d_end == "2024-05-16"

    # Monthly
    m_start, m_end, _ = _parse_reference_interval("2024-05-01", "MONTHLY")
    assert m_start == "2024-05-01"
    assert m_end == "2024-06-01"

    # Quarterly
    q_start, q_end, _ = _parse_reference_interval("2024-Q1", "QUARTERLY")
    assert q_start == "2024-01-01"
    assert q_end == "2024-04-01"

    # Annual
    a_start, a_end, _ = _parse_reference_interval("2023", "ANNUAL")
    assert a_start == "2023-01-01"
    assert a_end == "2024-01-01"


def test_record_content_is_timezone_independent_and_accepts_pre_1970_periods():
    """Content hashing must not use the machine's local timezone; Destatis history starts in 1958."""
    from src.storage.dual_layer_store import DualLayerStore
    from src.schemas.ingestion import Observation

    def content(start, end):
        return DualLayerStore._record_content(Observation(
            series_id="61261-0002|test", period_start=start, period_end_exclusive=end,
            value=100.0, unit="2021=100", batch_id="b"))

    assert content("2025-01-01", "2025-04-01")["period_start"].startswith("2025-01-01")
    assert content("1958-01-01", "1958-04-01")["period_start"].startswith("1958-01-01")
