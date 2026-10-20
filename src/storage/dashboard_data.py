"""
Dashboard Data Provider & Persistence Service for PowerLaw.
Provides unified access to:
1. Dual-Layer Graph Store (Nodes, Edges, Tabular History)
2. LanceDB Structured Rules (EU Law chunks, IF-THEN logic gates, NACE mappings)
3. Real-time & Curated News Corpus (Receptor Signals, Actions, Reactions)
"""

import os
import hashlib
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Union
import pandas as pd
import numpy as np

from src.models.dual_layer_model import NodeType
from src.storage.graph_store import DualLayerGraphStore

from src.config import get_settings

logger = logging.getLogger(__name__)

def get_configured_db_path() -> Path:
    """Return canonical LanceDB path from centralized Settings."""
    return get_settings().lancedb_path

# Directory configuration
DEFAULT_DB_PATH = str(get_configured_db_path())
NEWS_DIR = Path("data/raw/news")


# ============================================================================
# DATA ACCESS & KPI INTERFACE
# ============================================================================

_CACHED_GRAPH_STORES: Dict[str, DualLayerGraphStore] = {}

def get_graph_store(storage_dir: Optional[str] = None) -> DualLayerGraphStore:
    """Retrieve or initialize the DualLayerGraphStore per storage_dir.
    Empty stores remain empty in every mode."""
    if storage_dir is None:
        storage_dir = str(get_configured_db_path())
    abs_dir = str(Path(storage_dir).resolve())
    if abs_dir in _CACHED_GRAPH_STORES:
        cached = _CACHED_GRAPH_STORES[abs_dir]
        if hasattr(cached, "_get_disk_generation"):
            disk_gen = cached._get_disk_generation()
            if disk_gen != getattr(cached, "_generation", 0):
                cached = DualLayerGraphStore(storage_dir=storage_dir)
                _CACHED_GRAPH_STORES[abs_dir] = cached
        return cached

    store = DualLayerGraphStore(storage_dir=storage_dir)

    _CACHED_GRAPH_STORES[abs_dir] = store
    return store


def _canonical_law_rows(store: Any, cutoff: datetime) -> List[Dict[str, Any]]:
    rows = []
    for fact in store.query_facts(cutoff):
        if fact.data_origin != "live" or fact.predicate != "MANDATES" or not fact.payload_json:
            continue
        rule = json.loads(fact.payload_json)
        metadata = rule.get("metadata", {})
        if not metadata:
            continue
        law = store._canonical_entities.get(fact.subject_id)
        documents = store.documents_for(fact, cutoff)
        rows.append({"law_id": metadata.get("regulation_id", fact.subject_id),
                     "title": law.canonical_label if law else fact.subject_id,
                     "chunk_id": fact.object_id, "rule_type": metadata.get("law_type"),
                     "jurisdiction": law.jurisdiction if law else None,
                     "valid_from": fact.valid_from, "valid_to": fact.valid_to,
                     "summary": rule.get("summary", ""), "recitals": rule.get("background_context", ""),
                     "source_url": documents[0].canonical_url if documents else None,
                     "rule": rule, "fact_version_id": fact.fact_version_id, "data_origin": fact.data_origin,
                     "evidence_document_ids": [doc.document_id for doc in documents]})
    return rows


def _canonical_news_rows(store: Any, cutoff: datetime) -> List[Dict[str, Any]]:
    articles = {}
    for fact in store.query_facts(cutoff):
        if fact.data_origin != "live" or not fact.payload_json:
            continue
        payload = json.loads(fact.payload_json)
        kind = payload.get("kind")
        if kind not in {"action", "reaction"} or not isinstance(payload.get(kind), dict):
            continue
        documents = store.documents_for(fact, cutoff)
        if not documents:
            continue
        doc = documents[0]
        key = payload.get("article_id") or doc.document_id
        text = payload.get("evidence_text") or ""
        row = articles.setdefault(key, {"news_id": key, "title": payload.get("title") or (text.splitlines()[0][:300] if text else doc.canonical_url),
                "summary": text, "source_name": doc.publisher, "source_url": payload.get("canonical_url") or doc.canonical_url,
                "published_date": doc.published_at.isoformat() if doc.published_at else None,
                "available_at": doc.available_at.isoformat() if doc.available_at else None,
                "actions": [], "reactions": [], "evidence_document_ids": [], "fact_version_ids": [],
                "data_origin": "live", "claim_status": "reported"})
        row[kind + "s"].append(payload[kind])
        row["fact_version_ids"].append(fact.fact_version_id)
        row["evidence_document_ids"] = sorted(set(row["evidence_document_ids"]) | {d.document_id for d in documents})
    return list(articles.values())


