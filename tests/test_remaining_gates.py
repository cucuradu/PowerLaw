"""Independent remaining-gate checks; no product writes or live calls."""
import os
from pathlib import Path
import socket
import pytest
import torch
from datetime import timedelta
from tests.acceptance_fixtures import utc, stage_evidenced
from tests.test_label_acceptance import fixture, build, SID
from src.schemas.ingestion import Observation, Fact, Entity, SeriesDefinition
from src.storage.dual_layer_store import DualLayerStore
from src.storage.dashboard_data import get_construction_outlook
from src.gnn.graph_exporter import to_heterodata, fit_heterodata_transforms, apply_heterodata_transforms


def test_daily_rows_cannot_calibrate_a_quarterly_forecast(tmp_path):
    store, _ = fixture(tmp_path)
    observations = [Observation(series_id=SID, period_start=f"2025-01-{i:02d}",
        period_end_exclusive=f"2025-01-{i+1:02d}", value=100+i, unit="INDEX", source_unit="2021=100",
        available_at=utc("2025-02-01T00:00:00Z"), recorded_at=utc("2025-02-01T00:00:00Z"),
        vintage_id="daily", batch_id="daily", data_origin="test") for i in range(1,22)]
    stage_evidenced(store,"daily",observations=observations)
    result = get_construction_outlook(utc(2026),SID,store)
    assert result["interval"]["calibration_status"] != "calibrated", result["interval"]


def test_outlook_requires_a_frozen_prediction(tmp_path):
    store, _ = fixture(tmp_path)
    result = get_construction_outlook(utc(2026), SID, store)
    assert result["point_forecast"] is None, result["point_forecast"]


def test_retracted_unexpired_rule_is_absent_from_outlook(tmp_path):
    store, _ = fixture(tmp_path)
    fact = Fact(subject_id="law:a",predicate="MANDATES",object_id="rule:a",source_id="TEST",
        batch_id="rule",data_origin="test",available_at=utc(2020),recorded_at=utc(2020),valid_from=utc(2020))
    entities=[Entity(entity_id=i, entity_type=t,canonical_label=i,created_at=utc(2020),batch_id="rule",data_origin="test")
        for i,t in [("law:a","LAW"),("rule:a","ACTION")]]
    stage_evidenced(store,"rule",facts=[fact],entities=entities)
    tomb=fact.model_copy(update={"fact_version_id":"withdrawn", "is_retracted":True,"batch_id":"withdrawn",
        "available_at":utc(2021),"recorded_at":utc(2021),"supersedes_version_id":fact.fact_version_id})
    stage_evidenced(store,"withdrawn",facts=[tomb])
    assert not store.query_facts(utc(2026))
    assert get_construction_outlook(utc(2026),SID,store)["regulatory_context"] == []


def test_staged_document_is_not_outlook_evidence(tmp_path):
    store, _ = fixture(tmp_path,commit=False)
    stage_evidenced(store,"unrelated")
    staged_document = next(d.document_id for d in store._canonical_documents.values() if d.batch_id == "release")
    evidence = get_construction_outlook(utc(2026),SID,store)["evidence"]
    assert staged_document not in {d["document_id"] for d in evidence}, evidence


def test_no_cartesian_context_without_scope():
    raw={"cutoff":utc(2025).isoformat(),"nodes":[{"id":"rule:a","node_type":"RULE"},
        {"id":"series:a","node_type":"SERIES"},{"id":"series:b","node_type":"SERIES"}],"edges":[],"observations":[]}
    graph=to_heterodata(raw)
    key=("rule","context_for","series")
    assert key not in graph.edge_types or graph[key].edge_index.numel()==0


def test_unknown_and_inactive_values_do_not_fit_scaling():
    from torch_geometric.data import HeteroData
    graph=HeteroData()
    graph["series"].x=torch.tensor([[10.0],[20.0],[999.0]])
    graph["series"].feature_observed_mask=torch.tensor([[1.0],[1.0],[0.0]])
    graph["series"].active_mask=torch.tensor([True,True,False])
    profile=fit_heterodata_transforms([graph])
    assert profile["normalization"]["series"]["mean"] == [15.0]
    result=apply_heterodata_transforms(graph,profile)
    assert result["series"].x[-1].item()==0


def test_default_label_cutoff_does_not_advance_to_future_data(tmp_path):
    store,_=fixture(tmp_path,denominator_release="2099-01-01T00:00:00Z")
    assert not build(store)["samples"][0]["y_mask"][0,0]


def test_unknown_series_availability_is_not_distant_past(tmp_path):
    store=DualLayerStore(str(tmp_path))
    definition=SeriesDefinition(series_id=SID,source_id="TEST",dataset_id="61261-0002",
        provider_series_key="Q.DE.RESIDENTIAL",metric_name="construction_producer_price_index",
        unit="INDEX",frequency="Q",geography_id="DE",batch_id="definition",recorded_at=utc(2025))
    stage_evidenced(store,"definition",series=[definition],available=utc(2025))
    assert store.query_series(utc(2020)) == []

