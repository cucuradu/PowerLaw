"""
Data Migration & Source Auditing CLI for PowerLaw / ConstructPulse.
Implements the audit_sources() interface and migrate_data CLI for Task 1 & Task 3.
Audit is strictly read-only and does not instantiate seeding dashboard helpers.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

try:
    import lancedb
    HAS_LANCEDB = True
except ImportError:
    HAS_LANCEDB = False


def _validate_raw_references(df: pd.DataFrame) -> Dict[str, Any]:
    """Inspect and validate presence and syntax/existence of raw document references."""
    ref_cols = [c for c in df.columns if c in (
        "source_url", "raw_ref", "raw_file", "document_id", "source_document_id",
        "canonical_url", "pdf_path", "source_file"
    )]
    total = len(df)
    if not ref_cols or total == 0:
        return {
            "has_raw_references": False,
            "reference_columns": ref_cols,
            "total_records": total,
            "referenced_count": 0,
            "valid_count": 0,
            "coverage_rate": 0.0
        }

    referenced = 0
    valid = 0
    for _, row in df.iterrows():
        row_has_ref = False
        row_is_valid = False
        for rc in ref_cols:
            val = str(row.get(rc, "")).strip()
            if val and val not in ("None", "nan", ""):
                row_has_ref = True
                # Check whether reference is a valid URL or a reachable local path
                if val.startswith("http://") or val.startswith("https://"):
                    row_is_valid = True
                elif Path(val).exists() or Path("data/raw").joinpath(val).exists():
                    row_is_valid = True
        if row_has_ref:
            referenced += 1
        if row_is_valid:
            valid += 1

    return {
        "has_raw_references": True,
        "reference_columns": ref_cols,
        "total_records": total,
        "referenced_count": referenced,
        "valid_count": valid,
        "coverage_rate": round(valid / total, 4) if total > 0 else 0.0
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
    because a data_origin column exists; verified status requires verified production origin
    and confirmed raw document provenance.
    """
    has_origin = "data_origin" in cols or "origin" in cols

    # If demo or synthetic origin is present
    if "demo" in origin_counts:
        return "demo"
    if "synthetic" in origin_counts:
        return "synthetic"
    if "quarantined_legacy" in origin_counts:
        return "quarantined_legacy"

    # Strict verified requirement: explicitly production and has valid raw references
    if has_origin and set(origin_counts.keys()) == {"production"} and raw_ref_info.get("coverage_rate", 0.0) > 0.0:
        return "verified"

    # Default for all unproven or missing provenance is quarantined
    return "quarantined_legacy"


def audit_sources(paths: List[Path], inspect_minio: bool = True) -> Dict[str, Any]:
    """
    Performs a strictly read-only audit of legacy databases, parquet exports,
    storage directories, and remote object storage (MinIO).
    Unproven legacy origins are quarantined for classification.
    """
    report: Dict[str, Any] = {
        "audit_version": "1.1",
        "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        "paths_inspected": [],
        "minio_inspection": {
            "status": "unconfigured",
            "endpoint": None,
            "bucket": None,
            "accessible": False,
            "objects_count": 0,
            "total_bytes": 0
        },
        "summary": {
            "total_bytes": 0,
            "total_parquet_files": 0,
            "total_lancedb_tables": 0,
            "total_quarantined_records": 0,
            "total_records": 0
        }
    }

    # 0. MinIO / S3 Object Storage Inspection
    if inspect_minio:
        try:
            from src.config import get_settings
            settings = get_settings()
            if settings.object_backend == "s3" or settings.minio_endpoint:
                report["minio_inspection"]["endpoint"] = settings.minio_endpoint
                report["minio_inspection"]["bucket"] = settings.object_bucket
                report["minio_inspection"]["status"] = "configured"
                try:
                    from src.utils.minio_client import MinIOClient
                    client = MinIOClient()
                    if hasattr(client, "client") and client.client is not None:
                        found = client.client.bucket_exists(settings.object_bucket)
                        report["minio_inspection"]["accessible"] = bool(found)
                        if found:
                            objects = list(client.client.list_objects(
                                settings.object_bucket, prefix=settings.object_prefix, recursive=True
                            ))
                            report["minio_inspection"]["objects_count"] = len(objects)
                            report["minio_inspection"]["total_bytes"] = sum(
                                o.size for o in objects if hasattr(o, "size") and o.size
                            )
                            report["minio_inspection"]["status"] = "verified_connected"
                except Exception as me:
                    report["minio_inspection"]["status"] = f"connection_unverified: {me}"
        except Exception:
            pass

    # 1. Local Filesystem Audit
    for p in paths:
        path_obj = Path(p).resolve()
        path_report: Dict[str, Any] = {
            "path": str(path_obj),
            "exists": path_obj.exists(),
            "byte_size": 0,
            "parquet_files": {},
            "lancedb_tables": {},
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

                raw_ref_info = _validate_raw_references(df)
                temporal_info = _compute_temporal_coverage(df)
                classification = _classify_dataset(cols, origin_counts, raw_ref_info)

                path_report["parquet_files"][pq_file.name] = {
                    "rows": row_count,
                    "columns": cols,
                    "byte_size": pq_file.stat().st_size,
                    "duplicate_count": dup_count,
                    "duplicate_rate": round(dup_count / row_count, 4) if row_count > 0 else 0.0,
                    "classification": classification,
                    "origin_counts": origin_counts,
                    "raw_document_references": raw_ref_info,
                    "temporal_coverage": temporal_info
                }
                path_report["total_records"] += row_count
                if classification != "verified":
                    path_report["quarantined_records"] += row_count
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

                        raw_ref_info = _validate_raw_references(df)
                        temporal_info = _compute_temporal_coverage(df)
                        classification = _classify_dataset(cols, origin_counts, raw_ref_info)

                        path_report["lancedb_tables"][t_name] = {
                            "version": version,
                            "rows": row_count,
                            "columns": cols,
                            "duplicate_count": dup_count,
                            "duplicate_rate": round(dup_count / row_count, 4) if row_count > 0 else 0.0,
                            "classification": classification,
                            "origin_counts": origin_counts,
                            "raw_document_references": raw_ref_info,
                            "temporal_coverage": temporal_info
                        }
                        path_report["total_records"] += row_count
                        if classification != "verified":
                            path_report["quarantined_records"] += row_count
                        report["summary"]["total_lancedb_tables"] += 1
                    except Exception as te:
                        path_report["lancedb_tables"][t_name] = {"error": str(te)}
            except Exception as dbe:
                path_report["lancedb_error"] = str(dbe)

        report["summary"]["total_records"] += path_report["total_records"]
        report["summary"]["total_quarantined_records"] += path_report["quarantined_records"]
        report["paths_inspected"].append(path_report)

    return report


def main():
    parser = argparse.ArgumentParser(description="PowerLaw / ConstructPulse Data Migration & Audit Utility")
    parser.add_argument("--audit", action="store_true", help="Perform read-only audit of storage sources")
    parser.add_argument("--paths", nargs="*", default=["data/lancedb", "storage/lancedb", "data/production/lancedb"], help="Paths to inspect")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON report file path")
    parser.add_argument("--dry-run", action="store_true", help="Plan migration without applying mutations")

    args = parser.parse_args()

    if args.audit or not args.dry_run:
        target_paths = [Path(p) for p in args.paths]
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
