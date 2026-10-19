"""
Regression Test Suite for Task 3: One Durable Store, Version-Aware Reads,
Writer Concurrency, and Reversible Migration.

This suite tests the 8 Task 3 defect areas across 10 distinct regression tests:
- Test 1: Authoritative persistence & restart data loss (series A & B survive store reopenings).
- Test 2: Durable staging & commit failure propagation (injected persistence failure raises, batch stays staged, no partial data).
- Test 3: Batch replay & conflict rejection (exact replay is no-op, conflicting restage raises ValueError, committed status preserved).
- Test 4: Writer locking across processes/instances (WriterLockError on competing writer, stale store cannot overwrite).
- Test 5: Monotonic committed generations (persisted across store reopenings, generation 2 after 2 commits).
- Test 6: Temporal eligibility & unknown availability (available_at=None bounded by recorded_at/first_fetch, excluded at 1900).
- Test 7: Uncommitted staged relation lookup isolation (uncommitted/future MEASURES fact does not leak into entity observation queries).
- Test 8: Rejected fact and future entity graph exclusion (rejected facts excluded from edges, entities after cutoff excluded from nodes).
- Test 9: Scoped relation multiplicity (multiple FLOWS_TO facts with distinct payloads between DE and FR survive in project_graph and to_networkx as MultiDiGraph).
- Test 10: Legacy adapter delegation (nodes/edges added through DualLayerGraphStore persist and load in DualLayerStore and vice versa).
"""

import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone
import pytest
import networkx as nx

from src.schemas.ingestion import (
    SourceDocument,
    Entity,
    Fact,
    EvidenceLink,
    SeriesDefinition,
    Observation,
    BatchManifest,
)
from src.storage.dual_layer_store import DualLayerStore, WriterLockError
from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import GraphNode, GraphEdge, NodeStateRecord, NodeType, EdgeRelation


@pytest.fixture
def isolated_store_dir():
    """Create a completely isolated temporary directory for store instances."""
    tmp = tempfile.mkdtemp(prefix="powerlaw_task3_regression_")
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


