"""
Comprehensive test suite for EconomicNewsScraper and Event Extraction Pipeline.
"""

import os
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import numpy as np

from src.scrapers.news_scraper import (
    EconomicNewsScraper,
    generate_deterministic_embedding,
    FALLBACK_NEWS_CORPUS
)
from src.schemas.extraction import (
    NewsEntities,
    EconomicAction,
    EconomicReaction,
    StructuredEconomicEvent
)
from src.schemas.lancedb import NewsItem
from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import NodeType, EdgeRelation


class TestNewsScraper(unittest.TestCase):

    def setUp(self):
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

    # ---------------------------------------------------------------
    # 1. Deterministic Embedding Tests
    # ---------------------------------------------------------------
    def test_deterministic_embedding(self):
        text = "European carbon prices surge as ETS auction volumes tighten."
        vec1 = generate_deterministic_embedding(text, dim=768)
        vec2 = generate_deterministic_embedding(text, dim=768)

        self.assertEqual(len(vec1), 768)
        self.assertEqual(vec1, vec2, "Embedding must be 100% deterministic")
        
        # Check unit normalization
        norm = np.linalg.norm(np.array(vec1))
        self.assertAlmostEqual(norm, 1.0, places=4)

    # ---------------------------------------------------------------
    # 2. Entity and Actor Extraction Tests
    # ---------------------------------------------------------------
    def test_extract_actors(self):
        text = "ArcelorMittal and BASF announce restructuring in Germany and France impacting steel and chemical operations."
        entities = self.scraper.extract_actors(text)

        self.assertIn("ArcelorMittal", entities.companies)
        self.assertIn("BASF", entities.companies)
        self.assertIn("Germany", entities.countries)
        self.assertIn("France", entities.countries)
        self.assertIn("C24_BASIC_METALS", entities.sectors)
        self.assertIn("C20_CHEMICALS", entities.sectors)
        self.assertIn("EU", entities.geography)

    def test_extract_actors_empty(self):
        text = "General market observations with no specific references."
        entities = self.scraper.extract_actors(text)
        self.assertEqual(len(entities.companies), 0)
        self.assertEqual(len(entities.countries), 0)
        self.assertEqual(entities.geography, ["EU"])

    # ---------------------------------------------------------------
    # 3. Action Extraction Tests
    # ---------------------------------------------------------------
    def test_extract_action_production_cut(self):
        title = "ArcelorMittal slashes production by 20% across German mills"
        summary = "Steelmaker cuts output due to energy input costs."
        actors = self.scraper.extract_actors(f"{title} {summary}")
        actions = self.scraper.extract_actions(title, summary, actors)

        self.assertTrue(len(actions) >= 1)
        self.assertEqual(actions[0].action_type, "PRODUCTION_CUT")
        self.assertEqual(actions[0].actor, "ArcelorMittal")
        self.assertEqual(actions[0].magnitude, 20.0)
        self.assertEqual(actions[0].unit, "PERCENT")

    def test_extract_action_capex_investment(self):
        title = "BASF invests €500 million in low-carbon chemical plant"
        summary = "Chemical major funds expansion in Ludwigshafen."
        actors = self.scraper.extract_actors(f"{title} {summary}")
        actions = self.scraper.extract_actions(title, summary, actors)

        self.assertTrue(len(actions) >= 1)
        self.assertEqual(actions[0].action_type, "CAPEX_INVESTMENT")
        self.assertEqual(actions[0].magnitude, 500.0)
        self.assertEqual(actions[0].unit, "EUR_MILLION")

    def test_extract_action_tariff_imposition(self):
        title = "European Commission imposes 35% anti-dumping tariff on imported steel"
        summary = "Trade defense duties finalized for foreign metal imports."
        actors = self.scraper.extract_actors(f"{title} {summary}")
        actions = self.scraper.extract_actions(title, summary, actors)

        self.assertTrue(len(actions) >= 1)
        self.assertEqual(actions[0].action_type, "TARIFF_IMPOSITION")
        self.assertEqual(actions[0].magnitude, 35.0)
        self.assertEqual(actions[0].unit, "PERCENT")

    # ---------------------------------------------------------------
    # 4. Reaction Extraction Tests
    # ---------------------------------------------------------------
    def test_extract_reaction_price_surge(self):
        title = "Dutch TTF natural gas futures jump 15% on pipeline curtailment"
        summary = "Wholesale gas prices rise sharply amid supply worries."
        actors = self.scraper.extract_actors(f"{title} {summary}")
        reactions = self.scraper.extract_reactions(title, summary, actors)

        self.assertTrue(len(reactions) >= 1)
        self.assertEqual(reactions[0].reaction_type, "PRICE_CHANGE")
        self.assertEqual(reactions[0].direction, "INCREASE")
        self.assertEqual(reactions[0].affected_entity, "GAS_TTF")
        self.assertEqual(reactions[0].magnitude, 15.0)
        self.assertEqual(reactions[0].unit, "PERCENT")

    def test_extract_reaction_supply_decrease(self):
        title = "Potline shutdown causes domestic aluminium supply to drop by 150000 tonnes"
        summary = "Output declines across European smelting network."
        actors = self.scraper.extract_actors(f"{title} {summary}")
        reactions = self.scraper.extract_reactions(title, summary, actors)

        self.assertTrue(len(reactions) >= 1)
        self.assertEqual(reactions[0].direction, "DECREASE")
        self.assertEqual(reactions[0].affected_entity, "ALUMINIUM_PRIMARY")
        self.assertEqual(reactions[0].magnitude, 150000.0)
        self.assertEqual(reactions[0].unit, "TONNES")

    # ---------------------------------------------------------------
    # 5. Full Structured Event Extraction
    # ---------------------------------------------------------------
    def test_extract_structured_event(self):
        title = "Yara curtails nitrogen fertilizer output in Norway as gas input costs soar"
        summary = "Fertilizer production cuts of 25% take effect immediately, driving European wholesale agricultural prices up by 12%."
        evt = self.scraper.extract_structured_event(
            title=title,
            summary=summary,
            source_url="https://example.com/yara-fertilizer",
            source_name="AgriTrade Review",
            pub_date="2024-07-15"
        )

        self.assertIsInstance(evt, StructuredEconomicEvent)
        self.assertEqual(evt.timestamp, "2024-07-15")
        self.assertEqual(evt.headline, title)
        self.assertEqual(evt.source_url, "https://example.com/yara-fertilizer")
        self.assertIn("Yara International", evt.actors.companies)
        self.assertIn("Norway", evt.actors.countries)
        self.assertTrue(len(evt.actions) > 0)
        self.assertTrue(len(evt.reactions) > 0)

    # ---------------------------------------------------------------
    # 6. Fallback Offline Seed Generator Tests
    # ---------------------------------------------------------------
    def test_offline_seed_generator_benchmark(self):
        count = 5
        events = self.scraper.generate_offline_seeds(count=count, save_to_lancedb=False)

        self.assertEqual(len(events), count)
        for evt in events:
            self.assertIsInstance(evt, StructuredEconomicEvent)
            self.assertTrue(evt.event_id.startswith("NEWS_"))
            self.assertTrue(len(evt.headline) > 10)
            self.assertTrue(len(evt.actions) > 0)
            self.assertTrue(len(evt.reactions) > 0)

    def test_offline_seed_generator_synthetic(self):
        count = len(FALLBACK_NEWS_CORPUS) + 5
        events = self.scraper.generate_offline_seeds(count=count, save_to_lancedb=False)

        self.assertEqual(len(events), count)
        # Check that both benchmark and synthetic events are present
        synth_events = [e for e in events if "SEED" in e.event_id]
        self.assertTrue(len(synth_events) >= 5)

    # ---------------------------------------------------------------
    # 7. LanceDB Storage and Retrieval Tests
    # ---------------------------------------------------------------
    def test_lancedb_save_and_load(self):
        events = self.scraper.generate_offline_seeds(count=4, save_to_lancedb=True)
        self.assertEqual(len(events), 4)

        # Load back
        records = self.scraper.load_from_lancedb(limit=10)
        self.assertEqual(len(records), 4)
        
        record_ids = {r["news_id"] for r in records}
        for evt in events:
            self.assertIn(evt.event_id, record_ids)

        # Test deduplication: saving same events again should insert 0 new records
        new_count = self.scraper.save_to_lancedb(events)
        self.assertEqual(new_count, 0)

        # Total rows should still be 4
        records_after = self.scraper.load_from_lancedb()
        self.assertEqual(len(records_after), 4)

    # ---------------------------------------------------------------
    # 8. DualLayerGraphStore Integration Tests
    # ---------------------------------------------------------------
    def test_graph_store_integration(self):
        graph_store = DualLayerGraphStore(storage_dir=str(self.temp_dir.name / Path("graph_db")))
        scraper_with_graph = EconomicNewsScraper(
            data_dir=str(self.data_dir),
            lancedb_path=str(self.db_path),
            graph_store=graph_store,
            offline=True
        )

        events = scraper_with_graph.generate_offline_seeds(count=3, save_to_lancedb=False)
        self.assertTrue(len(events) > 0)

        # Verify Action and Reaction nodes exist
        action_nodes = [n for n in graph_store._nodes.values() if n.node_type == NodeType.ACTION]
        reaction_nodes = [n for n in graph_store._nodes.values() if n.node_type == NodeType.REACTION]
        self.assertTrue(len(action_nodes) >= 3)
        self.assertTrue(len(reaction_nodes) >= 3)

        # Verify TRIGGERS edges exist
        triggers_edges = [e for e in graph_store._edges if e.relation_type == EdgeRelation.TRIGGERS]
        self.assertTrue(len(triggers_edges) >= 3)

        # Verify behind-the-graph temporal history records exist
        self.assertTrue(len(graph_store._history) >= 6)

    # ---------------------------------------------------------------
    # 9. Mocked RSS / Atom Feed Parsing Tests
    # ---------------------------------------------------------------
    @patch("requests.get")
    def test_mock_rss_feed_parsing(self, mock_get):
        rss_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>EU Energy Monitor</title>
                <item>
                    <title>RWE Invests in Off-Shore Wind Capacity in North Sea</title>
                    <link>https://energy-monitor.eu/news/rwe-wind</link>
                    <pubDate>Mon, 15 Jul 2024 10:00:00 GMT</pubDate>
                    <description>RWE announces 1.2 billion capital allocation for offshore turbines.</description>
                </item>
            </channel>
        </rss>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = rss_xml
        mock_get.return_value = mock_response

        items = self.scraper.fetch_rss_feed("https://fake.url/rss", "Mock Source")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "RWE Invests in Off-Shore Wind Capacity in North Sea")
        self.assertEqual(items[0]["published_date"], "2024-07-15")
        self.assertEqual(items[0]["source_url"], "https://energy-monitor.eu/news/rwe-wind")

    @patch("requests.get")
    def test_mock_network_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection refused / timeout")
        items = self.scraper.fetch_rss_feed("https://unreachable.url/rss", "Fail Source")
        self.assertEqual(items, [], "Must return empty list on failure without raising unhandled exception")

    # ---------------------------------------------------------------
    # 10. Explicit Edge Cases
    # ---------------------------------------------------------------
    def test_empty_and_whitespace_inputs(self):
        # Empty inputs should not raise exceptions and return valid defaults
        evt = self.scraper.extract_structured_event(
            title="",
            summary="   ",
            source_url="",
            source_name="",
            pub_date=""
        )
        self.assertIsInstance(evt, StructuredEconomicEvent)
        self.assertEqual(len(evt.actors.companies), 0)
        self.assertEqual(len(evt.actions), 0)
        self.assertTrue(len(evt.reactions) > 0)
        self.assertEqual(evt.reactions[0].direction, "STABLE")

    def test_date_parser_edge_cases(self):
        # Test various date formats and fallbacks
        self.assertEqual(self.scraper._parse_date("2024-11-25T14:30:00Z"), "2024-11-25")
        self.assertEqual(self.scraper._parse_date("25 Nov 2024"), "2024-11-25")
        self.assertEqual(self.scraper._parse_date("2024/08/04 12:00"), "2024-08-04")
        # Empty string should return today's ISO date
        today_prefix = datetime.now(timezone.utc).strftime("%Y-")
        self.assertTrue(self.scraper._parse_date("").startswith(today_prefix))
        self.assertTrue(self.scraper._parse_date("not-a-date").startswith(today_prefix))

    def test_numeric_magnitude_edge_cases(self):
        # Negative percentage
        mag, unit = self.scraper._extract_magnitude("Output reduced by -12.5% in Q3")
        self.assertEqual(mag, -12.5)
        self.assertEqual(unit, "PERCENT")

        # Billions
        mag, unit = self.scraper._extract_magnitude("Investment of €2.4 billion approved")
        self.assertEqual(mag, 2400.0)
        self.assertEqual(unit, "EUR_MILLION")

        # Tonnes
        mag, unit = self.scraper._extract_magnitude("Capacity restricted by 75000 tonnes")
        self.assertEqual(mag, 75000.0)
        self.assertEqual(unit, "TONNES")

        # No numbers
        mag, unit = self.scraper._extract_magnitude("General discussions without numeric parameters")
        self.assertIsNone(mag)
        self.assertIsNone(unit)

    @patch("requests.get")
    def test_atom_feed_parsing(self, mock_get):
        atom_xml = b"""<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>European Energy News</title>
            <entry>
                <title>Equinor Extends Gas Supply Agreement With Germany</title>
                <link href="https://energy.eu/equinor-gas"/>
                <updated>2024-09-01T08:00:00Z</updated>
                <summary>Long-term contract signed for natural gas supply to German industrial grid.</summary>
            </entry>
        </feed>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = atom_xml
        mock_get.return_value = mock_response

        items = self.scraper.fetch_rss_feed("https://fake.url/atom", "Atom Source")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Equinor Extends Gas Supply Agreement With Germany")
        self.assertEqual(items[0]["published_date"], "2024-09-01")
        self.assertEqual(items[0]["source_url"], "https://energy.eu/equinor-gas")

    def test_json_disk_caching(self):
        events = self.scraper.generate_offline_seeds(count=3, save_to_lancedb=False)
        cache_path = self.data_dir / "test_cache.json"
        saved_path = self.scraper.cache_to_disk(events, output_file=cache_path)

        self.assertTrue(saved_path.exists())
        with open(saved_path, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)
        self.assertEqual(len(loaded_data), 3)
        self.assertIn("headline", loaded_data[0])
        self.assertIn("actions", loaded_data[0])
        self.assertIn("reactions", loaded_data[0])

    # ---------------------------------------------------------------
    # 11. Regression & Robustness Tests (Worker 2 Improvements)
    # ---------------------------------------------------------------
    def test_dict_input_save_and_cache(self):
        """Verify save_to_lancedb and cache_to_disk accept raw dictionary events."""
        dict_event = {
            "news_id": "TEST_DICT_001",
            "title": "ArcelorMittal Announces 10% Output Reduction in France",
            "published_date": "2024-04-12",
            "summary": "Steel output curtailed by 10% amid rising electricity tariffs.",
            "source_name": "Industry Wire",
            "source_url": "https://industry.test/001",
            "actors": {
                "companies": ["ArcelorMittal"],
                "countries": ["France"],
                "sectors": ["C24_BASIC_METALS"]
            }
        }
        # Save to LanceDB
        inserted = self.scraper.save_to_lancedb([dict_event])
        self.assertEqual(inserted, 1)

        # Cache to disk
        cache_file = self.data_dir / "dict_test.json"
        saved = self.scraper.cache_to_disk([dict_event], output_file=cache_file)
        self.assertTrue(saved.exists())
        with open(saved, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["event_id"], "TEST_DICT_001")

    def test_comma_numeric_magnitude(self):
        """Verify magnitudes formatted with commas (50,000 tonnes, 1,800 million) parse accurately."""
        mag1, unit1 = self.scraper._extract_magnitude("Capacity restricted by 50,000 tonnes")
        self.assertEqual(mag1, 50000.0)
        self.assertEqual(unit1, "TONNES")

        mag2, unit2 = self.scraper._extract_magnitude("Grant aid of €1,800 million confirmed")
        self.assertEqual(mag2, 1800.0)
        self.assertEqual(unit2, "EUR_MILLION")

    def test_plural_and_past_tense_reactions(self):
        """Verify plural nouns ('prices') and past tense verbs ('rose', 'plunged') classify correctly."""
        actors = self.scraper.extract_actors("EU Carbon")
        rx_rose = self.scraper.extract_reactions("EU Carbon Prices Rose 12% After Auction", "", actors)
        self.assertTrue(len(rx_rose) >= 1)
        self.assertEqual(rx_rose[0].reaction_type, "PRICE_CHANGE")
        self.assertEqual(rx_rose[0].direction, "INCREASE")
        self.assertEqual(rx_rose[0].magnitude, 12.0)

        rx_plunge = self.scraper.extract_reactions("Gas prices plunge 20% on storage glut", "", actors)
        self.assertTrue(len(rx_plunge) >= 1)
        self.assertEqual(rx_plunge[0].reaction_type, "PRICE_CHANGE")
        self.assertEqual(rx_plunge[0].direction, "DECREASE")
        self.assertEqual(rx_plunge[0].magnitude, 20.0)

    def test_decoupled_action_and_reaction_magnitude(self):
        """Verify action capex is not hijacked by reaction percent in the same article."""
        actors = self.scraper.extract_actors("Salzgitter invests €1.8 billion in DRI steel")
        text_act = "Salzgitter invests €1.8 billion in DRI steel"
        text_rx = "and expects hydrogen demand to jump 25%"
        
        actions = self.scraper.extract_actions(text_act, text_rx, actors)
        reactions = self.scraper.extract_reactions(text_act, text_rx, actors)

        self.assertEqual(actions[0].action_type, "CAPEX_INVESTMENT")
        self.assertEqual(actions[0].magnitude, 1800.0)
        self.assertEqual(actions[0].unit, "EUR_MILLION")

        self.assertEqual(reactions[0].reaction_type, "DEMAND_CHANGE")
        self.assertEqual(reactions[0].direction, "INCREASE")
        self.assertEqual(reactions[0].magnitude, 25.0)
        self.assertEqual(reactions[0].unit, "PERCENT")

    def test_rfc2822_date_parsing_with_timezones_and_weekdays(self):
        """Verify dates longer than 29 chars with weekdays and offsets parse without falling back to today."""
        self.assertEqual(self.scraper._parse_date("Wednesday, 15 Jul 2024 10:00:00 GMT"), "2024-07-15")
        self.assertEqual(self.scraper._parse_date("Mon, 15 Jul 2024 10:00:00 +02:00"), "2024-07-15")
        self.assertEqual(self.scraper._parse_date("15 July 2024"), "2024-07-15")

    def test_graph_store_affects_edges(self):
        """Verify that ingesting events into DualLayerGraphStore generates AFFECTS edges connecting to sectors."""
        graph_store = DualLayerGraphStore(storage_dir=str(self.temp_dir.name / Path("graph_db2")))
        # Add sector node to store
        from src.models.dual_layer_model import GraphNode, NodeType, EdgeRelation
        graph_store.add_node(GraphNode(id="C24_BASIC_METALS", label="Basic Metals", node_type=NodeType.SECTOR))
        
        scraper_with_graph = EconomicNewsScraper(
            data_dir=str(self.data_dir),
            lancedb_path=str(self.db_path),
            graph_store=graph_store,
            offline=True
        )

        test_evt = {
            "news_id": "TEST_METALS_01",
            "title": "ArcelorMittal Cuts Output in C24_BASIC_METALS",
            "published_date": "2024-02-01",
            "summary": "Curtailment in steel sector.",
            "actors": {
                "companies": ["ArcelorMittal"],
                "countries": ["Germany"],
                "sectors": ["C24_BASIC_METALS"]
            },
            "actions": [{
                "action_type": "PRODUCTION_CUT",
                "actor": "ArcelorMittal",
                "target": "C24_BASIC_METALS",
                "magnitude": 15.0,
                "unit": "PERCENT"
            }]
        }
        scraper_with_graph.ingest_news_into_graph_store([test_evt])

        affects_edges = [e for e in graph_store._edges if e.relation_type == EdgeRelation.AFFECTS]
        self.assertTrue(len(affects_edges) >= 1)
        self.assertEqual(affects_edges[0].target_id, "C24_BASIC_METALS")

    @patch("requests.get")
    def test_scrape_all_feeds_offline_mode_no_network(self, mock_get):
        """Verify that when offline=True, scrape_all_feeds makes zero HTTP requests."""
        offline_scraper = EconomicNewsScraper(
            data_dir=str(self.data_dir),
            lancedb_path=str(self.db_path),
            offline=True
        )
        events = offline_scraper.scrape_all_feeds(save_to_db=False)
        self.assertTrue(len(events) >= 10)
        mock_get.assert_not_called()

    @patch("src.scrapers.news_scraper.get_completion_with_json")
    def test_extract_with_llm_mocked(self, mock_llm):
        """Verify extract_with_llm properly decodes JSON and populates StructuredEconomicEvent."""
        mock_payload = json.dumps({
            "actors": {
                "companies": ["Thyssenkrupp"],
                "countries": ["Germany"],
                "sectors": ["C24_BASIC_METALS"],
                "geography": ["EU"]
            },
            "actions": [{
                "action_type": "PRODUCTION_CUT",
                "actor": "Thyssenkrupp",
                "target": "Duisburg Blast Furnace",
                "description": "Idling blast furnace 2",
                "magnitude": 20.0,
                "unit": "PERCENT"
            }],
            "reactions": [{
                "reaction_type": "SUPPLY_SHIFT",
                "affected_entity": "STEEL_HRC",
                "direction": "DECREASE",
                "magnitude": 20.0,
                "unit": "PERCENT",
                "timeframe": "3_MONTHS",
                "confidence": 0.95
            }],
            "receptor_signal": "BLAST_FURNACE_CURTAILMENT",
            "target_asset": "STEEL_HRC"
        })
        mock_llm.return_value = mock_payload

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake_key"}):
            llm_scraper = EconomicNewsScraper(use_llm=True, offline=False)
            evt = llm_scraper.extract_structured_event(
                title="Thyssenkrupp Idles Blast Furnace",
                summary="Production will be cut by 20% in Duisburg.",
                source_url="https://test.news/tk",
                source_name="Steel Monitor",
                pub_date="2024-05-01"
            )
            self.assertEqual(evt.receptor_signal, "BLAST_FURNACE_CURTAILMENT")
            self.assertEqual(evt.actors.companies, ["Thyssenkrupp"])
            self.assertEqual(evt.actions[0].action_type, "PRODUCTION_CUT")
            self.assertEqual(evt.reactions[0].affected_entity, "STEEL_HRC")

    @patch("src.scrapers.news_scraper.get_completion_with_json")
    def test_extract_with_llm_fallback_on_error(self, mock_llm):
        """Verify fallback to deterministic rule extraction when LLM call fails."""
        mock_llm.side_effect = Exception("API quota exceeded")

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake_key"}):
            llm_scraper = EconomicNewsScraper(use_llm=True, offline=False)
            evt = llm_scraper.extract_structured_event(
                title="ArcelorMittal cuts production by 15%",
                summary="German steel output reduced.",
                source_url="https://test.news/am",
                source_name="Steel Monitor",
                pub_date="2024-05-01"
            )
            # Should have fallen back cleanly to rule extractor
            self.assertEqual(evt.actors.companies, ["ArcelorMittal"])
            self.assertEqual(evt.actions[0].action_type, "PRODUCTION_CUT")


if __name__ == "__main__":
    unittest.main()

