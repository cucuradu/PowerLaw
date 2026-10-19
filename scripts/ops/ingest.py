"""CLI entry point for bounded recoverable ingestion, pilot orchestration, and operational review."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.config import get_settings
from src.ingestion import _safe_error, resume_batch, run_ingestion
from src.storage.ingestion_jobs import IngestionJobLedger
from src.storage.dual_layer_store import DualLayerStore
from src.storage.dashboard_data import get_production_overview
from src.utils.api_wrapper import check_connections
from src.utils.minio_client import enforce_cache_budget, verify_restore


VERIFIED_PILOT_SOURCES = [
    "EURLEX_CELLAR",
    "OFFICIAL_NEWS",
    "DESTATIS_GENESIS",
    "EUROSTAT_STS",
    "ECB_SDMX",
    "BUNDESBANK_SDMX",
    "SMARD_DE",
    "GISCO_GEOGRAPHY",
]

PILOT_STATISTICAL_SOURCES = {"DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "BUNDESBANK_SDMX", "SMARD_DE"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one bounded PowerLaw ingestion job or pilot orchestration")
    parser.add_argument("--source", help="registered source identifier")
    parser.add_argument("--pilot", action="store_true", help="execute all verified pilot sources within spec caps")
    parser.add_argument("--since", help="start date filter ISO YYYY-MM-DD")
    parser.add_argument("--until", help="end date filter ISO YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=100, help="max items per source")
    parser.add_argument("--dry-run", action="store_true", help="run discovery and parse without committing")
    parser.add_argument("--resume", metavar="BATCH_ID", help="resume and commit a staged batch from manifest")
    parser.add_argument("--review-report", metavar="RUN_ID", help="generate review report for an ingestion run")
    parser.add_argument("--status", action="store_true", help="display overview of production canonical state and runs")
    parser.add_argument("--enforce-cache", action="store_true", help="enforce cache and temp storage budgets")
    parser.add_argument("--verify-restore", metavar="MANIFEST_URI", help="verify and restore batch from manifest URI")
    parser.add_argument("--restore-dir", type=Path, help="target directory for verified restore")
    parser.add_argument("--consume-queue", action="store_true", help="consume pending ingestion jobs from queue")
    parser.add_argument("--check-connections", action="store_true", help="check connectivity to external providers")
    parser.add_argument("--output", type=Path, help="write output JSON to file")
    return parser


def consume_queue(
    settings: Optional[Any] = None,
    max_jobs: int = 50,
    ledger: Optional[IngestionJobLedger] = None,
    store: Optional[DualLayerStore] = None,
) -> Dict[str, Any]:
    """Poll and consume pending jobs from SQLite ledger."""
    cfg = settings or get_settings()
    job_ledger = ledger or IngestionJobLedger(cfg.sqlite_path)
    canonical_store = store or DualLayerStore(cfg.lancedb_path)

    pending = job_ledger.pending_jobs(limit=max_jobs)
    processed = []
    failed = []

    for job in pending:
        source_id = job["source_id"]
        run_id = job["run_id"]
        params = job.get("parameters", {})
        limit = params.get("limit", 100)
        since = params.get("since")
        until = params.get("until")
        dry_run = params.get("dry_run", False)

        try:
            report = run_ingestion(
                source_id,
                run_id=run_id,
                limit=limit,
                since=since,
                until=until,
                dry_run=dry_run,
                settings=cfg,
                ledger=job_ledger,
                store=canonical_store,
            )
            if report.get("status") not in {"committed", "empty", "planned", "dry_run"}:
                job_ledger.update_job_status(job["job_id"], "failed")
                failed.append({"job_id": job["job_id"], "source_id": source_id, "run_id": run_id, "report": report})
                continue
            job_ledger.update_job_status(job["job_id"], "completed")
            processed.append({"job_id": job["job_id"], "source_id": source_id, "run_id": run_id, "status": report.get("status", "committed"), "report": report})
        except Exception as exc:
            job_ledger.update_job_status(job["job_id"], "failed")
            failed.append({"job_id": job["job_id"], "source_id": source_id, "run_id": run_id, "error": _safe_error(exc)})

    return {
        "status": "partial_failure" if failed else "completed",
        "processed_count": len(processed),
        "failed_count": len(failed),
        "processed": processed,
        "failed": failed,
    }


def run_pilot(
    dry_run: bool = False,
    limit: int = 10,
    settings: Optional[Any] = None,
    ledger: Optional[IngestionJobLedger] = None,
    store: Optional[DualLayerStore] = None,
) -> Dict[str, Any]:
    """Execute enabled supported pilot sources under spec caps and configured budget."""
    from src.config import load_data_sources_registry
    cfg = settings or get_settings()
    if type(limit) is not int or limit <= 0:
        raise ValueError("pilot limit must be a positive integer")
    job_ledger = ledger or IngestionJobLedger(cfg.sqlite_path)
    canonical_store = store or DualLayerStore(cfg.lancedb_path)

    pilot_id = f"pilot_{uuid.uuid4().hex[:12]}"
    source_reports = {}
    total_discovered = 0
    total_fetched = 0
    total_staged = 0
    total_committed = 0
    total_accepted = 0
    total_review = 0
    total_rejected = 0
    total_cost = 0.0

    total_budget = float(getattr(cfg, "extraction_budget_usd", 0.0))
    job_ledger.ensure_run(pilot_id, "PILOT_ORCHESTRATOR", total_budget)

    configured_sources = load_data_sources_registry().get("sources", {})
    for source_id in VERIFIED_PILOT_SOURCES:
        if not configured_sources.get(source_id, {}).get("enabled"):
            continue
        try:
            cap = 20 if source_id == "EURLEX_CELLAR" else 300 if source_id == "OFFICIAL_NEWS" else 8
            report = run_ingestion(
                source_id,
                limit=min(limit, cap),
                dry_run=dry_run,
                settings=cfg,
                ledger=job_ledger,
                store=canonical_store,
                budget_run_id=pilot_id,
                max_series=8 if source_id in PILOT_STATISTICAL_SOURCES else None,
            )
            source_reports[source_id] = report
            total_discovered += report.get("discovered", 0)
            total_fetched += report.get("fetched", 0)
            total_staged += report.get("staged", 0)
            total_committed += report.get("committed", 0)
            total_accepted += report.get("accepted", 0)
            total_review += report.get("review", 0)
            total_rejected += report.get("rejected", 0)
            total_cost = job_ledger.spent_cost(pilot_id) if total_budget > 0 else (total_cost + report.get("cost_usd", 0.0))
        except Exception as exc:
            total_cost = job_ledger.spent_cost(pilot_id) if total_budget > 0 else total_cost
            source_reports[source_id] = {
                "run_id": f"err_{source_id}",
                "status": "failed",
                "error_code": type(exc).__name__,
                "error": _safe_error(exc),
            }

    all_failed = all(r.get("status") == "failed" for r in source_reports.values()) if source_reports else False
    has_failed = any(r.get("status") in {"failed", "partial_failure"} for r in source_reports.values())
    if not source_reports:
        overall_status = "blocked"
    elif all_failed:
        overall_status = "failed"
    elif has_failed:
        overall_status = "partial_failure"
    elif dry_run:
        overall_status = "dry_run"
    elif all(r.get("status") in {"committed", "empty"} for r in source_reports.values()):
        overall_status = "committed"
    else:
        overall_status = "partial_failure"

    if total_budget > 0:
        total_cost = job_ledger.spent_cost(pilot_id)

    return {
        "pilot_id": pilot_id,
        "status": overall_status,
        "dry_run": dry_run,
        "sources": source_reports,
        "total_discovered": total_discovered,
        "total_fetched": total_fetched,
        "total_staged": total_staged,
        "total_committed": total_committed,
        "total_accepted": total_accepted,
        "total_review": total_review,
        "total_rejected": total_rejected,
        "total_cost_usd": total_cost,
        "expansion_allowed": False,
        "quality_gates": {
            "held_out_human_review": {"status": "pending", "required_legal_provisions": 30, "required_news_items": 100, "required_statistical_series": 20},
            "legal_accuracy": {"status": "unmeasured", "minimum": .95, "unsupported_critical_obligations_allowed": 0},
            "news_precision": {"status": "unmeasured", "minimum": .95},
            "discovery_recall": {"status": "unmeasured", "minimum": .90},
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def generate_review_report(
    run_id: str,
    ledger: Optional[IngestionJobLedger] = None,
    settings: Optional[Any] = None
) -> Dict[str, Any]:
    """Compile audit and quality review report from the ingestion ledger."""
    cfg = settings or get_settings()
    job_ledger = ledger or IngestionJobLedger(cfg.sqlite_path)

    items = job_ledger.work_items(run_id)
    review_items = []
    total_accepted = 0
    total_review = 0
    total_rejected = 0

    for it in items:
        raw_p = it.get("payload_json") or it.get("payload") or {}
        if isinstance(raw_p, str):
            try:
                payload = json.loads(raw_p)
            except Exception:
                payload = {}
        else:
            payload = raw_p or {}

        acc = payload.get("accepted", 0)
        rev = payload.get("review", 0)
        rej = payload.get("rejected", 0)
        total_accepted += acc
        total_review += rev
        total_rejected += rej
        if rev > 0 or it.get("status") == "needs_review":
            review_items.append({
                "item_key": it["item_key"],
                "status": it["status"],
                "cursor_value": it.get("cursor_value"),
                "review_count": rev,
                "payload": payload,
            })

    spent = job_ledger.spent_cost(run_id)
    runs = job_ledger.recent_runs(limit=100)
    matched_run = next((r for r in runs if r["run_id"] == run_id), {})

    return {
        "run_id": run_id,
        "source_id": matched_run.get("source_id", "unknown"),
        "status": matched_run.get("status", "unknown"),
        "total_items": len(items),
        "total_accepted": total_accepted,
        "total_review_items": len(review_items),
        "total_review_assertions": total_review,
        "total_rejected": total_rejected,
        "cost_usd": spent,
        "items": review_items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()

    if args.check_connections:
        print(json.dumps(check_connections(settings), indent=2, sort_keys=True))
        return 0

    if args.status:
        overview = get_production_overview()
        overview["ai_spending"] = IngestionJobLedger(settings.sqlite_path).spending_summary()
        print(json.dumps(overview, indent=2, sort_keys=True, default=str))
        return 0

    if args.enforce_cache:
        report = enforce_cache_budget(settings=settings)
        rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0

    if args.verify_restore:
        target_dir = args.restore_dir or (settings.data_dir / "restored")
        try:
            report = verify_restore(args.verify_restore, target_dir, settings=settings)
            rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")
            print(rendered)
            return 0
        except Exception as exc:
            report = {"status": "failed", "error_code": type(exc).__name__, "error": _safe_error(exc)}
            print(json.dumps(report, indent=2, sort_keys=True))
            return 1

    if args.review_report:
        report = generate_review_report(args.review_report, settings=settings)
        rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0

    if args.consume_queue:
        report = consume_queue(settings=settings)
        rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report.get("failed_count", 0) == 0 else 1

    if args.pilot:
        report = run_pilot(dry_run=args.dry_run, limit=args.limit, settings=settings)
        rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report.get("status") in {"committed", "dry_run"} else 1

    if bool(args.source) == bool(args.resume):
        raise SystemExit("exactly one of --source, --resume, --pilot, --consume-queue, --review-report, --status, --enforce-cache, or --verify-restore is required")

    try:
        report = (
            resume_batch(args.resume, settings=settings)
            if args.resume
            else run_ingestion(args.source, limit=args.limit, since=args.since, until=args.until, dry_run=args.dry_run, settings=settings)
        )
    except Exception as exc:
        report = {"status": "failed", "error_code": type(exc).__name__, "error": _safe_error(exc)}

    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("status") not in {"failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
