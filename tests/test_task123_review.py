"""Regression cases missed by the first three ingestion-task checks."""

import json
from datetime import datetime, timezone

import pytest

from src.schemas.ingestion import BatchManifest, Entity, EntityAlias, EvidenceLink, Fact, Observation, SourceDocument
from src.storage.dual_layer_store import DualLayerStore, WriterLockError


def dt(year, month=1, day=1):
    return datetime(year, month, day, tzinfo=timezone.utc)


def stage(store, batch_id, *, facts=None, observations=None, documents=None, entities=None, evidence=None):
    docs = list(documents) if documents is not None else []
    ev_links = list(evidence) if evidence is not None else []
    facts_list = list(facts) if facts is not None else []
    obs_list = list(observations) if observations is not None else []
    ents = list(entities) if entities is not None else []

    if documents is None and evidence is None:
        needed_assertions = []
        for f in facts_list:
            if getattr(f, "quality_state", "accepted") == "accepted" and not getattr(f, "is_retracted", False):
                needed_assertions.append((f.fact_version_id, getattr(f, "available_at", None), getattr(f, "recorded_at", None)))
        for o in obs_list:
            if getattr(o, "quality_state", "accepted") == "accepted" and not getattr(o, "is_retracted", False):
                needed_assertions.append((o.revision_id, getattr(o, "available_at", None), getattr(o, "recorded_at", None)))

        if needed_assertions:
            earliest_avail = min((a[1] for a in needed_assertions if a[1] is not None), default=None)
            earliest_rec = min((a[2] for a in needed_assertions if a[2] is not None), default=None)
            doc = SourceDocument.from_content("SRC", f"https://example.com/{batch_id}", "P", f"Doc for {batch_id}".encode(), batch_id, available_at=earliest_avail)
            if earliest_rec is not None:
                doc = doc.model_copy(update={"recorded_at": earliest_rec})
            docs.append(doc)
            for assertion_id, a_avail, a_rec in needed_assertions:
                ev_kwargs = {
                    "assertion_version_id": assertion_id,
                    "document_version_id": doc.version_id,
                    "available_at": a_avail,
                    "batch_id": batch_id,
                }
                if a_rec is not None:
                    ev_kwargs["recorded_at"] = a_rec
                ev_links.append(EvidenceLink(**ev_kwargs))

    for o in obs_list:
        if o.series_id not in store._canonical_series:
            from src.schemas.ingestion import SeriesDefinition
            store.register_series([
                SeriesDefinition(
                    series_id=o.series_id,
                    source_id="SRC",
                    dataset_id="D",
                    provider_series_key=o.series_id,
                    metric_name=o.series_id,
                    unit=o.unit,
                )
            ])

    store.stage_batch(BatchManifest(batch_id=batch_id, source_id="SRC"),
                      documents=docs, facts=facts_list, observations=obs_list,
                      entities=ents, evidence_links=ev_links)


def entity(batch_id="b"):
    return Entity(entity_id="action:A", entity_type="ACTION", canonical_label="A", batch_id=batch_id, created_at=dt(2020))


def fact(batch_id="b", **kwargs):
    return Fact(subject_id="action:A", predicate="CAUSES", object_id="action:A", source_id="SRC",
                batch_id=batch_id, available_at=dt(2024), **kwargs)


def test_unknown_publication_is_bounded_by_recorded_time(tmp_path):
    store = DualLayerStore(tmp_path)
    obs = Observation(series_id="s", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                      value=1, unit="INDEX", recorded_at=dt(2026), batch_id="b")
    f = Fact(subject_id="action:A", predicate="CAUSES", object_id="action:A", source_id="SRC",
             batch_id="b", recorded_at=dt(2026))
    stage(store, "b", observations=[obs], facts=[f], entities=[entity()])
    store.commit_batch("b")
    assert store.query_observations(dt(2025)).num_rows == 0
    assert store.project_graph(dt(2025))["edges"] == []
    assert store.query_observations(dt(2027)).num_rows == 1


