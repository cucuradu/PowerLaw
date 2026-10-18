"""
Tests for centralized ingestion configuration, DB resolution, and production origin isolation (Task 1).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd

# Test that get_settings and Settings can be imported and verified
try:
    from src.config import get_settings, Settings
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False


class TestIngestionConfig(unittest.TestCase):
    """Verifies that centralized Settings handles path resolution and origin isolation."""

    def test_settings_importable_and_has_required_fields(self):
        """Settings must be importable and expose required paths, origins, and limits."""
        self.assertTrue(HAS_CONFIG, "src.config must provide get_settings and Settings")
        settings = get_settings()
        self.assertIsInstance(settings, Settings)
        self.assertIsInstance(settings.data_dir, Path)
        self.assertIsInstance(settings.lancedb_path, Path)
        self.assertIsInstance(settings.sqlite_path, Path)
        self.assertIsInstance(settings.mode, str)
        self.assertIn(settings.mode, ["production", "demo", "test"])
        self.assertIsInstance(settings.object_backend, str)
        self.assertIsInstance(settings.cache_dir, Path)
        self.assertIsInstance(settings.cache_limit_bytes, int)
        self.assertIsInstance(settings.temp_dir, Path)
        self.assertIsInstance(settings.temp_limit_bytes, int)
        self.assertIsInstance(settings.disk_floor_bytes, int)
        self.assertIsInstance(settings.extraction_budget_usd, (int, float))

    def test_entry_points_resolve_same_configured_db(self):
        """All entry points must resolve the exact same configured database path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_db = Path(tmp_dir) / "custom_lancedb"
            with patch.dict(os.environ, {"LANCEDB_PATH": str(custom_db)}):
                # Clear cached settings
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()
                settings = get_settings()
                self.assertEqual(settings.lancedb_path.resolve(), custom_db.resolve())

                # Check dashboard_data resolution
                import src.storage.dashboard_data as dd
                resolved_dd = dd.get_configured_db_path() if hasattr(dd, "get_configured_db_path") else Path(dd.DEFAULT_DB_PATH)
                self.assertEqual(Path(resolved_dd).resolve(), custom_db.resolve())

                # Check news_scraper default resolution
                from src.scrapers.news_scraper import EconomicNewsScraper
                scraper = EconomicNewsScraper(offline=True)
                self.assertEqual(Path(scraper.lancedb_path).resolve(), custom_db.resolve())

                # Check main DocumentProcessor default resolution
                import src.main as main_mod
                resolved_main = Path(main_mod.get_configured_db_path()) if hasattr(main_mod, "get_configured_db_path") else Path(main_mod.LANCEDB_PATH)
                self.assertEqual(resolved_main.resolve(), custom_db.resolve())

    def test_production_empty_store_stays_empty(self):
        """In production mode, opening an empty database must not silently inject demo seed data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            prod_db = Path(tmp_dir) / "prod_db"
            prod_db.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"POWERLAW_MODE": "production", "LANCEDB_PATH": str(prod_db)}):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                import src.storage.dashboard_data as dd
                store = dd.get_graph_store(storage_dir=str(prod_db))
                self.assertEqual(len(store.get_nodes()), 0, "Production store must stay empty, no auto-seeding")
                self.assertEqual(len(store.get_edges()), 0, "Production store must have 0 edges")
                self.assertEqual(len(store.get_history()), 0, "Production store must have 0 history records")

                # Ensure structured rules also stays empty in production
                dd.ensure_structured_rules_table(storage_dir=str(prod_db))
                laws = dd.get_all_laws_data(storage_dir=str(prod_db))
                self.assertEqual(len(laws), 0, "Production structured_rules table must not inject benchmark seed rules")

    def test_demo_mode_opts_in_to_seed_data(self):
        """Demo mode must explicitly opt in to populate benchmark seed data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            demo_db = Path(tmp_dir) / "demo_db"
            demo_db.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"POWERLAW_MODE": "demo", "LANCEDB_PATH": str(demo_db)}):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                import src.storage.dashboard_data as dd
                # In demo mode, explicit demo store or get_demo_graph_store populates seeds
                if hasattr(dd, "get_demo_graph_store"):
                    store = dd.get_demo_graph_store(storage_dir=str(demo_db))
                else:
                    store = dd.get_graph_store(storage_dir=str(demo_db))
                self.assertGreater(len(store.get_nodes()), 0, "Demo mode must populate seed nodes")
                self.assertGreater(len(store.get_edges()), 0, "Demo mode must populate seed edges")

    def test_failed_market_fetch_in_production_produces_no_synthetic_rows(self):
        """When yfinance fails in production mode, MarketDataIngester must NOT generate synthetic rows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            prod_db = Path(tmp_dir) / "prod_db"
            prod_db.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"POWERLAW_MODE": "production", "LANCEDB_PATH": str(prod_db)}):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                from src.storage.graph_store import DualLayerGraphStore
                from src.scrapers.market_data import MarketDataIngester

                store = DualLayerGraphStore(storage_dir=str(prod_db))
                ingester = MarketDataIngester(store)

                # Mock network fetch to return empty/None
                with patch.object(ingester, "_fetch_history", return_value=pd.DataFrame()):
                    res = ingester.ingest_tracked_instruments(period="1mo")
                    # In production, failure must result in 0 synthetic history rows written
                    self.assertEqual(res.get("history_rows_added", 0), 0)
                    self.assertEqual(len(store.get_history()), 0, "Must not inject synthetic history into store")
                    # Must report error or unavailable status
                    self.assertIn("status", res)
                    self.assertIn(res["status"], ["partial_failure", "unavailable", "failed"])

    def test_failed_news_fetch_in_production_produces_no_synthetic_rows(self):
        """When RSS feeds fail or return empty in production, scraper must NOT generate offline seed events."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            prod_db = Path(tmp_dir) / "prod_db"
            prod_db.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"POWERLAW_MODE": "production", "LANCEDB_PATH": str(prod_db)}):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                from src.storage.graph_store import DualLayerGraphStore
                from src.scrapers.news_scraper import EconomicNewsScraper

                store = DualLayerGraphStore(storage_dir=str(prod_db))
                scraper = EconomicNewsScraper(
                    data_dir=str(Path(tmp_dir) / "news"),
                    lancedb_path=str(prod_db),
                    graph_store=store,
                    offline=False
                )

                # Mock fetch_rss_feed to return empty list (failed/empty feed)
                with patch.object(scraper, "fetch_rss_feed", return_value=[]):
                    events = scraper.scrape_all_feeds(save_to_db=True)
                    self.assertEqual(len(events), 0, "Production scraper must return 0 events on failed feeds")
                    self.assertEqual(len(store.get_history()), 0, "Production store must have 0 synthetic rows")

    def test_failed_macro_fred_in_production_produces_no_synthetic_state(self):
        """When keyless FRED fails in production, MacroFREDEngine must return unavailable status without synthetic state."""
        with patch.dict(os.environ, {"POWERLAW_MODE": "production"}):
            import src.config
            if hasattr(src.config, "clear_settings_cache"):
                src.config.clear_settings_cache()

            from src.quant.macro_fred import MacroFREDEngine
            engine = MacroFREDEngine()

            with patch.object(engine, "fetch_fred_series", return_value=None):
                res = engine.compute_macro_risk()
                self.assertEqual(res.get("status"), "unavailable")
                self.assertIsNone(res.get("macro_risk"))

    def test_free_sources_register_loaded_and_verified(self):
        """Free-source register must be loadable and all sources must satisfy access_fee=0."""
        from src.config import load_data_sources_registry
        reg = load_data_sources_registry()
        self.assertIn("sources", reg)
        sources = reg["sources"]

        # Core required sources must be registered
        core_keys = [
            "DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "BUNDESBANK_SDMX",
            "SMARD_DE", "EURLEX_CELLAR", "OFFICIAL_NEWS", "GISCO_GEOGRAPHY"
        ]
        for ck in core_keys:
            self.assertIn(ck, sources, f"Core source {ck} must be present in register")
            meta = sources[ck]
            self.assertEqual(meta.get("access_fee"), 0)
            self.assertFalse(meta.get("requires_paid_subscription"))
            self.assertTrue(len(meta.get("rights", "")) > 0)
            self.assertTrue(len(meta.get("attribution", "")) > 0)
            # Untested candidates must be disabled
            self.assertFalse(meta.get("enabled", True), f"Candidate {ck} must remain disabled until tested")

    def test_paid_source_candidate_rejected(self):
        """A candidate requiring payment or subscription must be rejected by validator."""
        from src.config import validate_source_entry
        with self.assertRaises(ValueError):
            validate_source_entry("PAID_API", {"access_fee": 100, "requires_paid_subscription": False})
        with self.assertRaises(ValueError):
            validate_source_entry("SUB_API", {"access_fee": 0, "requires_paid_subscription": True})

    def test_primary_target_locked_to_destatis_61261_0002(self):
        """Primary construction price target must be locked to Destatis 61261-0002 residential PPI."""
        from src.config import get_primary_target_definition
        target = get_primary_target_definition()
        self.assertEqual(target["family_id"], "61261")
        self.assertEqual(target["table_id"], "61261-0002")
        self.assertEqual(target["geography"], "DE")
        self.assertEqual(target["frequency"], "QUARTERLY")
        self.assertEqual(target["measurement_kind"], "PRODUCER_PRICE_INDEX")
        self.assertIn("CONSTRUCTION_INPUT_COST_INDEX", target["prohibited_substitutions"])
        self.assertIn("MATERIAL_PPI", target["prohibited_substitutions"])
        self.assertIn("HOUSE_PRICE_INDEX", target["prohibited_substitutions"])
        self.assertEqual(target["comparable_eurostat_code"], "sts_copi_q")

    def test_audit_sources_quarantines_legacy_origins(self):
        """audit_sources must report summary and quarantine unproven legacy files."""
        from scripts.ops.migrate_data import audit_sources
        report = audit_sources([Path("data/lancedb"), Path("storage/lancedb")])
        self.assertIn("summary", report)
        self.assertIn("paths_inspected", report)
        self.assertGreater(report["summary"]["total_bytes"], 0)
        self.assertGreater(report["summary"]["total_records"], 0)
        # Verify quarantine count
        self.assertGreater(report["summary"]["total_quarantined_records"], 0)

    def test_trial_sources_rejected_by_validator(self):
        """Free-source validator must reject trial/evaluation tier sources even if free."""
        from src.config import validate_source_entry
        with self.assertRaises(ValueError) as ctx:
            validate_source_entry("TRIAL_SOURCE", {
                "access_fee": 0,
                "requires_paid_subscription": False,
                "trial": True,
                "rights_checked": True,
                "rights": "Trial License",
                "terms_checked_at": "2026-10-18"
            })
        self.assertIn("trial", str(ctx.exception).lower())

    def test_training_restricted_sources_rejected_by_validator(self):
        """Free-source validator must reject sources with training or commercial restrictions."""
        from src.config import validate_source_entry
        # Training forbidden
        with self.assertRaises(ValueError) as ctx:
            validate_source_entry("NO_TRAIN_API", {
                "access_fee": 0,
                "requires_paid_subscription": False,
                "training_allowed": False,
                "rights_checked": True,
                "rights": "Creative Commons NonCommercial NoDerivatives",
                "terms_checked_at": "2026-10-18"
            })
        self.assertIn("training or commercial reuse restrictions", str(ctx.exception).lower())

        # Non-commercial terms in rights string
        with self.assertRaises(ValueError) as ctx:
            validate_source_entry("NON_COMMERCIAL_API", {
                "access_fee": 0,
                "requires_paid_subscription": False,
                "rights_checked": True,
                "rights": "Non-commercial educational use only",
                "terms_checked_at": "2026-10-18"
            })
        self.assertIn("restrictions", str(ctx.exception).lower())

    def test_missing_rights_check_rejected_by_validator(self):
        """Free-source validator must reject entries without verified rights check."""
        from src.config import validate_source_entry
        with self.assertRaises(ValueError) as ctx:
            validate_source_entry("UNVERIFIED_RIGHTS", {
                "access_fee": 0,
                "requires_paid_subscription": False,
                "rights_checked": False,
                "rights": "Public domain",
                "terms_checked_at": "2026-10-18"
            })
        self.assertIn("rights check", str(ctx.exception).lower())

    def test_production_mode_news_reader_strictly_rejects_demo_records(self):
        """Isolated probe: in production mode, news reader must NEVER return demo records."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            prod_dir = Path(tmp_dir) / "data" / "production"
            raw_news_dir = prod_dir / "raw" / "news"
            raw_news_dir.mkdir(parents=True, exist_ok=True)

            # Write a probe with mixed demo and production records into news cache
            probe_news = [
                {
                    "news_id": "NEWS_DEMO_01",
                    "headline": "Demo Headline Should Not Leak",
                    "data_origin": "demo",
                    "origin": "demo"
                },
                {
                    "news_id": "NEWS_SYNTHETIC_02",
                    "headline": "Synthetic Headline Should Not Leak",
                    "data_origin": "synthetic"
                },
                {
                    "news_id": "NEWS_PROD_01",
                    "headline": "Official Production Event",
                    "data_origin": "production",
                    "origin": "production"
                }
            ]
            import json
            with open(raw_news_dir / "scraped_news.json", "w", encoding="utf-8") as f:
                json.dump(probe_news, f)

            with patch.dict(os.environ, {
                "POWERLAW_MODE": "production",
                "POWERLAW_DATA_DIR": str(prod_dir)
            }):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                import src.storage.dashboard_data as dd
                articles = dd.get_all_news_data()

                # Must contain ONLY the production record, never demo or synthetic
                self.assertEqual(len(articles), 1)
                self.assertEqual(articles[0]["news_id"], "NEWS_PROD_01")
                self.assertNotIn("NEWS_DEMO_01", [a["news_id"] for a in articles])

    def test_database_paths_default_to_centralized_settings(self):
        """All stores, exporters, and CLI arguments must default to centralized Settings path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_path = Path(tmp_dir) / "custom_lancedb"
            with patch.dict(os.environ, {"LANCEDB_PATH": str(custom_path)}):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                settings = src.config.get_settings()
                self.assertEqual(settings.database_path.resolve(), custom_path.resolve())

                # DualLayerGraphStore default
                from src.storage.graph_store import DualLayerGraphStore
                gs = DualLayerGraphStore()
                self.assertEqual(gs.storage_dir.resolve(), custom_path.resolve())

                # DualLayerStore default
                from src.storage.dual_layer_store import DualLayerStore
                ds = DualLayerStore()
                self.assertEqual(ds.storage_dir.resolve(), custom_path.resolve())

                # GNNGraphExporter default
                from src.gnn.graph_exporter import GNNGraphExporter
                exporter = GNNGraphExporter(store=None, storage_dir=None)
                self.assertEqual(exporter.store.storage_dir.resolve(), custom_path.resolve())

    def test_audit_includes_minio_raw_references_and_temporal_coverage(self):
        """audit_sources must include MinIO inspection, raw document reference validation, and temporal coverage."""
        from scripts.ops.migrate_data import audit_sources
        report = audit_sources([Path("data/lancedb")])
        self.assertIn("minio_inspection", report)
        self.assertIn("status", report["minio_inspection"])

        path_data = report["paths_inspected"][0]
        # Check parquet files inspection
        for f_name, f_data in path_data.get("parquet_files", {}).items():
            self.assertIn("raw_document_references", f_data)
            self.assertIn("temporal_coverage", f_data)
            self.assertIn("classification", f_data)
            # Unproven legacy without verified production origin must NOT be "verified"
            self.assertNotEqual(f_data["classification"], "verified")

    def test_primary_target_has_coverage_release_metadata_and_valid_fixture(self):
        """Primary target must contain coverage metadata, release metadata, and an on-disk response fixture."""
        from src.config import get_primary_target_definition
        target = get_primary_target_definition()

        self.assertIn("coverage_metadata", target)
        cov = target["coverage_metadata"]
        self.assertIn("geographic_coverage", cov)
        self.assertIn("sector_coverage", cov)
        self.assertIn("base_period", cov)

        self.assertIn("release_metadata", target)
        rel = target["release_metadata"]
        self.assertIn("frequency", rel)
        self.assertIn("reference_months", rel)
        self.assertIn("publication_lag_days", rel)

        self.assertIn("verification_evidence", target)
        ev = target["verification_evidence"]
        self.assertEqual(ev["status"], "VERIFIED_OFFICIAL_RESPONSE")
        fixture_path = Path(ev["fixture_path"])
        self.assertTrue(fixture_path.exists(), f"Fixture file {fixture_path} must exist on disk")

        # Verify fixture SHA256 integrity
        import hashlib
        with open(fixture_path, "rb") as f:
            actual_sha256 = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_sha256, ev["fixture_sha256"])