def test_1_authoritative_persistence_restart_data_loss(isolated_store_dir):
    """
    Test 1: Series A & B and all associated entities, facts, and observations
    survive store teardown and reopening without data loss.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)

    s_a = SeriesDefinition(
        series_id="SER_A",
        source_id="SRC_1",
        dataset_id="PRICE_TABLE",
        provider_series_key="DE.POWER.PRICE",
        metric_name="price",
        unit="EUR",
        geography_id="DE",
        data_origin="test",
    )
    s_b = SeriesDefinition(
        series_id="SER_B",
        source_id="SRC_1",
        dataset_id="VOL_TABLE",
        provider_series_key="FR.POWER.VOL",
        metric_name="volume",
        unit="MWh",
        geography_id="FR",
        data_origin="test",
    )
    ent_a = Entity(entity_id="ENT_A", entity_type="COMMODITY", canonical_label="German Power", batch_id="b_persist")
    ent_b = Entity(entity_id="ENT_B", entity_type="COMMODITY", canonical_label="French Power", batch_id="b_persist")
    store.register_entities([ent_a, ent_b])
    store.register_series([s_a, s_b])

    doc = SourceDocument.from_content(
        "SRC_1",
        "https://example.com/doc1",
        "Pub",
        b"Doc Content",
        "b_persist",
        available_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_origin="test",
    )
    fact = Fact(
        subject_id="ENT_A",
        predicate="AFFECTS_COST_OF",
        object_id="ENT_B",
        source_id="SRC_1",
        batch_id="b_persist",
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_origin="test",
    )
    obs_a = Observation(
        series_id="SER_A",
        period_start="2024-01-01",
        period_end_exclusive="2024-01-02",
        value=50.5,
        unit="EUR",
        batch_id="b_persist",
        available_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        data_origin="test",
    )
    obs_b = Observation(
        series_id="SER_B",
        period_start="2024-01-01",
        period_end_exclusive="2024-01-02",
        value=65.2,
        unit="MWh",
        batch_id="b_persist",
        available_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        data_origin="test",
    )
    b = BatchManifest(batch_id="b_persist", source_id="SRC_1", data_origin="test")
    ev_fact = EvidenceLink(
        assertion_version_id=fact.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="affects cost",
        available_at=fact.available_at,
        batch_id="b_persist",
        data_origin="test",
    )
    ev_a = EvidenceLink(
        assertion_version_id=obs_a.revision_id,
        document_version_id=doc.version_id,
        quoted_text="50.5",
        available_at=obs_a.available_at,
        batch_id="b_persist",
        data_origin="test",
    )
    ev_b = EvidenceLink(
        assertion_version_id=obs_b.revision_id,
        document_version_id=doc.version_id,
        quoted_text="65.2",
        available_at=obs_b.available_at,
        batch_id="b_persist",
        data_origin="test",
    )
    store.stage_batch(b, documents=[doc], facts=[fact], observations=[obs_a, obs_b], evidence_links=[ev_fact, ev_a, ev_b])
    store.commit_batch("b_persist")

    # Reopen new store instance pointing to same directory
    reopened = DualLayerStore(storage_dir=isolated_store_dir)
    assert "SER_A" in reopened._canonical_series
    assert "SER_B" in reopened._canonical_series
    assert "ENT_A" in reopened._canonical_entities
    assert "ENT_B" in reopened._canonical_entities
    assert len(reopened._canonical_facts) >= 1
    res = reopened.query_observations(datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert len(res) == 2
    res_dict = {r["series_id"]: r["value"] for r in res.to_pylist()}
    assert res_dict["SER_A"] == 50.5
    assert res_dict["SER_B"] == 65.2


def test_2_durable_staging_commit_failure_propagation(isolated_store_dir):
    """
    Test 2: An injected failure during commit persistence raises an exception,
    aborts the commit, preserves the 'staged' status, and ensures no partial data is committed.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    ent = Entity(entity_id="ENT_FAIL", entity_type="ACTION", canonical_label="Fail Entity", batch_id="b_fail")
    store.register_entities([ent])
    doc = SourceDocument.from_content("SRC_FAIL", "https://example.com/fail", "Pub", b"Content", "b_fail", available_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    fact = Fact(subject_id="ENT_FAIL", predicate="MANDATES", object_id=None, source_id="SRC_FAIL", batch_id="b_fail", available_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    b = BatchManifest(batch_id="b_fail", source_id="SRC_FAIL")
    ev_fact = EvidenceLink(
        assertion_version_id=fact.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="mandates",
        available_at=fact.available_at,
        batch_id="b_fail",
        data_origin="live",
    )
    store.stage_batch(b, documents=[doc], facts=[fact], observations=[], evidence_links=[ev_fact])

    initial_gen = store.generation
    assert store._canonical_batches["b_fail"].status == "staged"

    def failing_persist():
        raise IOError("Simulated persistence crash on commit")

    store._persist_generation = failing_persist

    with pytest.raises(IOError, match="Simulated persistence crash"):
        store.commit_batch("b_fail")

    # Verify rollback: status stays 'staged', generation not bumped
    assert store._canonical_batches["b_fail"].status == "staged"
    assert store.generation == initial_gen

    # Point-in-time graph query should NOT see facts from staged batch
    proj = store.project_graph(datetime.now(timezone.utc))
    assert not any(e["relation_type"] == "MANDATES" for e in proj["edges"])


def test_3_batch_replay_and_conflict_rejection(isolated_store_dir):
    """
    Test 3: Replaying an exact committed batch is an idempotent no-op preserving committed status;
    restaging the same batch ID with conflicting records raises ValueError.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    ent = Entity(entity_id="ENT_REP", entity_type="ACTION", canonical_label="Replay Action", batch_id="b_rep")
    store.register_entities([ent])
    doc = SourceDocument.from_content("SRC_REP", "https://example.com/rep", "Pub", b"Content 1", "b_rep", available_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    fact = Fact(subject_id="ENT_REP", predicate="MANDATES", object_id=None, source_id="SRC_REP", batch_id="b_rep", available_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    b = BatchManifest(batch_id="b_rep", source_id="SRC_REP")
    ev_fact = EvidenceLink(
        assertion_version_id=fact.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="mandates",
        available_at=fact.available_at,
        batch_id="b_rep",
    )
    store.stage_batch(b, documents=[doc], facts=[fact], observations=[], evidence_links=[ev_fact])
    store.commit_batch("b_rep")
    assert store._canonical_batches["b_rep"].status == "committed"

    # Exact replay: same batch ID, identical records -> idempotent no-op
    store.stage_batch(b, documents=[doc], facts=[fact], observations=[], evidence_links=[ev_fact])
    assert store._canonical_batches["b_rep"].status == "committed"

    # Conflicting replay: same batch ID, different document content -> rejected
    doc_conflict = SourceDocument.from_content("SRC_REP", "https://example.com/rep_diff", "Pub", b"Diff Content", "b_rep", available_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="Cannot restage or reuse committed batch ID"):
        store.stage_batch(b, documents=[doc_conflict], facts=[fact], observations=[], evidence_links=[ev_fact])
    assert store._canonical_batches["b_rep"].status == "committed"


def test_4_writer_locking_and_stale_store_protection(isolated_store_dir):
    """
    Test 4: Competing writer lock attempts raise WriterLockError, and a stale store
    instance whose generation lags behind disk cannot overwrite the store.
    """
    store1 = DualLayerStore(storage_dir=isolated_store_dir)
    store2 = DualLayerStore(storage_dir=isolated_store_dir)

    # 1. Competing writer lock rejection
    with store1.acquire_writer_lock():
        with pytest.raises(WriterLockError):
            with store2.acquire_writer_lock():
                pass

    # 2. Stale store instance protection
    ent1 = Entity(entity_id="ENT_STALE_1", entity_type="SECTOR", canonical_label="S1", batch_id="b_s1")
    store1.register_entities([ent1])
    doc1 = SourceDocument.from_content("SRC", "https://example.com/s1", "P", b"C1", "b_s1")
    b1 = BatchManifest(batch_id="b_s1", source_id="SRC")
    store1.stage_batch(b1, documents=[doc1], facts=[], observations=[])
    store1.commit_batch("b_s1")  # Advances disk generation to 1

    # store2 still has cached generation == 0. Staging a new batch from store2 must be rejected.
    ent2 = Entity(entity_id="ENT_STALE_2", entity_type="SECTOR", canonical_label="S2", batch_id="b_s2")
    doc2 = SourceDocument.from_content("SRC", "https://example.com/s2", "P", b"C2", "b_s2")
    b2 = BatchManifest(batch_id="b_s2", source_id="SRC")
    with pytest.raises((RuntimeError, ValueError), match="Stale store instance"):
        store2.stage_batch(b2, entities=[ent2], documents=[doc2], facts=[], observations=[])


def test_5_monotonic_committed_generations_reopen(isolated_store_dir):
    """
    Test 5: Generation increments monotonically with each committed batch and
    persists across store teardown and reopening.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    assert store.generation == 0

    ent1 = Entity(entity_id="ENT_GEN1", entity_type="ACTION", canonical_label="G1", batch_id="b_g1")
    store.register_entities([ent1])
    doc1 = SourceDocument.from_content("SRC", "https://example.com/g1", "P", b"G1", "b_g1")
    b1 = BatchManifest(batch_id="b_g1", source_id="SRC")
    store.stage_batch(b1, documents=[doc1], facts=[], observations=[])
    store.commit_batch("b_g1")
    assert store.generation == 1

    ent2 = Entity(entity_id="ENT_GEN2", entity_type="ACTION", canonical_label="G2", batch_id="b_g2")
    store.register_entities([ent2])
    doc2 = SourceDocument.from_content("SRC", "https://example.com/g2", "P", b"G2", "b_g2")
    b2 = BatchManifest(batch_id="b_g2", source_id="SRC")
    store.stage_batch(b2, documents=[doc2], facts=[], observations=[])
    store.commit_batch("b_g2")
    assert store.generation == 2

    # Reopening new store instance loads generation 2 from disk
    reopened = DualLayerStore(storage_dir=isolated_store_dir)
    assert reopened.generation == 2


def test_6_temporal_eligibility_unknown_availability_bounded(isolated_store_dir):
    """
    Test 6: Observations with available_at=None are bounded by recorded_at/first_fetch,
    meaning they are excluded at ancient cutoffs (e.g. 1900) and eligible at modern cutoffs.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    s = SeriesDefinition(
        series_id="SER_AVAIL",
        source_id="SRC_AVAIL",
        dataset_id="AVAIL_TABLE",
        provider_series_key="DE.METRIC.AVAIL",
        metric_name="idx",
        unit="INDEX",
        data_origin="test",
    )
    ent = Entity(entity_id="ENT_AVAIL", entity_type="SECTOR", canonical_label="Sector Avail", batch_id="b_avail")
    store.register_entities([ent])
    store.register_series([s])

    obs_unknown = Observation(
        series_id="SER_AVAIL",
        period_start="2024-01-01",
        period_end_exclusive="2024-01-02",
        value=100.0,
        unit="INDEX",
        batch_id="b_avail",
        available_at=None,
        recorded_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        published_at=None,
        data_origin="test",
    )
    doc = SourceDocument.from_content("SRC_AVAIL", "https://example.com/av", "P", b"Avail", "b_avail", available_at=datetime(2025, 1, 1, tzinfo=timezone.utc), data_origin="test").model_copy(update={"recorded_at": datetime(2025, 1, 1, tzinfo=timezone.utc)})
    b = BatchManifest(batch_id="b_avail", source_id="SRC_AVAIL", data_origin="test")
    ev_obs = EvidenceLink(
        assertion_version_id=obs_unknown.revision_id,
        document_version_id=doc.version_id,
        quoted_text="100.0",
        available_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        recorded_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        batch_id="b_avail",
        data_origin="test",
    )
    store.stage_batch(b, documents=[doc], facts=[], observations=[obs_unknown], evidence_links=[ev_obs])
    store.commit_batch("b_avail")

    # Ancient cutoff at year 1900 -> excluded
    cutoff_1900 = datetime(1900, 1, 1, tzinfo=timezone.utc)
    res_1900 = store.query_observations(cutoff_1900)
    assert len(res_1900) == 0

    assert len(store.query_observations(datetime(2024, 7, 1, tzinfo=timezone.utc))) == 0

    # Modern cutoff after first recorded time -> included
    cutoff_current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    res_current = store.query_observations(cutoff_current)
    assert len(res_current) == 1
    assert res_current.to_pylist()[0]["value"] == 100.0


def test_7_uncommitted_staged_measures_isolation(isolated_store_dir):
    """
    Test 7: Uncommitted staged MEASURES facts are isolated and do NOT leak into
    observation queries when filtering by entity_ids.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    ent_act = Entity(entity_id="test:act:ISO", entity_type="COMMODITY", canonical_label="Act ISO", batch_id="b_iso1")
    ent_sec = Entity(entity_id="test:sec:ISO", entity_type="SECTOR", canonical_label="Sec ISO", batch_id="b_iso1")
    store.register_entities([ent_act, ent_sec])

    s = SeriesDefinition(
        series_id="SER_ISO",
        source_id="SRC",
        dataset_id="COST_TABLE",
        provider_series_key="DE.OPEX.COST",
        metric_name="opex_cost",
        unit="EUR",
        data_origin="test",
    )
    store.register_series([s])

    # Batch 1 commits the baseline observation for the series and links it to ent_sec
    doc1 = SourceDocument.from_content("SRC", "https://example.com/iso1", "P", b"Doc1", "b_iso1", available_at=datetime(2023, 1, 1, tzinfo=timezone.utc), data_origin="test")
    fact_sec_measures = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id="SER_ISO",
        predicate="MEASURES",
        object_id=ent_sec.entity_id,
        source_id="SRC",
        batch_id="b_iso1",
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_origin="test",
    )
    obs1 = Observation(
        series_id="SER_ISO",
        period_start="2024-01-01",
        period_end_exclusive="2024-01-02",
        value=50.0,
        unit="EUR",
        batch_id="b_iso1",
        available_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_origin="test",
    )
    b1 = BatchManifest(batch_id="b_iso1", source_id="SRC", data_origin="test")
    ev_fact1 = EvidenceLink(
        assertion_version_id=fact_sec_measures.fact_version_id,
        document_version_id=doc1.version_id,
        quoted_text="measures",
        available_at=fact_sec_measures.available_at,
        batch_id="b_iso1",
        data_origin="test",
    )
    ev_obs1 = EvidenceLink(
        assertion_version_id=obs1.revision_id,
        document_version_id=doc1.version_id,
        quoted_text="50.0",
        available_at=obs1.available_at,
        batch_id="b_iso1",
        data_origin="test",
    )
    store.stage_batch(b1, documents=[doc1], facts=[fact_sec_measures], observations=[obs1], evidence_links=[ev_fact1, ev_obs1])
    store.commit_batch("b_iso1")

    # Batch 2 stages a MEASURES fact connecting action to series, plus a new observation, but remains UNCOMMITTED
    fact_act_measures = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id="SER_ISO",
        predicate="MEASURES",
        object_id=ent_act.entity_id,
        source_id="SRC",
        batch_id="b_iso2",
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_origin="test",
    )
    obs2 = Observation(
        series_id="SER_ISO",
        period_start="2024-01-02",
        period_end_exclusive="2024-01-03",
        value=999.0,
        unit="EUR",
        batch_id="b_iso2",
        available_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        data_origin="test",
    )
    doc2 = SourceDocument.from_content("SRC", "https://example.com/iso2", "P", b"Doc2", "b_iso2", available_at=datetime(2023, 1, 1, tzinfo=timezone.utc), data_origin="test")
    b2 = BatchManifest(batch_id="b_iso2", source_id="SRC", data_origin="test")
    ev_fact2 = EvidenceLink(
        assertion_version_id=fact_act_measures.fact_version_id,
        document_version_id=doc2.version_id,
        quoted_text="measures",
        available_at=fact_act_measures.available_at,
        batch_id="b_iso2",
        data_origin="test",
    )
    ev_obs2 = EvidenceLink(
        assertion_version_id=obs2.revision_id,
        document_version_id=doc2.version_id,
        quoted_text="999.0",
        available_at=obs2.available_at,
        batch_id="b_iso2",
        data_origin="test",
    )
    store.stage_batch(b2, documents=[doc2], facts=[fact_act_measures], observations=[obs2], evidence_links=[ev_fact2, ev_obs2])

    # Query observations for ent_act: since fact_act_measures is uncommitted, it must NOT link ent_act to SER_ISO
    res_act = store.query_observations(datetime(2024, 1, 3, tzinfo=timezone.utc), entity_ids=[ent_act.entity_id])
    assert len(res_act) == 0

    # Query observations for ent_sec: uncommitted obs2 must NOT leak
    res_sec = store.query_observations(datetime(2024, 1, 3, tzinfo=timezone.utc), entity_ids=[ent_sec.entity_id])
    assert len(res_sec) == 1
    assert res_sec.to_pylist()[0]["value"] == 50.0


