"""SQLite control-plane ledger for recoverable ingestion jobs."""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


class JobLeaseError(RuntimeError):
    """Raised when a source job is already leased by another worker."""


class BudgetError(RuntimeError):
    """Raised when a paid request cannot be reserved safely."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


class IngestionJobLedger:
    """Single-worker SQLite ledger with transactional state transitions."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cursor_key TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_id, run_id)
                );
                CREATE INDEX IF NOT EXISTS jobs_source_status ON jobs(source_id, status);
                CREATE TABLE IF NOT EXISTS source_cursors (
                    source_id TEXT NOT NULL,
                    cursor_key TEXT NOT NULL,
                    cursor_value TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, cursor_key)
                );
                CREATE TABLE IF NOT EXISTS run_context (
                    run_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    budget_usd REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    run_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cursor_value TEXT,
                    document_json TEXT,
                    payload_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, item_key)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    error_code TEXT,
                    error_message TEXT,
                    bytes INTEGER NOT NULL DEFAULT 0,
                    actual_cost_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS cost_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    reserved_usd REAL NOT NULL,
                    actual_usd REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS spending_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    limit_usd REAL NOT NULL CHECK(limit_usd>=0)
                );
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_uri TEXT,
                    archive_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            batch_columns = {row[1] for row in db.execute("PRAGMA table_info(batches)").fetchall()}
            if "manifest_uri" not in batch_columns:
                db.execute("ALTER TABLE batches ADD COLUMN manifest_uri TEXT")
            if "archive_sha256" not in batch_columns:
                db.execute("ALTER TABLE batches ADD COLUMN archive_sha256 TEXT")
            work_columns = {row[1] for row in db.execute("PRAGMA table_info(work_items)").fetchall()}
            if "payload_json" not in work_columns:
                db.execute("ALTER TABLE work_items ADD COLUMN payload_json TEXT")
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "parameters_json" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN parameters_json TEXT")
            run_columns = {row[1] for row in db.execute("PRAGMA table_info(run_context)").fetchall()}
            if "budget_run_id" not in run_columns:
                db.execute("ALTER TABLE run_context ADD COLUMN budget_run_id TEXT")
            columns = [row[1] for row in db.execute("PRAGMA table_info(source_cursors)").fetchall()]
            if columns and "source_id" in columns and "cursor_key" in columns:
                pk = [row[1] for row in sorted(db.execute("PRAGMA table_info(source_cursors)").fetchall(), key=lambda row: row[5]) if row[5] > 0]
                if pk != ["source_id", "cursor_key"]:
                    db.execute("ALTER TABLE source_cursors RENAME TO source_cursors_legacy")
                    db.execute("CREATE TABLE source_cursors (source_id TEXT NOT NULL, cursor_key TEXT NOT NULL, cursor_value TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(source_id, cursor_key))")
                    db.execute("INSERT OR REPLACE INTO source_cursors SELECT source_id, cursor_key, cursor_value, updated_at FROM source_cursors_legacy")
                    db.execute("DROP TABLE source_cursors_legacy")

    def submit_job(self, source_id: str, run_id: str, parameters: Optional[dict[str, Any]] = None) -> str:
        job_id = f"{source_id}:{run_id}"
        now = _iso(_now())
        params_json = json.dumps(parameters or {})
        with self._connection() as db:
            db.execute(
                "INSERT INTO jobs(job_id, source_id, run_id, status, parameters_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 'submitted', ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET status='submitted', parameters_json=excluded.parameters_json, updated_at=excluded.updated_at",
                (job_id, source_id, run_id, params_json, now, now),
            )
        return job_id

    def pending_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT job_id, source_id, run_id, status, parameters_json, created_at, updated_at "
                "FROM jobs WHERE status IN ('submitted', 'queued') ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                p_raw = r["parameters_json"] or "{}"
                try:
                    p = json.loads(p_raw)
                except Exception:
                    p = {}
                result.append({
                    "job_id": r["job_id"],
                    "source_id": r["source_id"],
                    "run_id": r["run_id"],
                    "status": r["status"],
                    "parameters": p,
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
            return result

    def get_job(self, job_id_or_run_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as db:
            row = db.execute(
                "SELECT job_id, source_id, run_id, status, parameters_json, created_at, updated_at "
                "FROM jobs WHERE job_id=? OR run_id=? LIMIT 1",
                (job_id_or_run_id, job_id_or_run_id),
            ).fetchone()
            if not row:
                return None
            p_raw = row["parameters_json"] or "{}"
            try:
                p = json.loads(p_raw)
            except Exception:
                p = {}
            return {
                "job_id": row["job_id"],
                "source_id": row["source_id"],
                "run_id": row["run_id"],
                "status": row["status"],
                "parameters": p,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def update_job_status(self, job_id_or_run_id: str, status: str) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE job_id=? OR run_id=?",
                (status, _iso(_now()), job_id_or_run_id, job_id_or_run_id),
            )

    def start_job(self, source_id: str, run_id: str) -> str:
        job_id = f"{source_id}:{run_id}"
        now = _iso(_now())
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO jobs(job_id, source_id, run_id, status, created_at, updated_at) VALUES (?, ?, ?, 'created', ?, ?)",
                (job_id, source_id, run_id, now, now),
            )
        return job_id

    def acquire_lease(self, source_id: str, run_id: str, lease_seconds: int = 300) -> str:
        job_id = self.start_job(source_id, run_id)
        token = secrets.token_urlsafe(24)
        now = _now()
        expiry = _iso(now + timedelta(seconds=lease_seconds))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT job_id, source_id, run_id, lease_expires_at FROM jobs WHERE status='running' AND lease_expires_at > ? LIMIT 1",
                (_iso(now),),
            ).fetchone()
            if active:
                raise JobLeaseError(f"operational worker lease already held by source {active['source_id']!r} ({active['job_id']})")
            db.execute(
                "UPDATE jobs SET status='running', lease_token=?, lease_expires_at=?, updated_at=? WHERE job_id=?",
                (token, expiry, _iso(now), job_id),
            )
        return token

    def renew_lease(self, source_id: str, run_id: str, token: str, lease_seconds: int = 300) -> None:
        with self._connection() as db:
            updated = db.execute(
                "UPDATE jobs SET lease_expires_at=?, updated_at=? WHERE source_id=? AND run_id=? AND lease_token=? AND status='running' AND lease_expires_at > ?",
                (_iso(_now() + timedelta(seconds=lease_seconds)), _iso(_now()), source_id, run_id, token, _iso(_now())),
            ).rowcount
            if not updated:
                raise JobLeaseError("lease is expired or no longer owned")

    def assert_lease(self, source_id: str, run_id: str, token: str) -> None:
        with self._connection() as db:
            row = db.execute("SELECT 1 FROM jobs WHERE source_id=? AND run_id=? AND lease_token=? AND status='running' AND lease_expires_at > ?", (source_id, run_id, token, _iso(_now()))).fetchone()
        if row is None:
            raise JobLeaseError("lease is expired or no longer owned")

    def release_lease(self, source_id: str, run_id: str, token: str, status: str = "finished") -> None:
        with self._connection() as db:
            updated = db.execute(
                "UPDATE jobs SET status=?, lease_token=NULL, lease_expires_at=NULL, updated_at=? WHERE source_id=? AND run_id=? AND lease_token=?",
                (status, _iso(_now()), source_id, run_id, token),
            ).rowcount
            if not updated:
                raise JobLeaseError("invalid or expired job lease")

    def cursor(self, source_id: str, cursor_key: str = "default") -> Optional[str]:
        with self._connection() as db:
            row = db.execute(
                "SELECT cursor_value FROM source_cursors WHERE source_id=? AND cursor_key=?",
                (source_id, cursor_key),
            ).fetchone()
        return row["cursor_value"] if row else None

    def advance_cursor(self, source_id: str, cursor_value: str, cursor_key: str = "default", *, run_id: Optional[str] = None, token: Optional[str] = None) -> None:
        with self._connection() as db:
            if run_id is not None or token is not None:
                self._assert_lease_connection(db, source_id, run_id, token)
            db.execute(
                "INSERT INTO source_cursors(source_id, cursor_key, cursor_value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source_id, cursor_key) DO UPDATE SET cursor_value=excluded.cursor_value, updated_at=excluded.updated_at",
                (source_id, cursor_key, cursor_value, _iso(_now())),
            )

    def ensure_run(self, run_id: str, source_id: str, budget_usd: float) -> None:
        if not math.isfinite(budget_usd) or budget_usd < 0:
            raise BudgetError("run budget must be finite and non-negative")
        with self._connection() as db:
            existing = db.execute("SELECT budget_usd FROM run_context WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None:
                if abs(existing["budget_usd"] - budget_usd) > 1e-12:
                    raise BudgetError("run budget is immutable")
                return
            db.execute("INSERT INTO run_context(run_id, source_id, budget_usd, created_at) VALUES (?, ?, ?, ?)", (run_id, source_id, budget_usd, _iso(_now())))

    def bind_budget_run(self, run_id: str, source_id: str, budget_usd: float, budget_run_id: Optional[str] = None) -> str:
        """Persist the spending pool so a child run cannot reset authorization on resume."""
        self.ensure_run(run_id, source_id, budget_usd)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT budget_run_id FROM run_context WHERE run_id=?", (run_id,)).fetchone()[0]
            if existing is not None and budget_run_id is not None and existing != budget_run_id:
                raise BudgetError("run budget authorization is immutable")
            authorization = existing or budget_run_id or run_id
            if authorization != run_id:
                if db.execute("SELECT 1 FROM cost_reservations WHERE run_id=? LIMIT 1", (run_id,)).fetchone():
                    raise BudgetError("a run with paid attempts cannot switch spending pools")
                parent = db.execute("SELECT budget_usd, budget_run_id FROM run_context WHERE run_id=?", (authorization,)).fetchone()
                if parent is None:
                    db.execute("INSERT INTO run_context(run_id, source_id, budget_usd, created_at) VALUES (?, ?, ?, ?)", (authorization, source_id, budget_usd, _iso(_now())))
                elif abs(parent["budget_usd"] - budget_usd) > 1e-12 or parent["budget_run_id"] not in (None, authorization):
                    raise BudgetError("shared run budget is immutable")
            db.execute("UPDATE run_context SET budget_run_id=? WHERE run_id=?", (authorization, run_id))
        return authorization

    def save_work_item(self, run_id: str, item_key: str, item: dict[str, Any], *, status: str = "discovered", cursor_value: Optional[str] = None, document: Optional[dict[str, Any]] = None, payload: Optional[dict[str, Any]] = None) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO work_items(run_id, item_key, item_json, status, cursor_value, document_json, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, item_key) DO UPDATE SET status=excluded.status, cursor_value=excluded.cursor_value, document_json=COALESCE(excluded.document_json, work_items.document_json), payload_json=COALESCE(excluded.payload_json, work_items.payload_json), updated_at=excluded.updated_at",
                (run_id, item_key, json.dumps(item, sort_keys=True), status, cursor_value, json.dumps(document, sort_keys=True) if document else None, json.dumps(payload, sort_keys=True) if payload else None, _iso(_now())),
            )

    def work_items(self, run_id: str, statuses: Optional[tuple[str, ...]] = None) -> list[dict[str, Any]]:
        with self._connection() as db:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = db.execute(f"SELECT * FROM work_items WHERE run_id=? AND status IN ({placeholders}) ORDER BY rowid", (run_id, *statuses)).fetchall()
            else:
                rows = db.execute("SELECT * FROM work_items WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def record_attempt(self, run_id: str, request_id: str, source_id: str, status: str, *, http_status: Optional[int] = None,
                       error_code: Optional[str] = None, error_message: Optional[str] = None, bytes_count: int = 0,
                       actual_cost_usd: float = 0.0) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO attempts(run_id, request_id, source_id, status, http_status, error_code, error_message, bytes, actual_cost_usd, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, request_id) DO UPDATE SET status=excluded.status, http_status=excluded.http_status, error_code=excluded.error_code, error_message=excluded.error_message, bytes=excluded.bytes, actual_cost_usd=excluded.actual_cost_usd",
                (run_id, request_id, source_id, status, http_status, error_code, error_message, bytes_count, actual_cost_usd, _iso(_now())),
            )

    def configure_spending_limit(self, limit_usd: float) -> None:
        """Persist one total authorization; reopening or a new run cannot reset it."""
        if not math.isfinite(limit_usd) or limit_usd < 0:
            raise BudgetError("total spending limit must be finite and non-negative")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT limit_usd FROM spending_policy WHERE singleton=1").fetchone()
            if existing is not None:
                if existing["limit_usd"] != limit_usd:
                    raise BudgetError("total spending limit is immutable")
                return
            db.execute("INSERT INTO spending_policy(singleton, limit_usd) VALUES (1, ?)", (limit_usd,))

    @staticmethod
    def _spending_summary(db: sqlite3.Connection) -> dict[str, Any]:
        row = db.execute(
            "SELECT (SELECT limit_usd FROM spending_policy WHERE singleton=1) AS limit_usd, "
            "COALESCE(SUM(CASE WHEN status IN ('settled','overrun') THEN actual_usd ELSE 0 END), 0) AS committed_usd, "
            "COALESCE(SUM(CASE WHEN status='reserved' THEN reserved_usd ELSE 0 END), 0) AS reserved_usd, "
            "COALESCE(SUM(CASE WHEN status IN ('reserved','overrun') THEN 1 ELSE 0 END), 0) AS unresolved_reservations, "
            "COALESCE(SUM(CASE WHEN status='overrun' THEN 1 ELSE 0 END), 0) AS overrun_reservations "
            "FROM cost_reservations"
        ).fetchone()
        summary = dict(row)
        summary["remaining_usd"] = (
            max(0.0, row["limit_usd"] - row["committed_usd"] - row["reserved_usd"])
            if row["limit_usd"] is not None else None
        )
        return summary

    def spending_summary(self) -> dict[str, Any]:
        """Report total ledger usage, including uncertain requests and overruns."""
        with self._connection() as db:
            return self._spending_summary(db)

    def reserve_cost(self, run_id: str, request_id: str, amount_usd: float, budget_usd: float, *, price_known: bool) -> str:
        if not math.isfinite(amount_usd) or amount_usd < 0 or not price_known:
            raise BudgetError("paid request price must be known, finite and non-negative")
        if not math.isfinite(budget_usd) or budget_usd < 0:
            raise BudgetError("run budget must be finite and non-negative")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            context = db.execute("SELECT budget_usd FROM run_context WHERE run_id=?", (run_id,)).fetchone()
            if context is None:
                db.execute("INSERT INTO run_context(run_id, source_id, budget_usd, created_at) VALUES (?, '', ?, ?)", (run_id, budget_usd, _iso(_now())))
                context = db.execute("SELECT budget_usd FROM run_context WHERE run_id=?", (run_id,)).fetchone()
            if abs(context["budget_usd"] - budget_usd) > 1e-12:
                raise BudgetError("run budget is immutable")
            existing = db.execute("SELECT reservation_id, status FROM cost_reservations WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if existing:
                raise BudgetError(f"request {request_id!r} has status {existing['status']!r} and cannot authorize new dispatch")
            unresolved = db.execute("SELECT request_id FROM cost_reservations WHERE run_id=? AND status IN ('reserved', 'overrun')", (run_id,)).fetchone()
            if unresolved:
                raise BudgetError(f"unresolved cost reservation for request {unresolved['request_id']!r} blocks additional dispatch until reconciled")
            spent = db.execute("SELECT COALESCE(SUM(CASE WHEN status IN ('reserved','settled','overrun') THEN COALESCE(actual_usd, reserved_usd) ELSE 0 END), 0) AS total FROM cost_reservations WHERE run_id=?", (run_id,)).fetchone()["total"]
            if budget_usd <= 0 or spent + amount_usd > budget_usd + 1e-12:
                raise BudgetError("paid request exceeds the remaining extraction budget")
            summary = self._spending_summary(db)
            if summary["limit_usd"] is not None:
                if summary["unresolved_reservations"]:
                    raise BudgetError("unresolved total cost reservation blocks additional dispatch until reconciled")
                if summary["limit_usd"] <= 0 or summary["committed_usd"] + summary["reserved_usd"] + amount_usd > summary["limit_usd"] + 1e-12:
                    raise BudgetError("paid request exceeds the remaining total spending limit")
            reservation_id = secrets.token_hex(16)
            db.execute(
                "INSERT INTO cost_reservations(reservation_id, run_id, request_id, reserved_usd, status, created_at) VALUES (?, ?, ?, ?, 'reserved', ?)",
                (reservation_id, run_id, request_id, amount_usd, _iso(_now())),
            )
            return reservation_id

    def settle_cost(self, run_id: str, request_id: str, actual_usd: float) -> None:
        if not math.isfinite(actual_usd) or actual_usd < 0:
            raise BudgetError("actual cost must be finite and non-negative")
        with self._connection() as db:
            row = db.execute("SELECT reserved_usd, status FROM cost_reservations WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if row is None or row["status"] != "reserved":
                raise BudgetError("reservation is not active")
            overrun = actual_usd > row["reserved_usd"] + 1e-12
            db.execute(
                "UPDATE cost_reservations SET actual_usd=?, status=? WHERE run_id=? AND request_id=? AND status='reserved'",
                (actual_usd, "overrun" if overrun else "settled", run_id, request_id),
            )
        if overrun:
            raise BudgetError("provider usage exceeded its authorized request bound; reconciliation required")

    def fail_cost(self, run_id: str, request_id: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE cost_reservations SET status='released' WHERE run_id=? AND request_id=? AND status='reserved'", (run_id, request_id))

    def spent_cost(self, run_id: str) -> float:
        with self._connection() as db:
            return float(db.execute("SELECT COALESCE(SUM(CASE WHEN status IN ('reserved','settled','overrun') THEN COALESCE(actual_usd, reserved_usd) ELSE 0 END), 0) AS total FROM cost_reservations WHERE run_id=?", (run_id,)).fetchone()["total"])

    def record_batch(self, batch_id: str, run_id: str, source_id: str, status: str, *, manifest_uri: Optional[str] = None, archive_sha256: Optional[str] = None, lease_token: Optional[str] = None) -> None:
        with self._connection() as db:
            if lease_token is not None:
                self._assert_lease_connection(db, source_id, run_id, lease_token)
            db.execute(
                "INSERT INTO batches(batch_id, run_id, source_id, status, manifest_uri, archive_sha256, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(batch_id) DO UPDATE SET status=excluded.status, manifest_uri=COALESCE(excluded.manifest_uri, batches.manifest_uri), archive_sha256=COALESCE(excluded.archive_sha256, batches.archive_sha256), updated_at=excluded.updated_at",
                (batch_id, run_id, source_id, status, manifest_uri, archive_sha256, _iso(_now()), _iso(_now())),
            )

    @staticmethod
    def _assert_lease_connection(db: sqlite3.Connection, source_id: str, run_id: Optional[str], token: Optional[str]) -> None:
        row = db.execute("SELECT 1 FROM jobs WHERE source_id=? AND run_id=? AND lease_token=? AND status='running' AND lease_expires_at > ?", (source_id, run_id, token, _iso(_now()))).fetchone()
        if row is None:
            raise JobLeaseError("lease is expired or no longer owned")

    def batch(self, batch_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as db:
            row = db.execute("SELECT batch_id, run_id, source_id, status, manifest_uri, archive_sha256, created_at, updated_at FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def redactable_attempts(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute("SELECT run_id, request_id, source_id, status, http_status, error_code, bytes, actual_cost_usd, created_at FROM attempts WHERE run_id=?", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT run_id, source_id, budget_usd, created_at FROM run_context ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            runs = []
            for r in rows:
                run_dict = dict(r)
                job_row = db.execute("SELECT status FROM jobs WHERE run_id=?", (r["run_id"],)).fetchone()
                batch_row = db.execute("SELECT status FROM batches WHERE run_id=?", (r["run_id"],)).fetchone()
                run_dict["status"] = batch_row["status"] if batch_row else (job_row["status"] if job_row else "created")
                runs.append(run_dict)
            return runs

    def last_success(self, source_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._connection() as db:
            query = "SELECT batch_id, run_id, source_id, status, manifest_uri, archive_sha256, created_at, updated_at FROM batches WHERE status='committed'"
            params: list[Any] = []
            if source_id:
                query += " AND source_id=?"
                params.append(source_id)
            query += " ORDER BY updated_at DESC LIMIT 1"
            row = db.execute(query, params).fetchone()
            if row:
                return dict(row)
            job_query = "SELECT job_id, run_id, source_id, status, created_at, updated_at FROM jobs WHERE status='completed'"
            job_params: list[Any] = []
            if source_id:
                job_query += " AND source_id=?"
                job_params.append(source_id)
            job_query += " ORDER BY updated_at DESC LIMIT 1"
            job_row = db.execute(job_query, job_params).fetchone()
            return dict(job_row) if job_row else None

    def oldest_pending_job(self) -> Optional[dict[str, Any]]:
        with self._connection() as db:
            row = db.execute(
                "SELECT batch_id, run_id, source_id, status, manifest_uri, archive_sha256, created_at, updated_at "
                "FROM batches WHERE status IN ('staged', 'running', 'pending', 'needs_review') ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            job_row = db.execute(
                "SELECT job_id, run_id, source_id, status, created_at, updated_at "
                "FROM jobs WHERE status IN ('running', 'pending') ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            return dict(job_row) if job_row else None


__all__ = ["BudgetError", "IngestionJobLedger", "JobLeaseError"]
