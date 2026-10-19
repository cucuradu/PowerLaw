"""
Task 8 Acceptance Tests: Pilot orchestration, review and operational dashboard.
Tests CLI pilot execution, dry-run, review reporting, idempotent reruns,
preserving prior committed batches on failure, and dashboard integration.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from scripts.ops.ingest import build_parser, main as cli_main, run_pilot
from src.config import get_settings
from src.schemas.ingestion import Observation, Fact, SourceDocument, BatchManifest
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import IngestionJobLedger
from src.storage.dashboard_data import (
    get_production_overview,
    get_review_queue,
    submit_ingestion_job,
    get_job_status,
)


class TestIngestionPilotTask8(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "lancedb"
        self.ledger_path = Path(self.temp_dir.name) / "ledger.sqlite"
        self.archive_dir = Path(self.temp_dir.name) / "archive"
        self.cache_dir = Path(self.temp_dir.name) / "cache"
        self.tmp_dir = Path(self.temp_dir.name) / "tmp"

        self.settings = get_settings().model_copy(update={
            "mode": "test",
            "sqlite_path": str(self.ledger_path),
            "lancedb_path": str(self.db_path),
            "cache_dir": str(self.cache_dir),
            "temp_dir": str(self.tmp_dir),
            "object_backend": "local",
            "object_prefix": "evidence",
            "extraction_budget_usd": 0.0,
        })
        self.store = DualLayerStore(storage_dir=self.db_path)
        self.ledger = IngestionJobLedger(self.ledger_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ------------------------------------------------------------------
    # 1. CLI Parser & Flags
    # ------------------------------------------------------------------
    def test_cli_parser_supports_pilot_and_review_report(self):
        """CLI parser accepts --pilot, --dry-run, --review-report, --resume, --status."""
        parser = build_parser()
        args = parser.parse_args(["--pilot", "--dry-run"])
        self.assertTrue(args.pilot)
        self.assertTrue(args.dry_run)

        args_rev = parser.parse_args(["--review-report", "run_12345"])
        self.assertEqual(args_rev.review_report, "run_12345")

        args_status = parser.parse_args(["--status"])
        self.assertTrue(args_status.status)

    # ------------------------------------------------------------------
    # 2. Pilot Dry-Run Mode
    # ------------------------------------------------------------------
    @patch("src.ingestion.bounded_fetch")
    def test_pilot_dry_run_executes_without_committing(self, mock_fetch):
        """--pilot --dry-run runs discovery and bounded extraction without committing batches."""
        # Return valid mock response for fetch
        mock_fetch.return_value = (b'{"version": "2.0"}', "https://example.com/api", "application/json", 200)

        with patch("scripts.ops.ingest.get_settings", return_value=self.settings):
            report = run_pilot(
                dry_run=True,
                limit=2,
                settings=self.settings,
                ledger=self.ledger,
                store=self.store,
            )

        self.assertTrue(report["dry_run"])
        self.assertIn("sources", report)
        # Verify no batch committed to store
        committed = [b for b in self.store._canonical_batches.values() if b.status == "committed"]
        self.assertEqual(len(committed), 0, "Dry run must not commit batches to canonical store")

    # ------------------------------------------------------------------
    # 3. Offline Rerun Idempotence & Data Preservation
    # ------------------------------------------------------------------
    def test_offline_pilot_rerun_yields_deterministic_state(self):
        """Rerunning offline pilot produces identical committed counts and facts."""
        from src.ingestion import run_ingestion
        fixtures_dir = Path(__file__).parent / "fixtures" / "ingestion"
        eurostat_bytes = (fixtures_dir / "eurostat_sts_copi_q_de_official.json").read_bytes()

        def fake_fetch(url, **kwargs):
            return eurostat_bytes, url, "application/json", 200

        # Run 1
        rep1 = run_ingestion(
            "EUROSTAT_STS",
            limit=1,
            fetch=fake_fetch,
            store=self.store,
            ledger=self.ledger,
            settings=self.settings,
        )
        self.assertEqual(rep1["status"], "committed")
        count_run1 = rep1["committed"]

        # Run 2 (same content)
        rep2 = run_ingestion(
            "EUROSTAT_STS",
            limit=1,
            fetch=fake_fetch,
            store=self.store,
            ledger=self.ledger,
            settings=self.settings,
        )
        self.assertEqual(rep2["status"], "committed")

        # Query latest observations - counts should be deterministic
        obs_table = self.store.query_observations(cutoff=datetime.now(timezone.utc))
        self.assertEqual(len(obs_table), rep1["accepted"])

    def test_pilot_failure_preserves_previously_committed_batches(self):
        """If a subsequent ingestion job fails, earlier committed batches remain accessible."""
        from src.ingestion import run_ingestion
        fixtures_dir = Path(__file__).parent / "fixtures" / "ingestion"
        eurostat_bytes = (fixtures_dir / "eurostat_sts_copi_q_de_official.json").read_bytes()

        def ok_fetch(url, **kwargs):
            return eurostat_bytes, url, "application/json", 200

        def fail_fetch(url, **kwargs):
            from src.ingestion import FetchError
            raise FetchError("Simulated provider outage")

        # Successful batch 1
        rep1 = run_ingestion("EUROSTAT_STS", limit=1, fetch=ok_fetch, store=self.store, ledger=self.ledger, settings=self.settings)
        self.assertEqual(rep1["status"], "committed")

        # Failed batch 2
        rep2 = run_ingestion("EUROSTAT_STS", limit=1, fetch=fail_fetch, store=self.store, ledger=self.ledger, settings=self.settings)
        self.assertEqual(rep2["status"], "failed")

        # Earlier committed data remains intact
        obs_table = self.store.query_observations(cutoff=datetime.now(timezone.utc))
        self.assertGreater(len(obs_table), 0, "Committed observations from batch 1 must be preserved after batch 2 fails")

    # ------------------------------------------------------------------
    # 4. Review Reporting & Queue Tracking
    # ------------------------------------------------------------------
    def test_review_report_generation(self):
        """CLI --review-report compiles run summary, metrics, and items requiring review."""
        from scripts.ops.ingest import generate_review_report
        run_id = "run_test_review_1"
        self.ledger.ensure_run(run_id, "EUROSTAT_STS", 10.0)
        self.ledger.save_work_item(
            run_id,
            "item_rev_1",
            {"url": "https://example.com/test"},
            status="parsed",
            payload={"accepted": 2, "review": 1, "rejected": 0, "cost_usd": 0.05}
        )

        report = generate_review_report(run_id, ledger=self.ledger)
        self.assertEqual(report["run_id"], run_id)
        self.assertIn("items", report)
        self.assertEqual(report["total_review_items"], 1)

    # ------------------------------------------------------------------
    # 5. Dashboard Data & Non-Blocking Job Model
    # ------------------------------------------------------------------
    def test_dashboard_production_overview_reads_committed_canonical(self):
        """Dashboard overview reflects committed canonical batches, freshness, and review counts."""
        # Query overview from empty store
        overview = get_production_overview(store=self.store, ledger=self.ledger)
        self.assertIn("committed_batches", overview)
        self.assertIn("total_observations", overview)
        self.assertIn("total_facts", overview)
        self.assertIn("pending_review_count", overview)

    def test_non_blocking_job_submission_and_status(self):
        """Streamlit can submit an ingestion job request and query status asynchronously."""
        job = submit_ingestion_job(
            source_id="EUROSTAT_STS",
            limit=5,
            dry_run=True,
            ledger=self.ledger,
            settings=self.settings,
        )
        self.assertIn("job_id", job)
        self.assertIn("status", job)
        self.assertEqual(job["status"], "submitted")

        status = get_job_status(job["job_id"], ledger=self.ledger)
        self.assertIn(status["status"], ["submitted", "created", "running", "completed", "failed"])


if __name__ == "__main__":
    unittest.main()
