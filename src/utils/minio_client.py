#!/usr/bin/env python3
"""
MinIO Client - Object Storage for Raw Data
Handles upload/download of PDFs, raw news, and research papers.
"""

import os
import io
import json
import hashlib
import tempfile
import posixpath
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union
from dotenv import load_dotenv

from src.config import get_settings

load_dotenv()
logger = logging.getLogger(__name__)

# MinIO configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Bucket names
BUCKET_LAWS = "laws"
BUCKET_NEWS = "news"
BUCKET_PAPERS = "papers"

def log(message: str) -> None:
    """Simple timestamped logger"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [MinIO] {message}")


class MinioClient:
    """
    MinIO client for raw data storage.
    
    Usage:
        client = MinioClient()
        
        # Upload PDF
        uri = client.upload_pdf("laws", "/path/to/law.pdf")
        # Returns: s3://laws/32024R1781.pdf
        
        # Upload raw JSON/HTML
        uri = client.upload_bytes("news", b'{"title": "..."}', "2024/12/article_hash.json")
        
        # Get download URL
        url = client.get_presigned_url("laws", "32024R1781.pdf")
    """
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        secure: Optional[bool] = None,
        auto_create_buckets: bool = True
    ):
        try:
            from minio import Minio
            ep = endpoint or MINIO_ENDPOINT
            ak = access_key or MINIO_ACCESS_KEY
            sk = secret_key or MINIO_SECRET_KEY
            sec = secure if secure is not None else MINIO_SECURE
            self.client = Minio(
                ep,
                access_key=ak,
                secret_key=sk,
                secure=sec
            )
            log(f"✅ Connected to MinIO at {ep}")
            if auto_create_buckets:
                self._ensure_buckets()
        except ImportError:
            raise ImportError("minio package not installed. Run: pip install minio")
        except Exception as e:
            log(f"❌ Failed to connect to MinIO: {e}")
            raise
    
    def _ensure_buckets(self) -> None:
        """Create required buckets if they don't exist"""
        for bucket in [BUCKET_LAWS, BUCKET_NEWS, BUCKET_PAPERS]:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                log(f"✅ Created bucket: {bucket}")
            else:
                log(f"📦 Bucket exists: {bucket}")
    
    def bucket_exists(self, bucket: str) -> bool:
        """Check if bucket exists without side effects."""
        return bool(self.client.bucket_exists(bucket))

    def list_buckets(self) -> List[str]:
        """List all buckets"""
        return [b.name for b in self.client.list_buckets()]
    
    def upload_pdf(self, bucket: str, file_path, object_name: str = None):
        """
        Upload a PDF file to MinIO.
        
        Args:
            bucket: Target bucket (laws, papers)
            file_path: Path to PDF file
            object_name: Optional custom object name (e.g. "folder/file.pdf")
            
        Returns:
            S3-style URI: s3://bucket/object_name
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if object_name is None:
            object_name = file_path.name
        
        self.client.fput_object(
            bucket,
            object_name,
            str(file_path),
            content_type="application/pdf"
        )
        
        uri = f"s3://{bucket}/{object_name}"
        # log(f"✅ Uploaded: {uri}") # Reduced spam
        return uri
    
    def upload_bytes(
        self, 
        bucket: str, 
        data: bytes, 
        object_name: str,
        content_type: str = "application/json"
    ) -> str:
        """
        Upload raw bytes to MinIO.
        
        Args:
            bucket: Target bucket
            data: Raw bytes to upload
            object_name: Object path (e.g., "2024/12/hash.json")
            content_type: MIME type
            
        Returns:
            S3-style URI
        """
        data_stream = io.BytesIO(data)
        data_length = len(data)
        
        self.client.put_object(
            bucket,
            object_name,
            data_stream,
            data_length,
            content_type=content_type
        )
        
        uri = f"s3://{bucket}/{object_name}"
        log(f"✅ Uploaded: {uri}")
        return uri
    
    def upload_json(self, bucket: str, data: Dict[str, Any], object_name: str) -> str:
        """
        Upload JSON data to MinIO.
        
        Args:
            bucket: Target bucket
            data: Dictionary to serialize as JSON
            object_name: Object path
            
        Returns:
            S3-style URI
        """
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        return self.upload_bytes(bucket, json_bytes, object_name, "application/json")
    
    def get_presigned_url(self, bucket: str, object_name: str, expires_hours: int = 24) -> str:
        """
        Get a presigned URL for direct download.
        
        Args:
            bucket: Bucket name
            object_name: Object path
            expires_hours: URL expiration time in hours
            
        Returns:
            Presigned URL string
        """
        from datetime import timedelta
        url = self.client.presigned_get_object(
            bucket,
            object_name,
            expires=timedelta(hours=expires_hours)
        )
        return url
    
    def download_bytes(self, bucket: str, object_name: str) -> bytes:
        """
        Download object as bytes.
        
        Args:
            bucket: Bucket name
            object_name: Object path
            
        Returns:
            Raw bytes
        """
        response = self.client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    
    def download_json(self, bucket: str, object_name: str) -> Dict[str, Any]:
        """
        Download and parse JSON object.
        
        Args:
            bucket: Bucket name
            object_name: Object path
            
        Returns:
            Parsed JSON as dictionary
        """
        data = self.download_bytes(bucket, object_name)
        return json.loads(data.decode('utf-8'))
    
    def list_objects(self, bucket: str, prefix: str = "") -> List[Dict[str, Any]]:
        """
        List objects in a bucket with optional prefix filter.
        
        Args:
            bucket: Bucket name
            prefix: Optional prefix to filter (e.g., "2024/12/")
            
        Returns:
            List of object metadata dicts
        """
        objects = self.client.list_objects(bucket, prefix=prefix, recursive=True)
        return [
            {
                "name": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None
            }
            for obj in objects
        ]
    
    def object_exists(self, bucket: str, object_name: str) -> bool:
        """Check if object exists in bucket"""
        try:
            self.client.stat_object(bucket, object_name)
            return True
        except:
            return False
    
    def delete_object(self, bucket: str, object_name: str) -> None:
        """Delete an object from bucket"""
        self.client.remove_object(bucket, object_name)
        log(f"🗑️ Deleted: s3://{bucket}/{object_name}")


class EvidenceArchive:
    """Verified content-addressed archive for ingestion evidence."""

    def __init__(self, settings=None, client=None):
        self.settings = settings or get_settings()
        self.backend = self.settings.object_backend.lower()
        if self.backend not in {"local", "s3"}:
            raise ValueError(f"Unsupported object backend: {self.backend!r}")
        prefix = str(self.settings.object_prefix).strip("/")
        if not prefix or prefix.startswith(".") or ".." in prefix.split("/") or "\\" in prefix:
            raise ValueError("object prefix must be a relative safe path")
        self.prefix = prefix
        self.client = client
        if self.backend == "local":
            self.root = Path(self.settings.cache_dir).resolve() / "evidence"
            self.root.mkdir(parents=True, exist_ok=True)
        else:
            if not self.settings.minio_secure:
                raise ValueError("remote object storage requires TLS")
            if not self.settings.minio_endpoint or not self.settings.minio_access_key or not self.settings.minio_secret_key:
                raise ValueError("S3 object storage requires endpoint and credentials")
            self.client = client or MinioClient(
                endpoint=self.settings.minio_endpoint,
                access_key=self.settings.minio_access_key,
                secret_key=self.settings.minio_secret_key,
                secure=self.settings.minio_secure,
                auto_create_buckets=False,
            )
            if not self.client.bucket_exists(self.settings.object_bucket):
                raise FileNotFoundError(f"Configured object bucket does not exist: {self.settings.object_bucket}")

    def _check_disk_floor(self, target_path: Path, required_bytes: int = 0) -> None:
        floor = getattr(self.settings, "disk_floor_bytes", 0)
        if floor > 0:
            check_path = target_path if target_path.exists() else target_path.parent
            while not check_path.exists() and check_path != check_path.parent:
                check_path = check_path.parent
            if check_path.exists():
                free_bytes = shutil.disk_usage(check_path).free
                if free_bytes - required_bytes < floor:
                    from src.ingestion import DiskFloorError
                    raise DiskFloorError(
                        f"Free disk space ({free_bytes} bytes) minus required ({required_bytes} bytes) is below configured disk floor ({floor} bytes)"
                    )

    def _object_name(self, digest: str) -> str:
        return f"{self.prefix}/{digest[:2]}/{digest}"

    def put(self, content: bytes, *, content_type: str = "application/octet-stream") -> tuple[str, str]:
        from src.ingestion import ArchiveError
        digest = hashlib.sha256(content).hexdigest()
        name = self._object_name(digest)
        if self.backend == "local":
            self._check_disk_floor(self.root, required_bytes=len(content))
            path = self.root / name
            if path.resolve().parent != (self.root / self.prefix / digest[:2]).resolve():
                raise ValueError("archive path escapes configured local root")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
                    temp.write(content)
                    temp.flush()
                    os.fsync(temp.fileno())
                    temporary_path = Path(temp.name)
                os.replace(temporary_path, path)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != digest:
                raise ArchiveError("local archive checksum verification failed")
            return path.resolve().as_uri(), digest

        self._check_disk_floor(Path(self.settings.temp_dir), required_bytes=len(content))
        try:
            self.client.upload_bytes(self.settings.object_bucket, content, name, content_type)
            downloaded = self.client.download_bytes(self.settings.object_bucket, name)
            if hashlib.sha256(downloaded).hexdigest() != digest:
                raise ArchiveError("object-store readback checksum verification failed")
            return f"s3://{self.settings.object_bucket}/{name}", digest
        except Exception as exc:
            if isinstance(exc, ArchiveError):
                raise
            raise ArchiveError(f"remote object storage error: {exc}") from exc

    def get(self, uri: str, expected_sha256: str) -> bytes:
        from src.ingestion import ArchiveError
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256.lower()):
            raise ValueError("expected checksum must be a SHA-256 hex digest")
        if uri.startswith("file://"):
            from urllib.parse import urlparse, unquote
            raw_path = unquote(urlparse(uri).path)
            if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
                raw_path = raw_path[1:]
            path = Path(raw_path)
            root = self.root.resolve()
            if root not in path.resolve().parents:
                raise ValueError("local archive URI is outside the configured root")
            try:
                content = path.read_bytes()
            except Exception as exc:
                raise ArchiveError(f"failed reading local archive file: {exc}") from exc
        elif uri.startswith("s3://"):
            from urllib.parse import urlparse
            parsed = urlparse(uri)
            if parsed.netloc != self.settings.object_bucket:
                raise ValueError("object URI points outside the configured bucket")
            object_name = parsed.path.lstrip("/")
            if not object_name.startswith(f"{self.prefix}/") or ".." in posixpath.normpath(object_name).split("/"):
                raise ValueError("object URI is outside the configured prefix")
            try:
                content = self.client.download_bytes(parsed.netloc, object_name)
            except Exception as exc:
                raise ArchiveError(f"remote object storage error: {exc}") from exc
        else:
            raise ValueError("archive URI must be file:// or s3://")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ArchiveError("archive checksum verification failed")
        return content


def enforce_cache_budget(
    settings: Optional[Any] = None,
    ledger: Optional[Any] = None,
    client: Optional[MinioClient] = None,
) -> Dict[str, Any]:
    """
    Enforce storage limits across cache and temporary directories.
    Evicts verified reproducible cached objects only.
    Guarantees that the sole unverified copy of an authoritative evidence document is NEVER deleted.
    """
    cfg = settings or get_settings()
    cache_dir = Path(cfg.cache_dir)
    temp_dir = Path(cfg.temp_dir)
    cache_limit = getattr(cfg, "cache_limit_bytes", 2 * 1024 * 1024 * 1024)
    temp_limit = getattr(cfg, "temp_limit_bytes", 512 * 1024 * 1024)
    disk_floor = getattr(cfg, "disk_floor_bytes", 2 * 1024 * 1024 * 1024)

    evicted_files_count = 0
    evicted_bytes = 0
    protected_files_count = 0

    # 1. Enforce temp directory budget (scratch/temporary downloads are reproducible)
    temp_files = []
    if temp_dir.exists():
        for p in temp_dir.rglob("*"):
            if p.is_file():
                try:
                    stat = p.stat()
                    temp_files.append((p, stat.st_size, stat.st_mtime))
                except OSError:
                    pass

    total_temp_bytes = sum(sz for _, sz, _ in temp_files)
    # Sort oldest first
    temp_files.sort(key=lambda x: x[2])
    for p, sz, _ in temp_files:
        if total_temp_bytes > temp_limit:
            try:
                p.unlink(missing_ok=True)
                evicted_files_count += 1
                evicted_bytes += sz
                total_temp_bytes -= sz
            except OSError:
                pass

    # 2. Enforce cache directory budget
    cache_files = []
    if cache_dir.exists():
        for p in cache_dir.rglob("*"):
            if p.is_file():
                try:
                    stat = p.stat()
                    cache_files.append((p, stat.st_size, stat.st_mtime))
                except OSError:
                    pass

    total_cache_bytes = sum(sz for _, sz, _ in cache_files)

    # In local backend mode, all files under cache_dir / "evidence" are sole copies of record!
    # They must NEVER be evicted.
    is_local_backend = cfg.object_backend == "local"
    s3_client = client or (get_minio_client() if cfg.object_backend == "s3" else None)

    cache_files.sort(key=lambda x: x[2])
    for p, sz, _ in cache_files:
        if is_local_backend:
            # Sole copy - protect from deletion
            protected_files_count += 1
            continue

        # In S3 mode, verify reproducible remote copy before evicting
        if total_cache_bytes > cache_limit:
            is_reproducible = False
            if s3_client is not None:
                try:
                    digest = p.name
                    object_name = f"{cfg.object_prefix}/{digest[:2]}/{digest}"
                    if s3_client.object_exists(cfg.object_bucket, object_name):
                        remote_bytes = s3_client.download_bytes(cfg.object_bucket, object_name)
                        remote_digest = hashlib.sha256(remote_bytes).hexdigest()
                        if remote_digest == digest:
                            is_reproducible = True
                        else:
                            logger.warning(f"Remote object {object_name} hash mismatch; protecting local copy")
                            is_reproducible = False
                except Exception as exc:
                    logger.warning(f"Failed verifying remote object {p.name}: {exc}; protecting local copy")
                    is_reproducible = False

            if is_reproducible:
                try:
                    p.unlink(missing_ok=True)
                    evicted_files_count += 1
                    evicted_bytes += sz
                    total_cache_bytes -= sz
                except OSError:
                    pass
            else:
                protected_files_count += 1
        else:
            protected_files_count += 1

    # Measure free disk
    data_dir = Path(cfg.data_dir)
    target_check = data_dir if data_dir.exists() else Path.cwd()
    free_disk_bytes = shutil.disk_usage(target_check).free
    disk_floor_breached = free_disk_bytes < disk_floor

    total_after = 0
    if temp_dir.exists():
        total_after += sum(p.stat().st_size for p in temp_dir.rglob("*") if p.is_file())
    if cache_dir.exists():
        total_after += sum(p.stat().st_size for p in cache_dir.rglob("*") if p.is_file())

    status = "enforced"
    if total_after > cache_limit or disk_floor_breached:
        status = "paused"

    return {
        "status": status,
        "total_bytes_before": total_temp_bytes + total_cache_bytes + evicted_bytes,
        "total_bytes_after": total_after,
        "evicted_files_count": evicted_files_count,
        "evicted_bytes": evicted_bytes,
        "protected_files_count": protected_files_count,
        "free_disk_bytes": free_disk_bytes,
        "disk_floor_breached": disk_floor_breached,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def verify_restore(
    manifest_uri: str,
    target_dir: Union[str, Path],
    *,
    trusted_checksum: Optional[str] = None,
    settings: Optional[Any] = None,
    archive: Optional[EvidenceArchive] = None,
    store: Optional[Any] = None,
    ledger: Optional[Any] = None,
) -> Dict[str, Any]:
    """Validate a complete batch, then promote a portable restore into fresh sinks.

    Canonical and SQLite reconstruction happens privately before publication. Sink
    renames roll back on ordinary errors; this is not a cross-filesystem transaction.
    """
    from src.ingestion import ArchiveError, DiskFloorError
    from src.schemas.ingestion import BatchManifest, Entity, EvidenceLink, Fact, Observation, SeriesDefinition, SourceDocument
    from src.storage.dual_layer_store import DualLayerStore
    from src.storage.ingestion_jobs import IngestionJobLedger
    cfg = settings or get_settings()
    arch = archive or EvidenceArchive(cfg)
    requested_target = Path(target_dir).absolute()
    def is_link(path):
        return path.is_symlink() or getattr(path, "is_junction", lambda: False)()
    if any(is_link(p) for p in (requested_target, *requested_target.parents)):
        raise ArchiveError("Restore target must not use symlinks or junctions")
    target_path = requested_target.resolve()
    if target_path.exists():
        if not target_path.is_dir() or any(target_path.iterdir()):
            raise ArchiveError(f"Target restore directory is not fresh / contains existing files: {target_path}")
    expected_digest = trusted_checksum
    if not expected_digest:
        uri_token = manifest_uri.rstrip("/").split("/")[-1]
        if len(uri_token) == 64 and all(c in "0123456789abcdefABCDEF" for c in uri_token):
            expected_digest = uri_token.lower()
    if not isinstance(expected_digest, str) or len(expected_digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_digest):
        raise ArchiveError("manifest verification requires a trusted expected digest")
    if manifest_uri.startswith("file://") or manifest_uri.startswith("s3://"):
        if manifest_uri.startswith("file://"):
            from urllib.parse import urlparse, unquote
            p_str = unquote(urlparse(manifest_uri).path)
            if os.name == "nt" and len(p_str) >= 3 and p_str[0] == "/" and p_str[2] == ":":
                p_str = p_str[1:]
            manifest_file = Path(p_str)
            if manifest_file.exists():
                manifest_bytes = manifest_file.read_bytes()
            else:
                digest = expected_digest or manifest_file.name
                manifest_bytes = arch.get(manifest_uri, digest)
        else:
            from urllib.parse import urlparse
            parsed = urlparse(manifest_uri)
            object_name = parsed.path.lstrip("/")
            digest = expected_digest or object_name.split("/")[-1]
            manifest_bytes = arch.get(manifest_uri, digest)
    else:
        p = Path(manifest_uri)
        if not p.exists():
            raise ArchiveError(f"manifest file does not exist: {manifest_uri}")
        manifest_bytes = p.read_bytes()

    # Verify manifest checksum
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha != expected_digest.lower():
        raise ArchiveError(f"manifest checksum mismatch: expected {expected_digest}, got {actual_manifest_sha}")

    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise ArchiveError(f"manifest is invalid JSON: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("manifest_version", "1") not in {"1", "2"}:
        raise ArchiveError("Unsupported manifest version or payload")
    reserved = {
        "manifest.json", "canonical_facts.json", "canonical_observations.json",
        "canonical_entities.json", "canonical_series.json", "canonical_evidence.json",
        "cursor.json", "restore_receipt.json", "archive_manifest.json", "canonical", "ingestion.sqlite"
    }
    names = set()
    for row in payload.get("documents", []):
        version = row.get("version_id") if isinstance(row, dict) else None
        if not isinstance(version, str) or not version or any(c in version for c in ("/", "\\", ":")) or ".." in version:
            raise ArchiveError(f"Path traversal detected in version_id: {version}")
        if version.lower() in reserved:
            raise ArchiveError(f"version_id matches reserved metadata filename: {version}")
        if version.lower() in names or version.rstrip(". ") != version:
            raise ArchiveError("Duplicate or unsafe document filename")
        names.add(version.lower())
        if type(row.get("byte_count")) is not int or row["byte_count"] < 0:
            raise ArchiveError("Document byte_count must be a non-negative integer")
    contracts = {"documents": (SourceDocument, "version_id"), "facts": (Fact, "fact_version_id"),
                 "observations": (Observation, "revision_id"), "entities": (Entity, "entity_id"),
                 "series": (SeriesDefinition, "series_id"), "evidence_links": (EvidenceLink, "evidence_id")}
    records = {}
    for name, (model, key) in contracts.items():
        rows = payload.get(name, [])
        if not isinstance(rows, list):
            raise ArchiveError(f"Manifest {name} must be a list")
        if any(not isinstance(row, dict) or not row.get(key) for row in rows):
            raise ArchiveError(f"Manifest {name} must retain original {key} identities")
        records[name] = [model.model_validate(row) for row in rows]
        identities = [getattr(row, key) for row in records[name]]
        if len(set(identities)) != len(identities):
            raise ArchiveError(f"Duplicate {name} identity")
    origins = {record.data_origin for group in records.values() for record in group if hasattr(record, "data_origin")}
    origin = payload.get("data_origin")
    if origin is None and payload.get("manifest_version", "1") == "1" and len(origins) == 1:
        origin = next(iter(origins))
    if origin not in {"live", "demo", "test"} or origins - {origin}:
        raise ArchiveError("Manifest has missing or inconsistent data_origin")
    batch_id, source_id = payload.get("batch_id"), payload.get("source_id")
    if not isinstance(batch_id, str) or not batch_id or not isinstance(source_id, str) or not source_id:
        raise ArchiveError("Manifest missing valid batch_id or source_id")
    metadata = payload.get("batch_metadata", {"batch_id": batch_id, "source_id": source_id, "data_origin": origin})
    if payload.get("manifest_version") == "2" and "batch_metadata" not in payload:
        raise ArchiveError("Version 2 manifest requires complete batch metadata")
    batch = BatchManifest.model_validate(metadata)
    if (batch.batch_id, batch.source_id, batch.data_origin) != (batch_id, source_id, origin):
        raise ArchiveError("Batch metadata disagrees with manifest identity or origin")
    if batch.status not in {"staged", "committed"}:
        raise ArchiveError("Cannot restore a failed or rolled-back batch")
    if any(value < 0 for key, value in batch.model_dump().items() if key.endswith("_count")):
        raise ArchiveError("Batch counts must be non-negative")
    if payload.get("manifest_version") == "2" and (batch.fetched_count != len(records["documents"]) or batch.discovered_count < batch.fetched_count):
        raise ArchiveError("Batch document counts disagree with manifest completeness")
    for group in records.values():
        for record in group:
            if getattr(record, "batch_id", None) != batch_id:
                raise ArchiveError("Portable manifest must contain all records in its batch")
    if any(doc.source_id != source_id or not doc.raw_uri for doc in records["documents"]):
        raise ArchiveError("Document source or raw URI is inconsistent with manifest")
    assertions = {fact.fact_version_id: "FACT" for fact in records["facts"]}
    assertions.update({obs.revision_id: "OBSERVATION" for obs in records["observations"]})
    for link in records["evidence_links"]:
        if assertions.get(link.assertion_version_id) != link.assertion_kind:
            raise ArchiveError("Evidence assertion_kind disagrees with assertion identity")
    cursor = payload.get("cursor_value")
    cursor_key = payload.get("cursor_key", "default")
    if (cursor is not None and not isinstance(cursor, str)) or not isinstance(cursor_key, str) or not cursor_key:
        raise ArchiveError("Invalid cursor metadata")

    destinations = [target_path]
    if store is not None:
        store._ensure_observations_loaded()
        if any(getattr(store, f"_canonical_{name}") for name in ("batches", "documents", "facts", "observations", "evidence", "entities", "series", "aliases")) or store.count_nodes() or store.count_history():
            raise ArchiveError("Restore store must be fresh and empty")
        destinations.append(Path(store.storage_dir).absolute())
    if ledger is not None:
        with ledger._connection() as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            if any(db.execute('SELECT 1 FROM "' + name.replace('"', '""') + '" LIMIT 1').fetchone() for name in tables):
                raise ArchiveError("Restore ledger must be fresh and empty")
        destinations.append(Path(ledger.path).absolute())
    for i, path in enumerate(destinations):
        if any(is_link(p) for p in (path, *path.parents)):
            raise ArchiveError("Restore sinks must not use symlinks or junctions")
        if any(path == other or path in other.parents or other in path.parents for other in destinations[:i]):
            raise ArchiveError("Restore sinks must not overlap")

    restored_docs = [doc.model_copy(update={"raw_uri": (target_path / doc.version_id).as_uri()}) for doc in records["documents"]]
    portable = dict(payload, data_origin=origin, documents=[d.model_dump(mode="json") for d in restored_docs])
    timestamp = datetime.now(timezone.utc).isoformat()
    raw_bytes = sum(doc.byte_count for doc in restored_docs)
    receipt = {"batch_id": batch_id, "manifest_uri": manifest_uri, "archive_sha256": expected_digest,
               "documents_restored": len(restored_docs), "bytes_restored": raw_bytes, "restored_at": timestamp,
               "entries": [{"version_id": doc.version_id, "raw_uri": doc.raw_uri, "sha256": doc.content_sha256,
                            "bytes": doc.byte_count} for doc in restored_docs]}
    files = {"archive_manifest.json": manifest_bytes, "manifest.json": json.dumps(portable, indent=2).encode(),
             "cursor.json": json.dumps({"cursor": cursor, "cursor_key": cursor_key}).encode(),
             "restore_receipt.json": json.dumps(receipt, indent=2).encode()}
    for name in ("facts", "observations", "entities", "series", "evidence_links"):
        filename = "canonical_evidence.json" if name == "evidence_links" else f"canonical_{name}.json"
        files[filename] = json.dumps(payload.get(name, []), indent=2).encode()

    # Reserve metadata and two canonical saves, including Arrow/Lance/SQLite overhead.
    # shortcut: fixed-format overhead allowance, reject larger actual footprints before promotion.
    allowance = 16 * 1024 * 1024 + 8 * len(manifest_bytes)
    allocation = raw_bytes + sum(len(value) + 4096 for value in files.values()) + allowance
    floor = getattr(cfg, "disk_floor_bytes", 0)
    def check_space(required):
        for destination in destinations:
            parent = destination.parent
            while not parent.exists():
                parent = parent.parent
            free = shutil.disk_usage(parent).free
            if free - required < floor:
                raise DiskFloorError(f"Restore would breach disk floor: free {free} - allocation {required} < floor {floor}")
    check_space(allocation)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=target_path.parent) as work:
        work_path = Path(work)
        prepared = work_path / "target"
        prepared.mkdir()
        shadow = DualLayerStore(prepared / "canonical")
        shadow_ledger = IngestionJobLedger(prepared / "ingestion.sqlite")
        restored_batch = batch.model_copy(update={"manifest_uri": (target_path / "archive_manifest.json").as_uri(),
                                                  "archive_sha256": expected_digest.lower()})
        shadow.stage_batch(restored_batch, restored_docs, records["facts"], records["observations"],
                           evidence_links=records["evidence_links"], entities=records["entities"], series=records["series"])
        check_space(raw_bytes + sum(len(value) + 4096 for value in files.values()) + allowance // 2)
        for original, restored in zip(records["documents"], restored_docs):
            try:
                content = arch.get(original.raw_uri, original.content_sha256)
            except Exception as exc:
                raise ArchiveError(f"checksum verification failed for {original.raw_uri}: {exc}") from exc
            if len(content) != original.byte_count or hashlib.sha256(content).hexdigest() != original.content_sha256:
                raise ArchiveError("Document byte_count or checksum does not agree with raw bytes")
            check_space(len(content))
            (prepared / restored.version_id).write_bytes(content)
        check_space(sum(len(value) + 4096 for value in files.values()) + allowance // 2)
        shadow.commit_batch(batch_id, committed_at=batch.committed_at)
        shadow_ledger.ensure_run(f"restore_{batch_id}", source_id, 0.0)
        shadow_ledger.record_batch(batch_id, f"restore_{batch_id}", source_id, "committed",
                                   manifest_uri=(target_path / "archive_manifest.json").as_uri(), archive_sha256=expected_digest)
        if cursor is not None:
            shadow_ledger.advance_cursor(source_id, cursor, cursor_key)
        for name, content in files.items():
            check_space(len(content) + 4096)
            (prepared / name).write_bytes(content)
        actual = sum(p.stat().st_size + 4096 for p in prepared.rglob("*") if p.is_file())
        if actual > allocation:
            raise DiskFloorError("Restore canonical footprint exceeds reserved allocation")
        check_space(0)
        promotions = []
        if store is not None:
            promotions.append((prepared / "canonical", Path(store.storage_dir).resolve()))
        if ledger is not None:
            promotions.append((prepared / "ingestion.sqlite", Path(ledger.path).resolve()))
        promotions.append((prepared, target_path))
        moved, backups = [], []
        try:
            for i, (source, destination) in enumerate(promotions):
                backup = work_path / f"backup-{i}"
                if destination.exists():
                    os.replace(destination, backup)
                    backups.append((backup, destination))
                os.replace(source, destination)
                moved.append((destination, source))
            if store is not None:
                store.__init__(store.storage_dir, nodes_table_name=store.nodes_table_name,
                               edges_table_name=store.edges_table_name, history_table_name=store.history_table_name)
        except BaseException:
            for destination, source in reversed(moved):
                os.replace(destination, source)
            for backup, destination in reversed(backups):
                os.replace(backup, destination)
            if store is not None:
                store.__init__(store.storage_dir, nodes_table_name=store.nodes_table_name,
                               edges_table_name=store.edges_table_name, history_table_name=store.history_table_name)
            raise
    return {"status": "restored", "batch_id": batch_id, "manifest_uri": manifest_uri,
            "documents_restored": len(restored_docs), "bytes_restored": raw_bytes,
            "target_dir": str(target_path), "timestamp": timestamp, "allocated_bytes": actual}



# ============ CONVENIENCE FUNCTIONS ============

_client: Optional[MinioClient] = None

def get_minio_client() -> MinioClient:
    """Get singleton MinIO client"""
    global _client
    if _client is None:
        _client = MinioClient()
    return _client


def upload_law_pdf(file_path: Union[str, Path]) -> str:
    """
    Upload a law PDF and return S3 URI.
    Uploads directly to root of 'laws' bucket (Flat structure), or uses EvidenceArchive in test/local mode.
    """
    path = Path(file_path)
    settings = get_settings()
    if settings.mode == "test" or settings.object_backend == "local" or os.environ.get("POWERLAW_MODE") == "test":
        archive = EvidenceArchive(settings)
        uri, _ = archive.put(path.read_bytes(), content_type="application/pdf")
        return uri
    object_name = path.name
    return get_minio_client().upload_pdf(BUCKET_LAWS, path, object_name)


def upload_news_raw(article_id: str, raw_text: str) -> str:
    """Upload raw news text and return S3 URI"""
    date_prefix = datetime.now().strftime("%Y/%m")
    object_name = f"{date_prefix}/{article_id}.txt"
    text_bytes = raw_text.encode('utf-8')
    return get_minio_client().upload_bytes(BUCKET_NEWS, text_bytes, object_name, "text/plain")


def upload_paper_pdf(file_path: Union[str, Path]) -> str:
    """Upload a research paper PDF and return S3 URI"""
    return get_minio_client().upload_pdf(BUCKET_PAPERS, file_path)


def upload_bytes(bucket: str, data: bytes, object_name: str, content_type: str = "application/json") -> str:
    """Upload raw bytes and return S3 URI"""
    return get_minio_client().upload_bytes(bucket, data, object_name, content_type)


# Alias for backward compatibility
MinIOClient = MinioClient


# ============ TEST ============

if __name__ == "__main__":
    print("Testing MinIO Client...")
    
    try:
        client = MinioClient()
        print(f"Buckets: {client.list_buckets()}")
        
        # Test upload bytes
        test_data = {"test": "Hello MinIO!", "timestamp": datetime.now().isoformat()}
        uri = client.upload_json("news", test_data, "test/test_article.json")
        print(f"Uploaded test: {uri}")
        
        # Test download
        downloaded = client.download_json("news", "test/test_article.json")
        print(f"Downloaded: {downloaded}")
        
        # Cleanup
        client.delete_object("news", "test/test_article.json")
        print("✅ All tests passed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        print("Make sure MinIO is running: docker-compose up -d minio")
