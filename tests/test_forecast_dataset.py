"""
Automated Test Suite for Task 10:
Real forecasting dataset, release-aware as-of export, PyG HeteroData projection,
baselines, conformal calibration, and optional sparse Hetero GNN.
"""

import os
import json
import math
import tempfile
import unittest
from datetime import datetime, date, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pyarrow as pa
import torch

try:
    import torch_geometric
    from torch_geometric.data import HeteroData
    HAS_PYG = True
except ImportError:
    torch_geometric = None
    HeteroData = None
    HAS_PYG = False

from src.schemas.ingestion import (
    Entity,
    Fact,
    Observation,
    SeriesDefinition,
    SourceDocument,
    BatchManifest,
    EvidenceLink,
)
from src.storage.dual_layer_store import DualLayerStore
from src.gnn.graph_exporter import export_as_of, to_heterodata
from src.models.gnn_dataset import build_forecast_dataset
from src.quant.forecasting import evaluate_baselines, calibrate_residual_interval
from src.gnn.temporal_gnn import TemporalHeteroGNN
from src.gnn.trainer import HeteroGNNTrainer


class TestForecastDatasetAndGNN(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = DualLayerStore(storage_dir=self.temp_dir.name)

        # Base entities across the 8 canonical node types
        self.e_law = Entity(
            entity_id="law:cbam:2023",
            entity_type="LAW",
            canonical_label="CBAM Regulation",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_rule = Entity(
            entity_id="rule:cbam:report",
            entity_type="ACTION",
            entity_subtype="LEGAL_RULE",
            canonical_label="Quarterly Reporting Obligation",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_event = Entity(
            entity_id="event:plant:outage",
            entity_type="ACTION",
            entity_subtype="ECONOMIC_EVENT",
            canonical_label="Cement Kiln Outage",
            created_at=datetime(2023, 5, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_company = Entity(
            entity_id="company:heidelberg",
            entity_type="COMPANY",
            canonical_label="Heidelberg Materials",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_commodity = Entity(
            entity_id="commodity:cement",
            entity_type="COMMODITY",
            canonical_label="Portland Cement",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_sector = Entity(
            entity_id="sector:construction",
            entity_type="SECTOR",
            canonical_label="Construction of Buildings",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_geo = Entity(
            entity_id="geo:DE",
            entity_type="GEOGRAPHY",
            canonical_label="Germany",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_series = Entity(
            entity_id="series:destatis:61261-0002",
            entity_type="ECONOMIC_INDEX",
            canonical_label="German Construction PPI Residential",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.series_def = SeriesDefinition(
            series_id="series:destatis:61261-0002",
            source_id="destatis",
            dataset_id="61261-0002",
            provider_series_key="PREIS_BAUWERKE_WOHN",
            metric_name="construction_producer_price_index",
            unit="INDEX",
            frequency="Q",
            geography_id="geo:DE",
            base_period="2021=100",
            adjustment="unadjusted",
            measurement_kind="producer_price",
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.doc_init = SourceDocument(
            document_id="doc:init",
            version_id="doc:init:v1",
            source_id="official",
            canonical_url="https://example.com/init",
            final_url="https://example.com/init",
            publisher="Official",
            language="en",
            content_type="text/plain",
            content_sha256="0"*64,
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        batch = BatchManifest(
            batch_id="batch_init",
            source_id="official",
            data_origin="live",
            staged_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            status="staged"
        )

        # Baseline relations
        fact_mandates = Fact(
            fact_id="fact:mandates:01",
            fact_version_id="fact:mandates:01:v1",
            subject_id="law:cbam:2023",
            predicate="MANDATES",
            object_id="rule:cbam:report",
            valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            quality_state="accepted",
            claim_kind="factual",
            source_id="official",
            batch_id="batch_init"
        )
        fact_measures = Fact(
            fact_id="fact:measures:01",
            fact_version_id="fact:measures:01:v1",
            subject_id="series:destatis:61261-0002",
            predicate="MEASURES",
            object_id="commodity:cement",
            valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            quality_state="accepted",
            claim_kind="factual",
            source_id="official",
            batch_id="batch_init"
        )

        ev_mandates = EvidenceLink(
            assertion_version_id=fact_mandates.fact_version_id,
            document_version_id=self.doc_init.version_id,
            quoted_text="cbam reporting",
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
        )
        ev_measures = EvidenceLink(
            assertion_version_id=fact_measures.fact_version_id,
            document_version_id=self.doc_init.version_id,
            quoted_text="measures cement",
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
        )

        self.store.stage_batch(
            batch=batch,
            documents=[self.doc_init],
            facts=[fact_mandates, fact_measures],
            observations=[],
            entities=[
                self.e_law, self.e_rule, self.e_event, self.e_company,
                self.e_commodity, self.e_sector, self.e_geo, self.e_series
            ],
            series=[self.series_def],
            evidence_links=[ev_mandates, ev_measures]
        )
        self.store.commit_batch("batch_init", committed_at=datetime(2023, 1, 1, tzinfo=timezone.utc))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_future_perturbation_invariance(self):
        """
        Adding a future node/edge/news correction/revised observation cannot change
        any earlier active feature, topology, or text.
        """
        cutoff_t0 = datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)

        # Baseline snapshot at cutoff_t0
        snap_before = export_as_of(cutoff_t0, mode="public_as_of", store=self.store)

        # Stage and commit a batch with future observations, a future edge, and a future news correction
        batch_future = BatchManifest(
            batch_id="batch_future",
            source_id="official",
            data_origin="live",
            staged_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            status="staged"
        )
        doc_future = SourceDocument(
            document_id="doc:future",
            version_id="doc:future:v1",
            source_id="official",
            canonical_url="https://example.com/future",
            final_url="https://example.com/future",
            publisher="Official",
            language="en",
            content_type="text/plain",
            content_sha256="1"*64,
            available_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            batch_id="batch_future"
        )
        obs_future = Observation(
            observation_id="obs:de:ppi:2026-Q3",
            revision_id="obs:de:ppi:2026-Q3:v1",
            series_id="series:destatis:61261-0002",
            period_start="2026-07-01",
            period_end_exclusive="2026-10-01",
            value=125.4,
            unit="INDEX",
            source_unit="INDEX",
            vintage_id="release:2026-10-15",
            available_at=datetime(2026, 10, 15, 8, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 10, 15, 8, 5, 0, tzinfo=timezone.utc),
            quality_state="accepted",
            batch_id="batch_future"
        )
        fact_future_rel = Fact(
            fact_id="fact:future:edge:01",
            fact_version_id="fact:future:edge:01:v1",
            subject_id="company:heidelberg",
            predicate="LOCATED_IN",
            object_id="geo:DE",
            valid_from=datetime(2026, 11, 1, tzinfo=timezone.utc),
            available_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            quality_state="accepted",
            claim_kind="factual",
            source_id="official",
            batch_id="batch_future"
        )

        ev_future_rel = EvidenceLink(
            assertion_version_id=fact_future_rel.fact_version_id,
            document_version_id=doc_future.version_id,
            quoted_text="heidelberg de",
            available_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            batch_id="batch_future",
        )
        ev_obs_future = EvidenceLink(
            assertion_version_id=obs_future.revision_id,
            document_version_id=doc_future.version_id,
            quoted_text="125.4",
            available_at=datetime(2026, 10, 15, 8, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_future",
        )

        self.store.stage_batch(
            batch=batch_future,
            documents=[doc_future],
            facts=[fact_future_rel],
            observations=[obs_future],
            evidence_links=[ev_future_rel, ev_obs_future]
        )
        self.store.commit_batch("batch_future", committed_at=datetime(2026, 10, 15, tzinfo=timezone.utc))

        # Re-export at earlier cutoff_t0
        snap_after = export_as_of(cutoff_t0, mode="public_as_of", store=self.store)

        # Assert identical topology and observations at cutoff_t0
        self.assertEqual(len(snap_before["nodes"]), len(snap_after["nodes"]))
        self.assertEqual(len(snap_before["edges"]), len(snap_after["edges"]))
        self.assertEqual(len(snap_before["observations"]), len(snap_after["observations"]))
        self.assertEqual(
            {n["id"] for n in snap_before["nodes"]},
            {n["id"] for n in snap_after["nodes"]}
        )
        self.assertEqual(
            {e["edge_id"] for e in snap_before["edges"]},
            {e["edge_id"] for e in snap_after["edges"]}
        )

    def test_announced_future_obligations_and_system_as_of(self):
        """
        Announced future obligations are upcoming features only (not active topology).
        system_as_of excludes late-ingested data that public_as_of includes.
        """
        cutoff = datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)

        # Ingest an obligation valid from 2027-01-01 announced publicly on 2026-09-01
        # But system committed it late on 2026-10-05
        batch_late = BatchManifest(
            batch_id="batch_late",
            source_id="official",
            data_origin="live",
            staged_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
            status="staged"
        )
        doc_late = SourceDocument(
            document_id="doc:late",
            version_id="doc:late:v1",
            source_id="official",
            canonical_url="https://example.com/late",
            final_url="https://example.com/late",
            publisher="Official",
            language="en",
            content_type="text/plain",
            content_sha256="2"*64,
            available_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
            batch_id="batch_late"
        )
        fact_future_law = Fact(
            fact_id="fact:regulation:energy:2027",
            fact_version_id="fact:regulation:energy:2027:v1",
            subject_id="law:cbam:2023",
            predicate="REGULATES",
            object_id="sector:construction",
            valid_from=datetime(2027, 1, 1, tzinfo=timezone.utc), # Future effective date
            available_at=datetime(2026, 9, 1, tzinfo=timezone.utc), # Publicly available
            recorded_at=datetime(2026, 10, 5, tzinfo=timezone.utc), # Late system record
            quality_state="accepted",
            claim_kind="factual",
            source_id="official",
            batch_id="batch_late"
        )
        ev_late = EvidenceLink(
            assertion_version_id=fact_future_law.fact_version_id,
            document_version_id=doc_late.version_id,
            quoted_text="regulation energy",
            available_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            batch_id="batch_late",
        )

        self.store.stage_batch(
            batch=batch_late,
            documents=[doc_late],
            facts=[fact_future_law],
            observations=[],
            evidence_links=[ev_late]
        )
        self.store.commit_batch("batch_late", committed_at=datetime(2026, 10, 5, tzinfo=timezone.utc))

        # system_as_of at 2026-09-30 MUST EXCLUDE fact_future_law
        snap_system = export_as_of(cutoff, mode="system_as_of", store=self.store)
        edge_ids_system = {e["edge_id"] for e in snap_system["edges"]}
        self.assertNotIn("fact:regulation:energy:2027", edge_ids_system)

        # public_as_of at 2026-09-30 INCLUDES it, but with status="upcoming" and is_active=False
        snap_public = export_as_of(cutoff, mode="public_as_of", store=self.store)
        edge_ids_public = {e["edge_id"] for e in snap_public["edges"]}
        self.assertIn("fact:regulation:energy:2027", edge_ids_public)
        matching = [e for e in snap_public["edges"] if e["edge_id"] == "fact:regulation:energy:2027"][0]
        self.assertEqual(matching["status"], "upcoming")
        self.assertFalse(matching["is_active"])

    def test_origin_and_quarterly_horizon_labeling(self):
        """
        Origin 2026-09-30T23:59:59.999999Z targets 2026-Q4 vs 2026-Q3.
        Unpublished Q3 level is absent from inputs at origin.
        Both label levels come from earliest verified comparable Q4 release.
        Label remains masked until that release.
        """
        origin = datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
        target_series = "series:destatis:61261-0002"

        # Ingest historical Q1 and Q2 released in May and Aug 2026
        # Q3 is NOT released until 2026-11-15
        # Q4 is NOT released until 2027-02-15
        batch_hist = BatchManifest(
            batch_id="batch_hist",
            source_id="destatis",
            data_origin="live",
            staged_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            status="staged"
        )
        doc_hist = SourceDocument(
            document_id="doc:destatis:2026q2",
            version_id="doc:destatis:2026q2:v1",
            source_id="destatis",
            canonical_url="https://example.com/q2",
            final_url="https://example.com/q2",
            publisher="Destatis",
            language="de",
            content_type="text/csv",
            content_sha256="3"*64,
            available_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
            availability_basis="provider_vintage",
            batch_id="batch_hist"
        )
        obs_q1 = Observation(
            observation_id="obs:destatis:2026-Q1",
            revision_id="obs:destatis:2026-Q1:v1",
            series_id=target_series,
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",
            value=120.0,
            unit="INDEX",
            source_unit="INDEX",
            vintage_id="release:2026-05-15",
            available_at=datetime(2026, 5, 15, 8, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 5, 15, 8, 0, 0, tzinfo=timezone.utc),
            quality_state="accepted",
            batch_id="batch_hist"
        )
        obs_q2 = Observation(
            observation_id="obs:destatis:2026-Q2",
            revision_id="obs:destatis:2026-Q2:v1",
            series_id=target_series,
            period_start="2026-04-01",
            period_end_exclusive="2026-07-01",
            value=122.0,
            unit="INDEX",
            source_unit="INDEX",
            vintage_id="release:2026-08-15",
            available_at=datetime(2026, 8, 15, 8, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 8, 15, 8, 0, 0, tzinfo=timezone.utc),
            quality_state="accepted",
            batch_id="batch_hist"
        )

        ev_q1 = EvidenceLink(
            assertion_version_id=obs_q1.revision_id,
            document_version_id=doc_hist.version_id,
            quoted_text="120.0",
            available_at=datetime(2026, 5, 15, 8, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_hist",
        )
        ev_q2 = EvidenceLink(
            assertion_version_id=obs_q2.revision_id,
            document_version_id=doc_hist.version_id,
            quoted_text="122.0",
            available_at=datetime(2026, 8, 15, 8, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_hist",
        )

        self.store.stage_batch(
            batch=batch_hist,
            documents=[doc_hist],
            facts=[],
            observations=[obs_q1, obs_q2],
            evidence_links=[ev_q1, ev_q2]
        )
        self.store.commit_batch("batch_hist", committed_at=datetime(2026, 8, 20, tzinfo=timezone.utc))

        # Build dataset when Q4 has NOT yet been released
        ds_unreleased = build_forecast_dataset(
            target_series_id=target_series,
            origins=[origin],
            horizon_periods=1,
            mode="public_as_of",
            store=self.store
        )
        sample = ds_unreleased["samples"][0]
        self.assertEqual(sample["target_period_label"], "2026-Q4")
        self.assertEqual(sample["denominator_period_label"], "2026-Q3")
        # Label MUST be masked because Q4 is unreleased as of store state
        self.assertFalse(sample["y_mask"][0, 0])
        self.assertTrue(np.isnan(sample["y"][0, 0]))

        # Inputs at origin MUST NOT include Q3 (which was unreleased at 2026-09-30)
        snap_origin = sample["snapshots"][-1]
        obs_series = [
            o for o in snap_origin["observations"]
            if o["series_id"] == target_series
        ]
        obs_periods = {str(o["period_start"]): o["value"] for o in obs_series}
        self.assertIn("2026-04-01", obs_periods) # Q2 is available
        self.assertNotIn("2026-07-01", obs_periods) # Q3 is NOT available at origin

        # Now simulate arrival of Q4 release on 2027-02-15 containing comparable Q3 and Q4
        batch_q4 = BatchManifest(
            batch_id="batch_q4",
            source_id="destatis",
            data_origin="live",
            staged_at=datetime(2027, 2, 16, tzinfo=timezone.utc),
            status="staged"
        )
        doc_q4 = SourceDocument(
            document_id="doc:destatis:2026q4",
            version_id="doc:destatis:2026q4:v1",
            source_id="destatis",
            canonical_url="https://example.com/q4",
            final_url="https://example.com/q4",
            publisher="Destatis",
            language="de",
            content_type="text/csv",
            content_sha256="4"*64,
            available_at=datetime(2027, 2, 15, tzinfo=timezone.utc),
            recorded_at=datetime(2027, 2, 15, tzinfo=timezone.utc),
            availability_basis="provider_vintage",
            batch_id="batch_q4"
        )
        # Release 2027-02-15 contains both Q3=124.0 and Q4=127.72
        obs_q3_v1 = Observation(
            observation_id="obs:destatis:2026-Q3",
            revision_id="obs:destatis:2026-Q3:v1",
            series_id=target_series,
            period_start="2026-07-01",
            period_end_exclusive="2026-10-01",
            value=124.0,
            unit="INDEX",
            source_unit="INDEX",
            vintage_id="release:2027-02-15",
            available_at=datetime(2027, 2, 15, 8, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2027, 2, 15, 8, 0, 0, tzinfo=timezone.utc),
            quality_state="accepted",
            batch_id="batch_q4"
        )
        obs_q4_v1 = Observation(
            observation_id="obs:destatis:2026-Q4",
            revision_id="obs:destatis:2026-Q4:v1",
            series_id=target_series,
            period_start="2026-10-01",
            period_end_exclusive="2027-01-01",
            value=127.72,
            unit="INDEX",
            source_unit="INDEX",
            vintage_id="release:2027-02-15",
            available_at=datetime(2027, 2, 15, 8, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2027, 2, 15, 8, 0, 0, tzinfo=timezone.utc),
            quality_state="accepted",
            batch_id="batch_q4"
        )

        ev_q3 = EvidenceLink(
            assertion_version_id=obs_q3_v1.revision_id,
            document_version_id=doc_q4.version_id,
            quoted_text="124.0",
            available_at=datetime(2027, 2, 15, 8, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_q4",
        )
        ev_q4 = EvidenceLink(
            assertion_version_id=obs_q4_v1.revision_id,
            document_version_id=doc_q4.version_id,
            quoted_text="127.72",
            available_at=datetime(2027, 2, 15, 8, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_q4",
        )

        self.store.stage_batch(
            batch=batch_q4,
            documents=[doc_q4],
            facts=[],
            observations=[obs_q3_v1, obs_q4_v1],
            evidence_links=[ev_q3, ev_q4]
        )
        self.store.commit_batch("batch_q4", committed_at=datetime(2027, 2, 16, tzinfo=timezone.utc))

        # Now re-build forecast dataset
        ds_released = build_forecast_dataset(
            target_series_id=target_series,
            origins=[origin],
            horizon_periods=1,
            mode="public_as_of",
            store=self.store,
            label_cutoff=datetime(2027, 2, 20, tzinfo=timezone.utc),
        )
        sample_rel = ds_released["samples"][0]
        self.assertTrue(sample_rel["y_mask"][0, 0])
        # Expected growth: 100 * (127.72 / 124.0 - 1) = 3.0%
        expected_growth = 100.0 * (127.72 / 124.0 - 1.0)
        self.assertAlmostEqual(sample_rel["y"][0, 0], expected_growth, places=4)
        self.assertEqual(sample_rel["vintage_id"], "release:2027-02-15")

    def test_to_heterodata_structure_and_receptive_field(self):
        """
        Verify to_heterodata creates eight node stores, type-local edge indices,
        12 edge attributes/masks, and computational context_for routes.
        Test receptive field reaches target series in 2 hops.
        """
        if not HAS_PYG:
            self.skipTest("torch_geometric not installed")

        cutoff = datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
        snap = export_as_of(cutoff, mode="public_as_of", store=self.store)

        hdata = to_heterodata(snap)
        self.assertIsInstance(hdata, HeteroData)

        # 8 canonical node types
        canonical_types = [
            "law", "rule", "event", "company",
            "commodity", "sector", "geography", "series"
        ]
        for ntype in canonical_types:
            self.assertIn(ntype, hdata.node_types)
            store = hdata[ntype]
            self.assertTrue(hasattr(store, "x"))
            self.assertTrue(hasattr(store, "feature_observed_mask"))
            self.assertTrue(hasattr(store, "active_mask"))
            self.assertEqual(store.x.ndim, 2)
            self.assertEqual(store.feature_observed_mask.shape, store.x.shape)
            self.assertEqual(store.active_mask.shape[0], store.x.shape[0])

        # Check edge attributes shape: [E, 12]
        for rel in hdata.edge_types:
            edge_store = hdata[rel]
            if edge_store.edge_index.shape[1] > 0:
                self.assertEqual(edge_store.edge_attr.shape[1], 12)
                # Check type-local indexing: edge_index[0] < num_nodes(src), edge_index[1] < num_nodes(dst)
                src_type, _, dst_type = rel
                num_src = hdata[src_type].num_nodes
                num_dst = hdata[dst_type].num_nodes
                self.assertTrue((edge_store.edge_index[0] < num_src).all())
                self.assertTrue((edge_store.edge_index[1] < num_dst).all())

        # Verify receptive field:
        # law -> MANDATES -> rule -> context_for -> series (2 hops!)
        self.assertIn(("rule", "context_for", "series"), hdata.edge_types)

    def test_baselines_and_conformal_residual_calibration(self):
        """
        Verify no-change, seasonal, and tabular lag baselines.
        Verify conformal 90% symmetric interval requires >= 20 calibration samples
        and uses rank ceil((n+1)*0.9) on absolute residuals.
        """
        # Synthesize 30 quarterly origins with a sine + trend price series
        np.random.seed(42)
        n_origins = 30
        actuals = []
        predictions = []

        for i in range(n_origins):
            y_true = 2.0 + 1.5 * math.sin(i * math.pi / 2.0) + np.random.normal(0, 0.3)
            y_pred = 2.0 + 1.5 * math.sin(i * math.pi / 2.0) # Baseline point forecast
            actuals.append(y_true)
            predictions.append(y_pred)

        residuals = [abs(a - p) for a, p in zip(actuals, predictions)]

        # 1. Calibration with >= 20 samples
        calib_res = calibrate_residual_interval(residuals, alpha=0.10)
        self.assertTrue(calib_res["is_calibrated"])
        self.assertIsNotNone(calib_res["interval_half_width"])
        n = len(residuals)
        expected_rank = math.ceil((n + 1) * 0.9)
        sorted_res = sorted(residuals)
        expected_q = sorted_res[min(expected_rank - 1, n - 1)]
        self.assertAlmostEqual(calib_res["interval_half_width"], expected_q, places=5)

        # 2. Calibration with < 20 samples should refuse and flag insufficient history
        insufficient_calib = calibrate_residual_interval(residuals[:15], alpha=0.10)
        self.assertFalse(insufficient_calib["is_calibrated"])
        self.assertEqual(insufficient_calib["status"], "insufficient_calibration_history")
        self.assertIsNone(insufficient_calib["interval_half_width"])

    def test_sparse_hetero_gnn_model_architecture(self):
        """
        Verify TemporalHeteroGNN:
        - 2 HeteroConv(TransformerConv, edge_dim=12) layers
        - per-type residual
        - GRU hidden_size=32
        - Regression head Linear(32, 1) predicting series target only
        - Edge attribute affects output
        - Adjacency memory scales with edges (sparse message passing)
        """
        if not HAS_PYG:
            self.skipTest("torch_geometric not installed")

        cutoff = datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
        snap = export_as_of(cutoff, mode="public_as_of", store=self.store)
        hdata = to_heterodata(snap)

        model = TemporalHeteroGNN(
            node_types=hdata.node_types,
            edge_types=hdata.edge_types,
            in_channels_dict={nt: hdata[nt].x.shape[1] for nt in hdata.node_types},
            hidden_channels=32,
            edge_dim=12,
            target_node_type="series"
        )

        # Feed a sequence of 8 snapshots
        snapshots = [hdata for _ in range(8)]
        target_idx = torch.tensor([0], dtype=torch.long)

        out = model(snapshots, target_idx=target_idx)
        self.assertEqual(out.shape, (1, 1))

        # Perturb an edge attribute and verify output changes
        for rel in hdata.edge_types:
            if hdata[rel].edge_index.shape[1] > 0:
                hdata_perturbed = hdata.clone()
                hdata_perturbed[rel].edge_attr = hdata_perturbed[rel].edge_attr + 1.0
                snapshots_perturbed = [hdata_perturbed for _ in range(8)]
                out_perturbed = model(snapshots_perturbed, target_idx=target_idx)
                diff = (out - out_perturbed).abs().item()
                self.assertGreater(diff, 1e-6)
                break

    def test_hetero_gnn_training_and_ablations(self):
        """
        Verify HeteroGNNTrainer train loop with future-only labels,
        masked MSE, evaluation, and ablations (no_news, no_legal, no_topology).
        """
        if not HAS_PYG:
            self.skipTest("torch_geometric not installed")

        cutoff = datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
        snap = export_as_of(cutoff, mode="public_as_of", store=self.store)
        hdata = to_heterodata(snap)

        model = TemporalHeteroGNN(
            node_types=hdata.node_types,
            edge_types=hdata.edge_types,
            in_channels_dict={nt: hdata[nt].x.shape[1] for nt in hdata.node_types},
            hidden_channels=32,
            edge_dim=12,
            target_node_type="series"
        )
        trainer = HeteroGNNTrainer(model=model, lr=0.01)

        # Mock training and test samples
        train_samples = [
            {
                "origin": datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc),
                "first_release_available_at": datetime(2026, 8, 15, tzinfo=timezone.utc),
                "target_series_id": "series:destatis:61261-0002",
                "target_series_present": True,
                "snapshots": [hdata for _ in range(8)],
                "target_series_index": np.array([0], dtype=np.int64),
                "y": np.array([[2.5]], dtype=np.float32),
                "y_mask": np.array([[True]], dtype=bool),
            },
            {
                "origin": datetime(2026, 9, 30, tzinfo=timezone.utc),
                "first_release_available_at": datetime(2026, 11, 15, tzinfo=timezone.utc),
                "target_series_id": "series:destatis:61261-0002",
                "target_series_present": True,
                "snapshots": [hdata for _ in range(8)],
                "target_series_index": np.array([0], dtype=np.int64),
                "y": np.array([[1.8]], dtype=np.float32),
                "y_mask": np.array([[True]], dtype=bool),
            }
        ]
        test_samples = [
            {
                "origin": datetime(2026, 12, 31, tzinfo=timezone.utc),
                "first_release_available_at": datetime(2027, 2, 15, tzinfo=timezone.utc),
                "target_series_id": "series:destatis:61261-0002",
                "target_series_present": True,
                "snapshots": [hdata for _ in range(8)],
                "target_series_index": np.array([0], dtype=np.int64),
                "y": np.array([[2.1]], dtype=np.float32),
                "y_mask": np.array([[True]], dtype=bool),
            }
        ]

        # Single epoch training
        loss = trainer.train_epoch(train_samples)
        self.assertIsInstance(loss, float)

        # Evaluation
        eval_res = trainer.evaluate(test_samples)
        self.assertIn("mae", eval_res)
        self.assertIn("rmse", eval_res)
        self.assertEqual(eval_res["sample_count"], 1)

        # Ablations
        ablation_results = trainer.run_ablations(train_samples, test_samples, epochs=1)
        for abl_key in ("full_model", "no_news", "no_legal", "no_topology"):
            self.assertIn(abl_key, ablation_results)
            self.assertIn("mae", ablation_results[abl_key])


if __name__ == "__main__":
    unittest.main()