def test_retraction_suppresses_earlier_fact_without_changing_history(tmp_path):
    store = DualLayerStore(tmp_path)
    old = fact()
    stage(store, "b", facts=[old], entities=[entity()])
    store.commit_batch("b")
    tombstone = old.model_copy(update={"fact_version_id": "retracted", "is_retracted": True,
                                       "available_at": dt(2025), "batch_id": "b2"})
    stage(store, "b2", facts=[tombstone])
    store.commit_batch("b2")
    assert len(store.project_graph(dt(2024, 6))["edges"]) == 1
    assert store.project_graph(dt(2025, 6))["edges"] == []


def test_rejected_observations_never_enter_training_queries(tmp_path):
    store = DualLayerStore(tmp_path)
    obs = Observation(series_id="s", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                      value=999, unit="INDEX", available_at=dt(2024), quality_state="rejected", batch_id="b")
    stage(store, "b", observations=[obs])
    store.commit_batch("b")
    assert store.query_observations(dt(2025)).num_rows == 0


def test_other_staged_batch_cannot_supply_evidence_or_endpoints(tmp_path):
    store = DualLayerStore(tmp_path)
    doc = SourceDocument.from_content("SRC", "https://example.com", "Pub", b"text", "uncommitted")
    stage(store, "uncommitted", documents=[doc], entities=[entity("uncommitted")])
    f = fact("other")
    ev = EvidenceLink(assertion_version_id=f.fact_version_id, document_version_id=doc.version_id)
    with pytest.raises(ValueError, match="uncommitted|missing"):
        stage(store, "other", facts=[f], evidence=[ev])


def test_replay_checks_content_not_only_supplied_ids(tmp_path):
    store = DualLayerStore(tmp_path)
    original = fact()
    stage(store, "b", facts=[original], entities=[entity()])
    store.commit_batch("b")
    changed = original.model_copy(update={"payload_json": '{"changed":true}'})
    with pytest.raises(ValueError, match="conflict|committed"):
        stage(store, "b", facts=[changed])


def test_duplicate_version_in_later_batch_preserves_first_commit(tmp_path):
    store = DualLayerStore(tmp_path)
    original = fact()
    stage(store, "b", facts=[original], entities=[entity()])
    store.commit_batch("b")
    stage(store, "b2", facts=[original.model_copy(update={"batch_id": "b2"})])
    assert len(store.project_graph(dt(2025))["edges"]) == 1
    assert DualLayerStore(tmp_path)._canonical_facts[original.fact_version_id].batch_id == "b"


@pytest.mark.parametrize("operation", ["register_entities", "register_series", "save"])
def test_all_persisting_entry_points_reject_stale_writers(tmp_path, operation):
    first = DualLayerStore(tmp_path)
    stale = DualLayerStore(tmp_path)
    stage(first, "b", entities=[entity()])
    first.commit_batch("b")
    with pytest.raises(RuntimeError, match="Stale store"):
        if operation == "save":
            stale.save()
        else:
            getattr(stale, operation)([])


def test_aliases_survive_reopening(tmp_path):
    store = DualLayerStore(tmp_path)
    alias = EntityAlias(alias_id="alias", entity_id="action:A", scheme="LEGACY", external_id="old", source_id="SRC", batch_id="b")
    store.register_aliases([alias])
    assert DualLayerStore(tmp_path)._canonical_aliases["alias"].external_id == "old"


def test_staged_only_writes_cannot_overwrite_each_other(tmp_path):
    first, second = DualLayerStore(tmp_path), DualLayerStore(tmp_path)
    stage(first, "first", entities=[entity("first")])
    with pytest.raises(RuntimeError, match="Stale store"):
        stage(second, "second")


def test_canonical_records_are_actually_in_lancedb(tmp_path):
    store = DualLayerStore(tmp_path)
    stage(store, "b", facts=[fact()], entities=[entity()])
    store.commit_batch("b")
    assert store.db.open_table("canonical_facts").count_rows() == 1


