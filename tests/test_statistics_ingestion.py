"""
Task 7 Acceptance Tests: Official construction/material statistics and measured context.
Tests Eurostat, Destatis, ECB, Bundesbank, SMARD statistics adapters,
multidimensional series keys, observation immutability, date bounds,
units/rebasing, and isolation from synthetic data.
"""

import os
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.schemas.ingestion import Observation, SeriesDefinition, SourceDocument
from src.storage.dual_layer_store import DualLayerStore
from src.scrapers.statistics import (
    normalize_series_response,
    fetch_series,
    parse_eurostat_jsonstat,
    parse_destatis_csv,
    parse_destatis_table_json,
    parse_sdmx_csv,
    parse_smard_csv,
)


class TestStatisticsIngestionTask7(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).parent / "fixtures" / "ingestion"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "lancedb"
        self.store = DualLayerStore(storage_dir=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ------------------------------------------------------------------
    # 1. Eurostat JSON-stat Parsing & Target Identity
    # ------------------------------------------------------------------
    def test_eurostat_jsonstat_parsing_extracts_correct_observations(self):
        """Eurostat JSON-stat multi-dimensional index flattens to exact observations."""
        fixture_path = self.fixtures_dir / "eurostat_sts_copi_q_de_official.json"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("EUROSTAT_STS", "sts_copi_q", content, fetched_at)
        self.assertGreater(len(observations), 0, "Must extract observations from Eurostat JSON-stat")

        # Check the primary producer price observation for Germany 2026-Q3
        prc_obs = [o for o in observations if "PRC_PRR" in o.series_id and o.unit == "2021=100"]
        self.assertEqual(len(prc_obs), 1, "Must find exactly one 2021=100 producer price index observation")
        obs = prc_obs[0]
        self.assertEqual(obs.value, 141.7)
        self.assertEqual(obs.period_start, "2026-07-01")
        self.assertEqual(obs.period_end_exclusive, "2026-10-01")
        self.assertEqual(obs.source_unit, "I21")
        self.assertEqual(obs.unit, "2021=100")
        self.assertFalse(obs.is_retracted)

    def test_producer_price_and_cost_series_never_share_identity(self):
        """Producer price (PRC_PRR) and Cost (COST) series must have distinct series_ids."""
        fixture_path = self.fixtures_dir / "eurostat_sts_copi_q_de_official.json"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("EUROSTAT_STS", "sts_copi_q", content, fetched_at)
        series_ids = {o.series_id for o in observations}
        self.assertGreaterEqual(len(series_ids), 2, "Must distinguish PRC_PRR and percentage change series")
        for sid in series_ids:
            self.assertFalse("PRC_PRR" in sid and "COST" in sid, "Producer price and cost must not collide")

    def test_missing_data_in_eurostat_recorded_as_none_with_reason(self):
        """Positions with no data in JSON-stat must produce value=None with missing_reason."""
        fixture_path = self.fixtures_dir / "eurostat_sts_copi_q_de_official.json"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("EUROSTAT_STS", "sts_copi_q", content, fetched_at)
        # In fixture, I15 (index 1) has no data
        none_obs = [o for o in observations if o.value is None]
        for o in none_obs:
            self.assertIsNotNone(o.missing_reason, "Missing observations must record missing_reason")
            self.assertNotEqual(o.value, 0.0, "Missing observation must not be fabricated as 0.0")

    # ------------------------------------------------------------------
    # 2. Destatis GENESIS Parsing (CSV and Table JSON)
    # ------------------------------------------------------------------
    def test_destatis_table_json_parsing(self):
        """Destatis GENESIS table JSON parses quarterly residential PPI correctly."""
        fixture_path = self.fixtures_dir / "destatis_61261_0002_table_official.json"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("DESTATIS_GENESIS", "61261-0002", content, fetched_at)
        self.assertGreater(len(observations), 0)

        # Look for primary target: residential new building, Bauleistungen am Bauwerk, einschl. USt
        res_obs = [o for o in observations if "BPNBK" in o.series_id and "BAULEISTBW" in o.series_id and "STEMW1=STEMW1" in o.series_id]
        self.assertEqual(len(res_obs), 4, "Must extract 4 quarters for 2025")
        by_period = {o.period_start: o.value for o in res_obs}
        self.assertEqual(by_period.get("2025-01-01"), 132.6)  # Q1 (Feb)
        self.assertEqual(by_period.get("2025-04-01"), 133.6)  # Q2 (Mai)
        self.assertEqual(by_period.get("2025-07-01"), 134.3)  # Q3 (Aug)
        self.assertEqual(by_period.get("2025-10-01"), 135.0)  # Q4 (Nov)

    def test_destatis_flat_csv_parsing(self):
        """Destatis flat semicolon CSV parses into valid Observation records."""
        fixture_path = self.fixtures_dir / "destatis_61261_0002_2025_official.csv"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("DESTATIS_GENESIS", "61261-0002", content, fetched_at)
        self.assertGreater(len(observations), 100)

        # Check missing value '-' handled properly
        confidential = [o for o in observations if o.original_value_text == "-"]
        self.assertGreater(len(confidential), 0)
        for o in confidential:
            self.assertIsNone(o.value)
            self.assertEqual(o.missing_reason, "CONFIDENTIAL")

    def test_destatis_material_ppi_monthly_series(self):
        """Destatis 61241-0004 material PPI parses monthly periods correctly."""
        fixture_path = self.fixtures_dir / "destatis_61241_0004_2025_official.csv"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("DESTATIS_GENESIS", "61241-0004", content, fetched_at)
        self.assertGreater(len(observations), 0)

        # Check that periods are monthly: e.g. 2025-01-01 to 2025-02-01
        for o in observations[:10]:
            self.assertTrue(o.period_start.endswith("-01"))
            self.assertTrue(o.period_end_exclusive.endswith("-01"))

    # ------------------------------------------------------------------
    # 3. Financial & Energy Context (ECB, Bundesbank, SMARD)
    # ------------------------------------------------------------------
    def test_ecb_sdmx_parsing(self):
        """ECB SDMX CSV format parses into daily exchange rate observations."""
        fixture_path = self.fixtures_dir / "ecb_exr_sdmx_sample.csv"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("ECB_SDMX", "EXR.D.USD.EUR.SP00.A", content, fetched_at)
        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[0].value, 1.0850)
        self.assertEqual(observations[0].period_start, "2024-05-15")
        self.assertEqual(observations[0].period_end_exclusive, "2024-05-16")
        self.assertEqual(observations[0].unit, "USD_PER_EUR")

    def test_bundesbank_sdmx_parsing(self):
        """Bundesbank SDMX CSV format parses into monthly housing loan rate observations."""
        fixture_path = self.fixtures_dir / "bundesbank_housing_loan_sample.csv"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("BUNDESBANK_SDMX", "BBSIS.M.I.ZST.WZ.EUR.A.A.A.000000.G.C.A.A._Z._Z.A", content, fetched_at)
        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[1].value, 3.85)
        self.assertEqual(observations[1].period_start, "2024-05-01")
        self.assertEqual(observations[1].period_end_exclusive, "2024-06-01")
        self.assertEqual(observations[1].unit, "PERCENT")

    def test_smard_power_price_parsing(self):
        """SMARD electricity market CSV parses into hourly/daily observations."""
        fixture_path = self.fixtures_dir / "smard_de_lu_power_sample.csv"
        content = fixture_path.read_bytes()
        fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

        observations = normalize_series_response("SMARD_DE", "MARKET_DATA_DAY_AHEAD_DE_LU", content, fetched_at)
        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[0].value, 65.20)
        self.assertEqual(observations[0].unit, "EUR_PER_MWH")

    # ------------------------------------------------------------------
    # 4. Observation Immutability, Revisions, and Temporal Cutoff
    # ------------------------------------------------------------------
    def test_observation_revisions_preserve_earlier_vintages(self):
        """A new revision for the same observation does not mutate the earlier revision."""
        from src.schemas.ingestion import BatchManifest, SourceDocument, EvidenceLink
        batch_1 = BatchManifest(batch_id="batch_v1", source_id="DESTATIS_GENESIS", manifest_uri="s3://b/m1", archive_sha256="h1")
        doc_1 = SourceDocument.from_content(
            source_id="DESTATIS_GENESIS",
            canonical_url="https://genesis.destatis.de/test/v1",
            publisher="Destatis",
            content_bytes=b"v1 data",
            batch_id="batch_v1",
            available_at=datetime(2025, 5, 1, tzinfo=timezone.utc),
        )
        obs_v1 = Observation(
            observation_id="obs_de_ppi_2025q1",
            revision_id="rev_1",
            series_id="DE_CONSTRUCTION_PPI_RESIDENTIAL_NEW_Q",
            period_start="2025-01-01",
            period_end_exclusive="2025-04-01",
            value=132.0,
            unit="2021=100",
            vintage_id="vintage_2025_05",
            available_at=datetime(2025, 5, 1, tzinfo=timezone.utc),
            batch_id="batch_v1",
        )
        link_1 = EvidenceLink(
            assertion_version_id=obs_v1.revision_id,
            document_version_id=doc_1.version_id,
            quoted_text="132.0",
            available_at=datetime(2025, 5, 1, tzinfo=timezone.utc),
            batch_id="batch_v1",
        )
        s_def = SeriesDefinition(
            series_id="DE_CONSTRUCTION_PPI_RESIDENTIAL_NEW_Q",
            source_id="DESTATIS_GENESIS",
            dataset_id="61261-0002",
            provider_series_key="61261-0002",
            metric_name="PPI",
            unit="2021=100",
            batch_id="batch_v1",
        )
        self.store.stage_batch(batch_1, [doc_1], [], [obs_v1], series=[s_def], evidence_links=[link_1])
        self.store.commit_batch("batch_v1")

        # Now revision v2 comes in
        batch_2 = BatchManifest(batch_id="batch_v2", source_id="DESTATIS_GENESIS", manifest_uri="s3://b/m2", archive_sha256="h2")
        doc_2 = SourceDocument.from_content(
            source_id="DESTATIS_GENESIS",
            canonical_url="https://genesis.destatis.de/test/v2",
            publisher="Destatis",
            content_bytes=b"v2 data",
            batch_id="batch_v2",
            available_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
        )
        obs_v2 = Observation(
            observation_id="obs_de_ppi_2025q1",
            revision_id="rev_2",
            series_id="DE_CONSTRUCTION_PPI_RESIDENTIAL_NEW_Q",
            period_start="2025-01-01",
            period_end_exclusive="2025-04-01",
            value=132.6,
            unit="2021=100",
            vintage_id="vintage_2025_08",
            supersedes_revision_id="rev_1",
            available_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
            batch_id="batch_v2",
        )
        link_2 = EvidenceLink(
            assertion_version_id=obs_v2.revision_id,
            document_version_id=doc_2.version_id,
            quoted_text="132.6",
            available_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
            batch_id="batch_v2",
        )
        self.store.stage_batch(batch_2, [doc_2], [], [obs_v2], evidence_links=[link_2])
        self.store.commit_batch("batch_v2")

        # As of 2025-06-01: value should be 132.0
        q_june = self.store.query_observations(cutoff=datetime(2025, 6, 1, tzinfo=timezone.utc))
        df_june = q_june.to_pandas()
        self.assertEqual(len(df_june), 1)
        self.assertEqual(df_june.iloc[0]["value"], 132.0)
        self.assertEqual(df_june.iloc[0]["revision_id"], "rev_1")

        # As of 2025-09-01: value should be revised 132.6
        q_sept = self.store.query_observations(cutoff=datetime(2025, 9, 1, tzinfo=timezone.utc))
        df_sept = q_sept.to_pandas()
        self.assertEqual(len(df_sept), 1)
        self.assertEqual(df_sept.iloc[0]["value"], 132.6)
        self.assertEqual(df_sept.iloc[0]["revision_id"], "rev_2")

    # ------------------------------------------------------------------
    # 5. Production Mode Isolation: No Yahoo or Synthetic Fallbacks
    # ------------------------------------------------------------------
    def test_production_mode_disallows_synthetic_or_prohibited_substitutions(self):
        """In production mode, financial proxies or synthetic series cannot substitute target."""
        from src.config import load_data_sources_registry
        reg = load_data_sources_registry()
        primary = reg["primary_target"]
        prohibited = primary.get("prohibited_substitutions", [])
        self.assertIn("CONSTRUCTION_INPUT_COST_INDEX", prohibited)
        self.assertIn("MATERIAL_PPI", prohibited)
        self.assertIn("HOUSE_PRICE_INDEX", prohibited)
        self.assertIn("EQUITY_STOCK_PRICE", prohibited)

    # ------------------------------------------------------------------
    # 6. Common Runner Integration
    # ------------------------------------------------------------------
    def test_common_runner_ingests_eurostat(self):
        """Common runner executes EUROSTAT_STS discover and parse into canonical store."""
        from src.ingestion import run_ingestion
        from src.storage.ingestion_jobs import IngestionJobLedger
        from types import SimpleNamespace

        eurostat_bytes = (self.fixtures_dir / "eurostat_sts_copi_q_de_official.json").read_bytes()
        def fake_fetch(url, **kwargs):
            return eurostat_bytes, url, "application/json", 200

        ledger_path = Path(self.temp_dir.name) / "ledger.sqlite"
        ledger = IngestionJobLedger(ledger_path)
        from src.config import get_settings
        settings = get_settings().model_copy(update={
            "mode": "test",
            "sqlite_path": str(ledger_path),
            "lancedb_path": str(self.db_path),
            "cache_dir": str(Path(self.temp_dir.name) / "cache"),
            "temp_dir": str(Path(self.temp_dir.name) / "tmp"),
            "object_backend": "local",
            "object_prefix": "evidence",
            "extraction_budget_usd": 0.0,
        })

        report = run_ingestion(
            "EUROSTAT_STS",
            limit=1,
            fetch=fake_fetch,
            store=self.store,
            ledger=ledger,
            settings=settings,
        )
        self.assertEqual(report["status"], "committed")
        self.assertGreater(report["accepted"], 0)
        self.assertGreater(report["committed"], 0)

        # Query committed observations from store
        table = self.store.query_observations(cutoff=datetime.now(timezone.utc))
        self.assertGreater(len(table), 0)


if __name__ == "__main__":
    unittest.main()
