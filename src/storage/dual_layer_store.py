"""
Dual-Layer Graph & Temporal Tabular Storage Engine.
Backed natively by LanceDB with dense 768-dim vector embeddings and temporal time-series.

Layer 1: Graph Topology
- Nodes: LawNode, ActionNode, ReactionNode, SectorNode, CommodityNode, EconomicIndexNode, BaseGraphNode
- Edges: Directed edges (TRIGGERS, MANDATES, REACTS_TO, AFFECTS_COST_OF, AMENDS, etc.)
- NetworkX conversion, pathfinding, and scale-free Power-Law degree distribution metrics.

Layer 2: Temporal Tabular State
- TabularNodeHistory: Behind-the-graph timestamped state records for each node.
- Point-in-time (as-of) queries: evaluate historical state at any point in time $t$.
- Time-series window queries, transition tracking, and GNN multimodal tensor exports.
"""

from __future__ import annotations

import json
import logging
import hashlib
import math
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import List, Dict, Any, Iterable, Optional, Tuple, Union
from datetime import datetime, timezone
import numpy as np
import pandas as pd
try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    nx = None
    HAS_NETWORKX = False

try:
    import lancedb
    HAS_LANCEDB = True
except ImportError:
    lancedb = None
    HAS_LANCEDB = False

import os
import pyarrow as pa
from src.schemas.graph_temporal import (
    BaseGraphNode,
    LawNode,
    ActionNode,
    ReactionNode,
    SectorNode,
    CommodityNode,
    EconomicIndexNode,
    NodeType,
    EdgeRelation,
    GraphEdge,
    TriggersEdge,
    MandatesEdge,
    ReactsToEdge,
    AffectsCostOfEdge,
    AmendsEdge,
    TabularNodeHistory,
    node_from_dict,
    edge_from_dict,
    parse_iso_or_date_to_utc,
    _is_nan,
)
from src.schemas.ingestion import (
    SourceDocument,
    Entity,
    EntityAlias,
    Fact,
    EvidenceLink,
    SeriesDefinition,
    Observation,
    BatchManifest,
)

logger = logging.getLogger(__name__)


def _locked_legacy_write(method):
    @wraps(method)
    def write(self, *args, **kwargs):
        if getattr(self, "_legacy_writer_thread", None) == threading.get_ident():
            return method(self, *args, **kwargs)
        with self._canonical_mutation():
            self._legacy_writer_thread = threading.get_ident()
            try:
                for name, table in (("nodes", self._nodes_tbl), ("edges", self._edges_tbl), ("history", self._history_tbl)):
                    table.checkout_latest()
                    committed_version = self._state["legacy_tables"][name]
                    if table.version != committed_version:
                        table.restore(committed_version)
                result = method(self, *args, **kwargs)
                self._save_canonical()
                return result
            finally:
                self._legacy_writer_thread = None
    return write


class WriterLockError(RuntimeError):
    """Raised when another writer currently holds the storage execution lock."""
    pass


class _WriterLock:
    """OS-held lock: process exit releases ownership without a stale lock file."""

    def __init__(self, lock_file: Path):
        self.lock_file = Path(lock_file)
        self._acquired = False
        self._fd = None

    def acquire(self) -> None:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)
            if os.name == "nt":
                import msvcrt
                if os.fstat(self._fd).st_size == 0:
                    os.write(self._fd, b"\0")
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._acquired = True
        except OSError:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise WriterLockError(f"Storage writer lock already held at {self.lock_file}")

    def release(self) -> None:
        if self._acquired:
            os.close(self._fd)
            self._fd = None
            self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