def test_migration_rejects_parent_overlap_and_existing_target(tmp_path):
    from scripts.ops.migrate_data import migrate_data
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        migrate_data([source], tmp_path, dry_run=True)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="empty|exist|new"):
        migrate_data([source], target, dry_run=False)


def test_fact_scope_identity_is_independent_of_json_key_order():
    left = fact(payload_json=json.dumps({"scope": {"country": "DE", "material": "cement"}}))
    right = fact(payload_json=json.dumps({"scope": {"material": "cement", "country": "DE"}}))
    assert left.fact_id == right.fact_id


def test_distinct_source_releases_do_not_share_version_id():
    left = fact()
    right = Fact(subject_id=left.subject_id, predicate=left.predicate, object_id=left.object_id,
                 source_id="SECOND_SOURCE", batch_id="b2", available_at=dt(2025))
    assert left.fact_id == right.fact_id
    assert left.fact_version_id != right.fact_version_id


def test_partial_lance_write_never_publishes_and_retry_recovers(tmp_path, monkeypatch):
    store = DualLayerStore(tmp_path)
    stage(store, "b", facts=[fact()], entities=[entity()])
    store.commit_batch("b")
    second = fact("b2", payload_json='{"revision":2}')
    observation = Observation(series_id="s", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                              value=2, unit="INDEX", available_at=dt(2025), batch_id="b2")
    def crash(*args, **kwargs):
        raise SystemExit("simulated process crash after some table writes")
    with monkeypatch.context() as patch:
        patch.setattr(store._obs_tbl, "add", crash)
        with pytest.raises(SystemExit):
            stage(store, "b2", facts=[second], observations=[observation])
    reopened = DualLayerStore(tmp_path)
    assert len(reopened._canonical_facts) == 1
    assert "b2" not in reopened._canonical_batches
    stage(reopened, "b2", facts=[second], observations=[observation])
    reopened.commit_batch("b2")
    assert len(DualLayerStore(tmp_path)._canonical_facts) == 2


def test_manifest_publication_failure_keeps_batch_staged(tmp_path, monkeypatch):
    store = DualLayerStore(tmp_path)
    stage(store, "b", facts=[fact()], entities=[entity()])
    original = store._atomic_json
    def fail_manifest(path, value):
        if path == store._state_path:
            raise OSError("manifest unavailable")
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_atomic_json", fail_manifest)
        with pytest.raises(OSError, match="manifest unavailable"):
            store.commit_batch("b")
    assert DualLayerStore(tmp_path)._canonical_batches["b"].status == "staged"
    assert store.project_graph(dt(2025))["edges"] == []
    store.commit_batch("b")
    assert DualLayerStore(tmp_path).generation == 1


def test_lazy_observation_read_is_pinned_before_another_writer_commits(tmp_path):
    writer = DualLayerStore(tmp_path)
    obs = Observation(series_id="s", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                      value=1, unit="INDEX", available_at=dt(2024), batch_id="b")
    stage(writer, "b", observations=[obs])
    writer.commit_batch("b")
    reader = DualLayerStore(tmp_path)
    second = Observation(series_id="s", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                         value=2, unit="INDEX", available_at=dt(2025), batch_id="b2")
    stage(writer, "b2", observations=[second])
    writer.commit_batch("b2")
    assert reader.query_observations(dt(2026)).to_pylist()[0]["value"] == 1
    assert DualLayerStore(tmp_path).query_observations(dt(2026)).to_pylist()[0]["value"] == 2


def test_os_releases_writer_lock_after_abrupt_process_exit(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c",
        "import os,sys; from pathlib import Path; from src.storage.dual_layer_store import _WriterLock; "
        "lock=_WriterLock(Path(sys.argv[1])); lock.acquire(); os._exit(0)",
        str(tmp_path / "ingestion.lock")], capture_output=True, timeout=15)
    assert result.returncode == 0
    with DualLayerStore(tmp_path).acquire_writer_lock():
        pass


