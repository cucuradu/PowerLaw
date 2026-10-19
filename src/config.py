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
    extraction_total_budget_eur: float = Field(default=0.0, ge=0, allow_inf_nan=False, description="Overall application spending ceiling in EUR")
    extraction_eur_usd_rate: float = Field(default=1.0, gt=0, allow_inf_nan=False, description="Verified USD per EUR used to freeze the spending ceiling")
    extraction_input_price_usd_per_1k: Optional[float] = Field(default=None, description="Verified paid input price")
    extraction_output_price_usd_per_1k: Optional[float] = Field(default=None, description="Verified paid output price")
    extraction_embedding_price_usd_per_1k: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False, description="Verified embedding model input-token price")
    google_api_key: Optional[str] = Field(default=None, description="Google Gemini API key")

    model_config = {"arbitrary_types_allowed": True}

    @property
    def database_path(self) -> Path:
        """Alias for canonical LanceDB database path."""
        return self.lancedb_path

    @property
    def extraction_total_budget_usd(self) -> float:
        # Leave ten percent of the EUR ceiling unused for FX and billing differences.
        return round(self.extraction_total_budget_eur * self.extraction_eur_usd_rate * 0.9, 6)

    def __repr__(self) -> str:
        """Redact sensitive keys from representation."""
        return (
            f"Settings(mode={self.mode!r}, data_dir={self.data_dir}, "
            f"lancedb_path={self.lancedb_path}, sqlite_path={self.sqlite_path}, "
            f"object_backend={self.object_backend!r}, object_bucket={self.object_bucket!r}, "
            f"extraction_budget_usd={self.extraction_budget_usd})"
        )


_SETTINGS_INSTANCE: Optional[Settings] = None
REPO_ROOT = Path(__file__).resolve().parent.parent


def _repo_path(value: str) -> Path:
    """Relative paths in .env/defaults are relative to the repository, not the caller's working directory."""
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def get_settings() -> Settings:
    """
    Returns the singleton Settings instance, resolved from environment variables.
    The repository .env is loaded first; variables already set in the process environment win.
    """
    global _SETTINGS_INSTANCE
    if _SETTINGS_INSTANCE is not None:
        return _SETTINGS_INSTANCE

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)

    mode = os.getenv("POWERLAW_MODE", "production").lower()
    if mode not in ["production", "demo", "test"]:
        mode = "production"

    # Base data directory
    if os.getenv("POWERLAW_DATA_DIR"):
        data_dir = _repo_path(os.environ["POWERLAW_DATA_DIR"])
    else:
        data_dir = _repo_path({"demo": "data/demo", "test": "data/test"}.get(mode, "data/production"))

    # LanceDB Path
    if os.getenv("LANCEDB_PATH"):
        lancedb_path = _repo_path(os.environ["LANCEDB_PATH"])
    else:
        lancedb_path = data_dir / "lancedb"

    # SQLite Path
    if os.getenv("POWERLAW_SQLITE_PATH"):
        sqlite_path = _repo_path(os.environ["POWERLAW_SQLITE_PATH"])
    else:
        sqlite_path = data_dir / "ingestion.sqlite"

    # Cache & Temp dirs
    cache_dir = _repo_path(os.getenv("POWERLAW_CACHE_DIR", str(data_dir / "cache")))
    temp_dir = _repo_path(os.getenv("POWERLAW_TEMP_DIR", str(data_dir / "temp")))

    # Numeric budgets
    cache_limit = int(os.getenv("POWERLAW_CACHE_LIMIT_BYTES", str(2 * 1024 * 1024 * 1024)))
    temp_limit = int(os.getenv("POWERLAW_TEMP_LIMIT_BYTES", str(512 * 1024 * 1024)))
    disk_floor = int(os.getenv("POWERLAW_DISK_FLOOR_BYTES", str(2 * 1024 * 1024 * 1024)))
    extraction_budget = float(os.getenv("POWERLAW_EXTRACTION_BUDGET_USD", "0.0"))
    input_price = os.getenv("POWERLAW_EXTRACTION_INPUT_PRICE_USD_PER_1K")
    output_price = os.getenv("POWERLAW_EXTRACTION_OUTPUT_PRICE_USD_PER_1K")
    embedding_price = os.getenv("POWERLAW_EXTRACTION_EMBEDDING_PRICE_USD_PER_1K")

    # Object storage
    object_backend = os.getenv("POWERLAW_OBJECT_BACKEND", "local")
    minio_endpoint = os.getenv("MINIO_ENDPOINT")
    minio_secure = os.getenv("MINIO_SECURE", "true").lower() in ("true", "1", "yes")
    minio_access_key = os.getenv("MINIO_ACCESS_KEY")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY")
    object_bucket = os.getenv("POWERLAW_OBJECT_BUCKET", "powerlaw-evidence")
    object_prefix = os.getenv("POWERLAW_OBJECT_PREFIX", "powerlaw")

    # Keep the legacy settings field for callers; authentication now uses ADC.
    google_api_key = None

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
        extraction_total_budget_eur=float(os.getenv("POWERLAW_EXTRACTION_TOTAL_BUDGET_EUR", "0")),
        extraction_eur_usd_rate=float(os.getenv("POWERLAW_EXTRACTION_EUR_USD_RATE", "1")),
        extraction_input_price_usd_per_1k=float(input_price) if input_price else None,
        extraction_output_price_usd_per_1k=float(output_price) if output_price else None,
        extraction_embedding_price_usd_per_1k=float(embedding_price) if embedding_price else None,
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
        registry_path = Path(__file__).resolve().parent.parent / "config" / "data_sources.json"

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


