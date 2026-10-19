"""
Automated Test Suite for Task 11:
Construction outlook with evidence, limitations, and strict absence of causal claims
or financial proxy substitution.
"""
from src.config import primary_target_series_id

import tempfile
import unittest
from datetime import datetime, timezone
import pytest

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
from src.storage.dashboard_data import get_construction_outlook

TARGET = primary_target_series_id()


class TestConstructionOutlook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = DualLayerStore(storage_dir=self.temp_dir.name)

        # Baseline entities
        self.e_law_active = Entity(
            entity_id="law:cbam:2023",
            entity_type="LAW",
            canonical_label="CBAM Regulation (EU) 2023/956",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_rule_active = Entity(
            entity_id="rule:cbam:art6",
            entity_type="ACTION",
            entity_subtype="LEGAL_RULE",
            canonical_label="CBAM Transitional Reporting",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_law_future = Entity(
            entity_id="law:epbd:2024",
            entity_type="LAW",
            canonical_label="EPBD Recast Directive (EU) 2024/1275",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_rule_future = Entity(
            entity_id="rule:epbd:zero_emission",
            entity_type="ACTION",
            entity_subtype="LEGAL_RULE",
            canonical_label="Zero-Emission Building Mandate 2028",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_event = Entity(
            entity_id="event:news:steel_curtailment",
            entity_type="ACTION",
            entity_subtype="ECONOMIC_EVENT",
            canonical_label="Reported Blast Furnace Idle in Duisburg",
            created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_series_construction = Entity(
            entity_id=TARGET,
            entity_type="ECONOMIC_INDEX",
            canonical_label="German Construction PPI Residential (Destatis 61261-0002)",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )
        self.e_series_smard = Entity(
            entity_id="series:smard:de:generation",
            entity_type="ECONOMIC_INDEX",
            canonical_label="German Wholesale Electricity Generation",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.series_def = SeriesDefinition(
            series_id=TARGET,
            source_id="destatis",
            dataset_id="61261-0002",
            provider_series_key="PREIS_BAUWERKE_WOHN",
            metric_name="construction_producer_price_index",
            unit="INDEX_2021_100",
            frequency="Q",
            geography_id="geo:DE",
            base_period="2021=100",
            adjustment="unadjusted",
            measurement_kind="producer_price",
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.doc_destatis = SourceDocument(
            document_id="doc:destatis:61261",
            version_id="doc:destatis:61261:v1",
            source_id="destatis",
            canonical_url="https://www.destatis.de/EN/Themes/Economy/Prices/Construction-Prices/_node.html",
            final_url="https://www.destatis.de/EN/Themes/Economy/Prices/Construction-Prices/_node.html",
            publisher="Statistisches Bundesamt",
            language="de",
            content_type="application/json",
            content_sha256="1"*64,
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.doc_eurlex = SourceDocument(
            document_id="doc:eurlex:cbam",
            version_id="doc:eurlex:cbam:v1",
            source_id="eurlex",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
            final_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
            publisher="Publications Office of the European Union",
            language="en",
            content_type="text/html",
            content_sha256="2"*64,
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.doc_news = SourceDocument(
            document_id="doc:news:reuters_steel",
            version_id="doc:news:reuters_steel:v1",
            source_id="reuters",
            canonical_url="https://reuters.com/business/steel-plant-curtailment",
            final_url="https://reuters.com/business/steel-plant-curtailment",
            publisher="Reuters News",
            language="en",
            content_type="text/html",
            content_sha256="3"*64,
            available_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            batch_id="batch_init"
        )

        self.fact_active_rule = Fact(
            fact_id="fact:active:01",
            fact_version_id="fact:active:01:v1",
            source_id="eurlex",
            subject_id="law:cbam:2023",
            predicate="MANDATES",
            object_id="rule:cbam:art6",
            valid_from=datetime(2023, 10, 1, tzinfo=timezone.utc),
            available_at=datetime(2023, 10, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 10, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
            evidence_document_id="doc:eurlex:cbam"
        )

        self.fact_future_rule = Fact(
            fact_id="fact:future:01",
            fact_version_id="fact:future:01:v1",
            source_id="eurlex",
            subject_id="law:epbd:2024",
            predicate="MANDATES",
            object_id="rule:epbd:zero_emission",
            valid_from=datetime(2028, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2024, 4, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2024, 4, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
            evidence_document_id="doc:eurlex:cbam"
        )

        self.fact_reported_event = Fact(
            fact_id="fact:event:01",
            fact_version_id="fact:event:01:v1",
            source_id="reuters",
            subject_id="event:news:steel_curtailment",
            predicate="IMPACTS",
            object_id=TARGET,
            valid_from=datetime(2024, 2, 1, tzinfo=timezone.utc),
            available_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
            evidence_document_id="doc:news:reuters_steel"
        )

        ev1 = EvidenceLink(
            assertion_version_id=self.fact_active_rule.fact_version_id,
            document_version_id=self.doc_eurlex.version_id,
            quoted_text="cbam",
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
        )
        ev2 = EvidenceLink(
            assertion_version_id=self.fact_future_rule.fact_version_id,
            document_version_id=self.doc_eurlex.version_id,
            quoted_text="epbd",
            available_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
        )
        ev3 = EvidenceLink(
            assertion_version_id=self.fact_reported_event.fact_version_id,
            document_version_id=self.doc_news.version_id,
            quoted_text="steel curtailment",
            available_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            batch_id="batch_init",
        )

        # Stage and commit initial entities and facts
        batch = BatchManifest(
            batch_id="batch_init",
            source_id="official",
            data_origin="live",
            staged_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            status="staged"
        )
        self.store.stage_batch(
            batch=batch,
            documents=[self.doc_destatis, self.doc_eurlex, self.doc_news],
            facts=[self.fact_active_rule, self.fact_future_rule, self.fact_reported_event],
            observations=[],
            entities=[
                self.e_law_active, self.e_rule_active,
                self.e_law_future, self.e_rule_future,
                self.e_event, self.e_series_construction, self.e_series_smard
            ],
            series=[self.series_def],
            evidence_links=[ev1, ev2, ev3],
        )
        self.store.commit_batch("batch_init")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_target_price_data(self):
        """When target series has no observations, returns clean report with explicit missing status and gaps."""
        cutoff = datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        assert outlook["target_series_id"] == TARGET
        assert "residential" in outlook["target_definition"].lower()
        assert outlook["latest_observation"] is None
        assert outlook["point_forecast"] is None
        assert outlook["interval"]["calibration_status"] in ("unavailable", "insufficient_calibration_history")
        assert any("missing" in g.lower() for g in outlook["source_gaps"])
        assert "limitations" in outlook
        assert any("caus" in lim.lower() for lim in outlook["limitations"])

    def test_stale_target_price_data(self):
        """When observations exist but are older than the expected lag, mark latest observation as STALE."""
        doc_old = SourceDocument.from_content(
            source_id="destatis",
            canonical_url="https://destatis.de/old_obs",
            publisher="Destatis",
            content_bytes=b"old obs",
            batch_id="batch_old_obs",
            available_at=datetime(2023, 8, 15, tzinfo=timezone.utc),
        )
        obs_old = Observation(
            observation_id="obs:61261:2023Q2",
            series_id=TARGET,
            period_start="2023-04-01",
            period_end_exclusive="2023-07-01",
            value=105.0,
            unit="INDEX_2021_100",
            vintage_id="vin:2023Q2:v1",
            available_at=datetime(2023, 8, 15, tzinfo=timezone.utc),
            recorded_at=datetime(2023, 8, 15, tzinfo=timezone.utc),
            batch_id="batch_old_obs",
            evidence_document_id=doc_old.document_id,
        )
        link_old = EvidenceLink(
            assertion_version_id=obs_old.revision_id,
            document_version_id=doc_old.version_id,
            quoted_text="105.0",
            available_at=datetime(2023, 8, 15, tzinfo=timezone.utc),
            batch_id="batch_old_obs",
        )
        batch = BatchManifest(
            batch_id="batch_old_obs",
            source_id="destatis",
            data_origin="live",
            staged_at=datetime(2023, 8, 15, tzinfo=timezone.utc),
            status="staged"
        )
        self.store.stage_batch(batch=batch, documents=[doc_old], facts=[], observations=[obs_old], evidence_links=[link_old])
        self.store.commit_batch("batch_old_obs")

        # Cutoff is 2024-06-30 (over 3 quarters later, definitely stale)
        cutoff = datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        assert outlook["latest_observation"] is not None
        assert outlook["latest_observation"]["period"] == "2023-Q2"
        assert outlook["latest_observation"]["status"] == "STALE"
        assert any("stale" in g.lower() for g in outlook["source_gaps"])

    def test_insufficient_calibration_history(self):
        """Fewer than 20 quarterly observations must explicitly yield insufficient calibration status."""
        doc_hist = SourceDocument.from_content(
            source_id="destatis",
            canonical_url="https://destatis.de/short_hist",
            publisher="Destatis",
            content_bytes=b"short hist",
            batch_id="batch_short_history",
            available_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        )
        observations = []
        links = []
        for i in range(1, 7):
            q_num = ((i - 1) % 4) + 1
            year = 2022 + ((i - 1) // 4)
            obs = Observation(
                observation_id=f"obs:61261:{year}Q{q_num}",
                series_id=TARGET,
                period_start=f"{year}-{(q_num-1)*3+1:02d}-01",
                period_end_exclusive=f"{year}-{q_num*3:02d}-28",
                value=100.0 + i * 1.5,
                unit="INDEX_2021_100",
                vintage_id=f"vin:{year}Q{q_num}:v1",
                available_at=datetime(year, q_num * 3, 1, tzinfo=timezone.utc),
                recorded_at=datetime(year, q_num * 3, 1, tzinfo=timezone.utc),
                batch_id="batch_short_history",
                evidence_document_id=doc_hist.document_id,
            )
            observations.append(obs)
            links.append(EvidenceLink(
                assertion_version_id=obs.revision_id,
                document_version_id=doc_hist.version_id,
                quoted_text=str(obs.value),
                available_at=datetime(year, q_num * 3, 1, tzinfo=timezone.utc),
                batch_id="batch_short_history",
            ))

        batch = BatchManifest(
            batch_id="batch_short_history",
            source_id="destatis",
            data_origin="live",
            staged_at=datetime(2023, 7, 1, tzinfo=timezone.utc),
            status="staged"
        )
        self.store.stage_batch(batch=batch, documents=[doc_hist], facts=[], observations=observations, evidence_links=links)
        self.store.commit_batch("batch_short_history")

        cutoff = datetime(2023, 7, 1, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        assert outlook["interval"]["calibration_status"] == "insufficient_calibration_history"
        assert outlook["interval"]["lower"] is None
        assert outlook["interval"]["upper"] is None
        assert any("insufficient" in g.lower() or "<20" in g for g in outlook["source_gaps"])

    def test_forthcoming_obligation_vs_active_obligation(self):
        """Active rules (valid_from <= cutoff) and forthcoming obligations (valid_from > cutoff) are strictly separated."""
        cutoff = datetime(2024, 6, 30, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        reg_context = outlook["regulatory_context"]
        active = [r for r in reg_context if r["status"] == "ACTIVE"]
        forthcoming = [r for r in reg_context if r["status"] == "FORTHCOMING"]

        active_labels = [r["label"] for r in active]
        forthcoming_labels = [r["label"] for r in forthcoming]

        assert any("cbam" in l.lower() for l in active_labels)
        assert any("epbd" in l.lower() or "zero-emission" in l.lower() for l in forthcoming_labels)
        assert not any("epbd" in l.lower() for l in active_labels)

    def test_reported_closure_vs_measured_capacity(self):
        """Supply context strictly distinguishes REPORTED news claims from MEASURED physical statistics."""
        # Add a measured SMARD electricity observation
        doc_smard = SourceDocument.from_content(
            source_id="smard",
            canonical_url="https://smard.de/generation",
            publisher="Bundesnetzagentur",
            content_bytes=b"smard",
            batch_id="batch_smard",
            available_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
        )
        obs_smard = Observation(
            observation_id="obs:smard:202402",
            series_id="series:smard:de:generation",
            period_start="2024-02-01",
            period_end_exclusive="2024-03-01",
            value=45000.0,
            unit="MWH",
            vintage_id="vin:smard:202402:v1",
            available_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            recorded_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            batch_id="batch_smard",
            evidence_document_id=doc_smard.document_id,
        )
        link_smard = EvidenceLink(
            assertion_version_id=obs_smard.revision_id,
            document_version_id=doc_smard.version_id,
            quoted_text="45000.0",
            available_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            batch_id="batch_smard",
        )
        series_smard = SeriesDefinition(
            series_id="series:smard:de:generation",
            source_id="smard",
            dataset_id="generation",
            provider_series_key="MARKET_DATA_DAY_AHEAD_DE_LU",
            metric_name="electricity_generation",
            unit="MWH",
            batch_id="batch_smard",
        )
        batch = BatchManifest(
            batch_id="batch_smard",
            source_id="smard",
            data_origin="live",
            staged_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            status="staged"
        )
        self.store.stage_batch(batch=batch, documents=[doc_smard], facts=[], observations=[obs_smard], series=[series_smard], evidence_links=[link_smard])
        self.store.commit_batch("batch_smard")

        cutoff = datetime(2024, 6, 30, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        supply = outlook["supply_context"]
        reported = [s for s in supply if s["kind"] == "REPORTED"]
        measured = [s for s in supply if s["kind"] == "MEASURED"]

        assert len(reported) > 0, "Expected at least one reported news claim"
        assert len(measured) > 0, "Expected at least one measured context item"
        assert any("blast furnace" in s["label"].lower() or "idle" in s["label"].lower() for s in reported)
        assert any("smard" in s["series_id"].lower() or "electricity" in s["label"].lower() for s in measured)
        # Ensure reported items have disclaimer or claim distinction
        assert all(s["claim_status"] == "unverified_signal" for s in reported)

    def test_correction_after_cutoff_is_excluded(self):
        """Observations revised after cutoff must not leak into the point-in-time outlook."""
        doc_v1 = SourceDocument.from_content(
            source_id="destatis",
            canonical_url="https://destatis.de/obs_v1",
            publisher="Destatis",
            content_bytes=b"obs v1",
            batch_id="batch_obs_v1",
            available_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
        )
        obs_v1 = Observation(
            observation_id="obs:61261:2023Q4",
            series_id=TARGET,
            period_start="2023-10-01",
            period_end_exclusive="2024-01-01",
            value=110.0,
            unit="INDEX_2021_100",
            vintage_id="vin:2023Q4:v1",
            available_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            recorded_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            batch_id="batch_obs_v1",
            evidence_document_id=doc_v1.document_id,
        )
        link_v1 = EvidenceLink(
            assertion_version_id=obs_v1.revision_id,
            document_version_id=doc_v1.version_id,
            quoted_text="110.0",
            available_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            batch_id="batch_obs_v1",
        )
        batch1 = BatchManifest(
            batch_id="batch_obs_v1",
            source_id="destatis",
            data_origin="live",
            staged_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            status="staged"
        )
        self.store.stage_batch(batch=batch1, documents=[doc_v1], facts=[], observations=[obs_v1], evidence_links=[link_v1])
        self.store.commit_batch("batch_obs_v1")

        doc_v2 = SourceDocument.from_content(
            source_id="destatis",
            canonical_url="https://destatis.de/obs_v2",
            publisher="Destatis",
            content_bytes=b"obs v2",
            batch_id="batch_obs_v2",
            available_at=datetime(2024, 8, 15, tzinfo=timezone.utc),
        )
        obs_v2 = Observation(
            observation_id="obs:61261:2023Q4",
            series_id=TARGET,
            period_start="2023-10-01",
            period_end_exclusive="2024-01-01",
            value=112.5,
            unit="INDEX_2021_100",
            vintage_id="vin:2023Q4:v2",
            available_at=datetime(2024, 8, 15, tzinfo=timezone.utc),
            recorded_at=datetime(2024, 8, 15, tzinfo=timezone.utc),
            batch_id="batch_obs_v2",
            evidence_document_id=doc_v2.document_id,
        )
        link_v2 = EvidenceLink(
            assertion_version_id=obs_v2.revision_id,
            document_version_id=doc_v2.version_id,
            quoted_text="112.5",
            available_at=datetime(2024, 8, 15, tzinfo=timezone.utc),
            batch_id="batch_obs_v2",
        )
        batch2 = BatchManifest(
            batch_id="batch_obs_v2",
            source_id="destatis",
            data_origin="live",
            staged_at=datetime(2024, 8, 15, tzinfo=timezone.utc),
            status="staged"
        )
        self.store.stage_batch(batch=batch2, documents=[doc_v2], facts=[], observations=[obs_v2], evidence_links=[link_v2])
        self.store.commit_batch("batch_obs_v2")

        # Query as of 2024-03-01: should see value=110.0 (v1), NOT 112.5 (v2)
        cutoff = datetime(2024, 3, 1, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        assert outlook["latest_observation"] is not None
        assert outlook["latest_observation"]["value"] == 110.0
        assert outlook["latest_observation"]["vintage_id"] == "vin:2023Q4:v1"

    def test_no_financial_proxy_substitution(self):
        """Rejects financial proxies and enforces that no equity/CPI/house-price proxy replaces construction PPI."""
        cutoff = datetime(2024, 6, 30, tzinfo=timezone.utc)

        # Passing an equity or CPI proxy must raise ValueError or return an error outlook refusing substitution
        with pytest.raises(ValueError, match="proxy"):
            get_construction_outlook(
                cutoff=cutoff,
                target_series_id="series:yahoo:DAX",
                store=self.store
            )

        with pytest.raises(ValueError, match="proxy"):
            get_construction_outlook(
                cutoff=cutoff,
                target_series_id="series:ecb:cpi_germany",
                store=self.store
            )

    def test_no_evidence_presented_as_causal_proof(self):
        """Disclaimers must explicitly declare that graph edges and models are not causal proof."""
        cutoff = datetime(2024, 6, 30, tzinfo=timezone.utc)
        outlook = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        assert "limitations" in outlook
        disclaimer = " ".join(outlook["limitations"]).lower()
        assert "caus" in disclaimer
        assert "proxy" in disclaimer or "financial" in disclaimer
        assert "evidence" in outlook
        assert len(outlook["evidence"]) > 0
        for ev in outlook["evidence"]:
            assert "url" in ev or "document_id" in ev

    def test_read_only_contract(self):
        """Calling get_construction_outlook must be strictly read-only and leave store unchanged."""
        batches_before = len(self.store._canonical_batches)
        obs_before = len(self.store._canonical_observations)
        facts_before = len(self.store._canonical_facts)

        cutoff = datetime(2024, 6, 30, tzinfo=timezone.utc)
        _ = get_construction_outlook(cutoff=cutoff, target_series_id=TARGET, store=self.store)

        assert len(self.store._canonical_batches) == batches_before
        assert len(self.store._canonical_observations) == obs_before
        assert len(self.store._canonical_facts) == facts_before


if __name__ == "__main__":
    unittest.main()
