"""
Unit and Contract Tests for Canonical Ingestion Schemas, Stable Identity,
Revisions, and Quality Decisions (Task 2).
"""

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Test imports from src.schemas.ingestion
try:
    from src.schemas.ingestion import (
        SourceDocument,
        Entity,
        EntityAlias,
        Fact,
        EvidenceLink,
        SeriesDefinition,
        Observation,
        QualityDecision,
        BatchManifest,
        DatasetManifest,
        PredictionRecord,
        stable_id,
        validate_candidate
    )
    HAS_INGESTION_SCHEMAS = True
except ImportError:
    HAS_INGESTION_SCHEMAS = False

from src.models.dual_layer_model import NodeType as ModelNodeType, EdgeRelation as ModelEdgeRelation
from src.schemas.graph_temporal import NodeType as SchemaNodeType, EdgeRelation as SchemaEdgeRelation


class TestIngestionSchemas(unittest.TestCase):
    """Test suite for canonical schema contracts, stable identity, and quality decisions."""

    def test_schemas_importable(self):
        """Canonical ingestion contracts and interfaces must be importable."""
        self.assertTrue(HAS_INGESTION_SCHEMAS, "src.schemas.ingestion must provide all canonical models")

    def test_stable_id_deterministic_and_reproducible(self):
        """stable_id must use SHA-256 and be 100% deterministic across calls and processes."""
        id1 = stable_id("entity", "DESTATIS", "61261-0002")
        id2 = stable_id("entity", "DESTATIS", "61261-0002")
        self.assertEqual(id1, id2, "stable_id must be completely deterministic")
        self.assertTrue(id1.startswith("entity_"), "stable_id should incorporate namespace prefix")
        # Ensure 64-char hex hash is present
        self.assertEqual(len(id1.split("_", 1)[1]), 64)

        # Different input produces different id
        id3 = stable_id("entity", "DESTATIS", "61261-0003")
        self.assertNotEqual(id1, id3)

    def test_exact_duplicate_releases_share_ids_corrections_change_version_ids(self):
        """Duplicate releases share document_id; revisions create new version_ids."""
        content1 = b"Official Gazette text 2026-10-18"
        doc1 = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
            publisher="Publications Office",
            content_bytes=content1,
            batch_id="batch_001"
        )
        doc2 = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
            publisher="Publications Office",
            content_bytes=content1,
            batch_id="batch_002"
        )
        self.assertEqual(doc1.document_id, doc2.document_id)
        self.assertEqual(doc1.version_id, doc2.version_id)

        # Corrected content generates new version_id with identical document_id
        content_corrected = b"Official Gazette text 2026-10-18 Corrigendum"
        doc3 = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
            publisher="Publications Office",
            content_bytes=content_corrected,
            batch_id="batch_003"
        )
        self.assertEqual(doc1.document_id, doc3.document_id)
        self.assertNotEqual(doc1.version_id, doc3.version_id)

    def test_null_quantity_remains_null(self):
        """Unknown or confidential values must remain None, never coerced to 0.0."""
        obs = Observation(
            series_id="series_destatis_61261_0002",
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",
            value=None,
            original_value_text=".",
            source_unit="INDEX",
            unit="INDEX",
            missing_reason="CONFIDENTIAL_OR_UNPUBLISHED",
            batch_id="batch_001"
        )
        self.assertIsNone(obs.value, "Missing observation value must remain None")
        self.assertEqual(obs.missing_reason, "CONFIDENTIAL_OR_UNPUBLISHED")

    def test_missing_dates_do_not_become_today_or_1900(self):
        """Missing publication or event dates must remain None, not defaulted to today/1970/1900."""
        doc = SourceDocument(
            document_id="doc_123",
            version_id="ver_123",
            source_id="OFFICIAL_NEWS",
            canonical_url="https://example.com/news/1",
            final_url="https://example.com/news/1",
            publisher="Press Corner",
            content_type="text/html",
            content_sha256="abc12345" * 8,
            byte_count=1024,
            published_at=None,
            source_updated_at=None,
            available_at=None,
            fetched_at=datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc),
            availability_basis="first_fetch",
            parser_version="1.0",
            coverage_status="COMPLETE",
            batch_id="batch_001"
        )
        self.assertIsNone(doc.published_at)
        self.assertIsNone(doc.available_at)

    def test_units_and_normalization_preservation(self):
        """Fraction, percent, and percentage point units must remain distinct and preserve lineage."""
        obs_pct = Observation(
            series_id="series_interest_rate",
            period_start="2026-01-01",
            period_end_exclusive="2026-02-01",
            value=3.75,
            original_value_text="3.75%",
            source_unit="PERCENT",
            unit="PERCENT",
            batch_id="batch_001"
        )
        obs_pp = Observation(
            series_id="series_rate_change",
            period_start="2026-01-01",
            period_end_exclusive="2026-02-01",
            value=0.25,
            original_value_text="25 bps",
            source_unit="BASIS_POINTS",
            unit="PERCENTAGE_POINTS",
            conversion_lineage_json='{"rule": "basis_points_to_percentage_points", "scale": 0.01}',
            batch_id="batch_001"
        )
        self.assertNotEqual(obs_pct.unit, obs_pp.unit)
        self.assertIn("basis_points", obs_pp.conversion_lineage_json)

    def test_public_availability_versus_reference_time(self):
        """A March observation released in May and revised in June must respect as-of cutoffs."""
        obs_v1 = Observation(
            series_id="series_destatis_61261_0002",
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",  # Q1 2026
            value=112.4,
            source_unit="INDEX",
            unit="INDEX",
            vintage_id="vintage_2026_05_15",
            available_at=datetime(2026, 5, 15, 9, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_may"
        )
        obs_v2 = Observation(
            series_id="series_destatis_61261_0002",
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",
            value=112.8,  # revised
            source_unit="INDEX",
            unit="INDEX",
            vintage_id="vintage_2026_06_20",
            available_at=datetime(2026, 6, 20, 9, 0, 0, tzinfo=timezone.utc),
            recorded_at=datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc),
            supersedes_revision_id=obs_v1.revision_id,
            batch_id="batch_june"
        )
        self.assertEqual(obs_v1.observation_id, obs_v2.observation_id)
        self.assertNotEqual(obs_v1.revision_id, obs_v2.revision_id)
        self.assertEqual(obs_v2.supersedes_revision_id, obs_v1.revision_id)

    def test_vocabulary_extensions_company_and_geography(self):
        """NodeType must support COMPANY and GEOGRAPHY, and EdgeRelation must support technical architecture relations."""
        # Check NodeType in both modules
        self.assertIn("COMPANY", ModelNodeType.__members__)
        self.assertIn("GEOGRAPHY", ModelNodeType.__members__)
        self.assertIn("COMPANY", SchemaNodeType.__members__)
        self.assertIn("GEOGRAPHY", SchemaNodeType.__members__)

        # Check EdgeRelation in both modules
        required_relations = [
            "PERFORMS", "BELONGS_TO", "ABOUT", "LOCATED_IN",
            "WITHIN", "MEASURES", "FLOWS_TO", "APPLIES_IN", "AFFECTS_COST_OF"
        ]
        for rel in required_relations:
            self.assertIn(rel, ModelEdgeRelation.__members__)
            self.assertIn(rel, SchemaEdgeRelation.__members__)

    def test_non_numeric_legal_obligations_permitted(self):
        """Legal rules without numeric thresholds must preserve condition structures without forced numbers."""
        fact = Fact(
            subject_id="LAW_32024L1275_EPBD",
            predicate="MANDATES",
            object_id="ACT_RENOVATION_PASSPORT",
            payload_json='{"condition": "national_transposition_by_2026", "non_numeric_obligation": "Establish digital building renovation passport scheme"}',
            claim_kind="source_claim",
            quality_state="accepted",
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        self.assertEqual(fact.predicate, "MANDATES")
        self.assertIn("renovation passport", fact.payload_json)

    def test_validate_candidate_decision(self):
        """validate_candidate must return structured QualityDecision with reason codes."""
        doc = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            publisher="Publications Office",
            content_bytes=b"Construction Products Regulation CPR Recast Article 6",
            batch_id="batch_001"
        )
        fact_valid = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_DIGITAL_PRODUCT_PASSPORT",
            claim_kind="source_claim",
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        decision = validate_candidate(fact_valid, doc)
        self.assertIsInstance(decision, QualityDecision)
        self.assertIn(decision.decision, ["accepted", "needs_review", "rejected", "out_of_scope"])
