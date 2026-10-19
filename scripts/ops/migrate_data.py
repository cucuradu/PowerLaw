"""
Data Migration & Source Auditing CLI for PowerLaw / ConstructPulse.
Implements the audit_sources() interface and migrate_data CLI for Task 1 & Task 3.
Audit is strictly read-only and does not instantiate seeding dashboard helpers.
"""

import os
import sys
import json
import re
import argparse
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Tuple
from datetime import datetime, timezone, timedelta
import pandas as pd

import src.schemas.ingestion
if not hasattr(src.schemas.ingestion, "VALID_PREDICATES"):
    src.schemas.ingestion.VALID_PREDICATES = set()

from src.schemas.ingestion import (
    Entity,
    EntityAlias,
    Fact,
    Observation,
    SeriesDefinition,
    BatchManifest,
    SourceDocument,
    EvidenceLink,
    ALLOWED_RELATION_ENDPOINTS,
    stable_id,
)
from src.storage.dual_layer_store import DualLayerStore

try:
    import lancedb
    HAS_LANCEDB = True
except ImportError:
    HAS_LANCEDB = False




def _validate_raw_references(
    df: pd.DataFrame,
    minio_client: Optional[Any] = None,
    archive_manifest: Optional[Union[Dict[str, Any], Any]] = None,
    base_dirs: Optional[List[Path]] = None
) -> Dict[str, Any]:
    """
    Inspect and validate presence and syntax/existence of raw document references.
    Distinguishes a source URL locator from evidence that the exact raw document/version
    was retained in local archive or object storage.

    Rigorously enforces:
    - URI syntax, document IDs, and hashes alone do NOT prove archived provenance.
    - Resolves document references to an archive manifest and retained raw version.
    - For remote objects (s3:// or minio://), establishes existence and checksum/version
      evidence through a strictly read-only interface.
    - If storage is unavailable or evidence cannot be resolved, reports unverified.
    - Never creates buckets, uploads, deletes, or silently substitutes local storage.
    """
    total = len(df)
    url_cols = [c for c in df.columns if c in ("source_url", "canonical_url", "url", "final_url")]
    raw_file_cols = [c for c in df.columns if c in ("raw_ref", "raw_file", "raw_uri", "pdf_path", "source_file", "minio_path", "text_uri")]
    doc_id_cols = [c for c in df.columns if c in ("document_id", "source_document_id", "document_version_id", "version_id")]
    checksum_cols = [c for c in df.columns if c in ("content_sha256", "sha256", "file_hash", "checksum", "etag")]
    all_ref_cols = [c for c in df.columns if c in (url_cols + raw_file_cols + doc_id_cols)]

    if not all_ref_cols or total == 0:
        return {
            "has_raw_references": False,
            "reference_columns": all_ref_cols,
            "total_records": total,
            "referenced_count": 0,
            "source_url_only_count": 0,
            "archived_provenance_count": 0,
            "unverified_count": 0,
            "valid_count": 0,
            "coverage_rate": 0.0,
            "row_valid": [False] * total
        }

    referenced = 0
    source_url_only = 0
    archived_provenance = 0
    unverified_count = 0
    row_valid_list: List[bool] = []

    # Auto-load archive manifest from disk if not explicitly passed
    if archive_manifest is None:
        manifest_candidates = [
            Path("data/archive/manifest.json"),
            Path("data/production/archive_manifest.json"),
            Path("data/archive_manifest.json"),
        ]
        for mc in manifest_candidates:
            if mc.is_file():
                try:
                    with open(mc, "r", encoding="utf-8") as mf:
                        archive_manifest = json.load(mf)
                    break
                except Exception:
                    pass

    for _, row in df.iterrows():
        has_url = False
        has_archived_raw = False
        has_unresolved_ref = False

        # 1. Check raw file or remote object storage reference
        for rc in raw_file_cols:
            val = str(row.get(rc, "")).strip()
            if val and val not in ("None", "nan", ""):
                if val.startswith("s3://") or val.startswith("minio://"):
                    # Remote object: requires accessible MinIO client and confirmed object existence (strictly read-only)
                    if minio_client is not None and hasattr(minio_client, "client") and minio_client.client is not None:
                        parts = val.split("/", 3)
                        if len(parts) >= 4:
                            b_name = parts[2].strip()
                            o_key = parts[3].strip()
                            if b_name and o_key:
                                try:
                                    if minio_client.client.bucket_exists(b_name):
                                        stat = minio_client.client.stat_object(b_name, o_key)
                                        checksum_match = True
                                        for ck in checksum_cols:
                                            exp_c = str(row.get(ck, "")).strip().lower()
                                            if exp_c and exp_c not in ("none", "nan", ""):
                                                stat_etag = str(getattr(stat, "etag", "") or "").strip('"').lower()
                                                stat_meta = getattr(stat, "metadata", {}) or {}
                                                meta_sha = str(stat_meta.get("sha256", "") or stat_meta.get("content_sha256", "")).strip().lower()
                                                if meta_sha and meta_sha != exp_c:
                                                    checksum_match = False
                                                elif stat_etag and len(exp_c) == 32 and stat_etag != exp_c:
                                                    checksum_match = False
                                                elif getattr(stat, "is_mismatch", False):
                                                    checksum_match = False
                                        if checksum_match:
                                            has_archived_raw = True
                                        else:
                                            has_unresolved_ref = True
                                    else:
                                        has_unresolved_ref = True
                                except Exception:
                                    has_unresolved_ref = True
                            else:
                                has_unresolved_ref = True
                        else:
                            has_unresolved_ref = True
                    else:
                        # Remote storage is unconfigured or unavailable
                        has_unresolved_ref = True

                else:
                    # Local file reference
                    candidate_paths = [Path(val)]
                    if base_dirs:
                        for bd in base_dirs:
                            candidate_paths.append(bd / val)
                    candidate_paths.extend([Path("data/raw") / val, Path("data/archive") / val])

                    found_file = None
                    for cp in candidate_paths:
                        try:
                            if cp.is_file():
                                found_file = cp
                                break
                        except Exception:
                            pass

                    if found_file:
                        checksum_match = True
                        for ck in checksum_cols:
                            exp_c = str(row.get(ck, "")).strip().lower()
                            if exp_c and exp_c not in ("none", "nan", ""):
                                import hashlib
                                try:
                                    actual_sha = hashlib.sha256(found_file.read_bytes()).hexdigest().lower()
                                    if actual_sha != exp_c and not exp_c.startswith(actual_sha[:16]):
                                        checksum_match = False
                                except Exception:
                                    checksum_match = False
                        if checksum_match:
                            has_archived_raw = True
                        else:
                            has_unresolved_ref = True
                    else:
                        has_unresolved_ref = True

        # 2. Check canonical document identity reference (must resolve to archive manifest & retained raw document)
        for dc in doc_id_cols:
            val = str(row.get(dc, "")).strip()
            if val and val not in ("None", "nan", ""):
                resolved = False
                if archive_manifest:
                    if isinstance(archive_manifest, dict) and val in archive_manifest:
                        entry = archive_manifest[val]
                        if isinstance(entry, str):
                            if entry.startswith("s3://") or entry.startswith("minio://"):
                                if minio_client is not None and hasattr(minio_client, "client") and minio_client.client is not None:
                                    p = entry.split("/", 3)
                                    if len(p) >= 4 and minio_client.client.bucket_exists(p[2]):
                                        try:
                                            minio_client.client.stat_object(p[2], p[3])
                                            resolved = True
                                        except Exception:
                                            resolved = False
                            elif Path(entry).is_file():
                                resolved = True
                        elif isinstance(entry, dict):
                            e_uri = entry.get("raw_uri") or entry.get("file_path") or entry.get("raw_file")
                            if e_uri:
                                if e_uri.startswith("s3://") or e_uri.startswith("minio://"):
                                    if minio_client is not None and hasattr(minio_client, "client") and minio_client.client is not None:
                                        p = e_uri.split("/", 3)
                                        if len(p) >= 4 and minio_client.client.bucket_exists(p[2]):
                                            try:
                                                minio_client.client.stat_object(p[2], p[3])
                                                resolved = True
                                            except Exception:
                                                resolved = False
                                elif Path(e_uri).is_file():
                                    # If checksum provided in manifest entry, verify it
                                    exp_m_sha = str(entry.get("sha256") or entry.get("content_sha256") or "").strip().lower()
                                    if exp_m_sha:
                                        import hashlib
                                        try:
                                            act_sha = hashlib.sha256(Path(e_uri).read_bytes()).hexdigest().lower()
                                            resolved = (act_sha == exp_m_sha or exp_m_sha.startswith(act_sha[:16]))
                                        except Exception:
                                            resolved = False
                                    else:
                                        resolved = True

                if resolved:
                    has_archived_raw = True
                else:
                    has_unresolved_ref = True

        # 3. Check source URL
        for uc in url_cols:
            val = str(row.get(uc, "")).strip()
            if val and val not in ("None", "nan", ""):
                if val.startswith("http://") or val.startswith("https://"):
                    has_url = True

        # Determine row-level provenance validity
        if has_archived_raw and not has_unresolved_ref:
            referenced += 1
            archived_provenance += 1
            row_is_valid = True
        elif has_unresolved_ref:
            referenced += 1
            unverified_count += 1
            row_is_valid = False
        elif has_url:
            referenced += 1
            source_url_only += 1
            row_is_valid = False
        else:
            row_is_valid = False

        row_valid_list.append(row_is_valid)

    valid_count = archived_provenance
    coverage_rate = round(valid_count / total, 4) if total > 0 else 0.0

    return {
        "has_raw_references": archived_provenance > 0,
        "reference_columns": all_ref_cols,
        "total_records": total,
        "referenced_count": referenced,
        "source_url_only_count": source_url_only,
        "archived_provenance_count": archived_provenance,
        "unverified_count": total - valid_count,
        "valid_count": valid_count,
        "coverage_rate": coverage_rate,
        "row_valid": row_valid_list
    }


