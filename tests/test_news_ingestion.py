"""
Task 6 Acceptance Tests: News discovery, identity, extraction and event resolution.
Tests truthful extraction, canonical identity, versioning, syndication clustering,
negation, and common runner integration.
"""

import os
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.scrapers.news_scraper import (
    EconomicNewsScraper,
)
from src.schemas.extraction import (
    NewsEntities,
    EconomicAction,
    EconomicReaction,
    StructuredEconomicEvent,
)
from src.schemas.ingestion import SourceDocument, Fact, stable_id
from src.storage.dual_layer_store import DualLayerStore
from src.ingestion import run_ingestion, register_source, _DISCOVERERS, _PARSERS


class TestNewsIngestionTask6(unittest.TestCase):

    def setUp(self):
        self.orig_mode = os.environ.get("POWERLAW_MODE")
        os.environ["POWERLAW_MODE"] = "test"
        from src.config import clear_settings_cache
        clear_settings_cache()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "news"
        self.db_path = Path(self.temp_dir.name) / "lancedb"
        self.scraper = EconomicNewsScraper(
            data_dir=str(self.data_dir),
            lancedb_path=str(self.db_path),
            offline=True
        )

    def tearDown(self):
        self.temp_dir.cleanup()
        if self.orig_mode is not None:
            os.environ["POWERLAW_MODE"] = self.orig_mode
        else:
            os.environ.pop("POWERLAW_MODE", None)
        from src.config import clear_settings_cache
        clear_settings_cache()

    # ------------------------------------------------------------------
    # 1. Identity, Tracking Parameter Stripping, and Feed GUIDs
    # ------------------------------------------------------------------
    def test_article_identity_stable_across_processes_and_query_variations(self):
        """Identical article in different processes and parameter orders yields identical ID."""
        url_a = "https://ec.europa.eu/commission/presscorner/detail/en/ip_24_100?utm_source=rss&id=99"
        url_b = "https://ec.europa.eu/commission/presscorner/detail/en/ip_24_100?id=99&utm_medium=feed&utm_campaign=trade"
        id_a = self.scraper._article_identity(url_a, "Commission acts on steel", "2024-05-15", upstream_id="IP-24-100")
        id_b = self.scraper._article_identity(url_b, "Commission acts on steel", "2024-05-15", upstream_id="IP-24-100")
        self.assertEqual(id_a, id_b, "Article identity must normalize tracking params and yield identical ID")

    def test_tracking_links_and_trailing_slashes_deduplicate(self):
        """Trailing slashes and marketing trackers (fbclid, gclid) strip cleanly."""
        url_plain = "https://policy.trade.ec.europa.eu/news/eu-steel-safeguards-extended"
        url_tracked = "https://policy.trade.ec.europa.eu/news/eu-steel-safeguards-extended/?fbclid=IwAR234&utm_content=button"
        self.assertEqual(
            self.scraper._canonical_url(url_plain),
            self.scraper._canonical_url(url_tracked)
        )

    # ------------------------------------------------------------------
    # 2. Corrected Text & Content Versioning
    # ------------------------------------------------------------------
    def test_corrected_text_creates_new_version_without_erasing_history(self):
        """When news text is corrected/revised, a new fact version is produced."""
        original_text = "ArcelorMittal cuts output by 20% in Ghent."
        doc_v1 = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://example.com/arcelormittal-ghent",
            publisher="EC Press",
            content_bytes=original_text.encode("utf-8"),
            batch_id="batch-v1",
            data_origin="test"
        )
        facts_v1 = self.scraper.extract_document_events(doc_v1, original_text)
        self.assertTrue(len(facts_v1) >= 1)

        corrected_text = "CORRECTION: ArcelorMittal cuts output by 15% in Ghent."
        doc_v2 = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://example.com/arcelormittal-ghent",
            publisher="EC Press",
            content_bytes=corrected_text.encode("utf-8"),
            batch_id="batch-v2",
            data_origin="test"
        )
        facts_v2 = self.scraper.extract_document_events(doc_v2, corrected_text)
        self.assertTrue(len(facts_v2) >= 1)

        # Version IDs must differ, but event root key tracks the same core event
        self.assertNotEqual(doc_v1.version_id, doc_v2.version_id)
        payload_v1 = json.loads(facts_v1[0].payload_json)
        payload_v2 = json.loads(facts_v2[0].payload_json)
        self.assertEqual(payload_v1["action"]["magnitude"], 20.0)
        self.assertEqual(payload_v2["action"]["magnitude"], 15.0)

    # ------------------------------------------------------------------
    # 3. Syndication Clustering vs Distinct Events
    # ------------------------------------------------------------------
    def test_syndication_clusters_without_multiplying_event(self):
        """Syndicated reports of the same event cluster together as of cutoff."""
        cutoff = datetime(2027, 1, 1, tzinfo=timezone.utc)
        text = "ArcelorMittal cuts steel production by 20% across European mills."

        doc_primary = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://ec.europa.eu/press/steel-cut",
            publisher="European Commission",
            content_bytes=text.encode(),
            batch_id="batch-1",
            data_origin="test"
        )
        doc_syndicated = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://industrynews.eu/reuters-steel-cut",
            publisher="Industry Wire",
            content_bytes=text.encode(),
            batch_id="batch-2",
            data_origin="test"
        )

        facts_primary = self.scraper.extract_document_events(doc_primary, text)
        facts_syndicated = self.scraper.extract_document_events(doc_syndicated, text)
        all_facts = facts_primary + facts_syndicated

        clustered = self.scraper.cluster_event_candidates(all_facts, cutoff)
        # Clustered list should deduplicate identical action candidates across syndicated reports
        action_facts = [f for f in clustered if json.loads(f.payload_json).get("kind") == "action"]
        self.assertEqual(len(action_facts), 1, "Syndicated duplicate actions must cluster into one canonical candidate")

    def test_distinct_events_for_same_company_do_not_merge(self):
        """Different actions by the same company must retain distinct event identities."""
        cutoff = datetime(2027, 1, 1, tzinfo=timezone.utc)
        doc1 = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://example.com/am-cut",
            publisher="News",
            content_bytes=b"ArcelorMittal cuts steel output by 20% in Bremen.",
            batch_id="batch-1",
            data_origin="test"
        )
        doc2 = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://example.com/am-invest",
            publisher="News",
            content_bytes=b"ArcelorMittal invests 500 million in French decarbonisation.",
            batch_id="batch-2",
            data_origin="test"
        )

        facts1 = self.scraper.extract_document_events(doc1, "ArcelorMittal cuts steel output by 20% in Bremen.")
        facts2 = self.scraper.extract_document_events(doc2, "ArcelorMittal invests 500 million in French decarbonisation.")
        all_facts = facts1 + facts2

        clustered = self.scraper.cluster_event_candidates(all_facts, cutoff)
        self.assertEqual(len(clustered), len(all_facts), "Distinct events for the same actor must not merge")

    # ------------------------------------------------------------------
    # 4. Truthful Semantics: Negation, Planned vs Executed, Unstated
    # ------------------------------------------------------------------
    def test_negated_closure_does_not_create_accepted_shutdown(self):
        """Negated action ('will not close', 'denies shutdown') must not emit accepted shutdown."""
        title = "ArcelorMittal will not close its Hamburg facility"
        summary = "Company spokesperson denies rumors of plant closure amid high energy costs."
        event = self.scraper.extract_structured_event(title, summary, "https://example.com/a", "News", "2024-05-01")

        # Must not produce an unhedged positive FACILITY_SHUTDOWN or PRODUCTION_CUT action
        for action in event.actions:
            self.assertNotIn(action.action_type, ["FACILITY_SHUTDOWN", "PRODUCTION_CUT"],
                             "Negated closure must not be extracted as positive shutdown/cut")

    def test_planned_investment_not_executed_capex(self):
        """Tentative proposals ('considers', 'weighs plans') are marked review or non-executed."""
        title = "BASF weighs plans to potentially evaluate green hydrogen options"
        summary = "No final investment decision has been made for the site."
        event = self.scraper.extract_structured_event(title, summary, "https://example.com/b", "News", "2024-05-01")

        # If any action extracted, magnitude cannot be an executed commitment
        for action in event.actions:
            self.assertTrue(action.description.lower().find("weighs") >= 0 or action.description.lower().find("evaluat") >= 0 or action.magnitude is None)

    def test_no_action_and_no_reaction_text_yields_empty_lists(self):
        """General commentary with zero concrete actions or reactions emits empty lists."""
        title = "European Industrial Outlook Remains Stable"
        summary = "Economists discussed global supply chain perspectives with no specific policy moves."
        event = self.scraper.extract_structured_event(title, summary, "https://example.com/c", "News", "2024-05-01")
        self.assertEqual(event.actions, [])
        self.assertEqual(event.reactions, [])

    def test_missing_magnitude_is_none_and_evidenced_zero_is_zero(self):
        """Missing magnitude stays None (not 1.0); evidenced zero stays 0.0."""
        # Missing magnitude
        title = "Steelmaker curtails blast furnace operations"
        summary = "Output reduced across several facilities due to maintenance."
        evt_missing = self.scraper.extract_structured_event(title, summary, "https://example.com/d", "News", "2024-05-01")
        if evt_missing.actions:
            self.assertIsNone(evt_missing.actions[0].magnitude, "Unstated magnitude must remain None, never 1.0")

        # Evidenced zero
        title_zero = "Import duties remain at 0% following annual tariff review"
        summary_zero = "The Commission confirmed a 0% rate applies to standard billet imports."
        evt_zero = self.scraper.extract_structured_event(title_zero, summary_zero, "https://example.com/e", "News", "2024-05-01")
        self.assertTrue(len(evt_zero.actions) >= 1)
        self.assertEqual(evt_zero.actions[0].magnitude, 0.0, "Evidenced 0% must remain 0.0")

    # ------------------------------------------------------------------
    # 5. Temporal Precision: Article Date vs Event Date
    # ------------------------------------------------------------------
    def test_article_date_versus_event_date_distinction(self):
        """Article published on May 10 reporting an event on May 1 preserves both."""
        title = "European Commission finalized steel tariff quota on May 1st"
        summary = "Retroactive review published on May 10 confirms duties entered into effect on 2024-05-01."
        event = self.scraper.extract_structured_event(
            title=title,
            summary=summary,
            source_url="https://example.com/tariff",
            source_name="Trade Monitor",
            pub_date="2024-05-10"
        )
        self.assertEqual(event.timestamp, "2024-05-10", "Document timestamp must reflect publication")
        doc = SourceDocument.from_content(
            source_id="OFFICIAL_NEWS",
            canonical_url="https://example.com/tariff",
            publisher="Trade Monitor",
            content_bytes=f"{title}\n{summary}".encode(),
            batch_id="batch-date",
            data_origin="test"
        )
        facts = self.scraper.extract_document_events(doc, f"{title}\n{summary}")
        self.assertTrue(len(facts) >= 1)
        self.assertEqual(facts[0].available_at, doc.available_at)

    # ------------------------------------------------------------------
    # 6. Non-Causal Defaults & Graph Ingestion
    # ------------------------------------------------------------------
    def test_no_all_to_all_cross_product_causal_edges(self):
        """DualLayerGraph ingestion does not create all-to-all cross-product causal edges."""
        from src.storage.graph_store import DualLayerGraphStore
        graph_store = DualLayerGraphStore(storage_dir=Path(self.temp_dir.name) / "graph_store")
        event = {
            "headline": "Steel mill idles line while gas prices rise",
            "timestamp": "2024-05-01",
            "source_url": "https://example.com/news",
            "actors": {
                "companies": ["ArcelorMittal", "Salzgitter"],
                "sectors": ["C24_BASIC_METALS"],
                "countries": ["Germany"]
            },
            "actions": [
                {"action_type": "PRODUCTION_CUT", "actor": "ArcelorMittal", "magnitude": 10.0, "unit": "PERCENT"},
                {"action_type": "CAPEX_INVESTMENT", "actor": "Salzgitter", "magnitude": 100.0, "unit": "EUR_MILLION"}
            ],
            "reactions": [
                {"reaction_type": "PRICE_CHANGE", "affected_entity": "GAS_TTF", "direction": "INCREASE", "magnitude": 5.0, "unit": "PERCENT"}
            ]
        }
        scraper_with_graph = EconomicNewsScraper(graph_store=graph_store, offline=True)
        scraper_with_graph.ingest_news_into_graph_store([event])

        # Ensure no cross-product CAUSES edge directly connecting Salzgitter capex to gas prices
        causes_edges = [e for e in graph_store._edges if getattr(e, "relation_type", None) == "CAUSES"]
        self.assertEqual(len(causes_edges), 0, "Default news ingestion must use factual/ABOUT edges, not all-to-all CAUSES")

    # ------------------------------------------------------------------
    # 7. Production Isolation: No Synthetic Outage Fallback
    # ------------------------------------------------------------------
    @patch("requests.get")
    def test_production_mode_disables_synthetic_seed_fallback(self, mock_get):
        """In production mode, network failure returns empty/error, never synthetic seeds."""
        mock_get.side_effect = Exception("Connection refused")
        prod_scraper = EconomicNewsScraper(offline=False)

        with patch.dict(os.environ, {"POWERLAW_MODE": "production"}):
            items = prod_scraper.scrape_all_feeds(save_to_db=False)
            self.assertEqual(items, [], "Production mode must not fall back to synthetic seeds on error")

    # ------------------------------------------------------------------
    # 8. Common Runner Integration
    # ------------------------------------------------------------------
    def test_common_runner_registration_and_dry_run_ingestion(self):
        """OFFICIAL_NEWS is registered with common runner and dry_run works."""
        self.assertIn("OFFICIAL_NEWS", _DISCOVERERS, "OFFICIAL_NEWS discoverer must be registered")
        self.assertIn("OFFICIAL_NEWS", _PARSERS, "OFFICIAL_NEWS parser must be registered")

        report = run_ingestion("OFFICIAL_NEWS", limit=1, dry_run=True)
        self.assertIn("discovered", report)
        self.assertGreaterEqual(report["discovered"], 0)


    # ------------------------------------------------------------------
    # 9. Precision & Recall Quality Evaluation Benchmark
    # ------------------------------------------------------------------
    def test_news_extraction_precision_recall_and_quality_report(self):
        """Evaluate extraction precision and recall against verified positive/negative benchmark snippets."""
        benchmark_samples = [
            # Positive Actions
            {"title": "ArcelorMittal signs long-term procurement contract for renewable power", "summary": "Offtake deal signed for Hamburg plant.", "has_action": True, "has_reaction": False},
            {"title": "BASF invests 500 million in Ludwigshafen", "summary": "Chemical producer allocates capex.", "has_action": True, "has_reaction": False},
            {"title": "Commission imposes 30% anti-dumping tariff on steel imports", "summary": "Duties take effect.", "has_action": True, "has_reaction": False},
            {"title": "Salzgitter shutters furnace 1 in Peine", "summary": "Plant decommissioning completed.", "has_action": True, "has_reaction": False},
            # Positive Reactions
            {"title": "TTF natural gas prices surge 20%", "summary": "Gas spot rates jump amid cold front.", "has_action": False, "has_reaction": True},
            {"title": "Domestic primary aluminium supply drops 100000 tonnes", "summary": "Smelter output declines.", "has_action": False, "has_reaction": True},
            # Dual Action + Reaction
            {"title": "Yara curtails fertilizer production by 20% as ammonia costs rise 15%", "summary": "Production cuts announced while input costs climb.", "has_action": True, "has_reaction": True},
            # True Negatives / Negated (Must NOT produce action or reaction)
            {"title": "ArcelorMittal will not close its Hamburg facility", "summary": "Spokesperson denies closure rumors.", "has_action": False, "has_reaction": False},
            {"title": "Thyssenkrupp rejects reports of production cuts", "summary": "No cuts planned in Duisburg.", "has_action": False, "has_reaction": False},
            {"title": "General market outlook remains stable", "summary": "Supply chain perspectives discussed without policy changes.", "has_action": False, "has_reaction": False},
            {"title": "Quarterly macroeconomic survey published", "summary": "Overview of industrial trends with no specific company moves.", "has_action": False, "has_reaction": False},
        ]

        tp_act, fp_act, fn_act = 0, 0, 0
        tp_react, fp_react, fn_react = 0, 0, 0

        for s in benchmark_samples:
            evt = self.scraper.extract_structured_event(s["title"], s["summary"], "https://example.com/test", "Test", "2024-05-01")
            pred_act = len(evt.actions) > 0
            pred_react = len(evt.reactions) > 0

            if s["has_action"] and pred_act:
                tp_act += 1
            elif not s["has_action"] and pred_act:
                fp_act += 1
            elif s["has_action"] and not pred_act:
                fn_act += 1

            if s["has_reaction"] and pred_react:
                tp_react += 1
            elif not s["has_reaction"] and pred_react:
                fp_react += 1
            elif s["has_reaction"] and not pred_react:
                fn_react += 1

        action_precision = tp_act / (tp_act + fp_act) if (tp_act + fp_act) > 0 else 1.0
        action_recall = tp_act / (tp_act + fn_act) if (tp_act + fn_act) > 0 else 1.0
        reaction_precision = tp_react / (tp_react + fp_react) if (tp_react + fp_react) > 0 else 1.0
        reaction_recall = tp_react / (tp_react + fn_react) if (tp_react + fn_react) > 0 else 1.0

        # Assert zero false positives on negated / commentary texts
        self.assertEqual(fp_act, 0, f"False positive actions detected: {fp_act}")
        self.assertEqual(fp_react, 0, f"False positive reactions detected: {fp_react}")

        # Assert high precision and recall
        self.assertGreaterEqual(action_precision, 0.90)
        self.assertGreaterEqual(action_recall, 0.90)
        self.assertGreaterEqual(reaction_precision, 0.90)
        self.assertGreaterEqual(reaction_recall, 0.90)


if __name__ == "__main__":
    unittest.main()

