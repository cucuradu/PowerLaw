"""
Tests for centralized ingestion configuration, DB resolution, and production origin isolation (Task 1).
"""

import os
import json
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
                laws = dd.get_all_laws_data(storage_dir=str(prod_db))
                self.assertEqual(len(laws), 0, "Production structured_rules table must not inject benchmark seed rules")

    def test_demo_mode_does_not_inject_sample_data(self):
        """Changing execution mode must not generate sample records."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            demo_db = Path(tmp_dir) / "demo_db"
            demo_db.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"POWERLAW_MODE": "demo", "LANCEDB_PATH": str(demo_db)}):
                import src.config
                if hasattr(src.config, "clear_settings_cache"):
                    src.config.clear_settings_cache()

                import src.storage.dashboard_data as dd
                store = dd.get_graph_store(storage_dir=str(demo_db))
                self.assertEqual(store.get_nodes(), [])
                self.assertEqual(store.get_edges(), [])

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
        verified_enabled = {
            "DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "OFFICIAL_NEWS",
            "BUNDESBANK_SDMX", "BUNDESBANK_RTD", "EURLEX_CELLAR",
        }
        for ck in core_keys:
            self.assertIn(ck, sources, f"Core source {ck} must be present in register")
            meta = sources[ck]
            self.assertEqual(meta.get("access_fee"), 0)
            self.assertFalse(meta.get("requires_paid_subscription"))
            self.assertTrue(len(meta.get("rights", "")) > 0)
            self.assertTrue(len(meta.get("attribution", "")) > 0)
            self.assertEqual(meta.get("enabled"), ck in verified_enabled)
            if ck in verified_enabled:
                self.assertTrue(meta.get("rights_checked"), f"Enabled source {ck} must be rights-checked")

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
            lancedb_dir = prod_dir / "lancedb"

            # Write a probe with mixed demo and loose records into uncommitted raw news cache
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
            ]
            import json
            with open(raw_news_dir / "scraped_news.json", "w", encoding="utf-8") as f:
                json.dump(probe_news, f)

            # Stage verified canonical production news into the store
            from datetime import datetime, timezone
            from src.storage.dual_layer_store import DualLayerStore
            from src.schemas.ingestion import Fact, Entity, BatchManifest, SourceDocument, EvidenceLink
            store = DualLayerStore(lancedb_dir)
            doc = SourceDocument.from_content("SRC", "https://example.com/doc", "Publisher", b"content", "b_news",
                                             available_at=datetime(2024, 1, 1, tzinfo=timezone.utc), data_origin="live")
            fact = Fact(subject_id="EVENT_prod", predicate="ABOUT", object_id="sector:steel", source_id="SRC",
                        available_at=datetime(2024, 1, 1, tzinfo=timezone.utc), recorded_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                        batch_id="b_news", data_origin="live",
                        payload_json=json.dumps({"kind": "action", "article_id": "NEWS_PROD_01", "event_id": "EVENT_prod",
                            "evidence_text": "Official Production Event", "action": {"action_type": "FACILITY_SHUTDOWN",
                            "actor": "Plant", "target": "sector:steel", "description": "Official Production Event", "magnitude": None}}))
            entities = [Entity(entity_id=k, entity_type=t, canonical_label=k, created_at=datetime(2024, 1, 1, tzinfo=timezone.utc), batch_id="b_news", data_origin="live")
                        for k, t in [("EVENT_prod", "ACTION"), ("sector:steel", "SECTOR")]]
            ev = EvidenceLink(assertion_version_id=fact.fact_version_id, document_version_id=doc.version_id,
                              quoted_text="Official Production Event", available_at=fact.available_at, batch_id="b_news", data_origin="live")
            b_news = BatchManifest(batch_id="b_news", source_id="SRC", data_origin="live")
            store.stage_batch(b_news, documents=[doc], facts=[fact], observations=[], entities=entities, evidence_links=[ev])
            store.commit_batch("b_news")

            with patch.dict(os.environ, {
                "POWERLAW_MODE": "production",
                "POWERLAW_DATA_DIR": str(prod_dir),
                "LANCEDB_PATH": str(lancedb_dir)
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
        self.assertEqual(ev["fixture_type"], "OFFICIAL_RESPONSE_FIELDS")
        self.assertEqual(ev["credential_method_used"], "API_TOKEN")
        self.assertIsNotNone(ev["verified_at"])
        self.assertIsNone(rel["next_scheduled_release"])
        self.assertFalse(ev["historical_release_vintages_verified"])

        fixture_path = Path(ev["fixture_path"])
        self.assertTrue(fixture_path.exists(), f"Fixture file {fixture_path} must exist on disk")

        # Inspect the provider's real response rather than a synthetic data envelope.
        with open(fixture_path, "r", encoding="utf-8") as f:
            fixture_data = json.load(f)
        self.assertEqual(fixture_data["Status"]["Code"], 0)
        content = fixture_data["Object"]["Content"]
        self.assertIn("Tabelle: 61261-0002", content)
        self.assertIn("2021=100", content)
        self.assertIn("2025;Wohngebäude;Bauleistungen am Bauwerk;132,6;132,6;133,6", content)
        self.assertNotIn("Parameter", fixture_data)

        # Verify fixture SHA256 integrity (verifies fixture file integrity without treating checksum as official proof)
        import hashlib
        with open(fixture_path, "rb") as f:
            actual_sha256 = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_sha256, ev["fixture_sha256"])

    def test_audit_provenance_classification_zero_partial_complete_mixed(self):
        """
        _classify_dataset and _validate_raw_references must accurately classify
        zero, partial, complete, and mixed provenance without falsely marking mixed datasets as verified.
        """
        from scripts.ops.migrate_data import _validate_raw_references, _classify_dataset
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sample_file = tmp_path / "raw_doc.pdf"
            sample_file.write_bytes(b"%PDF-1.4 sample content")

            # 1. Zero provenance: URLs only (http://...), no archived raw file
            df_zero = pd.DataFrame([
                {"id": 1, "data_origin": "production", "source_url": "https://example.com/law1"},
                {"id": 2, "data_origin": "production", "source_url": "https://example.com/law2"},
            ])
            ref_zero = _validate_raw_references(df_zero)
            self.assertEqual(ref_zero["archived_provenance_count"], 0)
            self.assertEqual(ref_zero["source_url_only_count"], 2)
            self.assertEqual(ref_zero["coverage_rate"], 0.0)
            class_zero = _classify_dataset(list(df_zero.columns), {"production": 2}, ref_zero)
            self.assertEqual(class_zero, "quarantined_legacy")

            # 2. Partial provenance: 1 out of 10 rows has valid raw document reference
            rows_partial = [{"id": i, "data_origin": "production", "source_url": "https://example.com"} for i in range(10)]
            rows_partial[0]["raw_file"] = str(sample_file)
            df_partial = pd.DataFrame(rows_partial)
            ref_partial = _validate_raw_references(df_partial)
            self.assertEqual(ref_partial["valid_count"], 1)
            self.assertEqual(ref_partial["total_records"], 10)
            self.assertEqual(ref_partial["coverage_rate"], 0.1)
            class_partial = _classify_dataset(list(df_partial.columns), {"production": 10}, ref_partial)
            # MUST NOT be labeled "verified"!
            self.assertEqual(class_partial, "partially_verified")

            # 3. Complete provenance: 100% of rows have verified archived raw document references
            rows_complete = [{"id": i, "data_origin": "production", "raw_file": str(sample_file)} for i in range(5)]
            df_complete = pd.DataFrame(rows_complete)
            ref_complete = _validate_raw_references(df_complete)
            self.assertEqual(ref_complete["valid_count"], 5)
            self.assertEqual(ref_complete["coverage_rate"], 1.0)
            class_complete = _classify_dataset(list(df_complete.columns), {"production": 5}, ref_complete)
            self.assertEqual(class_complete, "verified")

            # 4. Mixed origins: production + demo rows in same dataset
            df_mixed = pd.DataFrame([
                {"id": 1, "data_origin": "production", "raw_file": str(sample_file)},
                {"id": 2, "data_origin": "demo", "raw_file": str(sample_file)},
            ])
            ref_mixed = _validate_raw_references(df_mixed)
            class_mixed = _classify_dataset(list(df_mixed.columns), {"production": 1, "demo": 1}, ref_mixed)
            self.assertEqual(class_mixed, "mixed_provenance")

    def test_audit_minio_inspection_strictly_read_only(self):
        """
        audit_sources MinIO inspection must be strictly read-only:
        no bucket creation, no object upload, and no deletion.
        """
        from scripts.ops.migrate_data import audit_sources
        from unittest.mock import MagicMock, patch

        # Create mock MinIO client
        mock_minio = MagicMock()
        mock_minio.bucket_exists.return_value = True
        fake_obj = MagicMock()
        fake_obj.size = 1234
        mock_minio.list_objects.return_value = [fake_obj]

        mock_client_wrapper = MagicMock()
        mock_client_wrapper.client = mock_minio

        with patch.dict(os.environ, {
            "POWERLAW_OBJECT_BACKEND": "s3",
            "MINIO_ENDPOINT": "localhost:9000",
            "POWERLAW_OBJECT_BUCKET": "test-evidence-bucket",
            "POWERLAW_OBJECT_PREFIX": "test_prefix"
        }):
            import src.config
            if hasattr(src.config, "clear_settings_cache"):
                src.config.clear_settings_cache()

            with patch("src.utils.minio_client.MinioClient", return_value=mock_client_wrapper):
                report = audit_sources([], inspect_minio=True)

                # Verify connection report
                minio_rep = report["minio_inspection"]
                self.assertEqual(minio_rep["status"], "verified_connected")
                self.assertTrue(minio_rep["accessible"])
                self.assertEqual(minio_rep["objects_count"], 1)
                self.assertEqual(minio_rep["total_bytes"], 1234)

                # Strict assert: NO bucket creation, upload, or deletion occurred
                mock_minio.make_bucket.assert_not_called()
                mock_minio.put_object.assert_not_called()
                mock_minio.fput_object.assert_not_called()
                mock_minio.remove_object.assert_not_called()

        # Test missing bucket status
        mock_minio.bucket_exists.return_value = False
        with patch.dict(os.environ, {
            "POWERLAW_OBJECT_BACKEND": "s3",
            "MINIO_ENDPOINT": "localhost:9000",
            "POWERLAW_OBJECT_BUCKET": "nonexistent-bucket"
        }):
            import src.config
            if hasattr(src.config, "clear_settings_cache"):
                src.config.clear_settings_cache()

            with patch("src.utils.minio_client.MinioClient", return_value=mock_client_wrapper):
                report = audit_sources([], inspect_minio=True)
                self.assertEqual(report["minio_inspection"]["status"], "missing_bucket")
                self.assertFalse(report["minio_inspection"]["accessible"])
                mock_minio.make_bucket.assert_not_called()

    def test_archive_provenance_nonexistent_remote_object(self):
        """Production row with raw_uri='s3://nonexistent-bucket/missing-object' must NOT become verified."""
        from scripts.ops.migrate_data import _validate_raw_references, _classify_dataset
        df = pd.DataFrame([{
            "id": 1,
            "data_origin": "production",
            "raw_uri": "s3://nonexistent-bucket/missing-object"
        }])
        ref_info = _validate_raw_references(df)
        self.assertEqual(ref_info["archived_provenance_count"], 0)
        self.assertEqual(ref_info["valid_count"], 0)
        self.assertEqual(ref_info["coverage_rate"], 0.0)
        self.assertEqual(ref_info["unverified_count"], 1)

        classification = _classify_dataset(list(df.columns), {"production": 1}, ref_info)
        self.assertEqual(classification, "quarantined_legacy")

    def test_archive_provenance_unresolved_document_id(self):
        """Production row with document_id='doc_' + 64 chars must NOT become verified without resolving retained document."""
        from scripts.ops.migrate_data import _validate_raw_references, _classify_dataset
        fake_doc_id = "doc_" + "a" * 64
        df = pd.DataFrame([{
            "id": 1,
            "data_origin": "production",
            "document_id": fake_doc_id
        }])
        ref_info = _validate_raw_references(df)
        self.assertEqual(ref_info["archived_provenance_count"], 0)
        self.assertEqual(ref_info["valid_count"], 0)
        self.assertEqual(ref_info["coverage_rate"], 0.0)
        self.assertEqual(ref_info["unverified_count"], 1)

        classification = _classify_dataset(list(df.columns), {"production": 1}, ref_info)
        self.assertEqual(classification, "quarantined_legacy")

    def test_archive_provenance_checksum_mismatch(self):
        """Local file exists but checksum mismatches row expected hash: must report unverified."""
        from scripts.ops.migrate_data import _validate_raw_references, _classify_dataset
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            f = Path(tmp_dir) / "evidence.pdf"
            f.write_bytes(b"%PDF-1.4 Real Content")
            df = pd.DataFrame([{
                "id": 1,
                "data_origin": "production",
                "raw_file": str(f),
                "content_sha256": "0000000000000000000000000000000000000000000000000000000000000000"  # Wrong hash
            }])
            ref_info = _validate_raw_references(df)
            self.assertEqual(ref_info["valid_count"], 0)
            self.assertEqual(ref_info["coverage_rate"], 0.0)
            classification = _classify_dataset(list(df.columns), {"production": 1}, ref_info)
            self.assertEqual(classification, "quarantined_legacy")

    def test_archive_provenance_unavailable_remote_storage(self):
        """When MinIO client is unavailable or unconfigured, remote S3 URIs must report unverified."""
        from scripts.ops.migrate_data import _validate_raw_references, _classify_dataset
        df = pd.DataFrame([{
            "id": 1,
            "data_origin": "production",
            "raw_uri": "s3://some-bucket/document.pdf"
        }])
        ref_info = _validate_raw_references(df, minio_client=None)
        self.assertEqual(ref_info["valid_count"], 0)
        self.assertEqual(ref_info["coverage_rate"], 0.0)
        classification = _classify_dataset(list(df.columns), {"production": 1}, ref_info)
        self.assertEqual(classification, "quarantined_legacy")

    def test_archive_provenance_valid_retained_evidence_local_and_remote(self):
        """Test valid retained evidence: matching local file checksum and valid read-only remote object stat."""
        from scripts.ops.migrate_data import _validate_raw_references, _classify_dataset
        from unittest.mock import MagicMock
        import hashlib
        import tempfile

        # 1. Valid local file with matching checksum
        with tempfile.TemporaryDirectory() as tmp_dir:
            f = Path(tmp_dir) / "doc.pdf"
            content = b"%PDF-1.4 Verified Content"
            f.write_bytes(content)
            expected_hash = hashlib.sha256(content).hexdigest()

            df_local = pd.DataFrame([{
                "id": 1,
                "data_origin": "production",
                "raw_file": str(f),
                "content_sha256": expected_hash
            }])
            ref_local = _validate_raw_references(df_local)
            self.assertEqual(ref_local["valid_count"], 1)
            self.assertEqual(ref_local["coverage_rate"], 1.0)
            self.assertEqual(_classify_dataset(list(df_local.columns), {"production": 1}, ref_local), "verified")

            # 2. Valid resolved document_id in archive manifest pointing to local file
            doc_id = "doc_" + expected_hash
            manifest = {doc_id: {"file_path": str(f)}}
            df_doc = pd.DataFrame([{
                "id": 1,
                "data_origin": "production",
                "document_id": doc_id
            }])
            ref_doc = _validate_raw_references(df_doc, archive_manifest=manifest)
            self.assertEqual(ref_doc["valid_count"], 1)
            self.assertEqual(ref_doc["coverage_rate"], 1.0)
            self.assertEqual(_classify_dataset(list(df_doc.columns), {"production": 1}, ref_doc), "verified")

        # 3. Valid remote object via strictly read-only MinIO mock
        mock_minio = MagicMock()
        mock_minio.bucket_exists.return_value = True
        fake_stat = MagicMock()
        fake_stat.etag = "abc123etag"
        fake_stat.metadata = {}
        fake_stat.is_mismatch = False
        mock_minio.stat_object.return_value = fake_stat

        mock_wrapper = MagicMock()
        mock_wrapper.client = mock_minio

        df_remote = pd.DataFrame([{
            "id": 1,
            "data_origin": "production",
            "raw_uri": "s3://valid-bucket/verified-doc.pdf"
        }])
        ref_remote = _validate_raw_references(df_remote, minio_client=mock_wrapper)
        self.assertEqual(ref_remote["valid_count"], 1)
        self.assertEqual(ref_remote["coverage_rate"], 1.0)
        self.assertEqual(_classify_dataset(list(df_remote.columns), {"production": 1}, ref_remote), "verified")

        # Verify read-only: no write operations
        mock_minio.make_bucket.assert_not_called()
        mock_minio.put_object.assert_not_called()
        mock_minio.remove_object.assert_not_called()

    def test_archive_provenance_mixed_dataset_counts_accurate(self):
        """Mixed and partial datasets must retain accurate verified/quarantined counts without falsely verifying invalid rows."""
        from scripts.ops.migrate_data import _validate_raw_references, audit_sources
        import tempfile
        import hashlib

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            valid_file = tmp_path / "valid.pdf"
            content = b"%PDF-1.4 Valid Retained Document"
            valid_file.write_bytes(content)
            valid_hash = hashlib.sha256(content).hexdigest()

            # Parquet file with:
            # Row 0: production, valid file + hash -> verified
            # Row 1: production, nonexistent s3 object -> unverified/quarantined
            # Row 2: production, unresolved doc_ id -> unverified/quarantined
            # Row 3: demo, valid file -> quarantined (demo origin)
            df = pd.DataFrame([
                {"id": 1, "data_origin": "production", "raw_file": str(valid_file), "content_sha256": valid_hash},
                {"id": 2, "data_origin": "production", "raw_uri": "s3://nonexistent-bucket/missing-object"},
                {"id": 3, "data_origin": "production", "document_id": "doc_" + "a" * 64},
                {"id": 4, "data_origin": "demo", "raw_file": str(valid_file), "content_sha256": valid_hash},
            ])
            pq_path = tmp_path / "mixed.parquet"
            df.to_parquet(pq_path)

            report = audit_sources([tmp_path], inspect_minio=False)
            file_report = report["paths_inspected"][0]["parquet_files"]["mixed.parquet"]

            self.assertEqual(file_report["rows"], 4)
            # Exactly 1 row has verified production provenance
            self.assertEqual(file_report["verified_count"], 1)
            # The other 3 rows (unresolved S3, unresolved doc_id, demo origin) must be quarantined
            self.assertEqual(file_report["quarantined_count"], 3)
            self.assertEqual(file_report["classification"], "mixed_provenance")

    def test_migrate_data_row_level_provenance_isolation(self):
        """migrate_data must evaluate provenance at row level; an invalid row must never be verified even if another row in the same file is valid."""
        from scripts.ops.migrate_data import migrate_data
        import tempfile
        import hashlib

        with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as tgt_dir:
            s_path = Path(src_dir)
            valid_file = s_path / "retained.pdf"
            content = b"%PDF-1.4 Evidence"
            valid_file.write_bytes(content)
            valid_hash = hashlib.sha256(content).hexdigest()

            # nodes.parquet with 1 valid live node, 1 nonexistent S3 live node, 1 unresolved doc_id live node, 1 demo node
            nodes_df = pd.DataFrame([
                {"id": "node_valid", "label": "Valid Node", "data_origin": "live", "raw_file": str(valid_file), "content_sha256": valid_hash},
                {"id": "node_s3_missing", "label": "Missing S3", "data_origin": "live", "raw_uri": "s3://nonexistent-bucket/missing-object"},
                {"id": "node_doc_unresolved", "label": "Unresolved Doc", "data_origin": "live", "document_id": "doc_" + "b" * 64},
                {"id": "node_demo", "label": "Demo Node", "data_origin": "demo", "raw_file": str(valid_file), "content_sha256": valid_hash},
            ])
            nodes_df.to_parquet(s_path / "nodes.parquet")

            report = migrate_data(source_dirs=[s_path], target_dir=tgt_dir, dry_run=True)
            self.assertEqual(report["entities_mapped"], 4)
            # Exactly 1 row must be verified
            self.assertEqual(report["verified_records"], 1)
            # 3 rows must be quarantined
            self.assertEqual(report["quarantined_records"], 3)