def primary_target_series_id() -> str:
    """Canonical series_id of the forecast target, exactly as statistics ingestion stores it."""
    return load_data_sources_registry()["primary_target"]["provider_series_key"]


def validate_source_entry(source_id: str, entry: dict) -> None:
    """
    Validates that a candidate data source satisfies the free-source policy:
    1. Zero access fee and no paid subscription.
    2. No trial / evaluation tiers (even if currently free).
    3. No AI-training or commercial use restrictions.
    4. Verified rights check performed with terms documentation.
    """
    if "access_fee" not in entry or "requires_paid_subscription" not in entry:
        raise ValueError(
            f"Source {source_id} lacks explicit fee/subscription attestation. Both 'access_fee' (must be 0) and 'requires_paid_subscription' (must be False) are required."
        )
    access_fee = entry["access_fee"]
    requires_sub = entry["requires_paid_subscription"]

    # 1. Zero fees and no paid subscriptions
    if access_fee != 0 or requires_sub is not False:
        raise ValueError(
            f"Source {source_id} violates free-source policy (access_fee={access_fee}, "
            f"requires_paid_subscription={requires_sub}). Paid sources cannot be enabled."
        )

    # 2. Reject trial sources
    is_trial = (
        bool(entry.get("trial", False))
        or bool(entry.get("is_trial", False))
        or str(entry.get("tier", "")).lower() == "trial"
        or "trial" in str(entry.get("license", "")).lower()
        or "trial" in str(entry.get("rights", "")).lower()
        or "trial" in str(entry.get("name", "")).lower()
    )
    if is_trial:
        raise ValueError(
            f"Source {source_id} is a trial source. Trial/temporary tier sources cannot be enabled."
        )

    # 3. Reject AI training or commercial reuse restrictions
    is_restricted = (
        entry.get("training_allowed") is False
        or entry.get("commercial_use_allowed") is False
        or bool(entry.get("ai_training_restricted", False))
        or bool(entry.get("training_restricted", False))
        or "no-training" in str(entry.get("rights", "")).lower()
        or "non-commercial" in str(entry.get("rights", "")).lower()
        or "no commercial" in str(entry.get("rights", "")).lower()
        or ("restricted" in str(entry.get("rights", "")).lower() and "unrestricted" not in str(entry.get("rights", "")).lower())
    )
    if is_restricted:
        raise ValueError(
            f"Source {source_id} contains training or commercial reuse restrictions "
            f"({entry.get('rights', 'restricted')}). Training-restricted sources cannot be enabled."
        )

    # 4. Mandatory rights verification
    rights_checked = entry.get("rights_checked")
    rights_text = str(entry.get("rights", "")).strip()
    terms_checked_at = str(entry.get("terms_checked_at", "")).strip()

    if not rights_checked or not rights_text or not terms_checked_at:
        raise ValueError(
            f"Source {source_id} lacks verified rights check. Explicit 'rights_checked=True', "
            f"'rights', and 'terms_checked_at' are required before source registration."
        )


def get_primary_target_definition(registry_path: Optional[Path] = None) -> dict:
    """Returns the locked primary construction-price target definition."""
    registry = load_data_sources_registry(registry_path)
    target = registry.get("primary_target")
    if not target:
        raise KeyError("Primary target definition missing in data_sources.json")
    return target