def get_all_laws_data(storage_dir: Optional[str] = None, *, cutoff: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Retrieve structured laws and rules.
    Production reads committed canonical facts; isolated tests can read their own projection.
    """
    settings = get_settings()
    if storage_dir is None:
        storage_dir = str(get_configured_db_path())
    if settings.mode == "production":
        store = get_graph_store(storage_dir).canonical_store
        return _canonical_law_rows(store, cutoff or datetime.now(timezone.utc))
    # 1. Try LanceDB if populated
    try:
        import lancedb
        db = lancedb.connect(storage_dir)
        tables = []
        try:
            res = db.list_tables()
            tables = res.tables if hasattr(res, "tables") else list(res)
        except Exception:
            tables = db.table_names()
        if "structured_rules" in tables:
            tbl = db.open_table("structured_rules")
            df = tbl.search().limit(1000).to_pandas()
            if not df.empty:
                committed_laws = set()
                if settings.mode == "production":
                    try:
                        from src.storage.dual_layer_store import DualLayerStore
                        store = DualLayerStore(storage_dir)
                        committed_batches = {k for k, b in store._canonical_batches.items() if b.status == "committed"}
                        for f in store._canonical_facts.values():
                            if f.batch_id in committed_batches and not f.is_retracted and f.quality_state == "accepted":
                                if f.subject_id.startswith("law_"):
                                    committed_laws.add(f.subject_id[4:])
                                    committed_laws.add(f.subject_id)
                        if not committed_laws:
                            return []
                    except Exception:
                        return []
                results = []
                for _, row in df.iterrows():
                    law_id = str(row.get("law_id", ""))
                    if settings.mode == "production":
                        origin = str(row.get("data_origin", "")).lower()
                        if origin not in ("live", "production") or law_id not in committed_laws:
                            continue
                    rule_json_raw = row.get("gnn_rule_json", "{}")
                    parsed_rule = json.loads(rule_json_raw) if isinstance(rule_json_raw, str) else rule_json_raw
                    results.append({
                        "law_id": str(row.get("law_id", "")),
                        "title": f"Regulation {row.get('law_id', '')}",
                        "chunk_id": str(row.get("chunk_id", "")),
                        "rule_type": str(row.get("rule_type", "REGULATORY")),
                        "jurisdiction": str(row.get("jurisdiction", "EU")),
                        "valid_from": str(row.get("valid_from", "")),
                        "valid_to": str(row.get("valid_to", "")),
                        "summary": str(row.get("summary", "")),
                        "recitals": parsed_rule.get("background_context", "") if isinstance(parsed_rule, dict) else "",
                        "source_url": str(row.get("source_url", "")),
                        "rule": parsed_rule
                    })
                return results
    except Exception as e:
        logger.debug(f"LanceDB direct lookup skipped: {e}")

    return []


def get_all_news_data(cutoff: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Retrieve news articles with extracted receptor signals, actions, and reactions."""
    settings = get_settings()
    if settings.mode == "production":
        store = get_graph_store(str(settings.lancedb_path)).canonical_store
        articles = _canonical_news_rows(store, cutoff or datetime.now(timezone.utc))
    else:
        articles = []

    # 1. Resolve mode-appropriate news file path
    prod_news_file = settings.data_dir / "raw" / "news" / "scraped_news.json"
    shared_news_file = NEWS_DIR / "scraped_news.json"

    news_file = prod_news_file if prod_news_file.exists() else shared_news_file
    if settings.mode != "production" and news_file.exists():
        try:
            with open(news_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    articles = loaded
        except Exception as e:
            logger.warning(f"Failed to read news from {news_file}: {e}")

    # 2. Strict Origin Enforcement
    if settings.mode == "production":
        # Disallow demo, synthetic, test, or unverified origin records
        filtered_articles = []
        for a in articles:
            if not isinstance(a, dict):
                continue
            origin = str(a.get("data_origin", a.get("origin", ""))).lower()
            if origin in ("production", "live"):
                filtered_articles.append(a)
        articles = filtered_articles

        # In production mode, if empty, return empty list (never fall back to demo seeds!)
        if not articles:
            return []
    elif settings.mode == "test":
        # In test mode, exclude quarantined legacy items
        articles = [a for a in articles if isinstance(a, dict) and a.get("data_origin") != "quarantined_legacy"]

    # Normalize fields for unified consumption
    normalized = []
    for a in articles:
        item = dict(a)
        item["title"] = a.get("title") or a.get("headline") or "Untitled Event"
        item["headline"] = item["title"]
        item["news_id"] = a.get("news_id") or a.get("event_id") or f"NEWS_{len(normalized)+1}"
        item["event_id"] = item["news_id"]
        item["published_date"] = a.get("published_date") or a.get("timestamp") or None
        item["timestamp"] = item["published_date"]
        
        # Ensure actions and reactions lists exist
        reactions = a.get("reactions", [])
        actions = a.get("actions", [])
        
        item["actions"] = actions
        item["reactions"] = reactions

        # Helper for float conversion
        def _to_float(v, fallback=None):
            if v is None:
                return fallback
            try:
                return float(v)
            except (ValueError, TypeError):
                return fallback

        # Flattened conveniences
        first_rx = reactions[0] if reactions else {}
        item["direction"] = a.get("direction") or first_rx.get("direction")
        
        raw_delta = a.get("delta_value")
        if raw_delta is None and first_rx:
            raw_delta = first_rx.get("magnitude")
        item["delta_value"] = _to_float(raw_delta, None) if raw_delta is not None else None

        item["delta_unit"] = a.get("delta_unit") or first_rx.get("unit")
        item["timeframe"] = a.get("timeframe") or first_rx.get("timeframe")
        item["target_asset"] = a.get("target_asset") or first_rx.get("affected_entity")
        item["metric"] = a.get("metric")
        item["receptor_signal"] = a.get("receptor_signal")

        normalized.append(item)

    return normalized


def trigger_news_scrape(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Trigger news scraping and ingest into DualLayerGraphStore."""
    from src.scrapers.news_scraper import EconomicNewsScraper
    store = get_graph_store()
    scraper = EconomicNewsScraper(data_dir=str(NEWS_DIR), graph_store=store)
    articles = scraper.scrape_all_feeds()
    return articles


def get_system_kpis(storage_dir: Optional[str] = None) -> Dict[str, Any]:
    """Compute executive KPIs for dashboard overview."""
    store = get_graph_store(storage_dir=storage_dir)
    nodes = store.get_nodes()
    edges = store.get_edges()
    history = store.get_history()

    # Node count breakdown
    type_counts = {}
    for n in nodes:
        t = n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)
        type_counts[t] = type_counts.get(t, 0) + 1

    # Power Law metrics
    metrics = store.compute_power_law_metrics()

    # Time series range
    history_df = store.get_history_df()
    min_date = history_df["timestamp"].min() if not history_df.empty and "timestamp" in history_df.columns else None
    max_date = history_df["timestamp"].max() if not history_df.empty and "timestamp" in history_df.columns else None

    return {
        "total_laws": type_counts.get(NodeType.LAW.value, 0),
        "total_actions": type_counts.get(NodeType.ACTION.value, 0),
        "total_reactions": type_counts.get(NodeType.REACTION.value, 0),
        "total_sectors": type_counts.get(NodeType.SECTOR.value, 0),
        "total_commodities": type_counts.get(NodeType.COMMODITY.value, 0) + type_counts.get(NodeType.ECONOMIC_INDEX.value, 0),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "total_temporal_entries": len(history),
        "gamma": metrics.get("gamma", 1.0),
        "density": metrics.get("density", 0.0),
        "min_date": str(min_date) if min_date is not None else None,
        "max_date": str(max_date) if max_date is not None else None,
        "top_in_degree": metrics.get("top_in_degree", []),
        "top_out_degree": metrics.get("top_out_degree", [])
    }


# ============================================================================
# CANONICAL PRODUCTION & REVIEW QUEUE ACCESS
# ============================================================================

def get_production_overview(
    store: Optional[Any] = None,
    ledger: Optional[Any] = None
) -> Dict[str, Any]:
    """Read committed canonical dataset metrics and review status directly from verified stores."""
    from src.storage.dual_layer_store import DualLayerStore
    from src.storage.ingestion_jobs import IngestionJobLedger

    settings = get_settings()
    canonical_store = store or DualLayerStore(settings.lancedb_path)
    job_ledger = ledger or IngestionJobLedger(settings.sqlite_path)

    if hasattr(canonical_store, "_ensure_observations_loaded"):
        canonical_store._ensure_observations_loaded()

    committed_batches = [b for b in getattr(canonical_store, "_canonical_batches", {}).values() if b.status == "committed"]
    committed_batch_ids = {b.batch_id for b in committed_batches}
    obs_count = sum(1 for o in getattr(canonical_store, "_canonical_observations", {}).values()
                    if o.batch_id in committed_batch_ids and o.quality_state == "accepted" and not o.is_retracted)
    facts_count = sum(1 for f in getattr(canonical_store, "_canonical_facts", {}).values()
                      if f.batch_id in committed_batch_ids and f.quality_state == "accepted" and not f.is_retracted)
    docs_count = sum(1 for d in getattr(canonical_store, "_canonical_documents", {}).values()
                     if d.batch_id in committed_batch_ids)
    evidence_count = sum(1 for e in getattr(canonical_store, "_canonical_evidence", {}).values()
                         if e.batch_id in committed_batch_ids)

    # Review items from ledger across all runs
    review_count = 0
    with job_ledger._connection() as db:
        rows = db.execute("SELECT status, payload_json FROM work_items").fetchall()
        for row in rows:
            raw_p = row["payload_json"] or "{}"
            payload = json.loads(raw_p) if isinstance(raw_p, str) else (raw_p or {})
            if payload.get("review", 0) > 0 or row["status"] == "needs_review":
                review_count += 1

    # Freshness
    latest_commit = None
    if committed_batches:
        dates = [b.committed_at for b in committed_batches if b.committed_at]
        if dates:
            latest_commit = max(dates).isoformat()

    source_breakdown = {}
    for b in committed_batches:
        s = b.source_id
        source_breakdown[s] = source_breakdown.get(s, 0) + 1

    last_success = job_ledger.last_success()
    oldest_pending = job_ledger.oldest_pending_job()

    return {
        "committed_batches": len(committed_batches),
        "total_observations": obs_count,
        "total_facts": facts_count,
        "total_documents": docs_count,
        "total_evidence_links": evidence_count,
        "pending_review_count": review_count,
        "freshness": latest_commit,
        "last_success": last_success,
        "oldest_pending_job": oldest_pending,
        "source_breakdown": source_breakdown,
        "mode": getattr(settings, "mode", "production"),
    }


def get_review_queue(ledger: Optional[Any] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve items requiring expert or legal review from the ingestion ledger."""
    from src.storage.ingestion_jobs import IngestionJobLedger
    job_ledger = ledger or IngestionJobLedger(get_settings().sqlite_path)

    review_items = []
    with job_ledger._connection() as db:
        rows = db.execute(
            "SELECT w.run_id, r.source_id, w.item_key, w.status, w.cursor_value, w.payload_json "
            "FROM work_items w JOIN run_context r ON w.run_id = r.run_id "
            "ORDER BY w.rowid DESC"
        ).fetchall()
        for row in rows:
            raw_p = row["payload_json"] or "{}"
            p = json.loads(raw_p) if isinstance(raw_p, str) else (raw_p or {})
            if p.get("review", 0) > 0 or row["status"] == "needs_review":
                review_items.append({
                    "run_id": row["run_id"],
                    "source_id": row["source_id"],
                    "item_key": row["item_key"],
                    "status": row["status"],
                    "cursor_value": row["cursor_value"],
                    "payload": p,
                })
                if len(review_items) >= limit:
                    break
    return review_items


def submit_ingestion_job(
    source_id: str,
    limit: int = 10,
    dry_run: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
    budget_usd: Optional[float] = None,
    ledger: Optional[Any] = None,
    settings: Optional[Any] = None
) -> Dict[str, Any]:
    """Submit an asynchronous/non-blocking ingestion job request to the ledger."""
    import uuid
    from src.storage.ingestion_jobs import IngestionJobLedger

    job_settings = settings or get_settings()
    job_ledger = ledger or IngestionJobLedger(job_settings.sqlite_path)

    run_id = f"job_{uuid.uuid4().hex[:12]}"
    effective_budget = budget_usd if budget_usd is not None else float(getattr(job_settings, "extraction_budget_usd", 0.0))
    job_ledger.ensure_run(run_id, source_id, effective_budget)
    params = {
        "limit": limit,
        "dry_run": dry_run,
        "since": since,
        "until": until,
        "budget_usd": effective_budget,
    }
    job_ledger.submit_job(source_id, run_id, params)

    return {
        "job_id": run_id,
        "source_id": source_id,
        "status": "submitted",
        "limit": limit,
        "dry_run": dry_run,
        "since": since,
        "until": until,
        "budget_usd": effective_budget,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def get_job_status(job_id: str, ledger: Optional[Any] = None) -> Dict[str, Any]:
    """Query execution status of a submitted ingestion job."""
    from src.storage.ingestion_jobs import IngestionJobLedger
    job_ledger = ledger or IngestionJobLedger(get_settings().sqlite_path)

    job = job_ledger.get_job(job_id)
    if job:
        return {
            "job_id": job["run_id"],
            "source_id": job["source_id"],
            "status": job["status"],
            "created_at": str(job["created_at"]),
            "parameters": job["parameters"],
        }
    return {
        "job_id": job_id,
        "status": "unknown",
    }


# ============================================================================
# CONSTRUCTION OUTLOOK WITH EVIDENCE & LIMITATIONS (TASK 11)
# ============================================================================

def load_latest_forecast(path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Read the forecast record written by `scripts.ops.backtest_target` (read-only; None when absent or unreadable)."""
    path = Path(path) if path else Path(get_settings().data_dir) / "reports" / "forecast-latest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prediction_record_from_forecast(forecast: Optional[Dict[str, Any]]) -> Optional[Any]:
    """PredictionRecord for the outlook; growth and interval stay in percent of the last released level."""
    from src.schemas.ingestion import PredictionRecord
    from src.scrapers.statistics import _period_bounds
    if not forecast or forecast.get("forecast_growth_pct") is None or not forecast.get("target_period_start"):
        return None
    start = forecast["target_period_start"]
    interval = forecast.get("interval_growth_pct") or [None, None]
    digest = hashlib.sha256(json.dumps(forecast, sort_keys=True).encode()).hexdigest()
    return PredictionRecord(
        prediction_id=f"pred_{digest}", dataset_id=str(forecast.get("backtest_report")), model_id=forecast["model"],
        target_series_id=forecast["target_series_id"], forecast_origin=forecast["generated_at"],
        target_period_start=start, target_period_end_exclusive=_period_bounds(f"{start[:4]}-Q{(int(start[5:7]) - 1) // 3 + 1}")[1],
        point_change_pct=forecast["forecast_growth_pct"], lower90=interval[0], upper90=interval[1],
        calibration_status="CALIBRATED" if interval[0] is not None else "UNAVAILABLE",
        manifest_uri=forecast.get("backtest_report"))


def get_construction_outlook(
    cutoff: Optional[Union[datetime, str]] = None,
    target_series_id: Optional[str] = None,
    store: Optional[Any] = None,
    prediction_record: Optional[Any] = None,
    dataset_manifest: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Produce a dated construction price and regulatory outlook from committed observations and facts.
    Strictly enforces:
    1. German residential construction producer price index as primary target (no financial/equity/CPI proxy substitution).
    2. Point-in-time public availability (corrections published after cutoff are excluded).
    3. Conformal prediction interval with >=20 historical calibration quarters requirement.
    4. Explicit distinction between REPORTED news claims and MEASURED official statistics.
    5. Absence of causal claims for graph edges, news signals, and GNN predictions.
    6. Read-only operation: does not mutate storage or trigger ingestion/training.
    """
    from src.quant.forecasting import calibrate_residual_interval
    from src.config import primary_target_series_id

    target_series_id = target_series_id or primary_target_series_id()

    # Anti-proxy enforcement
    FORBIDDEN_PROXIES = {
        "equity", "dax", "sp500", "nasdaq", "cpi", "share_price", "stock", "yahoo",
        "commodity_metals", "house_price", "residential_property_sales"
    }
    target_lower = target_series_id.lower()
    for proxy in FORBIDDEN_PROXIES:
        if proxy in target_lower:
            raise ValueError(
                f"Financial proxy ({proxy}) is strictly prohibited from substituting for "
                f"official construction price measures (requested: '{target_series_id}')."
            )

    if "hicp" in target_lower or "cpi" in target_lower:
        raise ValueError(
            f"Consumer price indices (CPI/HICP) cannot substitute for construction PPI (requested: '{target_series_id}')."
        )

    # Ingested series_id formats: Destatis "61261-xxxx|dims...", Eurostat "EUROSTAT_STS|sts_copi_q|dims...".
    if not (
        target_lower.startswith("61261-")
        or target_lower.startswith("eurostat_sts|sts_copi")
    ):
        raise ValueError(
            f"Target series '{target_series_id}' is not a recognized construction producer-price index. "
            f"Financial proxies and unrelated indices cannot substitute for construction measures."
        )

    # Cutoff normalization
    if cutoff is None:
        cutoff = datetime.now(timezone.utc)
    elif isinstance(cutoff, str):
        cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    # Store resolution
    settings = get_settings()
    canonical_store = store or get_graph_store(str(settings.lancedb_path)).canonical_store

    # Retrieve committed observations as of cutoff
    obs_table = canonical_store.query_observations(cutoff=cutoff, mode="public_as_of")
    obs_list = obs_table.to_pylist() if hasattr(obs_table, "to_pylist") else []

    target_obs = [o for o in obs_list if o.get("series_id") == target_series_id]
    target_obs.sort(key=lambda o: (o.get("period_start", ""), o.get("available_at", "")))

    target_definition = "German Construction PPI Residential (Destatis 61261-0002)"
    series_def = getattr(canonical_store, "_canonical_series", {}).get(target_series_id)
    if series_def and series_def.metric_name:
        target_definition = f"German Construction PPI Residential ({series_def.metric_name})"
    target_units = "INDEX_2021_100"

    source_gaps: List[str] = []
    latest_observation: Optional[Dict[str, Any]] = None
    point_forecast: Optional[float] = None
    cur_q = ((cutoff.month - 1) // 3) + 1
    cur_y = cutoff.year
    next_q = 1 if cur_q == 4 else cur_q + 1
    next_y = cur_y + 1 if cur_q == 4 else cur_y
    target_period: str = f"{next_y}-Q{next_q}"
    model_info = "No validated forecast record"
    point_growth_pct = None

    if not target_obs:
        source_gaps.append(
            f"Missing price data: No observations recorded for target series '{target_series_id}' as of cutoff {cutoff.isoformat()}."
        )
        interval_data = {
            "lower": None,
            "upper": None,
            "confidence_level": 0.90,
            "calibration_status": "insufficient_calibration_history",
            "details": "No observations available for target series.",
        }
    else:
        # Unpublished quarters ("...") are stored as null rows; the latest observation is the last measured one.
        measured = [o for o in target_obs if o.get("value") is not None]
        latest = measured[-1] if measured else target_obs[-1]
        avail = latest.get("available_at")
        # Revisions can repeat a value (fetch-dated and release-dated); show when the value first became public.
        if hasattr(canonical_store, "query_observation_revisions"):
            first = [canonical_store._available(r) for r in canonical_store.query_observation_revisions(cutoff, "public_as_of")
                     if r.series_id == target_series_id and r.period_start == latest.get("period_start")
                     and r.value == latest.get("value")]
            avail = min(first) if first else avail
        if isinstance(avail, str):
            avail_dt = datetime.fromisoformat(avail.replace("Z", "+00:00"))
        elif isinstance(avail, datetime):
            avail_dt = avail
        else:
            avail_dt = None
        if avail_dt and avail_dt.tzinfo is None:
            avail_dt = avail_dt.replace(tzinfo=timezone.utc)

        # Staleness: > 180 days between the reference period's end and cutoff. Fetch time is not
        # used, because re-fetching an old release would otherwise make it look fresh.
        is_stale = False
        period_end = latest.get("period_end_exclusive")
        if period_end:
            end_dt = datetime.fromisoformat(str(period_end)[:10]).replace(tzinfo=timezone.utc)
            is_stale = (cutoff - end_dt).days > 180

        status = "STALE" if is_stale else (latest.get("status") or "AVAILABLE").upper()
        p_start = latest.get("period_start", "")
        period_str = ""
        if p_start and len(p_start) >= 7:
            y = p_start[:4]
            m = int(p_start[5:7])
            q = ((m - 1) // 3) + 1
            period_str = f"{y}-Q{q}"

        if is_stale:
            source_gaps.append(
                f"Latest available observation for {target_series_id} is from {period_str} "
                f"(period ended {str(period_end)[:10]}; available {avail_dt.date() if avail_dt else 'unknown'}), "
                f"which is over 180 days stale relative to cutoff {cutoff.date()}."
            )

        val = float(latest.get("value")) if latest.get("value") is not None else None
        latest_observation = {
            "observation_id": latest.get("observation_id"),
            "value": val,
            "unit": latest.get("unit", target_units),
            "period": period_str,
            "vintage_id": latest.get("vintage_id"),
            "status": status,
            "published_at": avail_dt.isoformat() if avail_dt else None,
        }

        # Count distinct eligible quarterly periods
        quarterly_periods = set()
        for o in target_obs:
            ps = o.get("period_start", "")
            pe = o.get("period_end_exclusive", "")
            val_o = o.get("value")
            if ps and pe and val_o is not None:
                try:
                    dt_s = datetime.fromisoformat(str(ps).replace("Z", "+00:00"))
                    dt_e = datetime.fromisoformat(str(pe).replace("Z", "+00:00"))
                    days = (dt_e - dt_s).days
                    if 80 <= days <= 95:
                        y = dt_s.year
                        q = ((dt_s.month - 1) // 3) + 1
                        quarterly_periods.add(f"{y}-Q{q}")
                except Exception:
                    pass
        N_hist = len(quarterly_periods)

        # Require a validated frozen prediction record for point forecast and calibrated interval
        point_forecast = None
        point_growth_pct = None
        interval_data = {
            "lower": None,
            "upper": None,
            "confidence_level": 0.90,
            "calibration_status": "insufficient_calibration_history" if N_hist < 20 else "unavailable",
            "details": f"Requires validated frozen prediction artifact and at least 20 historical calibration quarters; received {N_hist} quarters.",
        }
        if prediction_record is not None:
            pred_target = getattr(prediction_record, "target_series_id", None)
            pred_origin = getattr(prediction_record, "forecast_origin", None)
            origin_valid = pred_origin is not None and canonical_store._utc(pred_origin) <= cutoff
            # A forecast is only current if it targets the quarter right after the latest released one.
            pred_period = getattr(prediction_record, "target_period_start", None)
            if origin_valid and pred_target == target_series_id and pred_period and p_start and pred_period <= p_start:
                source_gaps.append(f"Forecast record targets {pred_period[:7]}, which is already released; "
                                   "rerun `python -m scripts.ops.backtest_target`.")
                origin_valid = False
            if origin_valid and pred_target == target_series_id:
                manifest_valid = True
                if dataset_manifest is not None:
                    if getattr(dataset_manifest, "dataset_id", None) != getattr(prediction_record, "dataset_id", None):
                        manifest_valid = False
                    if getattr(dataset_manifest, "target_spec_id", None) != target_series_id:
                        manifest_valid = False
                if manifest_valid:
                    point_growth_pct = float(getattr(prediction_record, "point_change_pct", 0.0))
                    if val is not None:
                        point_forecast = round(val * (1.0 + point_growth_pct / 100.0), 2)
                    # lower90/upper90 are growth in percent, like point_change_pct; shown as index levels.
                    low, high = getattr(prediction_record, "lower90", None), getattr(prediction_record, "upper90", None)
                    interval_data = {
                        "lower": round(val * (1 + low / 100), 2) if val is not None and low is not None else None,
                        "upper": round(val * (1 + high / 100), 2) if val is not None and high is not None else None,
                        "lower_growth_pct": low, "upper_growth_pct": high,
                        "confidence_level": 0.90,
                        "calibration_status": getattr(prediction_record, "calibration_status", "calibrated").lower(),
                        "details": "Conformal 90% interval from frozen prediction artifact.",
                    }
                    pred_q = (int(pred_period[5:7]) - 1) // 3 + 1
                    target_period = f"{pred_period[:4]}-Q{pred_q}"
                    model_info = getattr(prediction_record, "model_id", model_info)
                else:
                    source_gaps.append("Forecast artifact manifest mismatch: dataset or target specification differs.")
            else:
                source_gaps.append("Forecast artifact origin is after cutoff or target series does not match.")
        else:
            if N_hist < 20:
                source_gaps.append(
                    f"Insufficient calibration history: only {N_hist} quarterly observations available (<20 required) "
                    f"for 90% conformal prediction interval."
                )
            else:
                source_gaps.append(
                    f"No validated forecast artifact available for {target_series_id} as of {cutoff.date() if hasattr(cutoff, 'date') else cutoff}."
                )

    # Regulatory Context
    regulatory_context: List[Dict[str, Any]] = []
    canonical_entities = getattr(canonical_store, "_canonical_entities", {})
    eligible_batches = canonical_store._eligible_batches(cutoff, mode="public_as_of") if hasattr(canonical_store, "_eligible_batches") else set()
    canonical_docs = getattr(canonical_store, "_canonical_documents", {})

    # Evidence linkage resolution from eligible committed batches only
    assertion_to_doc: Dict[str, str] = {}
    canonical_evidence = getattr(canonical_store, "_canonical_evidence", {})
    for evi in canonical_evidence.values():
        if getattr(evi, "batch_id", None) not in eligible_batches:
            continue
        if getattr(evi, "available_at", None) and canonical_store._utc(evi.available_at) > cutoff:
            continue
        if hasattr(evi, "assertion_version_id") and hasattr(evi, "document_version_id"):
            doc = canonical_docs.get(evi.document_version_id)
            if doc and getattr(doc, "batch_id", None) in eligible_batches:
                if not getattr(doc, "available_at", None) or canonical_store._utc(doc.available_at) <= cutoff:
                    assertion_to_doc[evi.assertion_version_id] = doc.document_id

    referenced_doc_ids = set()
    for o in target_obs:
        rev_id = o.get("revision_id", "")
        doc_id = assertion_to_doc.get(rev_id)
        if doc_id:
            referenced_doc_ids.add(doc_id)

    # Use query_facts() for active and forthcoming legal rules, respecting retractions and tombstones
    eligible_facts = canonical_store.query_facts(cutoff, mode="public_as_of", include_expired=False) if hasattr(canonical_store, "query_facts") else []
    for fact in eligible_facts:
        if fact.predicate == "MANDATES":
            rule_ent = canonical_entities.get(fact.object_id)
            law_ent = canonical_entities.get(fact.subject_id)

            valid_from = fact.valid_from or (rule_ent.created_at if rule_ent else None)
            valid_from_utc = canonical_store._utc(valid_from) if valid_from else cutoff

            status = "ACTIVE" if valid_from_utc <= cutoff else "FORTHCOMING"
            label = (rule_ent.canonical_label if rule_ent else None) or (law_ent.canonical_label if law_ent else fact.object_id)

            docs = canonical_store.documents_for(fact, cutoff, mode="public_as_of") if hasattr(canonical_store, "documents_for") else []
            evi_doc_id = docs[0].document_id if docs else assertion_to_doc.get(fact.fact_version_id)

            regulatory_context.append({
                "law_id": fact.subject_id,
                "rule_id": fact.object_id,
                "label": label,
                "status": status,
                "valid_from": valid_from_utc.isoformat(),
                "evidence_document_id": evi_doc_id,
            })
            for d in docs:
                referenced_doc_ids.add(d.document_id)
            if evi_doc_id:
                referenced_doc_ids.add(evi_doc_id)

    # Supply Context (Reported news claims vs Measured official statistics)
    supply_context: List[Dict[str, Any]] = []

    # 1. Reported news claims from events (from eligible facts)
    for fact in eligible_facts:
        ent = canonical_entities.get(fact.subject_id)
        if ent and (ent.entity_subtype == "ECONOMIC_EVENT" or ent.entity_id.startswith("event:")):
            docs = canonical_store.documents_for(fact, cutoff, mode="public_as_of") if hasattr(canonical_store, "documents_for") else []
            evi_doc_id = docs[0].document_id if docs else assertion_to_doc.get(fact.fact_version_id)
            supply_context.append({
                "kind": "REPORTED",
                "claim_status": "unverified_signal",
                "label": ent.canonical_label,
                "entity_id": ent.entity_id,
                "predicate": fact.predicate,
                "target_id": fact.object_id,
                "evidence_document_id": evi_doc_id,
            })
            for d in docs:
                referenced_doc_ids.add(d.document_id)
            if evi_doc_id:
                referenced_doc_ids.add(evi_doc_id)

    # 2. Measured official statistics
    context_obs = [o for o in obs_list if o.get("series_id") != target_series_id]
    context_by_series: Dict[str, Dict[str, Any]] = {}
    for o in context_obs:
        sid = o.get("series_id")
        if sid:
            context_by_series[sid] = o

    for sid, o in context_by_series.items():
        s_ent = canonical_entities.get(sid)
        label = s_ent.canonical_label if s_ent else sid
        rev_id = o.get("revision_id", "")
        evi_doc_id = assertion_to_doc.get(rev_id)
        supply_context.append({
            "kind": "MEASURED",
            "claim_status": "official_statistic",
            "label": label,
            "series_id": sid,
            "value": float(o.get("value")) if o.get("value") is not None else None,
            "unit": o.get("unit"),
            "period": o.get("period_start"),
            "evidence_document_id": evi_doc_id,
        })
        if evi_doc_id:
            referenced_doc_ids.add(evi_doc_id)

    # Evidence Links: only documents attached to eligible displayed assertions/observations
    evidence: List[Dict[str, Any]] = []
    doc_map: Dict[str, Any] = {}
    for doc in canonical_docs.values():
        if hasattr(canonical_store, "_eligible_batches") and getattr(doc, "batch_id", None) not in eligible_batches:
            continue
        if getattr(doc, "available_at", None) and canonical_store._utc(doc.available_at) > cutoff:
            continue
        if hasattr(doc, "version_id") and doc.version_id:
            doc_map[doc.version_id] = doc
        if hasattr(doc, "document_id") and doc.document_id:
            doc_map[doc.document_id] = doc

    seen_doc_ids = set()
    for doc_id in sorted(referenced_doc_ids):
        doc = doc_map.get(doc_id)
        if doc and doc.document_id not in seen_doc_ids:
            seen_doc_ids.add(doc.document_id)
            avail_dt = getattr(doc, "available_at", None)
            evidence.append({
                "document_id": doc.document_id,
                "url": doc.canonical_url,
                "publisher": doc.publisher,
                "available_at": avail_dt.isoformat() if hasattr(avail_dt, "isoformat") else str(avail_dt),
            })

    # Limitations & Disclaimers
    limitations = [
        "Causality Disclaimer: Graph relationships, GNN predictions, and news correlations do not establish economic causation.",
        "Signal Quality Disclaimer: Reported news items represent unverified market claims and sentiment signals, not proven physical capacity changes.",
        "Anti-Proxy Guarantee: Official German residential construction producer prices (Destatis 61261-0002) are used directly; financial proxies (equities, CPI, housing sales) are strictly prohibited from substituting for construction benchmarks.",
        "Uncertainty & Calibration Limitations: Prediction intervals require at least 20 historical calibration quarters; where history is insufficient, intervals remain explicitly uncalibrated.",
    ]

    return {
        "cutoff": cutoff.isoformat(),
        "target_series_id": target_series_id,
        "target_definition": target_definition,
        "target_units": target_units,
        "latest_observation": latest_observation,
        "forecast_origin": cutoff.isoformat(),
        "target_period": target_period,
        "model_info": model_info,
        "point_forecast": point_forecast,
        "point_growth_pct": point_growth_pct,
        "interval": interval_data,
        "supply_context": supply_context,
        "regulatory_context": regulatory_context,
        "source_gaps": source_gaps,
        "evidence": evidence,
        "limitations": limitations,
    }


# ============================================================================
# FORECAST DRIVERS, EVENTS AND SURPRISES (read-only views over config and reports)
# ============================================================================

def _config_json(name: str) -> Dict[str, Any]:
    from src.config import REPO_ROOT
    return json.loads((REPO_ROOT / "config" / name).read_text(encoding="utf-8"))


def series_names() -> Dict[str, Dict[str, str]]:
    """series_id -> {"name", "status"} (active / retired / disabled)."""
    return _config_json("series_names.json")["series"]


def get_price_events() -> List[Dict[str, Any]]:
    """Dated events from config/price_events.json, oldest first."""
    return sorted(_config_json("price_events.json")["events"], key=lambda e: e["date"])


def event_labels(store: Any) -> Dict[str, str]:
    """Readable labels for extracted news events ("European Commission invests 3,400 EUR million in ...")."""
    labels = {}
    for fact in getattr(store, "_canonical_facts", {}).values():
        try:
            action = json.loads(fact.payload_json or "{}").get("action") or {}
        except ValueError:
            continue
        if action and str(fact.subject_id).startswith("EVENT_"):
            amount = f" {action['magnitude']:,.0f} {str(action.get('unit', '')).replace('_', ' ').lower()}" if action.get("magnitude") else ""
            labels[fact.subject_id] = f"{action.get('actor', '?')} {action.get('description', '')}{amount} ({action.get('target', '')})"
    return labels


def get_target_history(store: Any, cutoff: Optional[datetime] = None, since: str = "2000-01-01") -> List[Dict[str, Any]]:
    """Quarterly target levels as public at the cutoff, with quarter-on-quarter growth in percent."""
    from src.config import primary_target_series_id
    target = primary_target_series_id()
    rows = store.query_observations(cutoff=cutoff or datetime.now(timezone.utc), mode="public_as_of").to_pylist()
    levels = sorted((r["period_start"], r["value"]) for r in rows if r["series_id"] == target and r["value"] is not None)
    history, previous = [], None
    for period, value in levels:
        if period >= since:
            history.append({"period_start": period, "value": value,
                            "growth_pct": None if previous is None else 100 * (value / previous - 1)})
        previous = value
    return history


def get_forecast_surprises(forecast: Optional[Dict[str, Any]], n: int = 8) -> List[Dict[str, Any]]:
    """The model's largest past misses (next-release backtest) with events from the 6 months before the quarter end."""
    if not forecast or not forecast.get("backtest_report"):
        return []
    try:
        report = json.loads(Path(forecast["backtest_report"]).read_text(encoding="utf-8"))["next_release"]
    except (OSError, ValueError, KeyError):
        return []
    model, events = forecast["model"], get_price_events()
    rows = []
    for row in report["predictions"]:
        predicted = row["forecasts"].get(model)
        if predicted is None:
            continue
        start = datetime.fromisoformat(row["target_period_start"]).date()
        end = (pd.Timestamp(start) + pd.offsets.QuarterEnd(0)).date()
        nearby = [e for e in events if (pd.Timestamp(end) - pd.Timedelta(days=183)).date().isoformat() <= e["date"] <= end.isoformat()]
        rows.append({"quarter": f"{start.year}-Q{(start.month - 1) // 3 + 1}", "actual_pct": row["actual"],
                     "forecast_pct": predicted, "miss_pp": row["actual"] - predicted,
                     "events": [f"{e['date']}: {e['title']}" for e in nearby]})
    return sorted(rows, key=lambda r: -abs(r["miss_pp"]))[:n]


def get_forecast_drivers() -> Dict[str, Any]:
    """What feeds the forecast and how: from the predictor config and the forecast record."""
    names = series_names()
    predictors = _config_json("predictor_release_lags.json")["predictors"]
    inputs = []
    for key, spec in predictors.items():
        used = bool(spec.get("in_forecast"))
        members = spec.get("series_ids") or [spec["series_id"]]
        why = ("Release dates from the Bundesbank archive" if spec["availability"] == "evidence" else
               "Monthly survey, published at the end of each month" if spec.get("group") == "survey" else
               "Release dates assumed")
        inputs.append({"key": key, "name": spec["label"] if len(members) > 1 else names.get(members[0], {}).get("name", spec["label"]),
                       "status": "used" if used else "tested, not used",
                       "why": why + ("" if used else "; did not improve the backtest"),
                       "components": [names.get(s, {}).get("name", s) for s in members] if len(members) > 1 else []})
    tested = {s for spec in predictors.values() for s in (spec.get("series_ids") or [spec["series_id"]])}
    from src.config import primary_target_series_id
    other = [v["name"] for k, v in names.items() if v["status"] == "active" and k not in tested and k != primary_target_series_id()]
    return {"target": names.get(primary_target_series_id(), {}).get("name", "Forecast target"),
            "inputs": inputs, "stored_not_tested": other, "events": get_price_events()}


MODEL_NAMES = {
    "arx_vat_selected": "Main model: VAT, season, orders, material and energy prices, builders' price expectations",
    "arx_vat_evidence": "VAT, season, orders, material and energy prices",
    "ar4_seasonal_vat": "VAT and season only",
    "mean_4q": "Average of the last 4 quarters",
    "no_change": "No change",
}


def model_name(model_id: str) -> str:
    return MODEL_NAMES.get(model_id, model_id)


def _law_names() -> Dict[str, str]:
    from src.scrapers.eurlex import TARGET_LAWS
    return {f"law_{law['celex_id']}": law["title"].split(" - ")[0] for law in TARGET_LAWS}


def _price_changes(store: Any, cutoff: datetime, series_ids: set) -> Dict[str, Dict[str, Any]]:
    """Latest level and change on a year earlier for each series, as public at the cutoff."""
    rows = store.query_observations(cutoff=cutoff, mode="public_as_of").to_pylist()
    levels: Dict[str, Dict[str, float]] = {}
    for row in rows:
        if row["series_id"] in series_ids and row["value"] is not None:
            levels.setdefault(row["series_id"], {})[row["period_start"][:7]] = row["value"]
    changes = {}
    for series_id, by_month in levels.items():
        latest = max(by_month)
        year_ago = f"{int(latest[:4]) - 1}{latest[4:]}"
        if year_ago in by_month and by_month[year_ago]:
            changes[series_id] = {"period": latest, "yoy_pct": 100 * (by_month[latest] / by_month[year_ago] - 1)}
    return changes


PRICE_METRICS = {"producer_price", "output_price", "material_cost", "labour_cost", "wages"}


def get_sector_network(store: Any, cutoff: Optional[datetime] = None, year: Optional[int] = None,
                       top: int = 12) -> Dict[str, Any]:
    """Construction's neighbourhood as public at the cutoff, built only from accepted, evidenced facts.

    Suppliers and buyers come from one input-output year (Eurostat), materials from the product classification,
    price changes from the linked series, and laws from extracted or quoted legal provisions.
    """
    cutoff = cutoff or datetime.now(timezone.utc)
    facts = store.query_facts(cutoff=cutoff, mode="public_as_of", include_expired=True)
    entities = store._canonical_entities
    names = {**{k: v["name"] for k, v in series_names().items()}, **_law_names()}
    payload = lambda f: json.loads(f.payload_json or "{}")
    flows = [f for f in facts if f.predicate == "FLOWS_TO"]
    years = sorted({payload(f)["episode"] for f in flows})
    if not years:
        return {"year": None, "years": [], "nodes": [], "edges": []}
    year = year if year in years else years[-1]
    flows = [f for f in flows if payload(f)["episode"] == year]
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def add(entity_id: str, group: str, **extra) -> None:
        entity = entities.get(entity_id)
        label = names.get(entity_id) or (entity.canonical_label if entity else entity_id)
        nodes.setdefault(entity_id, {"id": entity_id, "label": label, "group": group, **extra})

    add("sec_F", "construction")
    kind = lambda eid: getattr(entities.get(eid), "entity_subtype", None)
    inputs = sorted((f for f in flows if f.object_id == "sec_F"), key=lambda f: -payload(f)["value_mio_eur"])
    uses = sorted((f for f in flows if f.subject_id == "sec_F" and f.object_id != "sec_F"), key=lambda f: -payload(f)["value_mio_eur"])
    members = [f for f in facts if f.predicate == "BELONGS_TO" and str(f.subject_id).startswith("commodity_")]
    material_sectors = {f.object_id for f in members}
    products = [f for f in inputs if kind(f.subject_id) is None]
    chosen = products[:top] + [f for f in products[top:] if f.subject_id in material_sectors]
    chosen += [f for f in inputs if kind(f.subject_id) == "PRIMARY_INPUT"]
    for f in chosen:
        p = payload(f)
        group = "own sector" if f.subject_id == "sec_F" else ("cost" if kind(f.subject_id) == "PRIMARY_INPUT" else "supplier")
        if f.subject_id != "sec_F":
            add(f.subject_id, group)
        edges.append({"source": f.subject_id, "target": "sec_F", "kind": "supplies" if group != "cost" else "cost",
                      "value_mio_eur": p["value_mio_eur"], "share": p.get("share"), "import_share": p.get("import_share")})
    for f in uses[:top]:
        p = payload(f)
        add(f.object_id, "buyer" if kind(f.object_id) is None else "final demand")
        edges.append({"source": "sec_F", "target": f.object_id, "kind": "buys", "value_mio_eur": p["value_mio_eur"], "share": p.get("share")})
    for f in members:
        if f.object_id in nodes:
            add(f.subject_id, "material")
            edges.append({"source": f.subject_id, "target": f.object_id, "kind": "part of"})
    measures = [f for f in facts if f.predicate == "MEASURES" and f.object_id in nodes]
    changes = _price_changes(store, cutoff, {f.subject_id for f in measures})
    for f in measures:
        if f.subject_id in changes and payload(f).get("metric") in PRICE_METRICS:
            node = nodes[f.object_id]
            node.setdefault("prices", []).append({"series": names.get(f.subject_id, f.subject_id), **changes[f.subject_id]})
    # Laws: directly (German laws) or through their extracted rules (EU laws: law MANDATES rule REGULATES node).
    rule_law = {f.object_id: f.subject_id for f in facts if f.predicate == "MANDATES"}
    rules: Dict[Tuple[str, str], List[str]] = {}
    for f in facts:
        if f.predicate == "REGULATES" and f.object_id in nodes:
            law = rule_law.get(f.subject_id, f.subject_id)
            if str(law).startswith("law_"):
                summary = payload(next((m for m in facts if m.predicate == "MANDATES" and m.object_id == f.subject_id), f)).get("summary")
                rules.setdefault((law, f.object_id), []).append(summary or payload(f).get("norm") or "")
    for (law, target), summaries in rules.items():
        add(law, "law", jurisdiction=getattr(entities.get(law), "jurisdiction", None))
        edges.append({"source": law, "target": target, "kind": "regulates", "rules": len(summaries),
                      "examples": [s for s in summaries if s][:3]})
    for f in facts:
        if f.predicate == "IMPLEMENTS" and f.subject_id in nodes:
            add(f.object_id, "law", jurisdiction="EU")
            edges.append({"source": f.subject_id, "target": f.object_id, "kind": "implements"})
    return {"year": year, "years": years, "nodes": list(nodes.values()), "edges": edges}


def load_projections(path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Projections written offline by scripts.ops.project_series (never computed by the dashboard)."""
    path = Path(path) if path else Path(get_settings().data_dir) / "reports" / "projections-latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