# Canonical PyArrow schemas for typed append-only version tables
DOCUMENTS_SCHEMA = pa.schema([
    pa.field("document_id", pa.string()),
    pa.field("version_id", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("upstream_id", pa.string()),
    pa.field("canonical_url", pa.string()),
    pa.field("final_url", pa.string()),
    pa.field("publisher", pa.string()),
    pa.field("language", pa.string()),
    pa.field("content_type", pa.string()),
    pa.field("content_sha256", pa.string()),
    pa.field("raw_uri", pa.string()),
    pa.field("text_uri", pa.string()),
    pa.field("byte_count", pa.int64()),
    pa.field("published_at", pa.timestamp("us", tz="UTC")),
    pa.field("source_updated_at", pa.timestamp("us", tz="UTC")),
    pa.field("available_at", pa.timestamp("us", tz="UTC")),
    pa.field("fetched_at", pa.timestamp("us", tz="UTC")),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC")),
    pa.field("availability_basis", pa.string()),
    pa.field("date_precision", pa.string()),
    pa.field("rights_json", pa.string()),
    pa.field("parser_version", pa.string()),
    pa.field("coverage_status", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("batch_id", pa.string()),
    pa.field("data_origin", pa.string()),
])

FACTS_SCHEMA = pa.schema([
    pa.field("fact_id", pa.string()),
    pa.field("fact_version_id", pa.string()),
    pa.field("subject_id", pa.string()),
    pa.field("predicate", pa.string()),
    pa.field("object_id", pa.string()),
    pa.field("payload_json", pa.string()),
    pa.field("event_at", pa.timestamp("us", tz="UTC")),
    pa.field("valid_from", pa.timestamp("us", tz="UTC")),
    pa.field("valid_to", pa.timestamp("us", tz="UTC")),
    pa.field("available_at", pa.timestamp("us", tz="UTC")),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC")),
    pa.field("source_id", pa.string()),
    pa.field("supersedes_version_id", pa.string()),
    pa.field("is_retracted", pa.bool_()),
    pa.field("claim_kind", pa.string()),
    pa.field("quality_state", pa.string()),
    pa.field("extractor_version", pa.string()),
    pa.field("batch_id", pa.string()),
    pa.field("data_origin", pa.string()),
])

OBSERVATIONS_SCHEMA = pa.schema([
    pa.field("observation_id", pa.string()),
    pa.field("revision_id", pa.string()),
    pa.field("series_id", pa.string()),
    pa.field("period_start", pa.string()),
    pa.field("period_end_exclusive", pa.string()),
    pa.field("value", pa.float64()),
    pa.field("original_value_text", pa.string()),
    pa.field("source_unit", pa.string()),
    pa.field("unit", pa.string()),
    pa.field("vintage_id", pa.string()),
    pa.field("published_at", pa.timestamp("us", tz="UTC")),
    pa.field("available_at", pa.timestamp("us", tz="UTC")),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC")),
    pa.field("source_updated_at", pa.timestamp("us", tz="UTC")),
    pa.field("provider_status", pa.string()),
    pa.field("missing_reason", pa.string()),
    pa.field("quality_state", pa.string()),
    pa.field("supersedes_revision_id", pa.string()),
    pa.field("is_retracted", pa.bool_()),
    pa.field("conversion_lineage_json", pa.string()),
    pa.field("batch_id", pa.string()),
    pa.field("data_origin", pa.string()),
])

BATCHES_SCHEMA = pa.schema([
    pa.field("batch_id", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("status", pa.string()),
    pa.field("discovered_count", pa.int64()),
    pa.field("fetched_count", pa.int64()),
    pa.field("staged_count", pa.int64()),
    pa.field("accepted_count", pa.int64()),
    pa.field("review_count", pa.int64()),
    pa.field("rejected_count", pa.int64()),
    pa.field("duplicate_count", pa.int64()),
    pa.field("committed_count", pa.int64()),
    pa.field("created_at", pa.timestamp("us", tz="UTC")),
    pa.field("staged_at", pa.timestamp("us", tz="UTC")),
    pa.field("committed_at", pa.timestamp("us", tz="UTC")),
    pa.field("error_message", pa.string()),
    pa.field("manifest_uri", pa.string()),
    pa.field("manifest_sha256", pa.string()),
    pa.field("archive_sha256", pa.string()),
    pa.field("data_origin", pa.string()),
])

EVIDENCE_SCHEMA = pa.schema([
    pa.field("evidence_id", pa.string()),
    pa.field("assertion_version_id", pa.string()),
    pa.field("assertion_kind", pa.string()),
    pa.field("document_version_id", pa.string()),
    pa.field("selector_type", pa.string()),
    pa.field("selector_json", pa.string()),
    pa.field("quoted_text", pa.string()),
    pa.field("support_type", pa.string()),
    pa.field("available_at", pa.timestamp("us", tz="UTC")),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC")),
    pa.field("batch_id", pa.string()),
])

SERIES_SCHEMA = pa.schema([
    pa.field("series_id", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("dataset_id", pa.string()),
    pa.field("provider_series_key", pa.string()),
    pa.field("dimensions_json", pa.string()),
    pa.field("metric_name", pa.string()),
    pa.field("unit", pa.string()),
    pa.field("currency", pa.string()),
    pa.field("frequency", pa.string()),
    pa.field("geography_id", pa.string()),
    pa.field("classification_version", pa.string()),
    pa.field("base_period", pa.string()),
    pa.field("adjustment", pa.string()),
    pa.field("measurement_kind", pa.string()),
    pa.field("available_at", pa.timestamp("us", tz="UTC")),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC")),
    pa.field("batch_id", pa.string()),
])

ENTITIES_SCHEMA = pa.schema([
    pa.field("entity_id", pa.string()),
    pa.field("entity_type", pa.string()),
    pa.field("entity_subtype", pa.string()),
    pa.field("canonical_label", pa.string()),
    pa.field("created_at", pa.timestamp("us", tz="UTC")),
    pa.field("jurisdiction", pa.string()),
    pa.field("metadata_json", pa.string()),
    pa.field("batch_id", pa.string()),
    pa.field("data_origin", pa.string()),
])

ALIASES_SCHEMA = pa.schema([
    pa.field("alias_id", pa.string()),
    pa.field("entity_id", pa.string()),
    pa.field("scheme", pa.string()),
    pa.field("external_id", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("available_at", pa.timestamp("us", tz="UTC")),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC")),
    pa.field("fact_version_id", pa.string()),
    pa.field("batch_id", pa.string()),
])



def _sanitize_record_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Strip NaN values so Pydantic model defaults apply cleanly."""
    clean = {}
    for k, v in d.items():
        # Cheap checks first: this runs for every stored row (hundreds of thousands of evidence links).
        if v is None or (isinstance(v, float) and v != v):
            continue
        if not isinstance(v, (str, int, float, bool, datetime)) and _is_nan(v):
            continue
        clean[k] = v
        if k in ("metadata_json", "properties_json") and isinstance(v, str):
            try:
                json.loads(v)
            except json.JSONDecodeError:
                try:
                    import ast
                    parsed = ast.literal_eval(v)
                    if isinstance(parsed, dict):
                        clean[k] = json.dumps(parsed)
                except (ValueError, SyntaxError):
                    pass
    return clean


class DualLayerStore:
    """
    LanceDB-backed Dual-Layer Storage Engine managing:
    1. Topological graph layer (Laws, Actions, Reactions, Sectors, with 768-dim vector embeddings).
    2. Behind-the-graph tabular temporal layer (timestamped metrics, states, audit logs).
    """

    def __init__(
        self,
        storage_dir: Optional[Union[str, Path]] = None,
        nodes_table_name: str = "nodes",
        edges_table_name: str = "edges",
        history_table_name: str = "tabular_history",
    ):
        if storage_dir is None:
            from src.config import get_settings
            storage_dir = get_settings().lancedb_path
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.nodes_table_name = nodes_table_name
        self.edges_table_name = edges_table_name
        self.history_table_name = history_table_name

        # In-memory working caches for rapid topological queries
        self._nodes: Dict[str, BaseGraphNode] = {}
        self._edges: Dict[str, GraphEdge] = {}
        self._history: List[TabularNodeHistory] = []
        self._history_loaded: bool = False

        # Single writer lock & committed generation tracker
        self._lock_file = self.storage_dir / "ingestion.lock"
        self._state_path = self.storage_dir / "canonical_state.json"
        self._state = self._read_canonical_state()
        self._generation: int = self._get_disk_generation()
        self._observations_loaded = False

        # Canonical in-memory indices for rapid query and as-of resolution
        self._canonical_batches: Dict[str, BatchManifest] = {}
        self._canonical_documents: Dict[str, SourceDocument] = {}
        self._canonical_facts: Dict[str, Fact] = {}
        self._canonical_observations: Dict[str, Observation] = {}
        self._canonical_evidence: Dict[str, EvidenceLink] = {}
        self._canonical_series: Dict[str, SeriesDefinition] = {}
        self._canonical_entities: Dict[str, Entity] = {}
        self._canonical_aliases: Dict[str, EntityAlias] = {}

        # Initialize LanceDB connection and tables
        if HAS_LANCEDB and lancedb is not None:
            self.db = lancedb.connect(str(self.storage_dir))
            self._init_tables()
            self._init_canonical_tables()
            self._load_from_storage()
        else:
            self.db = None

        # Load canonical datasets from persistent storage (observations loaded on demand)
        self._load_canonical_from_storage()
        self._refresh_canonical_projection()

    @property
    def generation(self) -> int:
        return self._generation

    def _get_disk_generation(self) -> int:
        state = self._read_canonical_state()
        if state:
            return int(state["generation"])
        gen_path = self.storage_dir / "generation.json"
        if gen_path.exists():
            try:
                with open(gen_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return int(data.get("generation", 0))
            except Exception:
                pass
        b_path = self.storage_dir / "canonical_batches.parquet"
        if b_path.exists():
            try:
                df = pd.read_parquet(b_path)
                return int((df["status"] == "committed").sum())
            except Exception:
                pass
        return 0

    def _persist_generation(self) -> None:
        self._atomic_json(self.storage_dir / "generation.json", {"generation": self._generation})

    def _read_canonical_state(self) -> dict:
        if not self._state_path.exists():
            return {}
        with self._state_path.open(encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state.get("tables"), dict) or not isinstance(state.get("revision"), int):
            raise ValueError("Invalid canonical state manifest; restore a verified backup")
        return state

    @staticmethod
    def _atomic_json(path: Path, value: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _check_stale_instance(self) -> None:
        disk_gen = self._get_disk_generation()
        if disk_gen > self._generation or self._read_canonical_state().get("revision", 0) != self._state.get("revision", 0):
            raise RuntimeError(
                f"Stale store instance: on-disk generation ({disk_gen}) is ahead of local generation ({self._generation}). Cannot overwrite committed data."
            )

    def _init_tables(self) -> None:
        """Initialize LanceDB tables with pydantic schemas safely without concurrency races."""
        def _get_or_create(table_name: str, schema: Any):
            try:
                if table_name in self.db:
                    return self.db.open_table(table_name)
            except Exception:
                pass
            try:
                return self.db.create_table(table_name, schema=schema, exist_ok=True)
            except ValueError:
                return self.db.open_table(table_name)

        self._nodes_tbl = _get_or_create(self.nodes_table_name, BaseGraphNode)
        self._edges_tbl = _get_or_create(self.edges_table_name, GraphEdge)
        self._history_tbl = _get_or_create(self.history_table_name, TabularNodeHistory)

    def _init_canonical_tables(self) -> None:
        """Initialize LanceDB canonical append-only version tables with explicit Arrow schemas."""
        if self.db is None:
            return
        def _get_or_create_arrow(name: str, schema: pa.Schema):
            try:
                if name in self.db:
                    return self.db.open_table(name)
            except Exception:
                pass
            try:
                return self.db.create_table(name, schema=schema, exist_ok=True)
            except Exception:
                return self.db.open_table(name)

        self._docs_tbl = _get_or_create_arrow("canonical_documents", DOCUMENTS_SCHEMA)
        self._facts_tbl = _get_or_create_arrow("canonical_facts", FACTS_SCHEMA)
        self._obs_tbl = _get_or_create_arrow("canonical_observations", OBSERVATIONS_SCHEMA)
        self._batches_tbl = _get_or_create_arrow("canonical_batches", BATCHES_SCHEMA)
        self._evidence_tbl = _get_or_create_arrow("canonical_evidence", EVIDENCE_SCHEMA)
        self._series_tbl = _get_or_create_arrow("canonical_series", SERIES_SCHEMA)
        self._entities_tbl = _get_or_create_arrow("canonical_entities", ENTITIES_SCHEMA)
        self._aliases_tbl = _get_or_create_arrow("canonical_aliases", ALIASES_SCHEMA)

    def _load_from_storage(self) -> None:
        """Load persisted graph entities from LanceDB tables into the in-memory working cache."""
        try:
            self._pin_legacy_tables()
            # Load Nodes
            if len(self._nodes_tbl) > 0:
                nodes_df = self._nodes_tbl.to_pandas()
                records = nodes_df.to_dict(orient="records")
                for r in records:
                    clean_r = _sanitize_record_dict(r)
                    node = node_from_dict(clean_r)
                    self._nodes[node.id] = node

            # Load Edges
            if len(self._edges_tbl) > 0:
                edges_df = self._edges_tbl.to_pandas()
                records = edges_df.to_dict(orient="records")
                for r in records:
                    clean_r = _sanitize_record_dict(r)
                    edge = edge_from_dict(clean_r)
                    self._edges[edge.id] = edge

            # Parquet fallback for nodes
            nodes_p = self.storage_dir / "graph_nodes.parquet"
            if not self._state and nodes_p.exists():
                try:
                    ndf = pd.read_parquet(nodes_p)
                    for _, r in ndf.iterrows():
                        clean_r = _sanitize_record_dict(r.to_dict())
                        nid = str(clean_r.get("id", ""))
                        if nid and nid not in self._nodes:
                            node = node_from_dict(clean_r)
                            self._nodes[nid] = node
                except Exception as e:
                    logger.debug(f"Fallback loading graph_nodes.parquet: {e}")

            # Parquet fallback for edges
            edges_p = self.storage_dir / "graph_edges.parquet"
            if not self._state and len(self._edges_tbl) == 0 and edges_p.exists():
                try:
                    edf = pd.read_parquet(edges_p)
                    occurrences = {}
                    for _, r in edf.iterrows():
                        clean_r = _sanitize_record_dict(r.to_dict())
                        if "source_id" in clean_r and "source" not in clean_r:
                            clean_r["source"] = clean_r["source_id"]
                        if "target_id" in clean_r and "target" not in clean_r:
                            clean_r["target"] = clean_r["target_id"]
                        if "relation_type" in clean_r and "relationship_type" not in clean_r:
                            clean_r["relationship_type"] = clean_r["relation_type"]
                        fingerprint = hashlib.sha256(json.dumps(clean_r, sort_keys=True, default=str).encode()).hexdigest()
                        base_id = clean_r.get("id") or f"legacy-edge:{fingerprint}"
                        occurrence = occurrences.get(base_id, 0)
                        occurrences[base_id] = occurrence + 1
                        eid = base_id if occurrence == 0 else f"{base_id}#{occurrence}"
                        clean_r["id"] = eid
                        if eid not in self._edges:
                            edge = edge_from_dict(clean_r)
                            self._edges[eid] = edge
                except Exception as e:
                    logger.debug(f"Fallback loading graph_edges.parquet: {e}")

            # Avoid eagerly loading complete observations or full tabular history into memory at initialization.
        except Exception as e:
            logger.warning(f"Error loading records from LanceDB: {e}")

    def _canonical_tables(self):
        return (
            ("batches", "batch_id", BatchManifest, BATCHES_SCHEMA, self._batches_tbl),
            ("documents", "version_id", SourceDocument, DOCUMENTS_SCHEMA, self._docs_tbl),
            ("facts", "fact_version_id", Fact, FACTS_SCHEMA, self._facts_tbl),
            ("observations", "revision_id", Observation, OBSERVATIONS_SCHEMA, self._obs_tbl),
            ("evidence", "evidence_id", EvidenceLink, EVIDENCE_SCHEMA, self._evidence_tbl),
            ("series", "series_id", SeriesDefinition, SERIES_SCHEMA, self._series_tbl),
            ("entities", "entity_id", Entity, ENTITIES_SCHEMA, self._entities_tbl),
            ("aliases", "alias_id", EntityAlias, ALIASES_SCHEMA, self._aliases_tbl),
        )

    def _read_canonical_table(self, name, table):
        if self._state:
            entry = self._state["tables"][name]
            table.checkout(entry["version"])
            return table.to_arrow().to_pylist()
        # Existing pre-manifest Parquet stores remain readable and untouched.
        legacy = self.storage_dir / f"canonical_{name}.parquet"
        return pd.read_parquet(legacy).to_dict("records") if legacy.exists() else []

    def _load_canonical_from_storage(self) -> None:
        """Load one pinned multi-table snapshot; malformed authoritative data fails closed."""
        if self.db is None:
            raise RuntimeError("LanceDB is required for the canonical store")
        for name, key, model, schema, table in self._canonical_tables():
            if name == "observations":
                if self._state:
                    table.checkout(self._state["tables"][name]["version"])
                continue
            target = getattr(self, f"_canonical_{name}")
            target.clear()
            for raw in self._read_canonical_table(name, table):
                record = model(**_sanitize_record_dict(raw))
                target[getattr(record, key)] = record

    def _ensure_observations_loaded(self) -> None:
        if not self._observations_loaded:
            self._canonical_observations.clear()
            for raw in self._read_canonical_table("observations", self._obs_tbl):
                record = Observation(**_sanitize_record_dict(raw))
                self._canonical_observations[record.revision_id] = record
            self._observations_loaded = True

    def _ensure_history_loaded(self) -> None:
        """On-demand load of tabular node history from LanceDB (avoiding eager load at init)."""
        if self._history_loaded and (self._history or len(self._history_tbl) == 0):
            return
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            try:
                hist_df = self._history_tbl.to_pandas()
                records = hist_df.to_dict(orient="records")
                for r in records:
                    clean_r = _sanitize_record_dict(r)
                    self._history.append(TabularNodeHistory(**clean_r))
            except Exception as e:
                logger.warning(f"Error loading history from LanceDB: {e}")

        if len(self._history) == 0 and not self._state:
            hist_pq = self.storage_dir / "node_tabular_history.parquet"
            if hist_pq.exists():
                try:
                    hdf = pd.read_parquet(hist_pq)
                    if not hdf.empty:
                        for _, row in hdf.iterrows():
                            r_dict = row.to_dict()
                            clean_r = _sanitize_record_dict(r_dict)
                            if "effective_timestamp" not in clean_r and "timestamp" in clean_r:
                                clean_r["effective_timestamp"] = clean_r["timestamp"]
                            self._history.append(TabularNodeHistory(**clean_r))
                except Exception as e:
                    logger.warning(f"Error loading history from Parquet: {e}")
        self._history_loaded = True

    def _pin_legacy_tables(self):
        for name, table in (("nodes", self._nodes_tbl), ("edges", self._edges_tbl), ("history", self._history_tbl)):
            version = self._state.get("legacy_tables", {}).get(name)
            if version is not None:
                table.checkout(version)

    def _save_canonical(self) -> None:
        """Publish a pinned set of immutable Lance versions after every table succeeds."""
        self._ensure_observations_loaded()
        if not self._state:
            # Preserve old compatibility files when first publishing a canonical manifest.
            self._ensure_history_loaded()
            fact_ids = {fact.fact_id for fact in self._canonical_facts.values()}
            for table, records in ((self._nodes_tbl, [v for k, v in self._nodes.items() if k not in self._canonical_entities]),
                                   (self._edges_tbl, [v for k, v in self._edges.items() if k not in fact_ids]),
                                   (self._history_tbl, self._history)):
                if records:
                    table.checkout_latest()
                    table.add(records, mode="overwrite")
        entries = dict(self._state.get("tables", {}))
        for name, key, model, schema, table in self._canonical_tables():
            records = getattr(self, f"_canonical_{name}")
            rows = [records[k].model_dump() for k in sorted(records)]
            digest = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
            if entries.get(name, {}).get("sha256") == digest:
                continue
            for row in rows:
                for field in schema:
                    if pa.types.is_timestamp(field.type) and isinstance(row.get(field.name), str):
                        row[field.name] = self._utc(row[field.name])
            arrow = pa.Table.from_pylist(rows, schema=schema)
            table.checkout_latest()
            # shortcut: changed tables use retained Lance snapshots; use delta writes for large backfills.
            table.add(arrow, mode="overwrite")
            entries[name] = {"version": table.version, "sha256": digest}
        state = {"revision": self._state.get("revision", 0) + 1,
                 "generation": self._generation, "tables": entries,
                 "legacy_tables": {"nodes": self._nodes_tbl.version, "edges": self._edges_tbl.version,
                                   "history": self._history_tbl.version}}
        for name, key, model, schema, table in self._canonical_tables():
            table.checkout(state["tables"][name]["version"])
        for name, table in (("nodes", self._nodes_tbl), ("edges", self._edges_tbl), ("history", self._history_tbl)):
            table.checkout(state["legacy_tables"][name])
        self._atomic_json(self._state_path, state)
        self._state = state

    def _refresh_canonical_projection(self) -> None:
        """Rebuild disposable compatibility caches from accepted committed facts."""
        self._nodes = {k: v for k, v in self._nodes.items() if k not in self._canonical_entities}
        fact_ids = {f.fact_id for f in self._canonical_facts.values()}
        self._edges = {k: v for k, v in self._edges.items() if k not in fact_ids}
        projection = self.project_graph(datetime.now(timezone.utc))
        for node in projection["nodes"]:
            if node["node_type"] != "UNKNOWN":
                self._nodes[node["id"]] = BaseGraphNode(**node)
        for edge in projection["edges"]:
            self._edges[edge["edge_id"]] = GraphEdge(
                id=edge["edge_id"], source=edge["source_id"], target=edge["target_id"] or "",
                relationship_type=edge["relation_type"], valid_from=edge["valid_from"],
                valid_to=edge["valid_to"], claim_kind=edge["claim_kind"], quality_state=edge["quality_state"],
                properties=json.loads(edge["payload"]) if edge["payload"] else {})

    # =========================================================================
    # 1. NODE OPERATIONS (Layer 1 - Graph)
    # =========================================================================

    @_locked_legacy_write
    def add_node(self, node: Union[BaseGraphNode, Dict[str, Any]]) -> BaseGraphNode:
        """
        Add or update a graph node in LanceDB and in-memory cache.
        Supports LawNode, ActionNode, ReactionNode, SectorNode or dictionaries.
        """
        if isinstance(node, dict):
            node_obj = node_from_dict(node)
        elif isinstance(node, BaseGraphNode):
            node_obj = node
        else:
            raise TypeError(f"Expected BaseGraphNode or dict, got {type(node)}")

        # Persistent upsert in LanceDB
        self._nodes_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute([node_obj])

        # Cache update
        self._nodes[node_obj.id] = node_obj
        return node_obj

    @_locked_legacy_write
    def add_nodes(self, nodes: List[Union[BaseGraphNode, Dict[str, Any]]]) -> List[BaseGraphNode]:
        """Batch add or update multiple graph nodes with deduplication."""
        node_objs = [
            node_from_dict(n) if isinstance(n, dict) else n
            for n in nodes
        ]
        if not node_objs:
            return []

        # Deduplicate within batch preserving last occurrence for each ID
        unique_nodes: Dict[str, BaseGraphNode] = {}
        for n in node_objs:
            unique_nodes[n.id] = n
        batch = list(unique_nodes.values())

        self._nodes_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(batch)

        for n in batch:
            self._nodes[n.id] = n

        return batch

    def get_node(self, node_id: str) -> Optional[BaseGraphNode]:
        """Fetch a node by unique identifier, checking memory cache with LanceDB fallback."""
        if node_id in self._nodes:
            return self._nodes[node_id]

        # Fallback query directly against LanceDB
        try:
            escaped_id = node_id.replace("'", "''")
            res = self._nodes_tbl.search().where(f"id == '{escaped_id}'").limit(1).to_pandas()
            if not res.empty:
                clean_r = _sanitize_record_dict(res.iloc[0].to_dict())
                node = node_from_dict(clean_r)
                self._nodes[node.id] = node
                return node
        except Exception as e:
            logger.debug(f"Direct query for node {node_id} failed: {e}")
        return None

    def get_nodes(self, node_type: Optional[str] = None) -> List[BaseGraphNode]:
        """Retrieve all nodes, optionally filtered by node_type (e.g. 'LAW', 'ACTION')."""
        if len(self._nodes) == 0 and len(self._nodes_tbl) > 0:
            self._load_from_storage()

        nodes = list(self._nodes.values())
        if node_type:
            target_type = node_type.upper()
            return [n for n in nodes if n.node_type.upper() == target_type]
        return nodes

    @_locked_legacy_write
    def delete_node(
        self,
        node_id: str,
        cascade_edges: bool = True,
        cascade_history: bool = False
    ) -> bool:
        """Delete a node and optionally its incident edges and tabular history."""
        if node_id not in self._nodes and len(self._nodes_tbl) == 0:
            return False

        escaped_id = node_id.replace("'", "''")
        try:
            self._nodes_tbl.delete(f"id == '{escaped_id}'")
        except Exception as e:
            logger.warning(f"Failed deleting node {node_id} from LanceDB: {e}")

        self._nodes.pop(node_id, None)

        if cascade_edges:
            edges_to_delete = [
                e for e in list(self._edges.values())
                if e.source == node_id or e.target == node_id
            ]
            for e in edges_to_delete:
                self.delete_edge(e.source, e.target, e.relationship_type)

        if cascade_history:
            self.delete_history(node_id)

        return True

    def count_nodes(self) -> int:
        """Return total number of nodes in the graph."""
        if len(self._nodes) == 0 and len(self._nodes_tbl) > 0:
            return self._nodes_tbl.count_rows()
        return len(self._nodes)

    def search_nodes_by_vector(
        self,
        query_vector: Union[List[float], np.ndarray],
        limit: int = 10,
        node_type: Optional[str] = None
    ) -> List[Tuple[BaseGraphNode, float]]:
        """
        Perform vector nearest-neighbor similarity search over the 768-dim node embeddings.
        Returns a list of (node, distance) tuples.
        """
        if isinstance(query_vector, np.ndarray):
            query_vector = query_vector.tolist()

        if len(query_vector) != 768:
            raise ValueError(f"query_vector must have 768 dimensions, got {len(query_vector)}")

        if len(self._nodes_tbl) == 0:
            return []

        search_query = self._nodes_tbl.search(query_vector).limit(limit)
        if node_type:
            search_query = search_query.where(f"node_type == '{node_type.upper()}'")

        df = search_query.to_pandas()
        results = []
        for _, row in df.iterrows():
            row_dict = _sanitize_record_dict(row.to_dict())
            dist = float(row_dict.pop("_distance", 0.0))
            node = node_from_dict(row_dict)
            results.append((node, dist))
        return results

    # =========================================================================
    # 2. EDGE OPERATIONS (Layer 1 - Graph)
    # =========================================================================

    @_locked_legacy_write
    def add_edge(self, edge: Union[GraphEdge, Dict[str, Any]]) -> GraphEdge:
        """Add or update a directional edge in LanceDB and in-memory cache."""
        if isinstance(edge, dict):
            edge_obj = edge_from_dict(edge)
        elif isinstance(edge, GraphEdge):
            edge_obj = edge
        else:
            raise TypeError(f"Expected GraphEdge or dict, got {type(edge)}")

        # Persistent upsert in LanceDB
        self._edges_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute([edge_obj])

        self._edges[edge_obj.id] = edge_obj
        return edge_obj

    @_locked_legacy_write
    def add_edges(self, edges: List[Union[GraphEdge, Dict[str, Any]]]) -> List[GraphEdge]:
        """Batch add or update directional edges with deduplication."""
        edge_objs = [
            edge_from_dict(e) if isinstance(e, dict) else e
            for e in edges
        ]
        if not edge_objs:
            return []

        # Deduplicate within batch preserving last occurrence
        unique_edges: Dict[str, GraphEdge] = {}
        for e in edge_objs:
            unique_edges[e.id] = e
        batch = list(unique_edges.values())

        self._edges_tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(batch)

        for e in batch:
            self._edges[e.id] = e

        return batch

    def get_edge(
        self,
        source: str,
        target: str,
        relationship_type: Optional[str] = None
    ) -> Optional[GraphEdge]:
        """Fetch an edge by source, target, and optional relationship type, with LanceDB fallback."""
        for edge in self._edges.values():
            if edge.source == source and edge.target == target:
                if relationship_type is None or edge.relationship_type == relationship_type:
                    return edge

        # Fallback query directly against LanceDB
        try:
            esc_s = source.replace("'", "''")
            esc_t = target.replace("'", "''")
            where_clause = f"source == '{esc_s}' AND target == '{esc_t}'"
            if relationship_type:
                esc_r = relationship_type.replace("'", "''")
                where_clause += f" AND relationship_type == '{esc_r}'"
            res = self._edges_tbl.search().where(where_clause).limit(1).to_pandas()
            if not res.empty:
                clean_r = _sanitize_record_dict(res.iloc[0].to_dict())
                edge = edge_from_dict(clean_r)
                self._edges[edge.id] = edge
                return edge
        except Exception as e:
            logger.debug(f"Direct query for edge {source}->{target} failed: {e}")

        return None

    def get_edges(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        relationship_type: Optional[str] = None
    ) -> List[GraphEdge]:
        """Retrieve edges matching source, target, and/or relationship_type filters."""
        if len(self._edges) == 0 and len(self._edges_tbl) > 0:
            self._load_from_storage()

        matched = list(self._edges.values())
        if source:
            matched = [e for e in matched if e.source == source]
        if target:
            matched = [e for e in matched if e.target == target]
        if relationship_type:
            matched = [e for e in matched if e.relationship_type == relationship_type]
        return matched

    @_locked_legacy_write
    def delete_edge(
        self,
        source: str,
        target: str,
        relationship_type: Optional[str] = None
    ) -> bool:
        """Delete matching edge(s) from LanceDB and in-memory cache."""
        to_delete = [
            e for e in list(self._edges.values())
            if e.source == source and e.target == target and (
                relationship_type is None or e.relationship_type == relationship_type
            )
        ]
        if not to_delete:
            return False

        for edge in to_delete:
            escaped_id = edge.id.replace("'", "''")
            try:
                self._edges_tbl.delete(f"id == '{escaped_id}'")
            except Exception as e:
                logger.warning(f"Error deleting edge {edge.id}: {e}")
            self._edges.pop(edge.id, None)

        return True

    def count_edges(self) -> int:
        """Return total number of edges in the graph."""
        if len(self._edges) == 0 and len(self._edges_tbl) > 0:
            return self._edges_tbl.count_rows()
        return len(self._edges)

    # =========================================================================
    # 3. GRAPH TOPOLOGY QUERIES (Layer 1 - Graph)
    # =========================================================================

    def get_in_edges(self, node_id: str) -> List[GraphEdge]:
        """Return all edges where target == node_id."""
        return [e for e in self._edges.values() if e.target == node_id]

    def get_out_edges(self, node_id: str) -> List[GraphEdge]:
        """Return all edges where source == node_id."""
        return [e for e in self._edges.values() if e.source == node_id]

    def get_neighbors(self, node_id: str, direction: str = "out") -> List[str]:
        """
        Get neighbor node IDs connected to node_id.
        Direction: 'out' (successors), 'in' (predecessors), 'both', or 'all'.
        """
        dir_clean = str(direction).lower().strip()
        neighbors = set()
        if dir_clean in ("out", "both", "all"):
            for e in self.get_out_edges(node_id):
                neighbors.add(e.target)
        if dir_clean in ("in", "both", "all"):
            for e in self.get_in_edges(node_id):
                neighbors.add(e.source)
        return sorted(list(neighbors))

    def get_subgraph(self, node_id: str, depth: int = 1) -> Dict[str, Any]:
        """
        Extract k-hop subgraph around node_id using breadth-first traversal.
        Returns dictionary with 'nodes', 'edges', and 'node_ids'.
        """
        root_node = self.get_node(node_id)
        if root_node is None:
            return {
                "root_id": node_id,
                "depth": depth,
                "node_ids": [],
                "nodes": [],
                "edges": [],
            }

        visited_nodes = {node_id}
        frontier = {node_id}
        collected_edges: Dict[str, GraphEdge] = {}

        for _ in range(max(1, depth)):
            next_frontier = set()
            for nid in frontier:
                incident = self.get_out_edges(nid) + self.get_in_edges(nid)
                for e in incident:
                    collected_edges[e.id] = e
                    other = e.target if e.source == nid else e.source
                    if other not in visited_nodes:
                        visited_nodes.add(other)
                        next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        subgraph_nodes = [
            self.get_node(nid) for nid in visited_nodes if self.get_node(nid) is not None
        ]

        return {
            "root_id": node_id,
            "depth": depth,
            "node_ids": sorted(list(visited_nodes)),
            "nodes": subgraph_nodes,
            "edges": list(collected_edges.values()),
        }

    def to_networkx(self) -> nx.MultiDiGraph:
        """Build and return a NetworkX directed multigraph from current nodes and edges."""
        G = nx.MultiDiGraph()
        for node in self._nodes.values():
            vec = list(node.vector) if hasattr(node.vector, "__iter__") else node.vector
            G.add_node(
                node.id,
                label=node.label,
                node_type=node.node_type,
                description=node.description,
                jurisdiction=node.jurisdiction,
                metadata=node.metadata_dict,
                vector=vec,
            )

        for edge in self._edges.values():
            rel = edge.relationship_type.value if hasattr(edge.relationship_type, "value") else str(edge.relationship_type)
            G.add_edge(
                edge.source,
                edge.target,
                key=edge.id,
                id=edge.id,
                relationship_type=rel,
                relation_type=rel,
                weight=float(edge.weight),
                weights=float(edge.weight),
                timestamp=edge.timestamp,
                metadata=edge.metadata_dict,
                properties=edge.properties if hasattr(edge, "properties") else {},
            )
        return G

    def find_paths(self, source_id: str, target_id: str, max_depth: int = 5) -> List[List[str]]:
        """Find all simple directed paths between source_id and target_id up to max_depth."""
        G = self.to_networkx()
        if source_id not in G or target_id not in G:
            return []
        try:
            return list(nx.all_simple_paths(G, source=source_id, target=target_id, cutoff=max_depth))
        except Exception as e:
            logger.warning(f"Path search error between {source_id} and {target_id}: {e}")
            return []

    def compute_topology_metrics(self) -> Dict[str, Any]:
        """
        Compute scale-free degree distribution and power-law properties:
        P(k) ~ k^(-gamma)
        Estimates power-law exponent gamma, graph density, and centrality rankings.
        """
        G = self.to_networkx()
        N = len(G.nodes())
        M = len(G.edges())

        if N == 0:
            return {
                "num_nodes": 0,
                "num_edges": 0,
                "density": 0.0,
                "gamma": 0.0,
                "degree_distribution": {},
                "top_in_degree": [],
                "top_out_degree": [],
                "top_betweenness": [],
            }

        degrees = [d for _, d in G.degree()]
        in_degrees = dict(G.in_degree())
        out_degrees = dict(G.out_degree())

        # Power-law exponent estimation via discrete MLE (Clauset et al.)
        non_zero_degrees = [d for d in degrees if d > 0]
        if len(non_zero_degrees) >= 3:
            d_min = min(non_zero_degrees)
            denom = sum(np.log(d / (d_min - 0.5)) for d in non_zero_degrees)
            gamma = 1.0 + len(non_zero_degrees) / denom if denom > 0 else 1.0
        else:
            gamma = 1.0

        betweenness = nx.betweenness_centrality(G) if N > 1 else {nid: 0.0 for nid in G.nodes()}

        top_in = sorted(in_degrees.items(), key=lambda x: x[1], reverse=True)[:10]
        top_out = sorted(out_degrees.items(), key=lambda x: x[1], reverse=True)[:10]
        top_bet = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:10]

        degree_counts = pd.Series(degrees).value_counts().sort_index().to_dict()

        return {
            "num_nodes": N,
            "num_edges": M,
            "density": round(float(nx.density(G)), 4),
            "gamma": round(float(gamma), 3),
            "degree_distribution": {int(k): int(v) for k, v in degree_counts.items()},
            "top_in_degree": [
                {"node_id": k, "label": self._nodes[k].label if k in self._nodes else k, "in_degree": v}
                for k, v in top_in
            ],
            "top_out_degree": [
                {"node_id": k, "label": self._nodes[k].label if k in self._nodes else k, "out_degree": v}
                for k, v in top_out
            ],
            "top_betweenness": [
                {"node_id": k, "label": self._nodes[k].label if k in self._nodes else k, "centrality": round(v, 4)}
                for k, v in top_bet
            ],
        }

    # =========================================================================
    # 4. TEMPORAL TABULAR OPERATIONS & POINT-IN-TIME (AS-OF) QUERIES (Layer 2)
    # =========================================================================

    @_locked_legacy_write
    def add_history_record(
        self,
        record: Union[TabularNodeHistory, Dict[str, Any]]
    ) -> TabularNodeHistory:
        """Append a timestamped state and metric record to tabular history."""
        if isinstance(record, dict):
            rec_obj = TabularNodeHistory(**record)
        elif isinstance(record, TabularNodeHistory):
            rec_obj = record
        else:
            raise TypeError(f"Expected TabularNodeHistory or dict, got {type(record)}")

        self._ensure_history_loaded()
        self._history_tbl.add([rec_obj])
        self._history.append(rec_obj)
        return rec_obj

    @_locked_legacy_write
    def add_history_records(
        self,
        records: List[Union[TabularNodeHistory, Dict[str, Any]]]
    ) -> List[TabularNodeHistory]:
        """Batch append timestamped state records to tabular history."""
        rec_objs = [
            TabularNodeHistory(**r) if isinstance(r, dict) else r
            for r in records
        ]
        if not rec_objs:
            return []

        self._ensure_history_loaded()
        self._history_tbl.add(rec_objs)
        self._history.extend(rec_objs)
        return rec_objs

    @_locked_legacy_write
    def delete_history(self, node_id: str) -> int:
        """Delete historical records for a specific node from LanceDB and cache."""
        initial_count = len([r for r in self._history if r.node_id == node_id])
        escaped_id = node_id.replace("'", "''")
        try:
            self._history_tbl.delete(f"node_id == '{escaped_id}'")
        except Exception as e:
            logger.warning(f"Error deleting history for node {node_id}: {e}")
        self._history = [r for r in self._history if r.node_id != node_id]
        return initial_count

    def count_history(self) -> int:
        """Return total number of records in tabular history."""
        if len(self._history) == 0 and len(self._history_tbl) > 0:
            return self._history_tbl.count_rows()
        return len(self._history)

    def query_state_at(
        self,
        timestamp: Union[str, datetime, int, float],
        node_id: Optional[str] = None,
        metric_name: Optional[str] = None
    ) -> Union[Optional[TabularNodeHistory], Dict[str, TabularNodeHistory]]:
        """
        Query historical tabular state at any point in time $t$.
        Computes point-in-time / as-of state where effective_timestamp <= t.

        - If node_id is provided: returns the single latest TabularNodeHistory for that node
          at or before timestamp $t$ (or None if no state existed yet).
        - If node_id is None: returns a dictionary mapping {node_id: TabularNodeHistory}
          representing the state of all nodes as of timestamp $t$.
        """
        # Sync if memory cache is empty but table has data
        self._ensure_history_loaded()

        t_utc = parse_iso_or_date_to_utc(timestamp)

        # Filter all records whose effective timestamp is <= t_utc
        valid_records = [
            r for r in self._history
            if r.timestamp_utc <= t_utc and (metric_name is None or r.metric_name == metric_name)
        ]

        if node_id:
            node_records = [r for r in valid_records if r.node_id == node_id]
            if not node_records:
                return None
            # Sort ascending and pick last record so tie-breaking exactly matches the all-nodes loop
            sorted_node = sorted(node_records, key=lambda r: r.timestamp_utc)
            return sorted_node[-1]

        # For all nodes: find the latest record for each node_id
        latest_by_node: Dict[str, TabularNodeHistory] = {}
        # Sort chronologically so subsequent records overwrite earlier ones
        sorted_records = sorted(valid_records, key=lambda r: r.timestamp_utc)
        for r in sorted_records:
            latest_by_node[r.node_id] = r

        return latest_by_node

    def query_history_range(
        self,
        node_id: Optional[str] = None,
        start_time: Optional[Union[str, datetime, int, float]] = None,
        end_time: Optional[Union[str, datetime, int, float]] = None,
        metric_name: Optional[str] = None
    ) -> List[TabularNodeHistory]:
        """
        Query historical tabular records within a time range [start_time, end_time],
        optionally filtered by node_id and metric_name.
        Returns records sorted chronologically ascending.
        """
        self._ensure_history_loaded()

        records = list(self._history)

        if node_id:
            records = [r for r in records if r.node_id == node_id]
        if metric_name:
            records = [r for r in records if r.metric_name == metric_name]

        if start_time is not None:
            start_utc = parse_iso_or_date_to_utc(start_time)
            records = [r for r in records if r.timestamp_utc >= start_utc]

        if end_time is not None:
            end_utc = parse_iso_or_date_to_utc(end_time)
            records = [r for r in records if r.timestamp_utc <= end_utc]

        return sorted(records, key=lambda r: r.timestamp_utc)

    def query_latest_state(
        self,
        node_id: Optional[str] = None,
        metric_name: Optional[str] = None
    ) -> Union[Optional[TabularNodeHistory], Dict[str, TabularNodeHistory]]:
        """Retrieve the absolute latest state record for a node or for all nodes."""
        self._ensure_history_loaded()

        records = [
            r for r in self._history
            if metric_name is None or r.metric_name == metric_name
        ]

        if node_id:
            node_records = [r for r in records if r.node_id == node_id]
            if not node_records:
                return None
            sorted_node = sorted(node_records, key=lambda r: r.timestamp_utc)
            return sorted_node[-1]

        latest_by_node: Dict[str, TabularNodeHistory] = {}
        for r in sorted(records, key=lambda x: x.timestamp_utc):
            latest_by_node[r.node_id] = r
        return latest_by_node

    def query_state_transitions(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve state transitions for a node ordered by time,
        recording whenever the categorical state_value changed.
        """
        self._ensure_history_loaded()

        node_records = sorted(
            [r for r in self._history if r.node_id == node_id],
            key=lambda r: r.timestamp_utc
        )
        transitions = []
        last_state = None

        for rec in node_records:
            if rec.state_value != last_state:
                transitions.append({
                    "effective_timestamp": rec.effective_timestamp,
                    "timestamp_utc": rec.timestamp_utc,
                    "from_state": last_state,
                    "to_state": rec.state_value,
                    "metric_name": rec.metric_name,
                    "metric_value": rec.metric_value,
                    "unit": rec.unit,
                    "source_url": rec.source_url,
                    "event_log": rec.event_log_parsed,
                })
                last_state = rec.state_value

        return transitions

    # =========================================================================
    # 5. INTEGRATED DUAL-LAYER SNAPSHOTS & GNN DATASET EXPORT
    # =========================================================================

    def get_node_snapshot(
        self,
        node_id: str,
        as_of: Optional[Union[str, datetime, int, float]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a complete dual-layer snapshot of a node:
        - Layer 1: Topological identity, label, in/out edges, and neighbors.
        - Layer 2: Point-in-time tabular state and metric reading as of time $t$.
        """
        node = self.get_node(node_id)
        if node is None:
            return None

        # Point-in-time state
        state_record = self.query_state_at(as_of, node_id=node_id) if as_of is not None else self.query_latest_state(node_id=node_id)

        return {
            "node": node.to_dict(),
            "in_edges": [e.to_dict() for e in self.get_in_edges(node_id)],
            "out_edges": [e.to_dict() for e in self.get_out_edges(node_id)],
            "neighbors_out": self.get_neighbors(node_id, direction="out"),
            "neighbors_in": self.get_neighbors(node_id, direction="in"),
            "as_of_timestamp": as_of,
            "temporal_state": state_record.to_dict() if state_record is not None else None,
        }

    def export_gnn_dataset(
        self,
        as_of: Optional[Union[str, datetime, int, float]] = None,
        vector_dim: Optional[int] = 16
    ) -> Dict[str, Any]:
        """
        Export dual-layer data structure into numeric matrices ready for downstream GNNs:
        - Node feature matrix X (N x D) combining embeddings, topological degrees, and temporal metrics as of time $t$.
        - Edge index tensor (2 x M)
        - Edge weights and types
        - Node ID to index mapping
        """
        node_ids = list(self._nodes.keys())
        node2idx = {nid: i for i, nid in enumerate(node_ids)}
        N = len(node_ids)

        if N == 0:
            return {"num_nodes": 0, "num_edges": 0, "X": [], "edge_index": [[], []]}

        G = self.to_networkx()
        in_deg = dict(G.in_degree())
        out_deg = dict(G.out_degree())
        max_in = max(in_deg.values()) if in_deg else 1
        max_out = max(out_deg.values()) if out_deg else 1

        node_types = ["LAW", "ACTION", "REACTION", "SECTOR", "COMMODITY", "ECONOMIC_INDEX"]
        type2idx = {t: i for i, t in enumerate(node_types)}

        # Snapshot of states as of time t
        states_as_of: Dict[str, TabularNodeHistory] = {}
        if as_of is not None:
            result = self.query_state_at(as_of)
            if isinstance(result, dict):
                states_as_of = result
        else:
            result = self.query_latest_state()
            if isinstance(result, dict):
                states_as_of = result

        feature_rows = []
        for nid in node_ids:
            node = self._nodes[nid]

            # One-hot node type encoding (6)
            oh = [0.0] * len(node_types)
            nt = node.node_type.upper()
            if nt in type2idx:
                oh[type2idx[nt]] = 1.0

            # Degree topological features (2)
            deg_feats = [
                float(in_deg.get(nid, 0)) / max(max_in, 1),
                float(out_deg.get(nid, 0)) / max(max_out, 1),
            ]

            # Point-in-time tabular metric reading (1)
            metric_val = 0.0
            if nid in states_as_of and states_as_of[nid] is not None:
                metric_val = float(states_as_of[nid].metric_value)

            # Vector embedding slice or full 768 representation
            if vector_dim is None:
                emb_slice = list(node.vector) if hasattr(node.vector, "__iter__") else [0.0] * 768
            else:
                emb_slice = list(node.vector[:vector_dim]) if hasattr(node.vector, "__iter__") else [0.0] * vector_dim

            row = oh + deg_feats + [metric_val] + emb_slice
            feature_rows.append(row)

        X = np.array(feature_rows, dtype=np.float32)

        # Directed edge index
        edge_sources = []
        edge_targets = []
        edge_weights = []
        edge_types = []

        all_relations = [
            EdgeRelation.TRIGGERS.value,
            EdgeRelation.MANDATES.value,
            EdgeRelation.REACTS_TO.value,
            EdgeRelation.AFFECTS_COST_OF.value,
            EdgeRelation.AMENDS.value,
            "REGULATES",
            "IMPACTS",
        ]
        rel2idx = {rel: i for i, rel in enumerate(all_relations)}

        for edge in self._edges.values():
            if edge.source in node2idx and edge.target in node2idx:
                edge_sources.append(node2idx[edge.source])
                edge_targets.append(node2idx[edge.target])
                edge_weights.append(float(edge.weight))
                edge_types.append(rel2idx.get(edge.relationship_type, 0))

        edge_index = np.array([edge_sources, edge_targets], dtype=np.int64)

        return {
            "num_nodes": N,
            "num_edges": len(edge_sources),
            "feature_dim": X.shape[1],
            "node_mapping": node2idx,
            "node_ids": node_ids,
            "X": X.tolist(),
            "edge_index": edge_index.tolist(),
            "edge_weights": edge_weights,
            "edge_types": edge_types,
        }

    # =========================================================================
    # 5B. CANONICAL INGESTION & AS-OF CONTRACTS (Task 3)
    # =========================================================================

    def redundant_evidence_ids(self) -> set:
        """DIRECT observation links that repeat an earlier archived release within the same batch.

        Within one batch all links share eligibility, and public availability only depends on the earliest
        evidenced document, so dropping the later ones changes no as-of read (vintage backfills linked
        every period to every vintage that repeats it).
        """
        earliest: Dict[tuple, Any] = {}
        redundant = set()
        for link in self._canonical_evidence.values():
            doc = self._canonical_documents.get(link.document_version_id)
            if (link.assertion_kind != "OBSERVATION" or link.support_type != "DIRECT" or doc is None
                    or doc.availability_basis != "archived_release"):
                continue
            key = (link.assertion_version_id, link.batch_id, doc.source_id, doc.data_origin)
            best = earliest.get(key)
            if best is None:
                earliest[key] = (link, doc)
                continue
            keep, drop = ((link, best[0]) if self._utc(doc.available_at) < self._utc(best[1].available_at)
                          else (best[0], link))
            redundant.add(drop.evidence_id)
            if keep is link:
                earliest[key] = (link, doc)
        return redundant

    def compact_redundant_evidence(self) -> int:
        """Remove `redundant_evidence_ids()` through the normal pinned-snapshot save; returns the count."""
        with self._canonical_mutation():
            redundant = self.redundant_evidence_ids()
            if redundant:
                self._canonical_evidence = {k: v for k, v in self._canonical_evidence.items() if k not in redundant}
                self._generation += 1  # readers holding the old snapshot reload
                self._persist_generation()
                self._save_canonical()
        return len(redundant)

    def acquire_writer_lock(self) -> _WriterLock:
        """Acquire single-writer execution lock. Competing writers are rejected."""
        return _WriterLock(self._lock_file)

    @contextmanager
    def _canonical_mutation(self):
        with self.acquire_writer_lock():
            self._check_stale_instance()
            if not self._state:
                self._save_canonical()
            snapshots = {name: dict(getattr(self, f"_canonical_{name}"))
                         for name, *_ in self._canonical_tables()}
            snapshots["batches"] = {k: v.model_copy(deep=True) for k, v in self._canonical_batches.items()}
            previous_generation, previous_state = self._generation, self._state
            previous_nodes, previous_edges = dict(self._nodes), dict(self._edges)
            previous_history, previous_history_loaded = list(self._history), self._history_loaded
            try:
                yield
            except BaseException:
                for name, records in snapshots.items():
                    setattr(self, f"_canonical_{name}", records)
                self._generation, self._state = previous_generation, previous_state
                self._nodes, self._edges = previous_nodes, previous_edges
                self._history, self._history_loaded = previous_history, previous_history_loaded
                self._observations_loaded = False
                self._pin_legacy_tables()
                raise

    @staticmethod
    def _record_content(record):
        content = record.model_dump(mode="json", exclude={"batch_id", "recorded_at", "fetched_at", "created_at", "available_at"})
        for key, value in list(content.items()):
            if key.endswith("_json") and isinstance(value, str):
                try:
                    content[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
            elif isinstance(value, str) and len(value) <= 35 and any(term in key for term in ("date", "time", "_at", "_from", "_to", "period", "timestamp", "valid", "available")):
                try:
                    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    content[key] = dt.isoformat()
                except (ValueError, OSError, OverflowError):
                    pass
        return content

    def _insert_immutable(self, target, records, key):
        for record in records:
            identity = getattr(record, key)
            previous = target.get(identity)
            if previous is not None:
                if self._record_content(previous) != self._record_content(record):
                    raise ValueError(f"Conflicting content for immutable record {identity}")
            else:
                target[identity] = record.model_copy(deep=True)

    def register_entities(self, entities: List[Entity]) -> None:
        with self._canonical_mutation():
            self._insert_immutable(self._canonical_entities, entities, "entity_id")
            self._save_canonical()

    def register_series(self, series: List[SeriesDefinition]) -> None:
        with self._canonical_mutation():
            self._insert_immutable(self._canonical_series, series, "series_id")
            self._save_canonical()

    def register_aliases(self, aliases: List[EntityAlias]) -> None:
        with self._canonical_mutation():
            self._insert_immutable(self._canonical_aliases, aliases, "alias_id")
            self._save_canonical()

    def stage_batch(
        self, batch: BatchManifest, documents: List[SourceDocument], facts: List[Fact],
        observations: List[Observation], evidence_links: Optional[List[EvidenceLink]] = None,
        entities: Optional[List[Entity]] = None, series: Optional[List[SeriesDefinition]] = None,
    ) -> None:
        """Durably stage validated references; exact replay never changes original ownership."""
        from src.schemas.ingestion import ALLOWED_RELATION_ENDPOINTS
        entities, series, evidence_links = entities or [], series or [], evidence_links or []
        for link in evidence_links:
            if link.batch_id and link.batch_id != batch.batch_id:
                raise ValueError("Evidence batch_id does not match staging batch")
        evidence_links = [link.model_copy(update={"batch_id": batch.batch_id}) for link in evidence_links]
        series = [s.model_copy(update={"batch_id": batch.batch_id}) if not getattr(s, "batch_id", None) else s for s in series]
        with self._canonical_mutation():
            self._ensure_observations_loaded()
            committed = {key for key, b in self._canonical_batches.items() if b.status == "committed"}
            fact_map = {key: row for key, row in self._canonical_facts.items() if row.batch_id in committed}
            obs_map = {key: row for key, row in self._canonical_observations.items() if row.batch_id in committed}
            fact_map.update((row.fact_version_id, row) for row in facts)
            obs_map.update((row.revision_id, row) for row in observations)
            doc_map = {key: row for key, row in self._canonical_documents.items() if row.batch_id in committed}
            doc_map.update((row.version_id, row) for row in documents)
            normalized_evidence = []
            for link in evidence_links:
                if link.assertion_version_id in fact_map and link.assertion_version_id in obs_map:
                    raise ValueError("Evidence assertion ID is ambiguous between fact and observation")
                expected_kind = "OBSERVATION" if link.assertion_version_id in obs_map else "FACT"
                assertion = (obs_map if expected_kind == "OBSERVATION" else fact_map).get(link.assertion_version_id)
                doc = doc_map.get(link.document_version_id)
                if assertion is None or doc is None:
                    raise ValueError("Evidence refers to a missing or uncommitted document or assertion")
                if "assertion_kind" in link.model_fields_set and link.assertion_kind != expected_kind:
                    raise ValueError("Evidence assertion_kind does not match its assertion")
                if assertion.data_origin != doc.data_origin or assertion.data_origin != batch.data_origin:
                    raise ValueError("Evidence document/assertion origin does not match staging batch origin")
                normalized_evidence.append(link.model_copy(update={"assertion_kind": expected_kind}))
            evidence_links = normalized_evidence
            groups = (("documents", "version_id", documents), ("facts", "fact_version_id", facts),
                      ("observations", "revision_id", observations), ("entities", "entity_id", entities),
                      ("series", "series_id", series), ("evidence", "evidence_id", evidence_links))
            payload = {name: sorted((self._record_content(r) for r in records),
                                   key=lambda row: json.dumps(row, sort_keys=True))
                       for name, key, records in groups}
            payload["batch"] = {"source_id": batch.source_id, "data_origin": batch.data_origin}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            existing = self._canonical_batches.get(batch.batch_id)
            if existing:
                if existing.manifest_sha256 == digest:
                    return
                raise ValueError(f"Cannot restage or reuse committed batch ID or staged batch ID {batch.batch_id!r} with conflicting records")
            for record in documents + facts + observations + entities:
                if record.batch_id != batch.batch_id:
                    raise ValueError("Record batch_id does not match the staging batch")
                if getattr(record, "data_origin", None) and record.data_origin != batch.data_origin:
                    raise ValueError(f"Record data_origin ({record.data_origin}) does not match staging batch data_origin ({batch.data_origin})")
            committed = {key for key, b in self._canonical_batches.items() if b.status == "committed"}
            known_docs = {key for key, doc in self._canonical_documents.items() if doc.batch_id in committed}
            known_docs.update(doc.version_id for doc in documents)
            assertions = {f.fact_version_id for f in facts} | {o.revision_id for o in observations}
            assertions.update(f.fact_version_id for f in self._canonical_facts.values() if f.batch_id in committed)
            assertions.update(o.revision_id for o in self._canonical_observations.values() if o.batch_id in committed)
            for link in evidence_links:
                if link.document_version_id not in known_docs or link.assertion_version_id not in assertions:
                    raise ValueError("Evidence refers to a missing or uncommitted document or assertion")

            endpoints = {key for key, ent in self._canonical_entities.items()
                         if ent.batch_id in committed or ent.batch_id == batch.batch_id or not getattr(ent, "batch_id", None)}
            endpoints.update(self._nodes.keys() - self._canonical_entities.keys())
            endpoints.update(ent.entity_id for ent in entities)
            endpoints.update(sid for sid, s in self._canonical_series.items()
                             if getattr(s, "batch_id", None) in committed or getattr(s, "batch_id", None) is None or getattr(s, "batch_id", None) == batch.batch_id)
            endpoints.update(item.series_id for item in series)
            endpoints.update(f.fact_id for f in self._canonical_facts.values() if f.batch_id in committed)
            endpoints.update(f.fact_id for f in facts)

            from src.schemas.ingestion import ALLOWED_RELATION_ENDPOINTS, resolve_entity_type

            entity_types = {}
            for k, ent in self._canonical_entities.items():
                if ent.batch_id in committed:
                    entity_types[k] = getattr(ent, "entity_type", None)
            for ent in entities:
                entity_types[ent.entity_id] = getattr(ent, "entity_type", None)

            evidence_assertions = {link.assertion_version_id for link in evidence_links if link.support_type == "DIRECT"}
            for link in self._canonical_evidence.values():
                assertion = fact_map.get(link.assertion_version_id) or obs_map.get(link.assertion_version_id)
                doc = doc_map.get(link.document_version_id)
                if (assertion is not None and doc is not None and link.batch_id in committed
                        and doc.data_origin == assertion.data_origin == batch.data_origin and link.support_type == "DIRECT"):
                    evidence_assertions.add(link.assertion_version_id)

            for f in facts:
                if f.subject_id not in endpoints or (f.object_id and f.object_id not in endpoints):
                    raise ValueError(f"Fact {f.fact_id} refers to a missing or uncommitted endpoint: subject={f.subject_id}, object={f.object_id}")
                if f.predicate not in ALLOWED_RELATION_ENDPOINTS:
                    raise ValueError(f"Fact {f.fact_id} has unsupported predicate/relation: {f.predicate!r}")
                rule = ALLOWED_RELATION_ENDPOINTS[f.predicate]
                if not rule.get("allow_null_object", False) and not f.object_id:
                    raise ValueError(f"Fact {f.fact_id} with predicate {f.predicate} requires non-null object_id")

                subj_type = entity_types.get(f.subject_id) or resolve_entity_type(f.subject_id, f.payload_json, role="subject", predicate=f.predicate)
                obj_type = entity_types.get(f.object_id) or resolve_entity_type(f.object_id, f.payload_json, role="object", predicate=f.predicate) if f.object_id else None
                if subj_type and subj_type not in rule["subject_types"]:
                    raise ValueError(f"Fact {f.fact_id} subject type {subj_type} not in allowed subject types {rule['subject_types']} for {f.predicate}")
                if obj_type and obj_type not in rule["object_types"]:
                    raise ValueError(f"Fact {f.fact_id} object type {obj_type} not in allowed object types {rule['object_types']} for {f.predicate}")

                if f.quality_state == "accepted" and not f.is_retracted:
                    if f.fact_version_id not in evidence_assertions:
                        raise ValueError(f"Accepted fact {f.fact_id} ({f.fact_version_id}) has no supporting document or evidence link")

            available_series_map = {s.series_id: s for s in series}
            for sid, s in self._canonical_series.items():
                s_batch = getattr(s, "batch_id", None)
                if s_batch in committed or s_batch is None:
                    available_series_map[sid] = s

            for obs in observations:
                if obs.series_id not in available_series_map:
                    raise ValueError(f"Observation refers to unregistered or uncommitted series: {obs.series_id}")
                s_def = available_series_map[obs.series_id]
                if obs.unit != s_def.unit:
                    raise ValueError(f"Observation unit ({obs.unit}) does not match series definition unit ({s_def.unit}) for {obs.series_id}")
                if obs.period_start >= obs.period_end_exclusive:
                    raise ValueError(f"Observation period_start ({obs.period_start}) must be strictly earlier than period_end_exclusive ({obs.period_end_exclusive})")
                if obs.value is not None:
                    if not math.isfinite(obs.value):
                        raise ValueError(f"Observation value must be finite, got {obs.value}")
                elif not obs.missing_reason and not obs.is_retracted:
                    raise ValueError("Observation with null value must have explicit missing_reason")
                if obs.quality_state == "accepted" and not obs.is_retracted:
                    if obs.revision_id not in evidence_assertions:
                        raise ValueError(f"Accepted observation {obs.observation_id} ({obs.revision_id}) has no supporting document or evidence link")
            for name, key, records in groups:
                self._insert_immutable(getattr(self, f"_canonical_{name}"), records, key)
            record = batch.model_copy(deep=True)
            record.status = "staged"
            record.staged_at = batch.staged_at or datetime.now(timezone.utc)
            record.staged_count = len(documents) + len(facts) + len(observations)
            record.manifest_sha256 = digest
            self._canonical_batches[batch.batch_id] = record
            self._save_canonical()

    def commit_batch(self, batch_id: str, committed_at: Optional[datetime] = None) -> None:
        with self._canonical_mutation():
            if batch_id not in self._canonical_batches:
                raise ValueError(f"Batch {batch_id} not found in staged batches")
            batch = self._canonical_batches[batch_id]
            if batch.status == "committed":
                return
            if batch.status != "staged":
                raise ValueError(f"Cannot commit batch in status {batch.status!r}")
            self._ensure_observations_loaded()
            now = committed_at or getattr(batch, "staged_at", None) or datetime.now(timezone.utc)
            batch.status = "committed"
            batch.committed_at = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
            batch.committed_count = sum(f.batch_id == batch_id for f in self._canonical_facts.values())
            batch.committed_count += sum(o.batch_id == batch_id for o in self._canonical_observations.values())
            self._generation += 1
            self._refresh_canonical_projection()
            self._persist_generation()
            self._save_canonical()

    @staticmethod
    def _utc(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def _eligible_batches(self, cutoff, mode):
        return {key for key, batch in self._canonical_batches.items()
                if batch.status == "committed" and
                (mode == "public_as_of" or (batch.committed_at is not None and self._utc(batch.committed_at) <= cutoff))}

    def _definition_available(self, definition, mode):
        """Public availability of a series definition: no later than its earliest release-evidenced observation.

        A measurement released on day T proves its definition was public on T. Only archived-release or
        provider-vintage documents count, so fetch-dated data never moves a definition. System-as-of keeps
        the registration time, i.e. when this system learned it.
        """
        registered = self._available(definition)
        if mode != "public_as_of":
            return registered
        key = (self._generation, len(self._canonical_evidence))
        if getattr(self, "_release_cache_key", None) != key:
            self._ensure_observations_loaded()
            committed = {k for k, b in self._canonical_batches.items() if b.status == "committed"}
            earliest = {}
            for link in self._canonical_evidence.values():
                obs = self._canonical_observations.get(link.assertion_version_id)
                doc = self._canonical_documents.get(link.document_version_id)
                if (obs is None or doc is None or link.support_type != "DIRECT"
                        or doc.availability_basis not in ("archived_release", "provider_vintage")
                        or link.batch_id not in committed or doc.batch_id not in committed or obs.batch_id not in committed):
                    continue
                when = max(self._available(obs), self._available(link), self._available(doc))
                if obs.series_id not in earliest or when < earliest[obs.series_id]:
                    earliest[obs.series_id] = when
            self._release_cache_key, self._release_earliest = key, earliest
        released = self._release_earliest.get(definition.series_id)
        return min(registered, released) if released is not None else registered

    def _available(self, record):
        if getattr(record, "available_at", None):
            return self._utc(record.available_at)
        if hasattr(record, "document_version_id") and record.document_version_id in self._canonical_documents:
            doc = self._canonical_documents[record.document_version_id]
            if getattr(doc, "available_at", None):
                return self._utc(doc.available_at)
        if hasattr(record, "revision_id") or hasattr(record, "fact_version_id"):
            v_id = getattr(record, "revision_id", None) or getattr(record, "fact_version_id", None)
            for ev in self._canonical_evidence.values():
                if ev.assertion_version_id == v_id and ev.available_at:
                    return self._utc(ev.available_at)
        if isinstance(record, SeriesDefinition):
            if record.available_at:
                return self._utc(record.available_at)
            if record.batch_id and record.batch_id in self._canonical_batches:
                batch_docs = [d for d in self._canonical_documents.values()
                              if d.batch_id == record.batch_id and getattr(d, "available_at", None)]
                if batch_docs:
                    return min(self._utc(d.available_at) for d in batch_docs)
                batch_obs = [o for o in self._canonical_observations.values()
                             if o.batch_id == record.batch_id and getattr(o, "available_at", None)]
                if batch_obs:
                    return min(self._utc(o.available_at) for o in batch_obs)
                batch = self._canonical_batches[record.batch_id]
                if getattr(batch, "committed_at", None):
                    return self._utc(batch.committed_at)
                if getattr(batch, "staged_at", None):
                    return self._utc(batch.staged_at)
            if getattr(record, "batch_id", None) is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if "recorded_at" in getattr(record, "model_fields_set", set()):
                return self._utc(record.recorded_at)
            return datetime.max.replace(tzinfo=timezone.utc)
        if isinstance(record, Entity):
            if getattr(record, "created_at", None):
                return self._utc(record.created_at)
            if record.batch_id and record.batch_id in self._canonical_batches:
                batch_docs = [d for d in self._canonical_documents.values()
                              if d.batch_id == record.batch_id and getattr(d, "available_at", None)]
                if batch_docs:
                    return min(self._utc(d.available_at) for d in batch_docs)
                batch = self._canonical_batches[record.batch_id]
                if getattr(batch, "committed_at", None):
                    return self._utc(batch.committed_at)
                if getattr(batch, "staged_at", None):
                    return self._utc(batch.staged_at)
            if getattr(record, "batch_id", None) is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            return datetime.max.replace(tzinfo=timezone.utc)
        if getattr(record, "batch_id", None) and record.batch_id in self._canonical_batches:
            batch = self._canonical_batches[record.batch_id]
            if getattr(batch, "committed_at", None):
                return self._utc(batch.committed_at)
            if getattr(batch, "staged_at", None):
                return self._utc(batch.staged_at)
        if getattr(record, "recorded_at", None):
            return self._utc(record.recorded_at)
        return datetime.max.replace(tzinfo=timezone.utc)

    def _latest_facts(self, cutoff, mode, eligible):
        latest = {}
        for fact in self._canonical_facts.values():
            if fact.batch_id not in eligible or self._available(fact) > cutoff:
                continue
            if mode == "system_as_of" and self._utc(fact.recorded_at) > cutoff:
                continue
            key = (self._available(fact), self._utc(fact.recorded_at), fact.fact_version_id)
            previous = latest.get(fact.fact_id)
            if previous is None or key > previous[0]:
                latest[fact.fact_id] = (key, fact)
        return [fact for _, fact in latest.values() if not fact.is_retracted and fact.quality_state == "accepted"]

    def _evidence_for_record(self, record, cutoff, mode, eligible):
        """Use the same provenance boundary for graph, observations and public readers."""
        kind = "OBSERVATION" if isinstance(record, Observation) else "FACT"
        version = record.revision_id if kind == "OBSERVATION" else record.fact_version_id
        index_key = (self._generation, id(self._canonical_evidence), len(self._canonical_evidence))
        if getattr(self, "_evidence_index_key", None) != index_key:
            evidence_by_assertion = {}
            for link in self._canonical_evidence.values():
                evidence_by_assertion.setdefault(
                    (link.assertion_kind, link.assertion_version_id), []
                ).append(link)
            self._evidence_index_key = index_key
            self._evidence_by_assertion = evidence_by_assertion
        result = []
        for link in self._evidence_by_assertion.get((kind, version), ()):
            doc = self._canonical_documents.get(link.document_version_id)
            owner = self._canonical_batches.get(link.batch_id)
            if (link.support_type != "DIRECT" or link.batch_id not in eligible
                    or doc is None or doc.batch_id not in eligible
                    or doc.data_origin != record.data_origin
                    or owner is None or owner.data_origin != record.data_origin):
                continue
            if self._available(link) > cutoff or self._available(doc) > cutoff:
                continue
            if mode == "system_as_of" and (self._utc(link.recorded_at) > cutoff or self._utc(doc.recorded_at) > cutoff):
                continue
            result.append(link)
        return result

    def evidence_for(self, record, cutoff: datetime, mode: str = "public_as_of") -> List[EvidenceLink]:
        if mode not in ("public_as_of", "system_as_of"):
            raise ValueError(f"Invalid mode: {mode!r}")
        cutoff = self._utc(cutoff)
        return [link.model_copy(deep=True) for link in self._evidence_for_record(record, cutoff, mode, self._eligible_batches(cutoff, mode))]

    def documents_for(self, record, cutoff: datetime, mode: str = "public_as_of") -> List[SourceDocument]:
        ids = {link.document_version_id for link in self.evidence_for(record, cutoff, mode)}
        return [self._canonical_documents[key].model_copy(deep=True) for key in sorted(ids)]

    def query_facts(self, cutoff: datetime, mode: str = "public_as_of", *, include_expired: bool = False) -> List[Fact]:
        """Latest committed nonretracted facts with eligible supporting evidence."""
        if mode not in ("public_as_of", "system_as_of"):
            raise ValueError(f"Invalid mode: {mode!r}")
        cutoff = self._utc(cutoff)
        eligible = self._eligible_batches(cutoff, mode)
        # Shallow copies suffice: every Fact field is an immutable scalar (str, datetime, bool, float).
        return [fact.model_copy() for fact in self._latest_facts(cutoff, mode, eligible)
                if (include_expired or not fact.valid_to or self._utc(fact.valid_to) > cutoff)
                and self._evidence_for_record(fact, cutoff, mode, eligible)]

    def query_series(self, cutoff: datetime, mode: str = "public_as_of") -> List[SeriesDefinition]:
        """Definitions are subject to the same committed, temporal boundary as measurements."""
        if mode not in ("public_as_of", "system_as_of"):
            raise ValueError(f"Invalid mode: {mode!r}")
        cutoff = self._utc(cutoff)
        eligible = self._eligible_batches(cutoff, mode)
        return [definition.model_copy(deep=True) for definition in self._canonical_series.values()
                if (definition.batch_id is None or definition.batch_id in eligible)
                and self._definition_available(definition, mode) <= cutoff
                and (mode == "public_as_of" or (self._utc(definition.recorded_at) if "recorded_at" in getattr(definition, "model_fields_set", set()) else self._available(definition)) <= cutoff)]

    def query_observation_revisions(self, cutoff: datetime, mode: str = "public_as_of",
                                    series_ids: Optional[Iterable[str]] = None) -> List[Observation]:
        """Eligible release history (optionally only `series_ids`); a withdrawn logical observation revokes its old labels."""
        if mode not in ("public_as_of", "system_as_of"):
            raise ValueError(f"Invalid mode: {mode!r}")
        cutoff = self._utc(cutoff)
        self._ensure_observations_loaded()
        eligible = self._eligible_batches(cutoff, mode)
        definitions = {s.series_id for s in self.query_series(cutoff, mode)}
        wanted = set(series_ids) if series_ids is not None else None
        visible = [o for o in self._canonical_observations.values()
                   if (wanted is None or o.series_id in wanted) and o.batch_id in eligible and self._available(o) <= cutoff
                   and (mode == "public_as_of" or self._utc(o.recorded_at) <= cutoff)]
        latest = {}
        for obs in visible:
            key = (self._available(obs), self._utc(obs.recorded_at), obs.revision_id)
            if obs.observation_id not in latest or key > latest[obs.observation_id][0]:
                latest[obs.observation_id] = (key, obs)
        withdrawn = {key for key, (_, obs) in latest.items() if obs.is_retracted or obs.quality_state != "accepted"}
        revoked = {obs.supersedes_revision_id for obs in visible if obs.is_retracted}
        return [o.model_copy(deep=True) for o in visible
                if o.observation_id not in withdrawn and o.revision_id not in revoked
                and not o.is_retracted and o.quality_state == "accepted"
                and o.series_id in definitions and o.period_start < o.period_end_exclusive
                and o.value is not None and math.isfinite(o.value)
                and self._evidence_for_record(o, cutoff, mode, eligible)]

    def query_observations(self, cutoff: datetime, mode: str = "public_as_of",
                           entity_ids: Optional[List[str]] = None, series_ids: Optional[Iterable[str]] = None) -> pa.Table:
        """Read accepted latest revisions from this instance's pinned committed snapshot (optionally only `series_ids`)."""
        if mode not in ("public_as_of", "system_as_of"):
            raise ValueError(f"Invalid mode: {mode!r}")
        cutoff = self._utc(cutoff)
        self._ensure_observations_loaded()
        eligible = self._eligible_batches(cutoff, mode)
        latest = {}
        wanted = set(series_ids) if series_ids is not None else None
        for obs in self._canonical_observations.values():
            if wanted is not None and obs.series_id not in wanted:
                continue
            if obs.batch_id not in eligible or self._available(obs) > cutoff:
                continue
            if mode == "system_as_of" and self._utc(obs.recorded_at) > cutoff:
                continue
            key = (self._available(obs), self._utc(obs.recorded_at), obs.revision_id)
            previous = latest.get(obs.observation_id)
            if previous is None or key > previous[0]:
                latest[obs.observation_id] = (key, obs)
        records = [obs for _, obs in latest.values()
                   if not obs.is_retracted and obs.quality_state == "accepted"]
        records = [obs for obs in records if self._evidence_for_record(obs, cutoff, mode, eligible)]
        valid_records = []
        for obs in records:
            if obs.period_start >= obs.period_end_exclusive:
                continue
            if obs.value is not None and not math.isfinite(obs.value):
                continue
            s_def = self._canonical_series.get(obs.series_id)
            if s_def is None and obs.series_id not in self._canonical_series:
                continue
            if s_def is not None and (
                (s_def.batch_id is not None and s_def.batch_id not in eligible)
                or self._definition_available(s_def, mode) > cutoff
                or (mode == "system_as_of" and (self._utc(s_def.recorded_at) if "recorded_at" in getattr(s_def, "model_fields_set", set()) else self._available(s_def)) > cutoff)
            ):
                continue
            valid_records.append(obs)
        records = valid_records
        if entity_ids is not None:
            targets = set(entity_ids)
            matches = set(targets)
            for sid, definition in self._canonical_series.items():
                if ((definition.batch_id is not None and definition.batch_id not in eligible)
                        or self._definition_available(definition, mode) > cutoff
                        or (mode == "system_as_of" and (self._utc(definition.recorded_at) if "recorded_at" in getattr(definition, "model_fields_set", set()) else self._available(definition)) > cutoff)
                        or ("recorded_at" in getattr(definition, "model_fields_set", set()) and self._utc(definition.recorded_at) > cutoff)):
                    continue
                geo = definition.geography_id
                if geo and (geo in targets or f"geo:{geo}" in targets):
                    matches.add(sid)
            for fact in self.query_facts(cutoff, mode):
                if fact.predicate != "MEASURES":
                    continue
                if fact.valid_from and self._utc(fact.valid_from) > cutoff:
                    continue
                if fact.valid_to and self._utc(fact.valid_to) <= cutoff:
                    continue
                payload = json.loads(fact.payload_json) if fact.payload_json else {}
                if fact.object_id in targets or (payload.get("subject_kind") == "edge" and payload.get("subject_id") in targets):
                    matches.add(fact.subject_id)
            records = [obs for obs in records if obs.series_id in matches]
        return pa.Table.from_pylist([obs.model_dump() for obs in records], schema=OBSERVATIONS_SCHEMA)

    def project_graph(self, cutoff: datetime, mode: str = "public_as_of") -> Dict[str, Any]:
        """Project latest versions, then apply retractions, quality and validity intervals."""
        if mode not in ("public_as_of", "system_as_of"):
            raise ValueError(f"Invalid mode: {mode!r}")
        cutoff = self._utc(cutoff)
        eligible = self._eligible_batches(cutoff, mode)
        edges, referenced = [], set()
        for fact in self.query_facts(cutoff, mode, include_expired=True):
            if fact.subject_id.startswith("series:"):
                s_def = self._canonical_series.get(fact.subject_id)
                if s_def and getattr(s_def, "batch_id", None) and s_def.batch_id not in eligible:
                    continue
            if fact.object_id and fact.object_id.startswith("series:"):
                s_def = self._canonical_series.get(fact.object_id)
                if s_def and getattr(s_def, "batch_id", None) and s_def.batch_id not in eligible:
                    continue

            valid_from = self._utc(fact.valid_from) if fact.valid_from else None
            valid_to = self._utc(fact.valid_to) if fact.valid_to else None
            status = "upcoming" if valid_from and valid_from > cutoff else "active"
            if valid_to and valid_to <= cutoff:
                status = "expired"
            evidence = self._evidence_for_record(fact, cutoff, mode, eligible)
            if fact.quality_state == "accepted" and not evidence:
                continue
            edges.append({
                "edge_id": fact.fact_id, "fact_version_id": fact.fact_version_id,
                "source_id": fact.subject_id, "target_id": fact.object_id, "relation_type": fact.predicate,
                "valid_from": valid_from.isoformat() if valid_from else None,
                "valid_to": valid_to.isoformat() if valid_to else None,
                "is_active": status == "active", "status": status, "claim_kind": fact.claim_kind,
                "quality_state": fact.quality_state, "payload": fact.payload_json,
                "provider_source_id": fact.source_id, "available_at": self._available(fact).isoformat(),
                "recorded_at": self._utc(fact.recorded_at).isoformat(), "batch_id": fact.batch_id,
                "data_origin": fact.data_origin, "evidence_ids": [link.evidence_id for link in evidence],
                "document_version_ids": sorted({link.document_version_id for link in evidence}),
            })
            referenced.add(fact.subject_id)
            if fact.object_id:
                referenced.add(fact.object_id)
        node_ids = set(referenced)
        for key, ent in self._canonical_entities.items():
            if ent.batch_id in eligible and self._utc(ent.created_at) <= cutoff:
                node_ids.add(key)
        # Measured series are graph nodes under their ingested series_id, so targets and features resolve.
        series = {d.series_id: d for d in self.query_series(cutoff, mode)}
        node_ids.update(series)
        nodes = []
        for key in sorted(node_ids):
            ent = self._canonical_entities.get(key)
            legacy = self._nodes.get(key)
            s_def = series.get(key) if ent is None else None
            if s_def is not None:
                nodes.append({"id": key, "label": s_def.metric_name, "node_type": "SERIES",
                              "node_subtype": s_def.measurement_kind, "jurisdiction": s_def.geography_id})
                continue
            nodes.append({"id": key, "label": ent.canonical_label if ent else legacy.label if legacy else key,
                          "node_type": ent.entity_type if ent else legacy.node_type if legacy else "UNKNOWN",
                          "node_subtype": ent.entity_subtype if ent else None,
                          "jurisdiction": ent.jurisdiction if ent else getattr(legacy, "jurisdiction", None)})
        return {"nodes": nodes, "edges": edges,
                "metadata": {"cutoff": cutoff.isoformat(), "mode": mode, "generation": self._generation,
                             "snapshot_revision": self._state.get("revision", 0),
                             "node_count": len(nodes), "edge_count": len(edges),
                             "active_edge_count": sum(edge["is_active"] for edge in edges)}}

    # =========================================================================
    # 6. DATAFRAME & PERSISTENCE HELPERS
    # =========================================================================


    def get_nodes_df(self) -> pd.DataFrame:
        """Return nodes as a pandas DataFrame."""
        data = [n.to_dict() for n in self._nodes.values()]
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["id", "label", "node_type", "metadata"])

    def get_edges_df(self) -> pd.DataFrame:
        """Return edges as a pandas DataFrame."""
        data = [e.to_dict() for e in self._edges.values()]
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["source", "target", "relationship_type", "weight"])

    def get_history_df(self, node_id: Optional[str] = None) -> pd.DataFrame:
        """Return tabular history as a chronologically sorted pandas DataFrame."""
        self._ensure_history_loaded()
        records = [r for r in self._history if r.node_id == node_id] if node_id else list(self._history)
        data = [r.to_dict() for r in records]
        if not data:
            return pd.DataFrame(columns=["record_id", "node_id", "effective_timestamp", "timestamp", "state_value", "metric_value"])
        df = pd.DataFrame(data)
        if "effective_timestamp" in df.columns and "timestamp" not in df.columns:
            df["timestamp"] = df["effective_timestamp"]
        if "timestamp" in df.columns and "effective_timestamp" not in df.columns:
            df["effective_timestamp"] = df["timestamp"]
        if "timestamp_utc" in df.columns:
            df = df.sort_values("timestamp_utc", ascending=True)
        return df

    def get_history(self, node_id: Optional[str] = None) -> List[TabularNodeHistory]:
        """Return tabular history as a list of TabularNodeHistory objects."""
        self._ensure_history_loaded()
        if node_id:
            return [r for r in self._history if r.node_id == node_id]
        return list(self._history)


    def save(self) -> None:
        """Persist under the same writer boundary as ingestion."""
        with self._canonical_mutation():
            self._save_legacy_views()
            self._save_canonical()

    def _save_legacy_views(self) -> None:
        """Export compatibility views; canonical reads never depend on these files."""
        nodes_df = self.get_nodes_df().copy()
        edges_df = self.get_edges_df().copy()
        history_df = self.get_history_df().copy()

        # Sanitize dict/list columns to JSON strings so PyArrow can write cleanly to Parquet
        for col in ["metadata", "properties"]:
            if col in nodes_df.columns:
                nodes_df[col] = nodes_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else ("{}" if pd.isna(x) else str(x)))
            if col in edges_df.columns:
                edges_df[col] = edges_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else ("{}" if pd.isna(x) else str(x)))
        for col in ["metadata", "event_log"]:
            if col in history_df.columns:
                history_df[col] = history_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else ("{}" if pd.isna(x) else str(x)))

        # Parquet backup
        nodes_df.to_parquet(self.storage_dir / "graph_nodes.parquet", index=False)
        edges_df.to_parquet(self.storage_dir / "graph_edges.parquet", index=False)
        if len(history_df) > 0 or not (self.storage_dir / "node_tabular_history.parquet").exists():
            history_df.to_parquet(self.storage_dir / "node_tabular_history.parquet", index=False)

    def load(self) -> None:
        """Reload state from storage."""
        self._nodes.clear()
        self._edges.clear()
        self._history.clear()
        self._history_loaded = False
        self._observations_loaded = False
        self._state = self._read_canonical_state()
        self._generation = self._get_disk_generation()
        self._load_from_storage()
        self._load_canonical_from_storage()
        self._refresh_canonical_projection()


# Class Aliases for seamless drop-in interoperability
DualLayerStorage = DualLayerStore
DualLayerGraphStore = DualLayerStore
DualLayerStorageEngine = DualLayerStore
