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

    def test_stable_id_delimiter_collision(self):
        """stable_id("x", "a:b", "c") must not equal stable_id("x", "a", "b:c")."""
        id1 = stable_id("x", "a:b", "c")
        id2 = stable_id("x", "a", "b:c")
        self.assertNotEqual(id1, id2, "Length-prefixed parts must prevent delimiter collisions")

    def test_stable_id_cross_process_determinism(self):
        """stable_id must produce identical hashes across separate Python processes."""
        import subprocess
        import sys
        cmd = [
            sys.executable,
            "-c",
            "from src.schemas.ingestion import stable_id; print(stable_id('entity', 'DESTATIS', '61261-0002'))"
        ]
        out = subprocess.check_output(cmd, text=True).strip()
        in_proc = stable_id("entity", "DESTATIS", "61261-0002")
        self.assertEqual(out, in_proc)

    def test_fact_revision_identity_substantive_changes(self):
        """Substantive temporal and content changes must update fact_version_id while preserving fact_id."""
        base_fact = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_PASSPORT",
            payload_json='{"rule": "create_passport", "priority": 1}',
            valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc),
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        # 1. Changing valid_from changes version_id but keeps fact_id
        changed_vf = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_PASSPORT",
            payload_json='{"rule": "create_passport", "priority": 1}',
            valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc),
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        self.assertEqual(base_fact.fact_id, changed_vf.fact_id)
        self.assertNotEqual(base_fact.fact_version_id, changed_vf.fact_version_id)

        # 2. Equivalent JSON with different key orders produces identical version_id
        reordered_json_fact = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_PASSPORT",
            payload_json='{"priority": 1, "rule": "create_passport"}',
            valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc),
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        self.assertEqual(base_fact.fact_version_id, reordered_json_fact.fact_version_id)

        # 3. Retraction changes version_id
        retracted_fact = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_PASSPORT",
            payload_json='{"rule": "create_passport", "priority": 1}',
            valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc),
            is_retracted=True,
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        self.assertNotEqual(base_fact.fact_version_id, retracted_fact.fact_version_id)

        # 4. Incidental timestamps (recorded_at) do NOT change version_id
        diff_recorded = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_PASSPORT",
            payload_json='{"rule": "create_passport", "priority": 1}',
            valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            source_id="EURLEX_CELLAR",
            batch_id="batch_001"
        )
        self.assertEqual(base_fact.fact_version_id, diff_recorded.fact_version_id)

    def test_evidence_link_selector_identity(self):
        """Distinct selectors within same document/assertion produce distinct evidence_ids."""
        e1 = EvidenceLink(
            assertion_version_id="factver_123",
            document_version_id="docver_456",
            selector_type="TEXT_SPAN",
            selector_json='{"start": 10, "end": 50}',
            quoted_text="First quote"
        )
        e2 = EvidenceLink(
            assertion_version_id="factver_123",
            document_version_id="docver_456",
            selector_type="TEXT_SPAN",
            selector_json='{"start": 60, "end": 120}',
            quoted_text="Second quote"
        )
        self.assertNotEqual(e1.evidence_id, e2.evidence_id)

        # Equivalent JSON in selector_json produces identical evidence_id
        e3 = EvidenceLink(
            assertion_version_id="factver_123",
            document_version_id="docver_456",
            selector_type="TEXT_SPAN",
            selector_json='{"end": 50, "start": 10}',
            quoted_text="First quote"
        )
        self.assertEqual(e1.evidence_id, e3.evidence_id)

    def test_observation_revision_identity(self):
        """Observation revision_id changes with value, unit, status, and retraction."""
        obs1 = Observation(
            series_id="series_1",
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",
            value=100.0,
            unit="INDEX",
            vintage_id="v1",
            batch_id="b1"
        )
        obs2 = Observation(
            series_id="series_1",
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",
            value=101.5,
            unit="INDEX",
            vintage_id="v2",
            batch_id="b2"
        )
        self.assertEqual(obs1.observation_id, obs2.observation_id)
        self.assertNotEqual(obs1.revision_id, obs2.revision_id)

        # Different recorded_at does NOT change revision_id
        obs_diff_rec = Observation(
            series_id="series_1",
            period_start="2026-01-01",
            period_end_exclusive="2026-04-01",
            value=100.0,
            unit="INDEX",
            vintage_id="v1",
            recorded_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            batch_id="b1"
        )
        self.assertEqual(obs1.revision_id, obs_diff_rec.revision_id)

    def test_validate_candidate_comprehensive_checks(self):
        """validate_candidate rejects malformed dates, reversed intervals, wrong namespaces, and unsupported relations."""
        doc = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://example.com/law",
            publisher="PO",
            content_bytes=b"Law text",
            batch_id="b1"
        )
        # 1. Malformed date
        obs_bad_date = Observation(
            series_id="series_1",
            period_start="not-a-date",
            period_end_exclusive="2026-04-01",
            value=10.0,
            unit="INDEX",
            batch_id="b1"
        )
        dec = validate_candidate(obs_bad_date, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("MALFORMED_OBSERVATION_PERIOD", dec.reason_codes)

        # 2. Reversed observation interval
        obs_reversed = Observation(
            series_id="series_1",
            period_start="2026-05-01",
            period_end_exclusive="2026-01-01",
            value=10.0,
            unit="INDEX",
            batch_id="b1"
        )
        dec = validate_candidate(obs_reversed, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("REVERSED_OBSERVATION_PERIOD", dec.reason_codes)

        # 3. Disallowed subject namespace (edge as subject)
        fact_edge_subj = Fact(
            subject_id="edge_12345",
            predicate="MANDATES",
            object_id="ACT_SOMETHING",
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec = validate_candidate(fact_edge_subj, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("INVALID_SUBJECT_NAMESPACE", dec.reason_codes)

        # 4. Disallowed object namespace
        fact_edge_obj = Fact(
            subject_id="LAW_123",
            predicate="MANDATES",
            object_id="rel_999",
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec = validate_candidate(fact_edge_obj, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("INVALID_OBJECT_NAMESPACE", dec.reason_codes)

        # 5. Unsupported relation predicate
        fact_bad_pred = Fact(
            subject_id="LAW_123",
            predicate="DISLIKES",
            object_id="ACT_SOMETHING",
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec = validate_candidate(fact_bad_pred, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("UNSUPPORTED_PREDICATE", dec.reason_codes)

        # 6. Reversed validity interval
        fact_rev_interval = Fact(
            subject_id="LAW_123",
            predicate="MANDATES",
            object_id="ACT_SOMETHING",
            valid_from=datetime(2030, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2025, 1, 1, tzinfo=timezone.utc),
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec = validate_candidate(fact_rev_interval, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("REVERSED_VALIDITY_INTERVAL", dec.reason_codes)

        # 7. Mismatched source
        fact_mismatched_src = Fact(
            subject_id="LAW_123",
            predicate="MANDATES",
            object_id="ACT_SOMETHING",
            source_id="DESTATIS_GENESIS",
            batch_id="b1"
        )
        dec = validate_candidate(fact_mismatched_src, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("SOURCE_ID_MISMATCH", dec.reason_codes)

        # 8. Mismatched data origin
        fact_mismatched_origin = Fact(
            subject_id="LAW_123",
            predicate="MANDATES",
            object_id="ACT_SOMETHING",
            source_id="EURLEX_CELLAR",
            batch_id="b1",
            data_origin="demo"
        )
        dec = validate_candidate(fact_mismatched_origin, doc)
        self.assertEqual(dec.decision, "rejected")
        self.assertIn("DATA_ORIGIN_MISMATCH", dec.reason_codes)

    def test_source_document_recent_fetch_of_old_publication(self):
        """Recently fetched revision of an old publication must not backdate available_at without vintage evidence."""
        old_pub_date = datetime(2018, 5, 10, tzinfo=timezone.utc)
        doc_recent = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018R0852",
            publisher="Publications Office",
            content_bytes=b"Old law text fetched today",
            batch_id="b_today",
            published_at=old_pub_date
        )
        self.assertEqual(doc_recent.published_at, old_pub_date)
        self.assertEqual(doc_recent.availability_basis, "first_fetch")
        # available_at must be near fetch time, NOT backdated to 2018!
        self.assertGreater(doc_recent.available_at, datetime(2025, 1, 1, tzinfo=timezone.utc))

        # With explicit verified vintage evidence
        archived_avail = datetime(2018, 5, 20, tzinfo=timezone.utc)
        doc_vintage = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018R0852",
            publisher="Publications Office",
            content_bytes=b"Old law text",
            batch_id="b_vintage",
            published_at=old_pub_date,
            availability_basis="archived_release",
            available_at=archived_avail
        )
        self.assertEqual(doc_vintage.availability_basis, "archived_release")
        self.assertEqual(doc_vintage.available_at, archived_avail)

    def test_non_numeric_legal_rules_in_actual_extraction_path(self):
        """Exercise actual GNNRule models, non-numeric validation, and rule_to_fact conversion."""
        from src.schemas.rule_schema import (
            GNNRule, Metadata, Scope, Timeline, Mechanism, Enforcement,
            ActionType, ActionOperator, Strictness, LawType, rule_to_fact
        )
        from src.prompts.rule_validation import fix_date_format

        # 1. Construct non-numeric obligation in GNNRule
        rule = GNNRule(
            metadata=Metadata(
                regulation_id="32024R1069",
                article_id="ART_6",
                law_type=LawType.REGULATION,
                parse_confidence=0.95
            ),
            scope=Scope(
                regions=["EU", "DE"],
                industrial_sectors=["C23"],
                node_types=["PRODUCER", "INFRASTRUCTURE"],
                affected_commodities=["CEMENT", "STEEL"]
            ),
            timeline=Timeline(
                activation_date=None,  # Unknown activation date - must remain null
                expiration_date=None
            ),
            mechanism=Mechanism(
                target_variable="compliance_status",
                action_type=ActionType.MANDATE,
                operator=ActionOperator.REQUIRES,
                value=None,  # Qualitative obligation without fabricated quantity
                unit="",
                non_numeric_obligation="Establish digital building product passport scheme",
                condition="Applies to all construction products placed on EU market after harmonized standard adoption",
                exception="Microenterprises exempt from digital format requirement until 2028",
                evidence_anchor="Article 6(1)-(3)"
            ),
            enforcement=Enforcement(
                strictness=Strictness.HARD,
                penalty_factor=1.0
            ),
            summary="Mandates digital product passport for construction products with microenterprise exemption."
        )
        self.assertIsNone(rule.mechanism.value)
        self.assertIsNone(rule.timeline.activation_date)
        self.assertEqual(rule.mechanism.action_type, ActionType.MANDATE)

        # 2. Verify numeric requirement for ADJUSTMENT and FIXING
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="opex_cost",
                action_type=ActionType.ADJUSTMENT,
                operator=ActionOperator.ADD_ABSOLUTE,
                value=None,  # ADJUSTMENT requires numeric value
                unit="EUR/t"
            )

        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.FIXING,
                operator=ActionOperator.SET_VALUE,
                value=None,  # FIXING requires numeric value
                unit="EUR"
            )

        # 3. Verify fix_date_format preserves null activation date
        rule_dict = rule.model_dump()
        fixed_dict = fix_date_format(rule_dict)
        self.assertIsNone(fixed_dict["timeline"]["activation_date"])

        # 4. Convert GNNRule to canonical Fact contract
        fact = rule_to_fact(rule)
        self.assertEqual(fact.subject_id, "LAW_32024R1069")
        self.assertEqual(fact.predicate, "MANDATES")
        self.assertIsNone(fact.valid_from)
        import json
        payload = json.loads(fact.payload_json)
        self.assertIsNone(payload["value"])
        self.assertIn("digital building product passport", payload["non_numeric_obligation"])
        self.assertIn("Microenterprises exempt", payload["exception"])
        self.assertIn("harmonized standard", payload["condition"])
        self.assertEqual(payload["evidence_anchor"], "Article 6(1)-(3)")

    def test_numeric_rules_omitted_and_explicit_none_and_valid_zero(self):
        """Test Mechanism validation for omitted value, explicit None, valid zero, finite numbers, and qualitative rules."""
        from src.schemas.rule_schema import Mechanism, ActionType, ActionOperator

        # 1. Omitted value for ADJUSTMENT must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.ADJUSTMENT,
                operator=ActionOperator.ADD_ABSOLUTE,
                unit="EUR"
            )

        # 2. Omitted value for FIXING must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.FIXING,
                operator=ActionOperator.SET_VALUE,
                unit="EUR"
            )

        # 3. Explicit None for ADJUSTMENT and FIXING must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.ADJUSTMENT,
                operator=ActionOperator.ADD_ABSOLUTE,
                value=None,
                unit="EUR"
            )
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.FIXING,
                operator=ActionOperator.SET_VALUE,
                value=None,
                unit="EUR"
            )

        # 4. Valid zero (0 and 0.0) must succeed
        m_zero1 = Mechanism(
            target_variable="price",
            action_type=ActionType.ADJUSTMENT,
            operator=ActionOperator.ADD_ABSOLUTE,
            value=0.0,
            unit="EUR"
        )
        self.assertEqual(m_zero1.value, 0.0)

        m_zero2 = Mechanism(
            target_variable="price",
            action_type=ActionType.FIXING,
            operator=ActionOperator.SET_VALUE,
            value=0,
            unit="EUR"
        )
        self.assertEqual(m_zero2.value, 0.0)

        # 5. Non-finite values (NaN, Inf) must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.ADJUSTMENT,
                operator=ActionOperator.ADD_ABSOLUTE,
                value=float("nan"),
                unit="EUR"
            )
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.FIXING,
                operator=ActionOperator.SET_VALUE,
                value=float("inf"),
                unit="EUR"
            )

        # 6. Incompatible operator / action_type must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.ADJUSTMENT,
                operator=ActionOperator.SET_VALUE,  # SET_VALUE incompatible with ADJUSTMENT
                value=10.0,
                unit="EUR"
            )

        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.MANDATE,
                operator=ActionOperator.ADD_ABSOLUTE,  # ADD_ABSOLUTE incompatible with MANDATE
                unit="EUR"
            )

        # 7. Qualitative rule with omitted or None value must succeed
        m_qual = Mechanism(
            target_variable="compliance_status",
            action_type=ActionType.MANDATE,
            operator=ActionOperator.REQUIRES,
            non_numeric_obligation="Must install digital measuring device",
            unit=""
        )
        self.assertIsNone(m_qual.value)
        self.assertEqual(m_qual.action_type, ActionType.MANDATE)

    def test_relation_endpoint_validation_table_driven(self):
        """Table-driven test covering valid, invalid, and unresolved relation endpoint combinations."""
        doc = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            publisher="Publications Office",
            content_bytes=b"Test Content",
            batch_id="b_table"
        )

        test_cases = [
            # Valid combinations
            ("LAW_32024R1069", "MANDATES", "ACT_PASSPORT", None, "accepted", "VALID_FACT_CONTRACT"),
            ("LAW_32024R1069", "MANDATES", None, '{"non_numeric_obligation": "Obligation"}', "accepted", "VALID_FACT_CONTRACT"),
            ("LAW_32024R1069", "REGULATES", "COMMODITY_STEEL", None, "accepted", "VALID_FACT_CONTRACT"),
            ("ACT_INVESTMENT", "TRIGGERS", "REACT_SUPPLY_DROP", None, "accepted", "VALID_FACT_CONTRACT"),
            ("ACT_EMISSION_CAP", "AFFECTS", "SEC_C23", None, "accepted", "VALID_FACT_CONTRACT"),
            ("LAW_CBAM", "AFFECTS_COST_OF", "COMMODITY_CEMENT", None, "accepted", "VALID_FACT_CONTRACT"),
            ("REACT_PRICE_HIKE", "IMPACTS", "INDEX_DE_PPI", None, "accepted", "VALID_FACT_CONTRACT"),
            ("ACT_STRIKE", "CAUSES", "REACT_OUTAGE", None, "accepted", "VALID_FACT_CONTRACT"),
            ("LAW_NEW", "AMENDS", "LAW_OLD", None, "accepted", "VALID_FACT_CONTRACT"),
            ("COMP_HOLCIM", "PERFORMS", "ACT_EXPANSION", None, "accepted", "VALID_FACT_CONTRACT"),
            ("COMP_HOLCIM", "BELONGS_TO", "SEC_C23", None, "accepted", "VALID_FACT_CONTRACT"),
            ("ACT_CLOSURE", "ABOUT", "COMMODITY_STEEL", None, "accepted", "VALID_FACT_CONTRACT"),
            ("COMP_HOLCIM", "LOCATED_IN", "GEO_DE", None, "accepted", "VALID_FACT_CONTRACT"),
            ("GEO_DE", "WITHIN", "GEO_EU", None, "accepted", "VALID_FACT_CONTRACT"),
            ("GEO_DE", "FLOWS_TO", "GEO_FR", None, "accepted", "VALID_FACT_CONTRACT"),
            ("LAW_CPR", "APPLIES_IN", "GEO_DE", None, "accepted", "VALID_FACT_CONTRACT"),
            ("SERIES_PPI", "MEASURES", "COMMODITY_CEMENT", None, "accepted", "VALID_FACT_CONTRACT"),
            ("SERIES_TRADE", "MEASURES", "edge_flow_1", '{"subject_kind": "edge"}', "accepted", "VALID_FACT_CONTRACT"),

            # Invalid combinations (incompatible endpoints)
            ("GEOGRAPHY_DE", "MANDATES", "GEOGRAPHY_FR", None, "rejected", "INCOMPATIBLE_RELATION_ENDPOINTS"),
            ("LAW_CPR", "WITHIN", "COMP_HOLCIM", None, "rejected", "INCOMPATIBLE_RELATION_ENDPOINTS"),
            ("COMMODITY_STEEL", "AMENDS", "SEC_C23", None, "rejected", "INCOMPATIBLE_RELATION_ENDPOINTS"),
            ("COMP_HOLCIM", "FLOWS_TO", "ACT_CLOSURE", None, "rejected", "INCOMPATIBLE_RELATION_ENDPOINTS"),
            ("LAW_CPR", "AMENDS", None, None, "rejected", "MISSING_REQUIRED_OBJECT_ENDPOINT"),

            # Disallowed edge IDs (when not MEASURES)
            ("edge_123", "MANDATES", "ACT_PASSPORT", None, "rejected", "INVALID_SUBJECT_NAMESPACE"),
            ("LAW_CPR", "MANDATES", "rel_456", None, "rejected", "INVALID_OBJECT_NAMESPACE"),

            # Unresolved endpoint types
            ("nonsense", "MANDATES", "garbage", None, "needs_review", "UNRESOLVED_SUBJECT_TYPE"),
            ("LAW_CPR", "MANDATES", "garbage", None, "needs_review", "UNRESOLVED_OBJECT_TYPE"),
            ("nonsense", "MANDATES", "ACT_PASSPORT", None, "needs_review", "UNRESOLVED_SUBJECT_TYPE"),
        ]

        for subj, pred, obj, payload, exp_dec, exp_code in test_cases:
            with self.subTest(subj=subj, pred=pred, obj=obj):
                f = Fact(
                    subject_id=subj,
                    predicate=pred,
                    object_id=obj,
                    payload_json=payload,
                    source_id="EURLEX_CELLAR",
                    batch_id="b_table"
                )
                decision = validate_candidate(f, doc)
                self.assertEqual(decision.decision, exp_dec, f"Failed for {subj} {pred} {obj}")
                self.assertTrue(
                    any(exp_code in code for code in decision.reason_codes),
                    f"Expected reason code containing '{exp_code}' in {decision.reason_codes} for {subj} {pred} {obj}"
                )

    def test_fact_availability_consistency_checks(self):
        """
        Facts must enforce availability consistency against SourceDocument.
        An assertion cannot claim earlier verified availability from a source fetched recently without vintage evidence.
        """
        now_utc = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
        old_pub_date = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        # 1. Old publication fetched today with availability_basis="first_fetch"
        doc_recent = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32000R0001",
            publisher="Publications Office",
            content_bytes=b"Old Regulation content fetched today",
            batch_id="b_recent",
            published_at=old_pub_date,
            availability_basis="first_fetch"
        )
        self.assertEqual(doc_recent.availability_basis, "first_fetch")
        # Source available_at is near now_utc
        self.assertGreater(doc_recent.available_at, datetime(2025, 1, 1, tzinfo=timezone.utc))

        # Fact claiming verified available_at in 2000 must be flagged needs_review
        fact_backdated = Fact(
            subject_id="LAW_32000R0001",
            predicate="MANDATES",
            object_id="ACT_OBLIGATION",
            available_at=old_pub_date,  # Backdated claim!
            source_id="EURLEX_CELLAR",
            batch_id="b_recent"
        )
        dec_backdated = validate_candidate(fact_backdated, doc_recent)
        self.assertEqual(dec_backdated.decision, "needs_review")
        self.assertIn("FACT_PREDATES_SOURCE_AVAILABILITY", dec_backdated.reason_codes)

        # Fact distinguishing validity (valid_from=2000) from availability (available_at=now or None)
        fact_valid_distinction = Fact(
            subject_id="LAW_32000R0001",
            predicate="MANDATES",
            object_id="ACT_OBLIGATION",
            valid_from=old_pub_date,  # Legal validity was in 2000
            available_at=doc_recent.available_at,  # Verified availability is today
            source_id="EURLEX_CELLAR",
            batch_id="b_recent"
        )
        dec_valid = validate_candidate(fact_valid_distinction, doc_recent)
        self.assertEqual(dec_valid.decision, "accepted")

        # Fact with unknown availability (available_at=None) stays null and accepted
        fact_null_avail = Fact(
            subject_id="LAW_32000R0001",
            predicate="MANDATES",
            object_id="ACT_OBLIGATION",
            valid_from=old_pub_date,
            available_at=None,
            source_id="EURLEX_CELLAR",
            batch_id="b_recent"
        )
        self.assertIsNone(fact_null_avail.available_at)
        dec_null = validate_candidate(fact_null_avail, doc_recent)
        self.assertEqual(dec_null.decision, "accepted")

        # 2. Document with verified historical archived release evidence
        archived_avail = datetime(2018, 5, 20, 10, 0, 0, tzinfo=timezone.utc)
        doc_archived = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018R0852",
            publisher="Publications Office",
            content_bytes=b"Archived vintage content",
            batch_id="b_archived",
            published_at=datetime(2018, 5, 10, tzinfo=timezone.utc),
            availability_basis="archived_release",
            available_at=archived_avail
        )
        self.assertEqual(doc_archived.availability_basis, "archived_release")
        self.assertEqual(doc_archived.available_at, archived_avail)

        # Fact with matching archived availability succeeds
        fact_archived = Fact(
            subject_id="LAW_32018R0852",
            predicate="MANDATES",
            object_id="ACT_OBLIGATION",
            available_at=archived_avail,
            source_id="EURLEX_CELLAR",
            batch_id="b_archived"
        )
        dec_archived = validate_candidate(fact_archived, doc_archived)
        self.assertEqual(dec_archived.decision, "accepted")

        # Fact predating even the archived version is flagged
        fact_predating_archived = Fact(
            subject_id="LAW_32018R0852",
            predicate="MANDATES",
            object_id="ACT_OBLIGATION",
            available_at=datetime(2010, 1, 1, tzinfo=timezone.utc),
            source_id="EURLEX_CELLAR",
            batch_id="b_archived"
        )
        dec_pred_archived = validate_candidate(fact_predating_archived, doc_archived)
        self.assertEqual(dec_pred_archived.decision, "needs_review")
        self.assertIn("FACT_PREDATES_SOURCE_AVAILABILITY", dec_pred_archived.reason_codes)

        # 3. Observations also respect source availability boundaries
        obs_backdated = Observation(
            series_id="SERIES_TEST",
            period_start="2000-01-01",
            period_end_exclusive="2000-04-01",
            value=105.2,
            unit="INDEX_2021",
            available_at=old_pub_date,
            batch_id="b_recent"
        )
        dec_obs_backdated = validate_candidate(obs_backdated, doc_recent)
        self.assertEqual(dec_obs_backdated.decision, "needs_review")
        self.assertIn("OBSERVATION_PREDATES_SOURCE_AVAILABILITY", dec_obs_backdated.reason_codes)

        obs_pred_archived = Observation(
            series_id="SERIES_TEST",
            period_start="2018-01-01",
            period_end_exclusive="2018-04-01",
            value=110.0,
            unit="INDEX_2021",
            available_at=datetime(2010, 1, 1, tzinfo=timezone.utc),
            batch_id="b_archived"
        )
        dec_obs_archived = validate_candidate(obs_pred_archived, doc_archived)
        self.assertEqual(dec_obs_archived.decision, "needs_review")
        self.assertIn("OBSERVATION_PREDATES_SOURCE_AVAILABILITY", dec_obs_archived.reason_codes)

        obs_valid = Observation(
            series_id="SERIES_TEST",
            period_start="2018-01-01",
            period_end_exclusive="2018-04-01",
            value=110.0,
            unit="INDEX_2021",
            available_at=archived_avail,
            batch_id="b_archived"
        )
        dec_obs_valid = validate_candidate(obs_valid, doc_archived)
        self.assertEqual(dec_obs_valid.decision, "accepted")

    def test_fact_first_fetch_availability_bypass_prevented(self):
        """A Fact with available_at in 2000 must NOT bypass verification when SourceDocument is created with available_at=None and first_fetch."""
        doc = SourceDocument(
            document_id="doc_direct",
            version_id="docver_direct",
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            final_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            publisher="Publications Office",
            content_sha256="abc12345",
            batch_id="b_direct",
            availability_basis="first_fetch",
            available_at=None
        )
        self.assertIsNone(doc.available_at, "SourceDocument should preserve None for available_at when explicitly passed None")

        # Fact claiming availability in year 2000
        fact_2000 = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_REPORTING",
            source_id="EURLEX_CELLAR",
            batch_id="b_direct",
            available_at=datetime(2000, 1, 1, tzinfo=timezone.utc)
        )
        dec = validate_candidate(fact_2000, doc)
        self.assertEqual(dec.decision, "needs_review")
        self.assertIn("FACT_PREDATES_SOURCE_AVAILABILITY", dec.reason_codes)

    def test_fact_missing_source_availability_flags_missing_evidence(self):
        """When source availability is unknown and basis is not first_fetch, candidate claiming verified availability must be flagged."""
        doc_unknown_avail = SourceDocument(
            document_id="doc_unknown",
            version_id="docver_unknown",
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            final_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            publisher="Publications Office",
            content_sha256="abc12345",
            batch_id="b_unknown",
            availability_basis="provider_vintage",
            available_at=None
        )
        fact_with_avail = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_REPORTING",
            source_id="EURLEX_CELLAR",
            batch_id="b_unknown",
            available_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        dec = validate_candidate(fact_with_avail, doc_unknown_avail)
        self.assertEqual(dec.decision, "needs_review")
        self.assertIn("FACT_AVAILABILITY_WITHOUT_SOURCE_EVIDENCE", dec.reason_codes)

        # But a Fact with unknown availability (None) stays accepted
        fact_null_avail = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_REPORTING",
            source_id="EURLEX_CELLAR",
            batch_id="b_unknown",
            available_at=None
        )
        dec_null = validate_candidate(fact_null_avail, doc_unknown_avail)
        self.assertEqual(dec_null.decision, "accepted")

    def test_fact_inconsistent_and_malformed_validity_dates(self):
        """Test validation of reversed validity dates, malformed date strings, and future publication dates."""
        doc = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            publisher="Publications Office",
            content_bytes=b"content",
            batch_id="b1"
        )
        # Reversed validity interval
        fact_rev = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_REPORTING",
            source_id="EURLEX_CELLAR",
            batch_id="b1",
            valid_from="2025-01-01",
            valid_to="2024-01-01"
        )
        dec_rev = validate_candidate(fact_rev, doc)
        self.assertEqual(dec_rev.decision, "rejected")
        self.assertIn("REVERSED_VALIDITY_INTERVAL", dec_rev.reason_codes)

        # Malformed validity date
        fact_malformed = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_REPORTING",
            source_id="EURLEX_CELLAR",
            batch_id="b1",
            valid_from="not-a-valid-date"
        )
        dec_mal = validate_candidate(fact_malformed, doc)
        self.assertEqual(dec_mal.decision, "rejected")
        self.assertIn("MALFORMED_VALIDITY_DATE", dec_mal.reason_codes)

        # Future publication date relative to fetch
        future_doc = SourceDocument(
            document_id="doc_fut",
            version_id="ver_fut",
            source_id="EURLEX_CELLAR",
            canonical_url="https://example.com/law",
            final_url="https://example.com/law",
            publisher="Publications Office",
            content_sha256="abc",
            batch_id="b1",
            published_at=datetime(2035, 1, 1, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        valid_fact = Fact(
            subject_id="LAW_32024R1069",
            predicate="MANDATES",
            object_id="ACT_REPORTING",
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec_fut = validate_candidate(valid_fact, future_doc)
        self.assertEqual(dec_fut.decision, "needs_review")
        self.assertIn("FUTURE_PUBLICATION_DATE", dec_fut.reason_codes)

    def test_mechanism_constraint_operator_compatibility_and_booleans(self):
        """Test Mechanism validation for CONSTRAINT operator compatibility and boolean rejection."""
        from src.schemas.rule_schema import Mechanism, ActionType, ActionOperator

        # CONSTRAINT with incompatible ADD_ABSOLUTE operator must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="emission_rate",
                action_type=ActionType.CONSTRAINT,
                operator=ActionOperator.ADD_ABSOLUTE,
                value=10.0,
                unit="TONNES"
            )

        # CONSTRAINT with MAX_LIMIT must succeed
        m_constraint = Mechanism(
            target_variable="emission_rate",
            action_type=ActionType.CONSTRAINT,
            operator=ActionOperator.MAX_LIMIT,
            value=10.0,
            unit="TONNES"
        )
        self.assertEqual(m_constraint.value, 10.0)

        # Boolean value passed to numeric Mechanism must fail
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.ADJUSTMENT,
                operator=ActionOperator.ADD_ABSOLUTE,
                value=True,
                unit="EUR"
            )
        with self.assertRaises(ValueError):
            Mechanism(
                target_variable="price",
                action_type=ActionType.FIXING,
                operator=ActionOperator.SET_VALUE,
                value=False,
                unit="EUR"
            )

    def test_fact_and_edge_namespaces_rejected_for_non_measures(self):
        """Using edge_, rel_, or fact_ prefixes for non-MEASURES predicates must be rejected."""
        doc = SourceDocument.from_content(
            source_id="EURLEX_CELLAR",
            canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
            publisher="Publications Office",
            content_bytes=b"content",
            batch_id="b1"
        )
        # fact_ prefix as subject of MANDATES
        f_subj = Fact(
            subject_id="fact_12345",
            predicate="MANDATES",
            object_id="act_456",
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec_s = validate_candidate(f_subj, doc)
        self.assertEqual(dec_s.decision, "rejected")
        self.assertIn("INVALID_SUBJECT_NAMESPACE", dec_s.reason_codes)

        # rel_ prefix as object of MANDATES
        f_obj = Fact(
            subject_id="law_12345",
            predicate="MANDATES",
            object_id="rel_456",
            source_id="EURLEX_CELLAR",
            batch_id="b1"
        )
        dec_o = validate_candidate(f_obj, doc)
        self.assertEqual(dec_o.decision, "rejected")
        self.assertIn("INVALID_OBJECT_NAMESPACE", dec_o.reason_codes)