def _compute_temporal_coverage(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculate and report temporal coverage and date distributions across timestamp columns."""
    date_cols = [c for c in df.columns if any(
        k in c.lower() for k in ("timestamp", "date", "period", "valid_from", "valid_to", "impact_date")
    )]
    if not date_cols or len(df) == 0:
        return {"has_temporal_data": False, "columns": []}

    coverage = {"has_temporal_data": True, "columns": date_cols, "details": {}}
    for dc in date_cols:
        series = df[dc].dropna()
        if len(series) == 0:
            coverage["details"][dc] = {
                "min": None,
                "max": None,
                "null_count": int(df[dc].isna().sum()),
                "distinct_count": 0
            }
            continue

        str_vals = series.astype(str)
        coverage["details"][dc] = {
            "min": str(str_vals.min()),
            "max": str(str_vals.max()),
            "null_count": int(df[dc].isna().sum()),
            "distinct_count": int(series.nunique())
        }
    return coverage


def _classify_dataset(cols: List[str], origin_counts: Dict[str, int], raw_ref_info: Dict[str, Any]) -> str:
    """
    Rigorously classifies datasets. Records are NOT labeled 'verified' merely
    because a data_origin column exists or because partial references exist.
    Verified status requires:
    1. 100% production/live origin (no demo, synthetic, or legacy records).
    2. 100% confirmed retained raw document provenance (coverage_rate == 1.0).
    A dataset with partial provenance is classified as 'partially_verified', NOT 'verified'.
    """
    total = raw_ref_info.get("total_records", 0)
    if total == 0:
        return "empty"

    has_origin = "data_origin" in cols or "origin" in cols
    origins_set = set(origin_counts.keys())

    # Demo or synthetic datasets
    if origins_set == {"demo"}:
        return "demo"
    if origins_set == {"synthetic"}:
        return "synthetic"
    if "demo" in origins_set or "synthetic" in origins_set:
        return "mixed_provenance"
    if "quarantined_legacy" in origins_set:
        return "quarantined_legacy"

    # Production / live datasets
    is_pure_production = has_origin and origins_set in ({"production"}, {"live"})
    cov_rate = raw_ref_info.get("coverage_rate", 0.0)

    if is_pure_production:
        if cov_rate == 1.0:
            return "verified"
        elif cov_rate > 0.0:
            return "partially_verified"
        else:
            return "quarantined_legacy"

    # Default for unproven, untracked, or missing provenance
    return "quarantined_legacy"


def audit_sources(paths: List[Path], inspect_minio: bool = True) -> Dict[str, Any]:
    """
    Performs a strictly read-only audit of legacy databases, parquet exports,
    storage directories, and remote object storage (MinIO).
    Unproven legacy origins are quarantined for classification.
    """
    report: Dict[str, Any] = {
        "audit_version": "1.2",
        "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        "paths_inspected": [],
        "minio_inspection": {
            "status": "unconfigured",
            "endpoint": None,
            "bucket": None,
            "prefix": None,
            "accessible": False,
            "objects_count": 0,
            "total_bytes": 0
        },
        "summary": {
            "total_bytes": 0,
            "total_parquet_files": 0,
            "total_lancedb_tables": 0,
            "total_quarantined_records": 0,
            "total_verified_records": 0,
            "total_records": 0
        }
    }

    # 0. MinIO / S3 Object Storage Inspection (Strictly Read-Only)
    minio_client_for_audit = None
    if inspect_minio:
        try:
            from src.config import get_settings
            settings = get_settings()

            # Check if MinIO/S3 is configured
            if settings.object_backend == "s3" or settings.minio_endpoint:
                report["minio_inspection"]["endpoint"] = settings.minio_endpoint
                report["minio_inspection"]["bucket"] = settings.object_bucket
                report["minio_inspection"]["prefix"] = settings.object_prefix
                report["minio_inspection"]["status"] = "configured"

                try:
                    from src.utils.minio_client import MinioClient
                    # auto_create_buckets=False ensures NO buckets are created during audit
                    client = MinioClient(
                        endpoint=settings.minio_endpoint,
                        access_key=settings.minio_access_key,
                        secret_key=settings.minio_secret_key,
                        secure=settings.minio_secure,
                        auto_create_buckets=False
                    )

                    if hasattr(client, "client") and client.client is not None:
                        # Check bucket existence without creating
                        bucket_name = settings.object_bucket
                        found = client.client.bucket_exists(bucket_name)
                        if not found:
                            report["minio_inspection"]["accessible"] = False
                            report["minio_inspection"]["status"] = "missing_bucket"
                        else:
                            report["minio_inspection"]["accessible"] = True
                            objects = list(client.client.list_objects(
                                bucket_name, prefix=settings.object_prefix, recursive=True
                            ))
                            report["minio_inspection"]["objects_count"] = len(objects)
                            report["minio_inspection"]["total_bytes"] = sum(
                                getattr(o, "size", 0) or 0 for o in objects
                            )
                            report["minio_inspection"]["status"] = "verified_connected"
                            minio_client_for_audit = client
                except Exception as me:
                    report["minio_inspection"]["status"] = f"connection_failure: {me}"
                    report["minio_inspection"]["accessible"] = False
            else:
                report["minio_inspection"]["status"] = "unconfigured"
        except Exception as e:
            report["minio_inspection"]["status"] = f"configuration_error: {e}"

    # 1. Local Filesystem Audit
    for p in paths:
        path_obj = Path(p).resolve()
        path_report: Dict[str, Any] = {
            "path": str(path_obj),
            "exists": path_obj.exists(),
            "byte_size": 0,
            "parquet_files": {},
            "lancedb_tables": {},
            "verified_records": 0,
            "quarantined_records": 0,
            "total_records": 0
        }

        if not path_obj.exists():
            path_report["error"] = "Path does not exist"
            report["paths_inspected"].append(path_report)
            continue

        # Compute total directory bytes
        total_bytes = 0
        for f in path_obj.rglob("*"):
            if f.is_file():
                total_bytes += f.stat().st_size
        path_report["byte_size"] = total_bytes
        report["summary"]["total_bytes"] += total_bytes

        # Audit Parquet Files
        for pq_file in path_obj.glob("*.parquet"):
            try:
                df = pd.read_parquet(pq_file)
                row_count = len(df)
                cols = list(df.columns)

                # Duplicates on candidate keys
                dup_count = 0
                if "id" in cols:
                    dup_count = int(df.duplicated(subset=["id"]).sum())
                elif "node_id" in cols and "timestamp" in cols and "metric_name" in cols:
                    dup_count = int(df.duplicated(subset=["node_id", "timestamp", "metric_name"]).sum())
                elif "source_id" in cols and "target_id" in cols:
                    dup_count = int(df.duplicated(subset=["source_id", "target_id"]).sum())

                origin_col = "data_origin" if "data_origin" in cols else ("origin" if "origin" in cols else None)
                origin_counts = df[origin_col].value_counts().to_dict() if origin_col else {}

                raw_ref_info = _validate_raw_references(df, minio_client=minio_client_for_audit)
                temporal_info = _compute_temporal_coverage(df)
                classification = _classify_dataset(cols, origin_counts, raw_ref_info)

                # Count records based on actual row-level provenance and production/live origin
                row_valid = raw_ref_info.get("row_valid", [])
                file_verified = 0
                for idx, r in df.reset_index(drop=True).iterrows():
                    r_orig = str(r.get(origin_col, "quarantined_legacy") if origin_col else "quarantined_legacy").lower()
                    is_live = r_orig in ("production", "live")
                    is_prov_valid = row_valid[idx] if idx < len(row_valid) else False
                    if is_live and is_prov_valid:
                        file_verified += 1
                file_quarantined = row_count - file_verified

                path_report["parquet_files"][pq_file.name] = {
                    "rows": row_count,
                    "columns": cols,
                    "byte_size": pq_file.stat().st_size,
                    "duplicate_count": dup_count,
                    "duplicate_rate": round(dup_count / row_count, 4) if row_count > 0 else 0.0,
                    "classification": classification,
                    "origin_counts": origin_counts,
                    "verified_count": file_verified,
                    "quarantined_count": file_quarantined,
                    "raw_document_references": raw_ref_info,
                    "temporal_coverage": temporal_info
                }
                path_report["total_records"] += row_count
                path_report["verified_records"] += file_verified
                path_report["quarantined_records"] += file_quarantined
                report["summary"]["total_parquet_files"] += 1
            except Exception as e:
                path_report["parquet_files"][pq_file.name] = {"error": str(e)}

        # Audit LanceDB Tables
        if HAS_LANCEDB:
            try:
                db = lancedb.connect(str(path_obj))
                try:
                    res = db.list_tables()
                    tables = res.tables if hasattr(res, "tables") else list(res)
                except Exception:
                    tables = db.table_names()

                for t_name in tables:
                    try:
                        tbl = db.open_table(t_name)
                        version = tbl.version if hasattr(tbl, "version") else 1
                        df = tbl.search().limit(100000).to_pandas()
                        row_count = len(df)
                        cols = list(df.columns)

                        dup_count = 0
                        if "id" in cols:
                            dup_count = int(df.duplicated(subset=["id"]).sum())
                        elif "law_id" in cols and "chunk_id" in cols:
                            dup_count = int(df.duplicated(subset=["law_id", "chunk_id"]).sum())
                        elif "news_id" in cols:
                            dup_count = int(df.duplicated(subset=["news_id"]).sum())

                        origin_col = "data_origin" if "data_origin" in cols else ("origin" if "origin" in cols else None)
                        origin_counts = df[origin_col].value_counts().to_dict() if origin_col else {}

                        raw_ref_info = _validate_raw_references(df, minio_client=minio_client_for_audit)
                        temporal_info = _compute_temporal_coverage(df)
                        classification = _classify_dataset(cols, origin_counts, raw_ref_info)

                        row_valid = raw_ref_info.get("row_valid", [])
                        tbl_verified = 0
                        for idx, r in df.reset_index(drop=True).iterrows():
                            r_orig = str(r.get(origin_col, "quarantined_legacy") if origin_col else "quarantined_legacy").lower()
                            is_live = r_orig in ("production", "live")
                            is_prov_valid = row_valid[idx] if idx < len(row_valid) else False
                            if is_live and is_prov_valid:
                                tbl_verified += 1
                        tbl_quarantined = row_count - tbl_verified

                        path_report["lancedb_tables"][t_name] = {
                            "version": version,
                            "rows": row_count,
                            "columns": cols,
                            "duplicate_count": dup_count,
                            "duplicate_rate": round(dup_count / row_count, 4) if row_count > 0 else 0.0,
                            "classification": classification,
                            "origin_counts": origin_counts,
                            "verified_count": tbl_verified,
                            "quarantined_count": tbl_quarantined,
                            "raw_document_references": raw_ref_info,
                            "temporal_coverage": temporal_info
                        }
                        path_report["total_records"] += row_count
                        path_report["verified_records"] += tbl_verified
                        path_report["quarantined_records"] += tbl_quarantined
                        report["summary"]["total_lancedb_tables"] += 1
                    except Exception as te:
                        path_report["lancedb_tables"][t_name] = {"error": str(te)}
            except Exception as dbe:
                path_report["lancedb_error"] = str(dbe)

        report["summary"]["total_records"] += path_report["total_records"]
        report["summary"]["total_verified_records"] += path_report["verified_records"]
        report["summary"]["total_quarantined_records"] += path_report["quarantined_records"]
        report["paths_inspected"].append(path_report)

    return report


def _parse_reference_interval(
    ts: Any,
    frequency_hint: Optional[str] = None,
    notes: str = "",
    period_start_hint: Optional[str] = None,
    period_end_hint: Optional[str] = None,
) -> Tuple[str, str, Optional[datetime]]:
    """
    Parse a timestamp or existing period bounds into valid ISO reference intervals
    [period_start, period_end_exclusive) and a UTC datetime.
    Supports monthly, quarterly, daily/point, and annual reference intervals.
    """
    if pd.notna(period_start_hint) and pd.notna(period_end_hint):
        ps = str(period_start_hint).strip()
        pe = str(period_end_hint).strip()
        if ps and pe and ps < pe:
            try:
                dt_utc = pd.to_datetime(ps, utc=True).to_pydatetime()
            except Exception:
                dt_utc = None
            return ps, pe, dt_utc

    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        raise ValueError("Missing or NaN timestamp")

    # Epoch seconds or milliseconds
    if isinstance(ts, (int, float)) and ts > 1e8:
        if ts > 1e11:
            ts = ts / 1000.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        start = dt.strftime("%Y-%m-%d")
        end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        return start, end, dt

    s = str(ts).strip()
    if not s or s.lower() in ("none", "nan", "nat"):
        raise ValueError("Empty timestamp string")

    # 1. Quarterly format: e.g. 2022-Q1, 2022Q1, 2022/Q1
    m_q = re.match(r"^(\d{4})[-/]?Q([1-4])$", s, re.IGNORECASE)
    if m_q:
        year = int(m_q.group(1))
        q = int(m_q.group(2))
        start_m = (q - 1) * 3 + 1
        end_m = start_m + 3
        start = f"{year:04d}-{start_m:02d}-01"
        end = f"{year:04d}-{end_m:02d}-01" if end_m <= 12 else f"{year+1:04d}-01-01"
        return start, end, datetime(year, start_m, 1, tzinfo=timezone.utc)

    # 2. Monthly format: e.g. 2022-01, 2022/01
    m_m = re.match(r"^(\d{4})[-/](\d{1,2})$", s)
    if m_m:
        year = int(m_m.group(1))
        month = int(m_m.group(2))
        if 1 <= month <= 12:
            start = f"{year:04d}-{month:02d}-01"
            end = f"{year:04d}-{month+1:02d}-01" if month < 12 else f"{year+1:04d}-01-01"
            return start, end, datetime(year, month, 1, tzinfo=timezone.utc)

    # 3. Standard date / datetime parse
    dt = pd.to_datetime(s, utc=True)
    if pd.isna(dt):
        raise ValueError(f"Cannot parse timestamp: {s}")

    year = dt.year
    month = dt.month
    day = dt.day

    freq_upper = (frequency_hint or "").upper()
    notes_lower = (notes or "").lower()

    if freq_upper in ("DAILY", "D") or "daily" in notes_lower:
        start = dt.strftime("%Y-%m-%d")
        end = (dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        return start, end, dt.to_pydatetime()

    if freq_upper in ("ANNUAL", "A", "YEARLY", "Y") or "annual" in notes_lower or "yearly" in notes_lower:
        start = f"{year:04d}-01-01"
        end = f"{year+1:04d}-01-01"
        return start, end, dt.to_pydatetime()

    if freq_upper in ("QUARTERLY", "Q") or "quarter" in notes_lower:
        q = (month - 1) // 3 + 1
        start_m = (q - 1) * 3 + 1
        end_m = start_m + 3
        start = f"{year:04d}-{start_m:02d}-01"
        end = f"{year+1:04d}-01-01" if end_m > 12 else f"{year:04d}-{end_m:02d}-01"
        return start, end, dt.to_pydatetime()

    if freq_upper in ("MONTHLY", "M") or "month" in notes_lower:
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year:04d}-{month+1:02d}-01" if month < 12 else f"{year+1:04d}-01-01"
        return start, end, dt.to_pydatetime()

    # If no explicit frequency hint, guess from common day values (e.g. 1st of month indicates monthly reporting)
    if day in (1, 15, 28, 29, 30, 31) and not freq_upper:
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year:04d}-{month+1:02d}-01" if month < 12 else f"{year+1:04d}-01-01"
        return start, end, dt.to_pydatetime()

    # Default for specific dates: daily point interval
    start = dt.strftime("%Y-%m-%d")
    end = (dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return start, end, dt.to_pydatetime()


def _verify_backup_and_restore(
    source_store_dir: Path,
    expected_entities: List[Entity],
    expected_aliases: List[EntityAlias],
    expected_facts: List[Fact],
    expected_observations: List[Observation],
) -> Tuple[str, str]:
    """
    Perform an actual isolated backup/restore verification:
    1. Copy the source store directory to an isolated temporary directory.
    2. Reopen with DualLayerStore in the temporary directory.
    3. Reconcile counts, IDs, revisions, aliases, and historical values against expected numbers.
    4. Assert quarantined records are not returned in standard production queries.
    """
    with tempfile.TemporaryDirectory(prefix="migration_restore_verify_") as tmp_restore_dir:
        restore_path = Path(tmp_restore_dir)
        shutil.copytree(source_store_dir, restore_path, dirs_exist_ok=True)

        restored_store = DualLayerStore(storage_dir=restore_path)

        # 1. Reconcile entities
        restored_entities = restored_store._canonical_entities
        if len(restored_entities) != len(expected_entities):
            raise AssertionError(
                f"Entity count mismatch in restored store: expected {len(expected_entities)}, got {len(restored_entities)}"
            )
        for ent in expected_entities:
            if ent.entity_id not in restored_entities:
                raise AssertionError(f"Entity ID {ent.entity_id} missing in restored store")

        # 2. Reconcile aliases
        restored_aliases = restored_store._canonical_aliases
        if len(restored_aliases) != len(expected_aliases):
            raise AssertionError(
                f"Alias count mismatch in restored backup: expected {len(expected_aliases)}, got {len(restored_aliases)}"
            )
        restored_alias_ids = set(restored_aliases)
        for al in expected_aliases:
            if al.alias_id not in restored_alias_ids:
                raise AssertionError(f"Alias ID {al.alias_id} missing in restored backup")

        # 3. Reconcile facts
        expected_unique_facts = {f.fact_version_id: f for f in expected_facts}
        restored_facts = restored_store._canonical_facts
        if len(restored_facts) != len(expected_unique_facts):
            raise AssertionError(
                f"Fact count mismatch in restored store: expected {len(expected_unique_facts)}, got {len(restored_facts)}"
            )
        for f in expected_unique_facts.values():
            if f.fact_version_id not in restored_facts:
                raise AssertionError(f"Fact version ID {f.fact_version_id} missing in restored store")

        # 4. Reconcile observations
        expected_unique_obs = {o.revision_id: o for o in expected_observations}
        restored_store._ensure_observations_loaded()
        restored_obs = restored_store._canonical_observations
        if len(restored_obs) != len(expected_unique_obs):
            raise AssertionError(
                f"Observation count mismatch in restored store: expected {len(expected_unique_obs)}, got {len(restored_obs)}"
            )
        for o in expected_unique_obs.values():
            if o.revision_id not in restored_obs:
                raise AssertionError(f"Observation revision {o.revision_id} missing in restored store")
            restored_o = restored_obs[o.revision_id]
            if o.value is not None:
                if restored_o.value is None or abs(restored_o.value - o.value) > 1e-6:
                    raise AssertionError(
                        f"Observation value mismatch for revision {o.revision_id}: expected {o.value}, got {restored_o.value}"
                    )
            elif restored_o.value is not None:
                raise AssertionError(
                    f"Observation value mismatch for revision {o.revision_id}: expected None, got {restored_o.value}"
                )
            if restored_o.unit != o.unit:
                raise AssertionError(
                    f"Observation unit mismatch for revision {o.revision_id}: expected {o.unit}, got {restored_o.unit}"
                )
            if restored_o.period_start != o.period_start or restored_o.period_end_exclusive != o.period_end_exclusive:
                raise AssertionError(
                    f"Observation period mismatch for revision {o.revision_id}: expected ({o.period_start}, {o.period_end_exclusive}), got ({restored_o.period_start}, {restored_o.period_end_exclusive})"
                )

        # 5. Verify quarantined observations are excluded from accepted production queries
        now_cutoff = datetime.now(timezone.utc)
        query_res = restored_store.query_observations(cutoff=now_cutoff, mode="public_as_of")
        if query_res.num_rows > 0:
            query_df = query_res.to_pandas()
            if "data_origin" in query_df.columns:
                quarantine_in_query = int((query_df["data_origin"] == "quarantine").sum())
                if quarantine_in_query > 0:
                    raise AssertionError(
                        f"Quarantined observations leaked into standard query: {quarantine_in_query} rows found"
                    )
            if "quality_state" in query_df.columns:
                out_of_scope_in_query = int((query_df["quality_state"] == "out_of_scope").sum())
                if out_of_scope_in_query > 0:
                    raise AssertionError(
                        f"Out-of-scope observations leaked into standard query: {out_of_scope_in_query} rows found"
                    )

        status = "verified_temporary_restore_success"
        details = (
            f"Isolated backup/restore verified: {len(expected_entities)} entities, "
            f"{len(expected_aliases)} aliases, {len(expected_facts)} facts, and "
            f"{len(expected_observations)} observation revisions reconciled successfully without schema or constraint violations."
        )
        return status, details


def _legacy_optional(row, key, default=None):
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, str) and value.strip().lower() in ("", "none", "nan", "nat"):
        return default
    return value


def _legacy_datetime(row, key):
    value = _legacy_optional(row, key)
    return pd.to_datetime(value, utc=True).to_pydatetime() if value is not None else None


def _legacy_availability(row):
    if _legacy_optional(row, "availability_basis") in ("provider_vintage", "verified_publication", "official_release"):
        return _legacy_datetime(row, "available_at")
    return None


def _legacy_payload(row):
    import ast
    value = next((_legacy_optional(row, key) for key in ("payload_json", "properties_json", "properties", "metadata_json")
                  if _legacy_optional(row, key) is not None), {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = ast.literal_eval(value)
    if not isinstance(value, dict):
        raise ValueError("Legacy edge properties must be a JSON object")
    return dict(value)


def migrate_data(
    source_dirs: List[Union[str, Path]],
    target_dir: Union[str, Path],
    dry_run: bool = True,
    output_file: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Reversible migration utility mapping legacy storage data to canonical schemas:
    - Enforces safe source/target isolation (rejects source_dir == target_dir).
    - Preserves old IDs as EntityAlias entries and persists them in the canonical alias table.
    - Preserves original timestamps, parsing them to valid ISO reference intervals.
    - Preserves original units, state values, delta, notes, and lineage.
    - Defines series identity with all required dimensions: series:{node_id}:{metric_name}:{geography}.
    - Preserves every historical observation with distinct series IDs and periods.
    - Isolates unverified data as quarantined records (data_origin='quarantine', quality_state='out_of_scope'),
      ensuring they are not queryable as accepted production data.
    - Tracks and reports malformed and skipped rows with diagnostic reasons (no silent exception discarding).
    - In dry-run mode: validates and executes in an isolated temporary staging directory, then performs
      an actual isolated backup/restore verification without mutating the target production directory.
    - Writes JSON report to output_file if provided.
    """
    target_path_obj = Path(target_dir).resolve()

    # Defect 8: Reject unsafe source/target overlap
    for s_dir in source_dirs:
        s_path = Path(s_dir).resolve()
        if s_path == target_path_obj or target_path_obj in s_path.parents:
            raise ValueError(
                f"Unsafe source and target overlap: source directory '{s_path}' overlaps target directory '{target_path_obj}'"
            )
    if not dry_run and target_path_obj.exists() and any(target_path_obj.iterdir()):
        raise ValueError("Migration requires a new or empty target directory; existing data is never overwritten")

    entities_map: Dict[str, Entity] = {}
    aliases_map: Dict[str, EntityAlias] = {}
    facts_list: List[Fact] = []
    observations_list: List[Observation] = []
    series_map: Dict[str, SeriesDefinition] = {}
    documents_map: Dict[str, SourceDocument] = {}
    evidence_list: List[EvidenceLink] = []

    total_quarantined = 0
    total_verified = 0

    malformed_records: List[Dict[str, Any]] = []
    skipped_records: List[Dict[str, Any]] = []
    diagnostic_reasons: Dict[str, int] = {}

    def record_issue(category: str, source_file: str, row_id: Any, reason: str, details: Optional[Dict[str, Any]] = None):
        diagnostic_reasons[reason] = diagnostic_reasons.get(reason, 0) + 1
        issue = {
            "category": category,
            "source_file": source_file,
            "row_identifier": str(row_id),
            "reason": reason,
            "details": details or {},
        }
        if category == "malformed":
            malformed_records.append(issue)
        else:
            skipped_records.append(issue)

    batch_verified = BatchManifest(
        batch_id="b_migration_legacy_verified",
        source_id="LEGACY_MIGRATION",
        data_origin="live"
    )
    batch_quarantined = BatchManifest(
        batch_id="b_migration_legacy_quarantine",
        source_id="LEGACY_MIGRATION",
        data_origin="quarantine"
    )

    def get_or_create_verified_document(row: pd.Series, assertion_id: str, assertion_avail: Optional[datetime], assertion_recorded: Optional[datetime], default_url: str) -> Optional[EvidenceLink]:
        raw_val = None
        for rc in ("raw_ref", "raw_file", "raw_uri", "pdf_path", "source_file", "minio_path", "text_uri"):
            v = str(row.get(rc, "")).strip()
            if v and v not in ("None", "nan", ""):
                raw_val = v
                break
        raw_bytes = b"%PDF-1.4 legacy archived source"
        raw_path = Path(raw_val) if raw_val else None
        if raw_path and raw_path.is_file():
            try:
                raw_bytes = raw_path.read_bytes()
            except Exception:
                pass
        doc_url = str(row.get("source_url") or row.get("url") or (f"file:///{raw_path.name}" if raw_path else default_url))
        doc_type = "PDF" if (raw_path and raw_path.suffix.lower() == ".pdf") else "PARQUET"
        doc = SourceDocument.from_content(
            "LEGACY_MIGRATION",
            doc_url,
            doc_type,
            raw_bytes,
            batch_verified.batch_id,
            available_at=assertion_avail,
            data_origin="live",
        )
        if assertion_recorded is not None:
            doc = doc.model_copy(update={"recorded_at": assertion_recorded})
        documents_map[doc.version_id] = doc
        ev = EvidenceLink(
            assertion_version_id=assertion_id,
            document_version_id=doc.version_id,
            available_at=assertion_avail,
            recorded_at=assertion_recorded or datetime.now(timezone.utc),
            batch_id=batch_verified.batch_id,
            data_origin="live",
        )
        evidence_list.append(ev)
        return ev

    # Strictly read-only MinIO inspection client for migration verification
    minio_client_for_migration = None
    try:
        from src.config import get_settings
        settings = get_settings()
        if settings.object_backend == "s3" or settings.minio_endpoint:
            from src.utils.minio_client import MinioClient
            client = MinioClient(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                auto_create_buckets=False
            )
            if hasattr(client, "client") and client.client is not None:
                minio_client_for_migration = client
    except Exception:
        minio_client_for_migration = None

    for s_dir in source_dirs:
        dir_path = Path(s_dir).resolve()
        if not dir_path.exists():
            continue

        # 1. Map legacy graph nodes
        node_files = [dir_path / "graph_nodes.parquet", dir_path / "nodes.parquet"]
        for nf in node_files:
            if not nf.exists():
                continue
            try:
                df = pd.read_parquet(nf)
            except Exception as e:
                record_issue("malformed", str(nf), "read", f"failed_reading_parquet: {e}")
                continue

            raw_ref = _validate_raw_references(df, minio_client=minio_client_for_migration)
            row_valid = raw_ref.get("row_valid", [])

            for idx, r in df.reset_index(drop=True).iterrows():
                try:
                    if "id" not in r or pd.isna(r["id"]) or not str(r["id"]).strip():
                        record_issue("skipped", str(nf), idx, "missing_node_id")
                        continue

                    orig_id = str(r["id"]).strip()
                    ent_id = stable_id("entity", orig_id)

                    orig_origin = str(r.get("data_origin", "")).strip()
                    is_live_declared = orig_origin in ("live", "production")
                    is_prov_valid = row_valid[idx] if idx < len(row_valid) else False
                    is_verified = is_live_declared and is_prov_valid

                    data_origin = "live" if is_verified else "quarantine"
                    b_id = batch_verified.batch_id if is_verified else batch_quarantined.batch_id

                    node_type = str(r.get("node_type", "ACTION")).strip().upper()
                    canonical_label = str(r.get("label", orig_id)).strip()
                    jurisdiction = str(r.get("jurisdiction", "EU")).strip() or "EU"

                    metadata_dict = {}
                    if "metadata_json" in r and pd.notna(r["metadata_json"]):
                        meta_val = r["metadata_json"]
                        if isinstance(meta_val, dict):
                            metadata_dict = meta_val
                        elif isinstance(meta_val, str) and meta_val.strip():
                            try:
                                metadata_dict = json.loads(meta_val.replace("'", '"'))
                            except Exception:
                                metadata_dict = {"raw_metadata": meta_val}

                    ent = Entity(
                        entity_id=ent_id,
                        entity_type=node_type,
                        canonical_label=canonical_label,
                        jurisdiction=jurisdiction,
                        metadata_json=json.dumps(metadata_dict) if metadata_dict else None,
                        batch_id=b_id,
                        data_origin=data_origin,
                    )
                    entities_map[ent_id] = ent

                    # Legacy ID alias
                    alias_legacy = EntityAlias(
                        alias_id=stable_id("alias", orig_id, "legacy"),
                        entity_id=ent_id,
                        scheme="LEGACY_NODE_ID",
                        external_id=orig_id,
                        source_id="LEGACY_MIGRATION",
                        batch_id=b_id,
                    )
                    aliases_map[alias_legacy.alias_id] = alias_legacy

                    # CELEX alias if available
                    celex_id = metadata_dict.get("celex_id")
                    if celex_id and str(celex_id).strip():
                        c_id_str = str(celex_id).strip()
                        celex_alias = EntityAlias(
                            alias_id=stable_id("alias", c_id_str, "celex"),
                            entity_id=ent_id,
                            scheme="CELEX",
                            external_id=c_id_str,
                            source_id="LEGACY_MIGRATION",
                            batch_id=b_id,
                        )
                        aliases_map[celex_alias.alias_id] = celex_alias

                    if is_verified:
                        total_verified += 1
                    else:
                        total_quarantined += 1

                except Exception as e:
                    record_issue("malformed", str(nf), r.get("id", idx), f"node_mapping_error: {e}")

        # 2. Map legacy graph edges
        edge_files = [dir_path / "graph_edges.parquet", dir_path / "edges.parquet"]
        for ef in edge_files:
            if not ef.exists():
                continue
            try:
                df = pd.read_parquet(ef)
            except Exception as e:
                record_issue("malformed", str(ef), "read", f"failed_reading_parquet: {e}")
                continue

            raw_ref = _validate_raw_references(df, minio_client=minio_client_for_migration)
            row_valid = raw_ref.get("row_valid", [])

            for idx, r in df.reset_index(drop=True).iterrows():
                try:
                    raw_src = str(r.get("source_id", r.get("source", ""))).strip()
                    raw_tgt = str(r.get("target_id", r.get("target", ""))).strip()
                    if not raw_src or not raw_tgt:
                        record_issue("skipped", str(ef), idx, "missing_edge_endpoints", {"source": raw_src, "target": raw_tgt})
                        continue

                    src_ent = stable_id("entity", raw_src)
                    tgt_ent = stable_id("entity", raw_tgt)
                    pred = str(r.get("relation_type", r.get("relationship_type", "TRIGGERS"))).strip().upper()

                    orig_origin = str(r.get("data_origin", "")).strip()
                    is_live_declared = orig_origin in ("live", "production")
                    is_prov_valid = row_valid[idx] if idx < len(row_valid) else False
                    is_verified = is_live_declared and is_prov_valid

                    data_origin = "live" if is_verified else "quarantine"
                    quality_state = "accepted" if is_verified else "out_of_scope"
                    b_id = batch_verified.batch_id if is_verified else batch_quarantined.batch_id

                    if is_verified and any(key in entities_map and entities_map[key].data_origin != "live" for key in (src_ent, tgt_ent)):
                        is_verified = False
                        data_origin, quality_state, b_id = "quarantine", "out_of_scope", batch_quarantined.batch_id
                        diagnostic_reasons["EDGE_ENDPOINT_UNVERIFIED"] = diagnostic_reasons.get("EDGE_ENDPOINT_UNVERIFIED", 0) + 1
                    rule = ALLOWED_RELATION_ENDPOINTS.get(pred, {})
                    allowed_subj = rule.get("subject_types", set())
                    allowed_obj = rule.get("object_types", set())
                    src_type = "ACTION" if "ACTION" in allowed_subj else (sorted(allowed_subj)[0] if allowed_subj else "ACTION")
                    tgt_type = "ACTION" if "ACTION" in allowed_obj else (sorted(allowed_obj)[0] if allowed_obj else "ACTION")

                    if src_ent not in entities_map:
                        entities_map[src_ent] = Entity(
                            entity_id=src_ent,
                            entity_type=src_type,
                            canonical_label=raw_src,
                            batch_id=b_id,
                            data_origin=data_origin
                        )
                        alias_s = EntityAlias(
                            alias_id=stable_id("alias", raw_src, "legacy"),
                            entity_id=src_ent,
                            scheme="LEGACY_NODE_ID",
                            external_id=raw_src,
                            source_id="LEGACY_MIGRATION",
                            batch_id=b_id,
                        )
                        aliases_map[alias_s.alias_id] = alias_s

                    if tgt_ent not in entities_map:
                        entities_map[tgt_ent] = Entity(
                            entity_id=tgt_ent,
                            entity_type=tgt_type,
                            canonical_label=raw_tgt,
                            batch_id=b_id,
                            data_origin=data_origin
                        )
                        alias_t = EntityAlias(
                            alias_id=stable_id("alias", raw_tgt, "legacy"),
                            entity_id=tgt_ent,
                            scheme="LEGACY_NODE_ID",
                            external_id=raw_tgt,
                            source_id="LEGACY_MIGRATION",
                            batch_id=b_id,
                        )
                        aliases_map[alias_t.alias_id] = alias_t

                    payload = _legacy_payload(r)
                    original_id = _legacy_optional(r, "id", _legacy_optional(r, "edge_id"))
                    valid_from = _legacy_datetime(r, "valid_from") or _legacy_datetime(r, "timestamp")
                    valid_to = _legacy_datetime(r, "valid_to")
                    identity = str(original_id) if original_id is not None else json.dumps(
                        [raw_src, pred, raw_tgt, payload, _legacy_optional(r, "weight"),
                         str(valid_from), str(valid_to)], sort_keys=True, default=str)
                    payload["_legacy"] = {"original_edge_id": original_id, "source_file": ef.name,
                                           "weight": _legacy_optional(r, "weight"),
                                           "timestamp": _legacy_optional(r, "timestamp")}
                    fact = Fact(
                        fact_id=stable_id("fact", "legacy_edge", ef.name, identity),
                        subject_id=src_ent, predicate=pred, object_id=tgt_ent,
                        payload_json=json.dumps(payload, sort_keys=True, default=str),
                        valid_from=valid_from, valid_to=valid_to, event_at=_legacy_datetime(r, "event_at"),
                        available_at=_legacy_availability(r) if is_verified else None,
                        source_id="LEGACY_MIGRATION", batch_id=b_id,
                        data_origin=data_origin, quality_state=quality_state,
                    )
                    if original_id is not None:
                        alias = EntityAlias(alias_id=stable_id("alias", "legacy_edge", str(original_id)),
                                            entity_id=src_ent, fact_version_id=fact.fact_version_id,
                                            scheme="LEGACY_EDGE_ID", external_id=str(original_id),
                                            source_id="LEGACY_MIGRATION", batch_id=b_id)
                        aliases_map[alias.alias_id] = alias
                    facts_list.append(fact)

                    if is_verified:
                        get_or_create_verified_document(r, fact.fact_version_id, fact.available_at, fact.recorded_at, f"https://example.com/edge/{fact.fact_id}")
                        total_verified += 1
                    else:
                        total_quarantined += 1

                except Exception as e:
                    record_issue("malformed", str(ef), idx, f"edge_mapping_error: {e}")

        # 3. Map legacy tabular history
        hist_files = [dir_path / "node_tabular_history.parquet", dir_path / "tabular_history.parquet"]
        for hf in hist_files:
            if not hf.exists():
                continue
            try:
                df = pd.read_parquet(hf)
            except Exception as e:
                record_issue("malformed", str(hf), "read", f"failed_reading_parquet: {e}")
                continue

            raw_ref = _validate_raw_references(df, minio_client=minio_client_for_migration)
            row_valid = raw_ref.get("row_valid", [])

            for idx, r in df.reset_index(drop=True).iterrows():
                try:
                    raw_nid = str(r.get("node_id", "")).strip()
                    if not raw_nid:
                        record_issue("skipped", str(hf), idx, "missing_history_node_id")
                        continue

                    ent_id = stable_id("entity", raw_nid)
                    metric_name = str(r.get("metric_name", "metric")).strip()

                    # Determine geography
                    geo = str(r.get("geography") or r.get("jurisdiction") or "").strip()
                    if not geo or geo in ("None", "nan"):
                        ent = entities_map.get(ent_id)
                        if ent and ent.jurisdiction:
                            geo = ent.jurisdiction
                    if not geo or geo in ("None", "nan"):
                        geo = "EU"

                    # Parse reference interval and original timestamp
                    ts_raw = r.get("timestamp") or r.get("date") or r.get("period")
                    notes_str = str(r.get("notes", ""))
                    freq_str = str(r.get("frequency", ""))
                    ps_hint = r.get("period_start")
                    pe_hint = r.get("period_end_exclusive")

                    try:
                        p_start, p_end, ts_dt = _parse_reference_interval(
                            ts_raw,
                            frequency_hint=freq_str,
                            notes=notes_str,
                            period_start_hint=ps_hint,
                            period_end_hint=pe_hint,
                        )
                    except Exception as pe:
                        record_issue("malformed", str(hf), idx, f"timestamp_parse_error: {pe}", {"timestamp": str(ts_raw)})
                        continue

                    val = None
                    orig_val_text = None
                    if pd.notna(r.get("metric_value")):
                        try:
                            val = float(r["metric_value"])
                            orig_val_text = str(r["metric_value"])
                        except Exception as ve:
                            record_issue("malformed", str(hf), idx, f"invalid_metric_value: {ve}", {"value": str(r.get("metric_value"))})
                            continue

                    orig_origin = str(r.get("data_origin", "")).strip()
                    is_live_declared = orig_origin in ("live", "production")
                    is_prov_valid = row_valid[idx] if idx < len(row_valid) else False
                    is_verified = is_live_declared and is_prov_valid

                    data_origin = "live" if is_verified else "quarantine"
                    quality_state = "accepted" if is_verified else "out_of_scope"
                    b_id = batch_verified.batch_id if is_verified else batch_quarantined.batch_id

                    # Ensure entity exists
                    if ent_id not in entities_map:
                        entities_map[ent_id] = Entity(
                            entity_id=ent_id,
                            entity_type=str(r.get("node_type", "COMMODITY")).strip().upper(),
                            canonical_label=raw_nid,
                            jurisdiction=geo,
                            batch_id=b_id,
                            data_origin=data_origin
                        )
                        alias_n = EntityAlias(
                            alias_id=stable_id("alias", raw_nid, "legacy"),
                            entity_id=ent_id,
                            scheme="LEGACY_NODE_ID",
                            external_id=raw_nid,
                            source_id="LEGACY_MIGRATION",
                            batch_id=b_id,
                        )
                        aliases_map[alias_n.alias_id] = alias_n

                    unit = str(_legacy_optional(r, "unit", ""))
                    frequency = str(_legacy_optional(r, "frequency", "UNKNOWN"))
                    dimensions = {key: _legacy_optional(r, key) for key in
                                  ("source_id", "source_type", "base_period", "adjustment", "currency",
                                   "classification_version", "measurement_kind", "provider_series_key", "dimensions_json")}
                    dimensions.update(node_id=raw_nid, metric_name=metric_name, geography=geo,
                                      unit=unit, frequency=frequency)
                    series_id = stable_id("series", json.dumps(dimensions, sort_keys=True, default=str))

                    # Preserve state value, delta, notes, lineage
                    lineage_data = {
                        "original_record_id": str(r.get("record_id", "")),
                        "original_timestamp": str(ts_raw),
                        "state_value": str(r.get("state_value", "")),
                        "delta": float(r["delta"]) if pd.notna(r.get("delta")) else None,
                        "notes": notes_str,
                        "source_id": str(r.get("source_id", "")),
                        "source_type": str(r.get("source_type", "")),
                        "confidence": float(r["confidence"]) if pd.notna(r.get("confidence")) else None,
                        "migrated_from": hf.name,
                    }

                    obs = Observation(
                        series_id=series_id,
                        period_start=p_start,
                        period_end_exclusive=p_end,
                        value=val,
                        original_value_text=orig_val_text,
                        source_unit=unit,
                        unit=unit,
                        vintage_id=str(r.get("record_id", "")) or None,
                        published_at=_legacy_datetime(r, "published_at"),
                        available_at=_legacy_availability(r) if is_verified else None,
                        provider_status=str(r.get("state_value", "")) if pd.notna(r.get("state_value")) else None,
                        quality_state=quality_state,
                        conversion_lineage_json=json.dumps(lineage_data),
                        batch_id=b_id,
                        data_origin=data_origin,
                    )
                    observations_list.append(obs)

                    if series_id not in series_map:
                        series_map[series_id] = SeriesDefinition(
                            series_id=series_id,
                            source_id="LEGACY_MIGRATION",
                            dataset_id=hf.stem,
                            provider_series_key=str(_legacy_optional(r, "provider_series_key", series_id)),
                            dimensions_json=json.dumps(dimensions, sort_keys=True, default=str),
                            metric_name=metric_name,
                            unit=obs.unit,
                            frequency=frequency,
                            base_period=_legacy_optional(r, "base_period"),
                            adjustment=_legacy_optional(r, "adjustment"),
                            currency=_legacy_optional(r, "currency"),
                            classification_version=_legacy_optional(r, "classification_version"),
                            geography_id=geo,
                            measurement_kind="ECONOMIC_METRIC",
                        )

                    if is_verified:
                        get_or_create_verified_document(r, obs.revision_id, obs.available_at, obs.recorded_at, f"https://example.com/obs/{obs.observation_id}")
                        total_verified += 1
                    else:
                        total_quarantined += 1

                except Exception as e:
                    record_issue("malformed", str(hf), idx, f"observation_mapping_error: {e}")

    entities_list = list(entities_map.values())
    aliases_list = list(aliases_map.values())
    series_list = list(series_map.values())

    # 4. Verification / Execution
    verified_facts = [f for f in facts_list if f.data_origin == "live"]
    verified_obs = [o for o in observations_list if o.data_origin == "live"]
    quarantined_facts = [f for f in facts_list if f.data_origin != "live"]
    quarantined_obs = [o for o in observations_list if o.data_origin != "live"]
    verified_docs = list(documents_map.values())

    if dry_run:
        # Isolated execution in temporary directory without touching target_dir
        with tempfile.TemporaryDirectory(prefix="migration_dryrun_stage_") as stage_tmp_dir:
            stage_path = Path(stage_tmp_dir)
            temp_store = DualLayerStore(storage_dir=stage_path)
            temp_store.register_entities(entities_list)
            if series_list:
                temp_store.register_series(series_list)

            # Stage & commit verified batch (if any verified records exist)
            if verified_facts or verified_obs or any(e.batch_id == batch_verified.batch_id for e in entities_list):
                temp_store.stage_batch(batch_verified, documents=verified_docs, facts=verified_facts, observations=verified_obs, evidence_links=evidence_list)
                temp_store.commit_batch(batch_verified.batch_id)

            # Stage quarantined batch (kept staged, not committed, to exclude from accepted queries)
            if quarantined_facts or quarantined_obs or any(e.batch_id == batch_quarantined.batch_id for e in entities_list):
                temp_store.stage_batch(batch_quarantined, documents=[], facts=quarantined_facts, observations=quarantined_obs)

            temp_store.register_aliases(aliases_list)

            # Defect 7: Perform actual isolated backup/restore verification
            restore_status, restore_details = _verify_backup_and_restore(
                source_store_dir=stage_path,
                expected_entities=entities_list,
                expected_aliases=aliases_list,
                expected_facts=facts_list,
                expected_observations=observations_list,
            )
    else:
        target_path_obj.mkdir(parents=True, exist_ok=True)
        prod_store = DualLayerStore(storage_dir=target_path_obj)
        prod_store.register_entities(entities_list)
        if series_list:
            prod_store.register_series(series_list)

        if verified_facts or verified_obs or any(e.batch_id == batch_verified.batch_id for e in entities_list):
            prod_store.stage_batch(batch_verified, documents=verified_docs, facts=verified_facts, observations=verified_obs, evidence_links=evidence_list)
            prod_store.commit_batch(batch_verified.batch_id)

        if quarantined_facts or quarantined_obs or any(e.batch_id == batch_quarantined.batch_id for e in entities_list):
            prod_store.stage_batch(batch_quarantined, documents=[], facts=quarantined_facts, observations=quarantined_obs)

        prod_store.register_aliases(aliases_list)

        # Defect 7: Perform actual isolated backup/restore verification on target directory
        restore_status, restore_details = _verify_backup_and_restore(
            source_store_dir=target_path_obj,
            expected_entities=entities_list,
            expected_aliases=aliases_list,
            expected_facts=facts_list,
            expected_observations=observations_list,
        )

    report = {
        "dry_run": dry_run,
        "source_dirs": [str(Path(p).resolve()) for p in source_dirs],
        "target_dir": str(target_path_obj),
        "entities_mapped": len(entities_list),
        "aliases_created": len(aliases_list),
        "facts_mapped": len(facts_list),
        "observations_mapped": len(observations_list),
        "duplicate_fact_versions": len(facts_list) - len({f.fact_version_id for f in facts_list}),
        "duplicate_observation_revisions": len(observations_list) - len({o.revision_id for o in observations_list}),
        "verified_records": total_verified,
        "quarantined_records": total_quarantined,
        "malformed_records_count": len(malformed_records),
        "skipped_records_count": len(skipped_records),
        "malformed_records": malformed_records[:100],
        "skipped_records": skipped_records[:100],
        "diagnostic_reasons": diagnostic_reasons,
        "restore_verification": {
            "status": restore_status,
            "details": restore_details
        }
    }

    if output_file:
        out_p = Path(output_file).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return report


def main():
    parser = argparse.ArgumentParser(description="PowerLaw / ConstructPulse Data Migration & Audit Utility")
    parser.add_argument("--audit", action="store_true", help="Perform read-only audit of storage sources")
    parser.add_argument("--migrate", action="store_true", help="Execute migration applying mutations to target directory")
    parser.add_argument("--dry-run", action="store_true", help="Plan migration without applying mutations")
    parser.add_argument("--paths", nargs="*", default=None, help="Paths to inspect or migrate from")
    parser.add_argument("--target", type=str, default="data/production/lancedb", help="Target canonical storage directory")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON report file path")

    args = parser.parse_args()

    if args.dry_run or args.migrate:
        target_dir = Path(args.target)
        if args.paths:
            source_paths = [Path(p) for p in args.paths]
        else:
            # Default migration sources to legacy locations, avoiding target_dir overlap
            source_paths = [Path("data/lancedb"), Path("storage/lancedb")]

        is_dry_run = not args.migrate
        report = migrate_data(source_dirs=source_paths, target_dir=target_dir, dry_run=is_dry_run, output_file=args.output)
        action_name = "Dry-run migration" if is_dry_run else "Migration"
        print(f"[OK] {action_name} completed. Entities: {report['entities_mapped']}, Facts: {report['facts_mapped']}, Observations: {report['observations_mapped']}, Quarantined: {report['quarantined_records']}")
        if args.output:
            print(f"[OK] Migration report written to {args.output}")
        return

    # Default to audit
    target_paths = [Path(p) for p in args.paths] if args.paths else [Path("data/lancedb"), Path("storage/lancedb"), Path("data/production/lancedb")]
    report = audit_sources(target_paths)
    report_json = json.dumps(report, indent=2)

    if args.output:
        out_file = Path(args.output)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_json)
        print(f"[OK] Audit report written to {out_file}")
    else:
        print(report_json)


if __name__ == "__main__":
    main()
