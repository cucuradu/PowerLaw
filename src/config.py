"""
Centralized Configuration & Origin Management for PowerLaw / ConstructPulse.
Implements the environment-aware path policy and origin isolation from Task 1.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class Settings(BaseModel):
    """
    Unified settings defining canonical store locations, storage limits,
    object storage credentials, and execution origin (production / demo / test).
    """
    mode: str = Field(default="production", description="Execution origin: production, demo, test")
    data_dir: Path = Field(default=Path("./data/production"), description="Root data directory")
    lancedb_path: Path = Field(default=Path("./data/production/lancedb"), description="Canonical LanceDB storage path")
    sqlite_path: Path = Field(default=Path("./data/production/ingestion.sqlite"), description="Local SQLite ledger path")
    
    # Object Storage (S3 / Cloudflare R2 / MinIO)
    object_backend: str = Field(default="local", description="Object storage backend: local or s3")
    minio_endpoint: Optional[str] = Field(default=None, description="Host endpoint for S3-compatible service")
    minio_secure: bool = Field(default=True, description="Whether to connect over HTTPS (TLS)")
    minio_access_key: Optional[str] = Field(default=None, description="S3 Access Key ID")
    minio_secret_key: Optional[str] = Field(default=None, description="S3 Secret Access Key")
    object_bucket: str = Field(default="powerlaw-evidence", description="Private bucket name")
    object_prefix: str = Field(default="powerlaw", description="Object key prefix")
    
    # Storage & Temporary Budgets
    cache_dir: Path = Field(default=Path("./data/production/cache"), description="Local cache directory")
    cache_limit_bytes: int = Field(default=2 * 1024 * 1024 * 1024, description="Maximum cache size in bytes (2 GiB)")
    temp_dir: Path = Field(default=Path("./data/production/temp"), description="Temporary download directory")
    temp_limit_bytes: int = Field(default=512 * 1024 * 1024, description="Maximum temp directory size (512 MiB)")
    disk_floor_bytes: int = Field(default=2 * 1024 * 1024 * 1024, description="Minimum free disk floor before pausing (2 GiB)")
    
    # Extraction & AI Budget
    extraction_budget_usd: float = Field(default=0.0, description="Run-wide spending budget for paid LLM extraction in USD")
    google_api_key: Optional[str] = Field(default=None, description="Google Gemini API key")

    model_config = {"arbitrary_types_allowed": True}

    def __repr__(self) -> str:
        """Redact sensitive keys from representation."""
        return (
            f"Settings(mode={self.mode!r}, data_dir={self.data_dir}, "
            f"lancedb_path={self.lancedb_path}, sqlite_path={self.sqlite_path}, "
            f"object_backend={self.object_backend!r}, object_bucket={self.object_bucket!r}, "
            f"extraction_budget_usd={self.extraction_budget_usd})"
        )


_SETTINGS_INSTANCE: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Returns the singleton Settings instance, resolved from environment variables.
    """
    global _SETTINGS_INSTANCE
    if _SETTINGS_INSTANCE is not None:
        return _SETTINGS_INSTANCE

    mode = os.getenv("POWERLAW_MODE", "production").lower()
    if mode not in ["production", "demo", "test"]:
        mode = "production"

    # Base data directory
    if os.getenv("POWERLAW_DATA_DIR"):
        data_dir = Path(os.environ["POWERLAW_DATA_DIR"])
    elif mode == "demo":
        data_dir = Path("./data/demo")
    elif mode == "test":
        data_dir = Path("./data/test")
    else:
        data_dir = Path("./data/production")

    # LanceDB Path
    if os.getenv("LANCEDB_PATH"):
        lancedb_path = Path(os.environ["LANCEDB_PATH"])
    else:
        lancedb_path = data_dir / "lancedb"

    # SQLite Path
    if os.getenv("POWERLAW_SQLITE_PATH"):
        sqlite_path = Path(os.environ["POWERLAW_SQLITE_PATH"])
    else:
        sqlite_path = data_dir / "ingestion.sqlite"

    # Cache & Temp dirs
    cache_dir = Path(os.getenv("POWERLAW_CACHE_DIR", str(data_dir / "cache")))
    temp_dir = Path(os.getenv("POWERLAW_TEMP_DIR", str(data_dir / "temp")))

    # Numeric budgets
    cache_limit = int(os.getenv("POWERLAW_CACHE_LIMIT_BYTES", str(2 * 1024 * 1024 * 1024)))
    temp_limit = int(os.getenv("POWERLAW_TEMP_LIMIT_BYTES", str(512 * 1024 * 1024)))
    disk_floor = int(os.getenv("POWERLAW_DISK_FLOOR_BYTES", str(2 * 1024 * 1024 * 1024)))
    extraction_budget = float(os.getenv("POWERLAW_EXTRACTION_BUDGET_USD", "0.0"))

    # Object storage
    object_backend = os.getenv("POWERLAW_OBJECT_BACKEND", "local")
    minio_endpoint = os.getenv("MINIO_ENDPOINT")
    minio_secure = os.getenv("MINIO_SECURE", "true").lower() in ("true", "1", "yes")
    minio_access_key = os.getenv("MINIO_ACCESS_KEY")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY")
    object_bucket = os.getenv("POWERLAW_OBJECT_BUCKET", "powerlaw-evidence")
    object_prefix = os.getenv("POWERLAW_OBJECT_PREFIX", "powerlaw")

    google_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    _SETTINGS_INSTANCE = Settings(
        mode=mode,
        data_dir=data_dir,
        lancedb_path=lancedb_path,
        sqlite_path=sqlite_path,
        object_backend=object_backend,
        minio_endpoint=minio_endpoint,
        minio_secure=minio_secure,
        minio_access_key=minio_access_key,
        minio_secret_key=minio_secret_key,
        object_bucket=object_bucket,
        object_prefix=object_prefix,
        cache_dir=cache_dir,
        cache_limit_bytes=cache_limit,
        temp_dir=temp_dir,
        temp_limit_bytes=temp_limit,
        disk_floor_bytes=disk_floor,
        extraction_budget_usd=extraction_budget,
        google_api_key=google_api_key
    )
    return _SETTINGS_INSTANCE