def test_string_validity_dates_round_trip_without_epoch_defaults(tmp_path):
    store = DualLayerStore(tmp_path)
    stage(store, "b", facts=[fact(valid_from="2024-01-01", valid_to="2026-01-01")], entities=[entity()])
    store.commit_batch("b")
    assert DualLayerStore(tmp_path).project_graph(dt(2025))["edges"][0]["status"] == "active"


def test_append_after_reopen_preserves_all_legacy_history(tmp_path):
    store = DualLayerStore(tmp_path)
    store.add_history_record({"record_id":"one", "node_id":"A", "effective_timestamp":"2024-01-01", "metric_value":1})
    reopened = DualLayerStore(tmp_path)
    reopened.add_history_record({"record_id":"two", "node_id":"A", "effective_timestamp":"2024-02-01", "metric_value":2})
    assert {row.record_id for row in reopened.get_history()} == {"one", "two"}


def test_legacy_adapter_propagates_write_failure(tmp_path, monkeypatch):
    from src.storage.graph_store import DualLayerGraphStore
    adapter = DualLayerGraphStore(tmp_path)
    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(adapter.canonical_store, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        adapter.save()


def test_legacy_adapter_preserves_metadata_and_audit_fields(tmp_path):
    from src.models.dual_layer_model import GraphNode, NodeStateRecord, NodeType
    from src.storage.graph_store import DualLayerGraphStore
    adapter = DualLayerGraphStore(tmp_path)
    adapter.add_node(GraphNode(id="A", label="Actor", node_type=NodeType.ACTION,
                               activation_date="2024-01-01", metadata={"custom":["evidence"]}))
    adapter.add_state_record(NodeStateRecord(node_id="A", node_type=NodeType.ACTION, timestamp="2024-01-01",
                                             state_value="ACTIVE", metric_name="price",
                                             metric_value=1, delta=2, confidence=.7, notes="original", source_id="doc"))
    adapter.save()
    reopened = DualLayerGraphStore(tmp_path)
    assert reopened.get_node("A").metadata["custom"] == ["evidence"]
    record = reopened.get_history()[0]
    assert (record.notes, record.delta, record.confidence, record.source_id) == ("original", 2, .7, "doc")


def test_legacy_mutation_rejects_stale_adapter_before_writing(tmp_path):
    from src.models.dual_layer_model import GraphNode, NodeType
    from src.storage.graph_store import DualLayerGraphStore
    first, stale = DualLayerGraphStore(tmp_path), DualLayerGraphStore(tmp_path)
    first.add_node(GraphNode(id="first", label="First", node_type=NodeType.ACTION))
    with pytest.raises(RuntimeError, match="Stale store"):
        stale.add_node(GraphNode(id="bad", label="Bad", node_type=NodeType.ACTION))
    assert DualLayerGraphStore(tmp_path).get_node("bad") is None


def test_replay_accepts_equivalent_json_without_changing_stored_record(tmp_path):
    store = DualLayerStore(tmp_path)
    original = fact(payload_json='{"scope":"DE","amount":1}')
    stage(store, "b", facts=[original], entities=[entity()])
    store.commit_batch("b")
    equivalent = fact(payload_json='{"amount": 1, "scope": "DE"}')
    stage(store, "b", facts=[equivalent], entities=[entity()])
    assert store._canonical_facts[original.fact_version_id].payload_json == original.payload_json


def test_fact_identity_includes_all_supplied_scope_dimensions():
    left = fact(payload_json='{"commodity":"cement","scope":"DE"}')
    right = fact(payload_json='{"commodity":"cement","scope":"FR"}')
    assert left.fact_id != right.fact_id


def test_equivalent_timezones_produce_same_version_identity():
    from datetime import timedelta
    first = fact()
    second = Fact(subject_id=first.subject_id, predicate=first.predicate, object_id=first.object_id,
                  source_id=first.source_id, batch_id="b", available_at=datetime(2024, 1, 1, 1, tzinfo=timezone(timedelta(hours=1))))
    assert first.fact_version_id == second.fact_version_id


@pytest.mark.parametrize("kind", ["fact", "observation"])
def test_malformed_date_suffix_is_rejected(kind):
    from src.schemas.ingestion import validate_candidate
    doc = SourceDocument.from_content("SRC", "https://example.com", "P", b"x", "b")
    if kind == "fact":
        candidate = Fact(subject_id="law:X", predicate="MANDATES", object_id="action:A", source_id="SRC",
                         batch_id="b", valid_from="2024-01-01garbage")
    else:
        candidate = Observation(series_id="s", period_start="2024-01-01garbage", period_end_exclusive="2024-04-01",
                                value=1, unit="INDEX", batch_id="b")
    decision = validate_candidate(candidate, doc)
    assert decision.decision == "rejected"
    expected = "MALFORMED_VALIDITY_DATE" if kind == "fact" else "MALFORMED_OBSERVATION_PERIOD"
    assert expected in decision.reason_codes


def test_registered_target_codes_select_four_official_quarters():
    import csv
    import hashlib
    from pathlib import Path
    from src.config import get_primary_target_definition
    target = get_primary_target_definition()
    evidence = target["verification_evidence"]
    path = Path(evidence["flat_fixture_path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["flat_fixture_sha256"]
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    selected = {}
    for row in rows:
        dims = {row[f"{n}_variable_code"]: row[f"{n}_variable_attribute_code"] for n in range(1, 6)}
        if all(dims.get(k) == v for k, v in target["provider_dimension_codes"].items()):
            assert row["value_unit"] == "2021=100"
            assert row["value_variable_code"] == "PRE002"
            selected[dims["QUARM1"]] = float(row["value"].replace(",", "."))
    assert selected == {"MONAT02":132.6, "MONAT05":133.6, "MONAT08":134.3, "MONAT11":135.0}


def test_official_context_keys_resolve_without_replacing_target():
    import csv
    from pathlib import Path
    from src.config import load_data_sources_registry
    registry = load_data_sources_registry()
    euro = registry["primary_target"]["eurostat_crosscheck"]
    data = json.loads(Path(euro["fixture_path"]).read_text(encoding="utf-8"))
    for name, key in euro["dimensions"].items():
        assert key in data["dimension"][name]["category"]["index"]
    assert euro["dimensions"]["indic_bt"] == "PRC_PRR"
    source = registry["sources"]["DESTATIS_GENESIS"]
    for series in source["material_context_series"].values():
        with Path(series["fixture_path"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        selected = [row for row in rows if row["3_variable_attribute_code"] == series["dimensions"]["GP19M2"]]
        assert len(selected) == 12
        assert all(row["value_unit"] == series["base_period"] for row in selected)
    verified_enabled = {
        "DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "OFFICIAL_NEWS",
        "BUNDESBANK_SDMX", "SMARD_DE", "EURLEX_CELLAR",
    }
    assert {key for key, meta in registry["sources"].items() if meta["enabled"]} == verified_enabled
    assert all(registry["sources"][key]["rights_checked"] for key in verified_enabled)


def test_failed_legacy_write_is_not_republished_by_next_write(tmp_path, monkeypatch):
    store = DualLayerStore(tmp_path)
    store.add_node({"id":"kept", "label":"Kept", "node_type":"ACTION"})
    original = store._atomic_json
    def fail_manifest(path, value):
        if path == store._state_path:
            raise OSError("unpublished")
        original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_atomic_json", fail_manifest)
        with pytest.raises(OSError):
            store.add_node({"id":"failed", "label":"Failed", "node_type":"ACTION"})
    store.add_node({"id":"next", "label":"Next", "node_type":"ACTION"})
    assert {node.id for node in DualLayerStore(tmp_path).get_nodes()} == {"kept", "next"}


def test_legacy_parallel_edges_survive_fallback_and_reopen(tmp_path):
    import pandas as pd
    rows = [{"source_id":"A", "target_id":"B", "relation_type":"FLOWS_TO", "properties_json":str({"commodity":"cement"})},
            {"source_id":"A", "target_id":"B", "relation_type":"FLOWS_TO", "properties_json":str({"commodity":"steel"})}]
    pd.DataFrame(rows + [rows[0]]).to_parquet(tmp_path / "graph_edges.parquet", index=False)
    store = DualLayerStore(tmp_path)
    assert len(store.get_edges()) == 3
    assert {edge.metadata_dict["commodity"] for edge in store.get_edges()} == {"cement", "steel"}
    store.save()
    assert len(DualLayerStore(tmp_path).get_edges()) == 3


def test_new_evidence_stays_invisible_until_its_batch_commits(tmp_path):
    store = DualLayerStore(tmp_path)
    doc = SourceDocument.from_content("SRC", "https://example.com", "P", b"x", "b", available_at=dt(2024))
    original = fact()
    base_link = EvidenceLink(assertion_version_id=original.fact_version_id, document_version_id=doc.version_id,
                             available_at=dt(2024))
    stage(store, "b", documents=[doc], facts=[original], entities=[entity()], evidence=[base_link])
    store.commit_batch("b")
    doc2 = SourceDocument.from_content("SRC", "https://example.com/2", "P2", b"x2", "links", available_at=dt(2024))
    link = EvidenceLink(assertion_version_id=original.fact_version_id, document_version_id=doc2.version_id,
                        available_at=dt(2024))
    stage(store, "links", documents=[doc2], evidence=[link])
    assert store.project_graph(dt(2025))["edges"][0]["evidence_ids"] == [base_link.evidence_id]
    store.commit_batch("links")
    assert set(store.project_graph(dt(2025))["edges"][0]["evidence_ids"]) == {base_link.evidence_id, link.evidence_id}


def test_future_document_does_not_change_past_evidence(tmp_path):
    store = DualLayerStore(tmp_path)
    original = fact()
    doc1 = SourceDocument.from_content("SRC", "https://example.com/1", "P", b"doc1", "b", available_at=dt(2024))
    base_link = EvidenceLink(assertion_version_id=original.fact_version_id, document_version_id=doc1.version_id,
                             available_at=dt(2024))
    stage(store, "b", documents=[doc1], facts=[original], entities=[entity()], evidence=[base_link])
    store.commit_batch("b")
    doc = SourceDocument.from_content("SRC", "https://example.com", "P", b"later", "b2", available_at=dt(2026))
    link = EvidenceLink(assertion_version_id=original.fact_version_id, document_version_id=doc.version_id,
                        available_at=dt(2026))
    stage(store, "b2", documents=[doc], evidence=[link])
    store.commit_batch("b2")
    assert store.project_graph(dt(2025))["edges"][0]["document_version_ids"] == [doc1.version_id]
    assert store.project_graph(dt(2027))["edges"][0]["document_version_ids"] == sorted([doc1.version_id, doc.version_id])


def test_unknown_series_geography_cannot_filter_historical_data(tmp_path):
    from src.schemas.ingestion import SeriesDefinition
    store = DualLayerStore(tmp_path)
    definition = SeriesDefinition(series_id="s", source_id="SRC", dataset_id="d", provider_series_key="k",
                                  metric_name="price", unit="INDEX", geography_id="DE", recorded_at=dt(2026))
    obs = Observation(series_id="s", period_start="2024-01-01", period_end_exclusive="2024-04-01",
                      value=1, unit="INDEX", available_at=dt(2024), batch_id="b")
    store.register_series([definition])
    stage(store, "b", observations=[obs])
    store.commit_batch("b")
    assert store.query_observations(dt(2025), entity_ids=["geo:DE"]).num_rows == 0


def legacy_source(tmp_path, rows, name="node_tabular_history.parquet"):
    import pandas as pd
    source = tmp_path / "legacy"
    source.mkdir()
    raw = source / "retained.pdf"
    raw.write_bytes(b"%PDF-1.4 archived source")
    for row in rows:
        row.update(raw_file=str(raw), data_origin="live")
    pd.DataFrame(rows).to_parquet(source / name, index=False)
    return source


def test_migration_never_turns_reference_time_into_publication_time(tmp_path):
    from scripts.ops.migrate_data import migrate_data
    source = legacy_source(tmp_path, [{"node_id":"A", "record_id":"one", "timestamp":"2020-01-01",
                                       "metric_name":"price", "metric_value":100, "unit":"INDEX"}])
    target = tmp_path / "target"
    migrate_data([source], target, dry_run=False)
    store = DualLayerStore(target)
    assert store.query_observations(dt(2021)).num_rows == 0


def test_migration_keeps_units_in_separate_series(tmp_path):
    from scripts.ops.migrate_data import migrate_data
    rows = [{"node_id":"A", "record_id":unit, "timestamp":"2020-01-01", "metric_name":"price",
             "metric_value":100, "unit":unit} for unit in ("2021=100", "2015=100")]
    source = legacy_source(tmp_path, rows)
    target = tmp_path / "target"
    migrate_data([source], target, dry_run=False)
    store = DualLayerStore(target)
    assert len(store._canonical_series) == 2
    assert store.query_observations(dt(2027)).num_rows == 2


def test_migration_preserves_parallel_edge_ids_payload_and_validity(tmp_path):
    from scripts.ops.migrate_data import migrate_data
    rows = [{"id":identity, "source_id":"A", "target_id":"B", "relation_type":"FLOWS_TO", "weight":.5,
             "valid_from":"2020-01-01", "properties_json":json.dumps({"commodity":commodity})}
            for identity, commodity in (("edge1","cement"),("edge2","steel"))]
    source = legacy_source(tmp_path, rows, "graph_edges.parquet")
    target = tmp_path / "target"
    migrate_data([source], target, dry_run=False)
    facts = list(DualLayerStore(target)._canonical_facts.values())
    assert len(facts) == 2
    assert {json.loads(f.payload_json)["commodity"] for f in facts} == {"cement", "steel"}
    assert all(f.valid_from is not None for f in facts)


@pytest.mark.parametrize("known_endpoints", [["A", "B"], ["A"]])
def test_migration_quarantines_edges_with_unverified_endpoints(tmp_path, known_endpoints):
    import pandas as pd
    from scripts.ops.migrate_data import migrate_data
    source = legacy_source(tmp_path, [{"id":"e", "source_id":"A", "target_id":"B", "relation_type":"ABOUT"}], "graph_edges.parquet")
    pd.DataFrame([{"id":key, "label":key} for key in known_endpoints]).to_parquet(source / "graph_nodes.parquet", index=False)
    target = tmp_path / "target"
    report = migrate_data([source], target, dry_run=False)
    assert report["quarantined_records"] >= len(known_endpoints) + 1
    assert DualLayerStore(target).project_graph(dt(2027))["edges"] == []


def test_migration_commits_verified_entity_only_batch_before_quarantine_edges(tmp_path):
    import pandas as pd
    from scripts.ops.migrate_data import migrate_data
    source = legacy_source(tmp_path, [{"id":"A", "label":"A"}], "graph_nodes.parquet")
    pd.DataFrame([{"id":"e", "source_id":"A", "target_id":"B", "relation_type":"ABOUT"}]).to_parquet(source / "graph_edges.parquet", index=False)
    target = tmp_path / "target"
    report = migrate_data([source], target, dry_run=False)
    assert report["verified_records"] == 1
    assert DualLayerStore(target)._canonical_batches["b_migration_legacy_verified"].status == "committed"