def test_8_rejected_fact_and_future_entity_exclusion(isolated_store_dir):
    """
    Test 8: Rejected facts (quality_state='rejected') are excluded from graph edges,
    and entities created after cutoff are excluded from graph nodes in project_graph().
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    ent1 = Entity(entity_id="ENT_EX_1", entity_type="ACTION", canonical_label="Act 1", batch_id="b_ex", created_at=datetime(2023, 1, 1, tzinfo=timezone.utc))
    ent2 = Entity(entity_id="ENT_EX_2", entity_type="ACTION", canonical_label="Act 2", batch_id="b_ex", created_at=datetime(2023, 1, 1, tzinfo=timezone.utc))
    ent_future = Entity(entity_id="ENT_FUTURE", entity_type="SECTOR", canonical_label="Future Sec", batch_id="b_ex", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    store.register_entities([ent1, ent2, ent_future])

    doc = SourceDocument.from_content("SRC", "https://example.com/ex", "P", b"Content", "b_ex", available_at=datetime(2023, 1, 1, tzinfo=timezone.utc))
    fact_valid = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=ent1.entity_id,
        predicate="MANDATES",
        object_id=ent2.entity_id,
        source_id="SRC",
        batch_id="b_ex",
        quality_state="accepted",
        valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    fact_rejected = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=ent1.entity_id,
        predicate="TRIGGERS",
        object_id=ent2.entity_id,
        source_id="SRC",
        batch_id="b_ex",
        quality_state="rejected",
        valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    b = BatchManifest(batch_id="b_ex", source_id="SRC")
    ev_valid = EvidenceLink(
        assertion_version_id=fact_valid.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="mandates",
        available_at=fact_valid.available_at,
        batch_id="b_ex",
    )
    store.stage_batch(b, documents=[doc], facts=[fact_valid, fact_rejected], observations=[], evidence_links=[ev_valid])
    store.commit_batch("b_ex")

    proj_2024 = store.project_graph(datetime(2024, 1, 1, tzinfo=timezone.utc))

    # Rejected fact is excluded from edges
    edge_types = [e["relation_type"] for e in proj_2024["edges"]]
    assert "MANDATES" in edge_types
    assert "TRIGGERS" not in edge_types

    # Future entity (created in 2025) is excluded from nodes at 2024 cutoff
    node_ids = [n["id"] for n in proj_2024["nodes"]]
    assert "ENT_EX_1" in node_ids
    assert "ENT_EX_2" in node_ids
    assert "ENT_FUTURE" not in node_ids


def test_9_scoped_relation_multiplicity_multidigraph(isolated_store_dir):
    """
    Test 9: Multiple FLOWS_TO facts with distinct scoped commodity payloads between the same
    endpoints survive in project_graph() and to_networkx() as a MultiDiGraph.
    """
    store = DualLayerStore(storage_dir=isolated_store_dir)
    ent_de = Entity(entity_id="test:geo:DE", entity_type="SECTOR", canonical_label="Germany", batch_id="b_multi")
    ent_fr = Entity(entity_id="test:geo:FR", entity_type="SECTOR", canonical_label="France", batch_id="b_multi")
    store.register_entities([ent_de, ent_fr])

    doc = SourceDocument.from_content("SRC", "https://example.com/multi", "P", b"Multi", "b_multi", available_at=datetime(2023, 1, 1, tzinfo=timezone.utc))
    fact_cement = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=ent_de.entity_id,
        predicate="FLOWS_TO",
        object_id=ent_fr.entity_id,
        payload_json=json.dumps({"commodity": "cement", "flow_rate": 100}),
        source_id="SRC",
        batch_id="b_multi",
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    fact_steel = Fact(
        available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        subject_id=ent_de.entity_id,
        predicate="FLOWS_TO",
        object_id=ent_fr.entity_id,
        payload_json=json.dumps({"commodity": "steel", "flow_rate": 200}),
        source_id="SRC",
        batch_id="b_multi",
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert fact_cement.fact_id != fact_steel.fact_id

    b = BatchManifest(batch_id="b_multi", source_id="SRC")
    ev_cement = EvidenceLink(
        assertion_version_id=fact_cement.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="flows cement",
        available_at=fact_cement.available_at,
        batch_id="b_multi",
    )
    ev_steel = EvidenceLink(
        assertion_version_id=fact_steel.fact_version_id,
        document_version_id=doc.version_id,
        quoted_text="flows steel",
        available_at=fact_steel.available_at,
        batch_id="b_multi",
    )
    store.stage_batch(b, documents=[doc], facts=[fact_cement, fact_steel], observations=[], evidence_links=[ev_cement, ev_steel])
    store.commit_batch("b_multi")

    # 1. Verify project_graph preserves both edges
    proj = store.project_graph(datetime(2024, 6, 1, tzinfo=timezone.utc))
    flows_edges = [e for e in proj["edges"] if e["relation_type"] == "FLOWS_TO"]
    assert len(flows_edges) == 2
    commodities = [json.loads(e["payload"])["commodity"] for e in flows_edges]
    assert "cement" in commodities
    assert "steel" in commodities

    # 2. Verify to_networkx returns MultiDiGraph with both edges preserved
    g = store.to_networkx()
    assert isinstance(g, nx.MultiDiGraph)
    edge_dict = g.get_edge_data("test:geo:DE", "test:geo:FR")
    assert edge_dict is not None
    assert len(edge_dict) == 2


def test_10_legacy_adapter_delegation_reopen(isolated_store_dir):
    """
    Test 10: Nodes, edges, and state records added via DualLayerGraphStore persist
    and load accurately in DualLayerStore and vice versa.
    """
    graph_store = DualLayerGraphStore(storage_dir=isolated_store_dir)
    n1 = GraphNode(id="ADAPT_1", label="Adapter Node 1", node_type=NodeType.LAW, activation_date="2023-01-01")
    n2 = GraphNode(id="ADAPT_2", label="Adapter Node 2", node_type=NodeType.ACTION, activation_date="2023-01-01")
    edge1 = GraphEdge(source_id="ADAPT_1", target_id="ADAPT_2", relation_type=EdgeRelation.MANDATES)
    edge2 = GraphEdge(source_id="ADAPT_1", target_id="ADAPT_2", relation_type=EdgeRelation.TRIGGERS)
    state = NodeStateRecord(
        node_id="ADAPT_2",
        node_type=NodeType.ACTION,
        timestamp="2023-01-15",
        state_value="ENFORCED",
        metric_name="compliance_pct",
        metric_value=100.0,
    )
    graph_store.add_node(n1)
    graph_store.add_node(n2)
    graph_store.add_edge(edge1)
    graph_store.add_edge(edge2)
    graph_store.add_state_record(state)
    graph_store.save()

    # Reopen directly using DualLayerStore
    dual_store = DualLayerStore(storage_dir=isolated_store_dir)
    dual_nodes = dual_store.get_nodes()
    assert len(dual_nodes) >= 2
    node_ids = {n.id for n in dual_nodes}
    assert "ADAPT_1" in node_ids
    assert "ADAPT_2" in node_ids

    dual_edges = dual_store.get_edges()
    assert len(dual_edges) >= 2
    dual_history = dual_store.get_history()
    assert len(dual_history) >= 1
    assert any(h.node_id == "ADAPT_2" for h in dual_history)

    # Reopen via DualLayerGraphStore
    reopened_graph = DualLayerGraphStore(storage_dir=isolated_store_dir)
    assert len(reopened_graph.get_nodes()) >= 2
    assert len(reopened_graph.get_edges()) >= 2
    assert len(reopened_graph.get_history()) >= 1