def clear_settings_cache() -> None:
    """Clears the cached Settings instance (primarily for test isolation)."""
    global _SETTINGS_INSTANCE
    _SETTINGS_INSTANCE = None


def load_data_sources_registry(registry_path: Optional[Path] = None) -> dict:
    """
    Loads and validates the companion free-source register from config/data_sources.json.
    Enforces access_fee == 0 and requires_paid_subscription is False for all tracked sources.
    """
    if registry_path is None:
        registry_path = Path("config/data_sources.json")

    if not registry_path.exists():
        raise FileNotFoundError(f"Data sources registry not found at {registry_path}")

    import json
    with open(registry_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate sources
    sources = data.get("sources", {})
    for src_id, src_meta in sources.items():
        validate_source_entry(src_id, src_meta)

    return data


def validate_source_entry(source_id: str, entry: dict) -> None:
    """
    Validates that a candidate data source satisfies the free-source policy:
    access_fee == 0, requires_paid_subscription is False, and no automatic paid fallback.
    """
    access_fee = entry.get("access_fee", 0)
    requires_sub = entry.get("requires_paid_subscription", False)

    if access_fee > 0 or requires_sub:
        raise ValueError(
            f"Source {source_id} violates free-source policy (access_fee={access_fee}, "
            f"requires_paid_subscription={requires_sub}). Paid sources cannot be enabled."
        )


def get_primary_target_definition(registry_path: Optional[Path] = None) -> dict:
    """Returns the locked primary construction-price target definition."""
    registry = load_data_sources_registry(registry_path)
    target = registry.get("primary_target")
    if not target:
        raise KeyError("Primary target definition missing in data_sources.json")
    return target
