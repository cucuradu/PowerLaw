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


def audit_sources(paths: List[Path]) -> Dict[str, Any]:
    """
    Performs a strictly read-only audit of legacy databases, parquet exports,
    and storage directories. Unproven legacy origins are quarantined for classification.
    """
    report: Dict[str, Any] = {
        "audit_version": "1.0",
        "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        "paths_inspected": [],
        "summary": {
            "total_bytes": 0,
            "total_parquet_files": 0,
            "total_lancedb_tables": 0,
            "total_quarantined_records": 0,
            "total_records": 0
        }
    }

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

        # 1. Audit Parquet Files
        for pq_file in path_obj.glob("*.parquet"):
            try:
                df = pd.read_parquet(pq_file)
                row_count = len(df)
                cols = list(df.columns)
                # Check for duplicates on candidate keys
                dup_count = 0
                if "id" in cols:
                    dup_count = int(df.duplicated(subset=["id"]).sum())
                elif "node_id" in cols and "timestamp" in cols and "metric_name" in cols:
                    dup_count = int(df.duplicated(subset=["node_id", "timestamp", "metric_name"]).sum())
                elif "source_id" in cols and "target_id" in cols:
                    dup_count = int(df.duplicated(subset=["source_id", "target_id"]).sum())

                # Classification: Unproven legacy files are quarantined
                has_origin = "data_origin" in cols
                origin_counts = df["data_origin"].value_counts().to_dict() if has_origin else {}
                classification = "verified" if has_origin else "quarantined_legacy"

                path_report["parquet_files"][pq_file.name] = {
                    "rows": row_count,
                    "columns": cols,
                    "byte_size": pq_file.stat().st_size,
                    "duplicate_count": dup_count,
                    "duplicate_rate": round(dup_count / row_count, 4) if row_count > 0 else 0.0,
                    "classification": classification,
                    "origin_counts": origin_counts
                }
                path_report["total_records"] += row_count
                if classification == "quarantined_legacy":
                    path_report["quarantined_records"] += row_count
                report["summary"]["total_parquet_files"] += 1
            except Exception as e:
                path_report["parquet_files"][pq_file.name] = {"error": str(e)}

        # 2. Audit LanceDB Tables
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

                        has_origin = "data_origin" in cols
                        origin_counts = df["data_origin"].value_counts().to_dict() if has_origin else {}
                        classification = "verified" if has_origin else "quarantined_legacy"

                        path_report["lancedb_tables"][t_name] = {
                            "version": version,
                            "rows": row_count,
                            "columns": cols,
                            "duplicate_count": dup_count,
                            "duplicate_rate": round(dup_count / row_count, 4) if row_count > 0 else 0.0,
                            "classification": classification,
                            "origin_counts": origin_counts
                        }
                        path_report["total_records"] += row_count
                        if classification == "quarantined_legacy":
                            path_report["quarantined_records"] += row_count
                        report["summary"]["total_lancedb_tables"] += 1
                    except Exception as te:
                        path_report["lancedb_tables"][t_name] = {"error": str(te)}
            except Exception as dbe:
                # Not a LanceDB directory or error opening
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
