"""
Canonical Ingestion Contracts & Schemas for PowerLaw / ConstructPulse.
Implements the canonical data models, SHA-256 stable identity, versioning,
evidence linking, and quality decision interfaces from Task 2.
"""

import json
import math
import hashlib
from datetime import datetime, date, timezone
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field, model_validator, field_validator


# ---------------------------------------------------------------------------
# Valid predicates – controlled vocabulary aligned with EdgeRelation enum
# ---------------------------------------------------------------------------
VALID_PREDICATES = frozenset({
    "MANDATES", "TRIGGERS", "REGULATES", "AFFECTS", "AFFECTS_COST_OF",
    "IMPACTS", "CAUSES", "AMENDS", "PERFORMS", "BELONGS_TO", "ABOUT",
    "LOCATED_IN", "WITHIN", "MEASURES", "FLOWS_TO", "APPLIES_IN", "REACTS_TO",
})

# Entity types that represent nodes (not edges)
NODE_ENTITY_TYPES = frozenset({
    "LAW", "ACTION", "REACTION", "SECTOR", "COMMODITY",
    "ECONOMIC_INDEX", "COMPANY", "GEOGRAPHY",
})


def stable_id(namespace: str, *parts: str) -> str:
    """
    Generates a deterministic 64-character SHA-256 stable identity string
    prefixed with the namespace.

    Uses length-prefixed serialization so that
    ``stable_id("x", "a:b", "c") != stable_id("x", "a", "b:c")``.
    """
    normalized_ns = str(namespace).strip().lower()
    # Length-prefix each part to avoid delimiter collisions
    encoded_parts = []
    for p in parts:
        s = str(p).strip()
        encoded_parts.append(f"{len(s)}:{s}")
    raw_key = f"{normalized_ns}|{'|'.join(encoded_parts)}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"{namespace}_{digest}"


def _canonical_json(value: Optional[str]) -> str:
    """Return a deterministic canonical JSON string (sorted keys).

    If *value* is valid JSON, it is re-serialized with sorted keys so that
    logically equivalent payloads always produce the same string. Non-JSON
    values (including ``None``) are returned as-is or as empty string.
    """
    if not value:
        return ""
    try:
        obj = json.loads(value)
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return value


def _parse_date_str(value: Optional[Union[datetime, date, str]]) -> Optional[str]:
    """Validate and normalise an ISO-8601 date string or datetime. Returns the
    normalised ``YYYY-MM-DD`` string or *None* for genuinely unknown dates.
    Raises ``ValueError`` on malformed input.
    """
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError, IndexError):
        raise ValueError(f"Malformed date: {s!r} – expected YYYY-MM-DD")
    return parsed.date().isoformat()


def _identity_timestamp(value) -> str:
    if value is None:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, AttributeError):
        return str(value)  # Invalid candidates retain stable IDs until validation rejects them.


class SourceDocument(BaseModel):
    """
    Canonical source document model representing an immutable retrieved artifact.
    """
    document_id: str = Field(..., description="Stable SHA-256 ID of the source + canonical_url")
    version_id: str = Field(..., description="Stable SHA-256 ID of the document_id + content_sha256")
    source_id: str = Field(..., description="Source identifier, e.g. EURLEX_CELLAR, DESTATIS_GENESIS")
    upstream_id: Optional[str] = Field(default=None, description="Upstream provider identifier, e.g. CELEX, GUID")
    canonical_url: str = Field(..., description="Normalized canonical URL")
    final_url: str = Field(..., description="Final URL after following redirects")
    publisher: str = Field(..., description="Publisher name")
    language: str = Field(default="en", description="ISO language code")
    content_type: str = Field(default="text/html", description="MIME content type")
    content_sha256: str = Field(..., description="SHA-256 hex digest of the raw content bytes")
    raw_uri: Optional[str] = Field(default=None, description="Object storage URI to raw bytes, e.g. s3://bucket/key")
    text_uri: Optional[str] = Field(default=None, description="Object storage URI to parsed text")
    byte_count: int = Field(default=0, description="Size in bytes")
    published_at: Optional[datetime] = Field(default=None, description="Publisher declared date/time (UTC)")
    source_updated_at: Optional[datetime] = Field(default=None, description="Publisher declared update date/time (UTC)")
    available_at: Optional[datetime] = Field(default=None, description="Earliest verified public availability (UTC)")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Fetch timestamp (UTC)")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Commit timestamp (UTC)")
    availability_basis: str = Field(default="first_fetch", description="first_fetch, provider_vintage, or archived_release")
    date_precision: str = Field(default="DAY", description="YEAR, MONTH, DAY, HOUR, MINUTE, SECOND")
    rights_json: Optional[str] = Field(default=None, description="JSON metadata on licensing, attribution, and re-use")
    parser_version: str = Field(default="1.0", description="Parser version that processed this document")
    coverage_status: str = Field(default="COMPLETE", description="COMPLETE, PARTIAL, SCANNED_PDF, OCR_REVIEW")
    schema_version: str = Field(default="1.0", description="Schema version")
    batch_id: str = Field(..., description="Batch identifier in which this document was retrieved")
    data_origin: str = Field(default="live", description="live, demo, or test")

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_content(
        cls,
        source_id: str,
        canonical_url: str,
        publisher: str,
        content_bytes: bytes,
        batch_id: str,
        final_url: Optional[str] = None,
        published_at: Optional[datetime] = None,
        data_origin: str = "live",
        availability_basis: str = "first_fetch",
        available_at: Optional[datetime] = None,
    ) -> "SourceDocument":
        """Factory constructor computing deterministic content hash and version IDs."""
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        doc_id = stable_id("doc", source_id, canonical_url)
        ver_id = stable_id("docver", doc_id, sha256)
        now_utc = datetime.now(timezone.utc)
        if available_at is not None:
            resolved_avail = available_at
        elif availability_basis == "first_fetch":
            resolved_avail = now_utc
        else:
            resolved_avail = published_at
        return cls(
            document_id=doc_id,
            version_id=ver_id,
            source_id=source_id,
            canonical_url=canonical_url,
            final_url=final_url or canonical_url,
            publisher=publisher,
            content_sha256=sha256,
            byte_count=len(content_bytes),
            published_at=published_at,
            available_at=resolved_avail,
            fetched_at=now_utc,
            recorded_at=now_utc,
            batch_id=batch_id,
            data_origin=data_origin,
            availability_basis=availability_basis,
        )


class Entity(BaseModel):
    """
    Canonical entity identity representing an economic actor, law, sector, material, or geography.
    """
    entity_id: str = Field(..., description="Stable canonical entity ID")
    entity_type: str = Field(..., description="LAW, ACTION, REACTION, SECTOR, COMMODITY, ECONOMIC_INDEX, COMPANY, GEOGRAPHY")
    entity_subtype: Optional[str] = Field(default=None, description="Subtype e.g. LEGAL_RULE, ECONOMIC_EVENT, NUTS2")
    canonical_label: str = Field(..., description="Canonical human-readable label")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    jurisdiction: Optional[str] = Field(default="EU", description="Geographic jurisdiction: EU, DE, etc.")
    metadata_json: Optional[str] = Field(default=None, description="JSON metadata")
    batch_id: str = Field(..., description="Ingestion batch ID")
    data_origin: str = Field(default="live", description="live, demo, test")


class EntityAlias(BaseModel):
    """
    External identifiers and aliases mapping to canonical entities.
    """
    alias_id: str = Field(..., description="Stable alias ID")
    entity_id: str = Field(..., description="Canonical entity ID")
    scheme: str = Field(..., description="CELEX, NACE, NUTS, LEI, TICKER, SERIES_KEY")
    external_id: str = Field(..., description="External identifier value")
    source_id: str = Field(..., description="Provider source ID")
    available_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fact_version_id: Optional[str] = None
    batch_id: str = Field(...)


class Fact(BaseModel):
    """
    Canonical versioned relational assertion, obligation, event, or attribute.
    """
    fact_id: str = Field(default="", description="Stable identity across revisions")
    fact_version_id: str = Field(default="", description="Versioned identity for this exact assertion")
    subject_id: str = Field(..., description="Subject entity ID")
    predicate: str = Field(..., description="MANDATES, REGULATES, AMENDS, PERFORMS, BELONGS_TO, ABOUT, etc.")
    object_id: Optional[str] = Field(default=None, description="Object entity ID")
    payload_json: Optional[str] = Field(default=None, description="Structured non-numeric obligations or logic gates")
    event_at: Optional[datetime] = Field(default=None, description="When the event occurred")
    valid_from: Optional[Union[datetime, str]] = Field(default=None, description="Application date [from")
    valid_to: Optional[Union[datetime, str]] = Field(default=None, description="Expiration date to)")
    available_at: Optional[datetime] = Field(default=None, description="Verified public availability")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_id: str = Field(..., description="Data source identifier")
    supersedes_version_id: Optional[str] = Field(default=None, description="Prior fact_version_id corrected by this fact")
    is_retracted: bool = Field(default=False, description="Tombstone flag")
    claim_kind: str = Field(default="source_claim", description="source_claim, measured_association, expert_hypothesis")
    quality_state: str = Field(default="accepted", description="accepted, needs_review, rejected, out_of_scope")
    extractor_version: str = Field(default="1.0")
    batch_id: str = Field(...)
    data_origin: str = Field(default="live")

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def populate_ids(self) -> "Fact":
        if not self.fact_id:
            scope_key = ""
            if self.payload_json:
                try:
                    p = json.loads(self.payload_json)
                    if isinstance(p, dict):
                        scope = {k: p[k] for k in ["product", "commodity", "scope", "episode", "material", "metric", "target"] if k in p}
                        if len(scope) == 1:
                            key, value = next(iter(scope.items()))
                            # Preserve existing simple scope IDs while canonicalizing nested scopes.
                            token = json.dumps(value, sort_keys=True, separators=(",", ":")) if isinstance(value, (dict, list)) else str(value)
                            scope_key = f"{key}={token}"
                        elif scope:
                            scope_key = json.dumps(scope, sort_keys=True, separators=(",", ":"))
                except Exception:
                    pass
            parts = [self.subject_id, self.predicate, self.object_id or ""]
            if scope_key:
                parts.append(scope_key)
            self.fact_id = stable_id("fact", *parts)
        if not self.fact_version_id:
            payload_str = _canonical_json(self.payload_json) if self.payload_json else ""
            vf = _identity_timestamp(self.valid_from)
            vt = _identity_timestamp(self.valid_to)
            ev = _identity_timestamp(self.event_at)
            available = _identity_timestamp(self.available_at)
            self.fact_version_id = stable_id("factver", self.fact_id, self.source_id, available,
                                             payload_str, vf, vt, ev, str(self.is_retracted))
        return self


class EvidenceLink(BaseModel):
    """
    Auditable mapping from a Fact or Observation revision to exact source document evidence.
    """
    evidence_id: str = Field(default="", description="Stable evidence link ID")
    assertion_version_id: str = Field(..., description="fact_version_id or revision_id")
    assertion_kind: str = Field(default="FACT", description="FACT or OBSERVATION")
    document_version_id: str = Field(..., description="source_document version_id")
    selector_type: str = Field(default="TEXT_SPAN", description="TEXT_SPAN, ARTICLE_PARAGRAPH, TABLE_CELL, SDMX_DIMENSION")
    selector_json: Optional[str] = Field(default=None, description="Coordinates, article numbers, or cell coordinates")
    quoted_text: Optional[str] = Field(default=None, description="Exact cited source snippet")
    support_type: str = Field(default="DIRECT", description="DIRECT, CONTEXT, INFERRED")
    available_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    batch_id: Optional[str] = Field(default=None, description="Owning ingestion batch; unowned legacy evidence requires review")

    @model_validator(mode="after")
    def populate_id(self) -> "EvidenceLink":
        if not self.evidence_id:
            selector_str = _canonical_json(self.selector_json) if self.selector_json else ""
            quote_str = self.quoted_text or ""
            self.evidence_id = stable_id("evi", self.assertion_version_id, self.document_version_id, self.selector_type, selector_str, quote_str)
        return self


class SeriesDefinition(BaseModel):
    """
    Immutable specification of an economic/statistical time series.
    """
    series_id: str = Field(..., description="Stable series ID")
    source_id: str = Field(..., description="DESTATIS_GENESIS, EUROSTAT_STS, ECB_SDMX, etc.")
    dataset_id: str = Field(..., description="Table or dataflow ID, e.g. 61261-0002")
    provider_series_key: str = Field(..., description="Full provider dimension series key")
    dimensions_json: Optional[str] = Field(default=None, description="JSON dictionary of all series dimensions")
    metric_name: str = Field(..., description="e.g. producer_price_index")
    unit: str = Field(..., description="Normalized unit e.g. 2021=100, EUR/t, PERCENT")
    currency: Optional[str] = Field(default=None, description="EUR, USD, etc.")
    frequency: str = Field(default="QUARTERLY", description="MONTHLY, QUARTERLY, ANNUAL, DAILY")
    geography_id: Optional[str] = Field(default=None, description="DE, EU, etc.")
    classification_version: Optional[str] = Field(default=None, description="NACE Rev.2, etc.")
    base_period: Optional[str] = Field(default=None, description="e.g. 2021=100")
    adjustment: Optional[str] = Field(default="NSA", description="NSA (None), CA (Calendar), SA (Seasonal)")
    measurement_kind: str = Field(default="PRODUCER_PRICE_INDEX")
    available_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    batch_id: Optional[str] = Field(default=None, description="Ingestion batch ID")


class Observation(BaseModel):
    """
    Append-only observation measurement for a series and reference period.
    """
    observation_id: str = Field(default="", description="Natural key: series_id + period")
    revision_id: str = Field(default="", description="Versioned revision key")
    series_id: str = Field(..., description="Foreign key to SeriesDefinition")
    period_start: str = Field(..., description="ISO Date YYYY-MM-DD [inclusive start")
    period_end_exclusive: str = Field(..., description="ISO Date YYYY-MM-DD exclusive end)")
    value: Optional[float] = Field(default=None, description="Measured numeric value; None if unpublished/confidential")
    original_value_text: Optional[str] = Field(default=None, description="Raw source text before parsing/scaling")
    source_unit: str = Field(default="", description="Original unit in source publication")
    unit: str = Field(..., description="Normalized unit")
    vintage_id: Optional[str] = Field(default=None, description="Provider release vintage identifier")
    published_at: Optional[datetime] = None
    available_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_updated_at: Optional[datetime] = None
    provider_status: Optional[str] = Field(default=None, description="Provisional (p), Revised (r), Normal")
    missing_reason: Optional[str] = Field(default=None, description="CONFIDENTIAL, NOT_YET_PUBLISHED, BREAK")
    quality_state: str = Field(default="accepted")
    supersedes_revision_id: Optional[str] = Field(default=None)
    is_retracted: bool = Field(default=False)
    conversion_lineage_json: Optional[str] = None
    batch_id: str = Field(...)
    data_origin: str = Field(default="live")

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def populate_ids(self) -> "Observation":
        if not self.observation_id:
            self.observation_id = stable_id("obs", self.series_id, self.period_start, self.period_end_exclusive)
        if not self.revision_id:
            vintage_token = self.vintage_id or (_identity_timestamp(self.available_at) if self.available_at else "initial")
            val_token = str(self.value) if self.value is not None else f"missing_{self.missing_reason}"
            self.revision_id = stable_id("obsrev", self.observation_id, vintage_token, val_token, str(self.is_retracted))
        return self


ALLOWED_QUALITY_DECISIONS = frozenset({"accepted", "needs_review", "rejected", "out_of_scope"})


class QualityDecision(BaseModel):
    """
    Audit decision produced by deterministic verification or manual review.
    """
    decision: str = Field(..., description="accepted, needs_review, rejected, out_of_scope")
    reason_codes: List[str] = Field(default_factory=list, description="Machine-readable diagnostic reason codes")
    check_version: str = Field(default="1.0")
    reviewer: str = Field(default="automated_validator")
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    override_reason: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def validate_decision_state(cls, v: str) -> str:
        if v not in ALLOWED_QUALITY_DECISIONS:
            raise ValueError(f"Invalid quality decision state {v!r}. Must be one of {sorted(ALLOWED_QUALITY_DECISIONS)}")
        return v


class BatchManifest(BaseModel):
    """
    Metadata manifest coordinating an atomic ingestion batch.
    """
    batch_id: str = Field(..., description="Unique batch identifier")
    source_id: str = Field(..., description="Provider source identifier")
    status: str = Field(default="staged", description="staged, committed, failed, rolled_back")
    discovered_count: int = 0
    fetched_count: int = 0
    staged_count: int = 0
    accepted_count: int = 0
    review_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    committed_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    staged_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    manifest_uri: Optional[str] = None
    # Digest of the canonical staged payload. This is distinct from the
    # archived manifest object's digest.
    manifest_sha256: Optional[str] = None
    archive_sha256: Optional[str] = None
    data_origin: str = Field(default="live", description="live, demo, or test")


class DatasetManifest(BaseModel):
    """
    Immutable manifest defining a frozen model training/forecasting dataset.
    """
    dataset_id: str = Field(...)
    manifest_uri: str = Field(...)
    manifest_sha256: str = Field(...)
    committed_generation: int = Field(...)
    temporal_mode: str = Field(default="public_as_of", description="public_as_of or system_as_of")
    target_spec_id: str = Field(...)
    feature_profile_id: str = Field(...)
    split_spec_json: Optional[str] = None
    input_table_versions_json: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PredictionRecord(BaseModel):
    """
    Model forecast output record. Model predictions are strictly separated from ground-truth observations.
    """
    prediction_id: str = Field(...)
    dataset_id: str = Field(...)
    model_id: str = Field(...)
    target_series_id: str = Field(...)
    forecast_origin: datetime = Field(..., description="Point-in-time instant when forecast was generated")
    target_period_start: str = Field(...)
    target_period_end_exclusive: str = Field(...)
    point_change_pct: float = Field(...)
    lower90: Optional[float] = None
    upper90: Optional[float] = None
    calibration_status: str = Field(default="UNAVAILABLE", description="CALIBRATED, UNAVAILABLE, INSUFFICIENT_HISTORY")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    manifest_uri: Optional[str] = None


# ---------------------------------------------------------------------------
# Allowed relation endpoints and entity type resolution
# ---------------------------------------------------------------------------
ALLOWED_RELATION_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "MANDATES": {
        "subject_types": {"LAW", "ACTION"},
        "object_types": {"ACTION"},
        "allow_null_object": True,
    },
    "REGULATES": {
        "subject_types": {"LAW", "ACTION"},
        "object_types": {"COMMODITY", "SECTOR", "COMPANY", "ACTION", "ECONOMIC_INDEX", "GEOGRAPHY"},
        "allow_null_object": False,
    },
    "TRIGGERS": {
        "subject_types": {"ACTION", "REACTION", "LAW"},
        "object_types": {"ACTION", "REACTION"},
        "allow_null_object": False,
    },
    "REACTS_TO": {
        "subject_types": {"REACTION"},
        "object_types": {"ACTION", "LAW", "REACTION"},
        "allow_null_object": False,
    },
    "AFFECTS": {
        "subject_types": {"ACTION", "REACTION", "LAW"},
        "object_types": {"SECTOR", "COMMODITY", "COMPANY", "ECONOMIC_INDEX", "GEOGRAPHY"},
        "allow_null_object": False,
    },
    "AFFECTS_COST_OF": {
        "subject_types": {"LAW", "ACTION", "REACTION", "COMMODITY"},
        "object_types": {"COMMODITY", "SECTOR", "COMPANY"},
        "allow_null_object": False,
    },
    "IMPACTS": {
        "subject_types": {"ACTION", "REACTION", "LAW"},
        "object_types": {"COMMODITY", "SECTOR", "ECONOMIC_INDEX", "COMPANY"},
        "allow_null_object": False,
    },
    "CAUSES": {
        "subject_types": {"ACTION", "REACTION", "LAW"},
        "object_types": {"REACTION", "ACTION"},
        "allow_null_object": False,
    },
    "AMENDS": {
        "subject_types": {"LAW"},
        "object_types": {"LAW"},
        "allow_null_object": False,
    },
    "IMPLEMENTS": {
        "subject_types": {"LAW"},
        "object_types": {"LAW"},
        "allow_null_object": False,
    },
    "PERFORMS": {
        "subject_types": {"COMPANY", "SECTOR"},
        "object_types": {"ACTION"},
        "allow_null_object": False,
    },
    "BELONGS_TO": {
        "subject_types": {"COMPANY", "ACTION", "COMMODITY"},
        "object_types": {"SECTOR", "COMPANY"},
        "allow_null_object": False,
    },
    "ABOUT": {
        "subject_types": {"ACTION", "REACTION", "ECONOMIC_INDEX", "LAW"},
        "object_types": {"SECTOR", "COMMODITY", "COMPANY"},
        "allow_null_object": False,
    },
    "LOCATED_IN": {
        "subject_types": {"COMPANY", "ACTION", "REACTION", "SECTOR", "COMMODITY", "ECONOMIC_INDEX"},
        "object_types": {"GEOGRAPHY"},
        "allow_null_object": False,
    },
    "WITHIN": {
        "subject_types": {"GEOGRAPHY"},
        "object_types": {"GEOGRAPHY"},
        "allow_null_object": False,
    },
    "FLOWS_TO": {
        "subject_types": {"GEOGRAPHY", "SECTOR", "COMPANY"},
        "object_types": {"GEOGRAPHY", "SECTOR", "COMPANY"},
        "allow_null_object": False,
    },
    "APPLIES_IN": {
        "subject_types": {"LAW", "ACTION"},
        "object_types": {"GEOGRAPHY"},
        "allow_null_object": False,
    },
    "MEASURES": {
        "subject_types": {"ECONOMIC_INDEX", "EDGE"},
        "object_types": {"COMMODITY", "SECTOR", "COMPANY", "GEOGRAPHY", "ECONOMIC_INDEX", "EDGE"},
        "allow_null_object": True,
    },
}


def resolve_entity_type(
    entity_id: Optional[str],
    payload_json: Optional[str] = None,
    role: str = "subject",
    predicate: Optional[str] = None
) -> Optional[str]:
    """
    Resolves canonical entity type from entity ID namespace/prefix or payload metadata.
    Returns NodeType name (e.g. 'LAW', 'ACTION', 'SECTOR', etc.) or 'EDGE' for edge subjects,
    or None if the entity type cannot be resolved.
    """
    if not entity_id or not isinstance(entity_id, str):
        return None

    raw_id = entity_id.strip()
    id_lower = raw_id.lower()

    # Check payload for explicit typing first
    if payload_json:
        try:
            payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
            if isinstance(payload, dict):
                if role == "subject":
                    if payload.get("subject_kind") == "edge" or str(payload.get("subject_type", "")).upper() == "EDGE":
                        return "EDGE"
                    st = payload.get("subject_type")
                    if st and str(st).upper() in NODE_ENTITY_TYPES:
                        return str(st).upper()
                elif role == "object":
                    if (payload.get("subject_kind") == "edge" or str(payload.get("object_type", "")).upper() == "EDGE") and predicate == "MEASURES":
                        return "EDGE"
                    ot = payload.get("object_type")
                    if ot and str(ot).upper() in NODE_ENTITY_TYPES:
                        return str(ot).upper()
        except Exception:
            pass

    # Strip environment/origin prefixes like demo:, live:, test:, quarantined:
    stripped = id_lower
    for pfx in ("demo:", "live:", "test:", "quarantine:", "quarantined:"):
        if stripped.startswith(pfx):
            stripped = stripped[len(pfx):]
            break

    # Check for edge-subject measurement representation
    if predicate == "MEASURES" and (stripped.startswith("edge_") or stripped.startswith("rel_") or stripped.startswith("fact_")):
        return "EDGE"

    # Match canonical namespace / prefixes
    if stripped.startswith(("law_", "law:", "celex_", "celex:", "eli_", "eli:")):
        return "LAW"
    if stripped.startswith(("act_", "action:", "action_", "rule_", "rule:", "event_", "event:", "legal_rule_", "economic_event_")):
        return "ACTION"
    if stripped.startswith(("react_", "reaction:", "reaction_")):
        return "REACTION"
    if stripped.startswith(("sec_", "sector:", "sector_", "nace_", "nace:")):
        return "SECTOR"
    if stripped.startswith(("commodity:", "commodity_", "mat_", "mat:", "material:", "material_")):
        return "COMMODITY"
    if stripped.startswith(("index_", "index:", "series_", "series:", "econ_", "econ:", "economic_index:", "economic_index_")):
        return "ECONOMIC_INDEX"
    if stripped.startswith(("company:", "company_", "comp_", "comp:", "lei_", "lei:")):
        return "COMPANY"
    if stripped.startswith(("geo_", "geo:", "geography:", "geography_", "nuts_", "nuts:", "country_", "country:", "iso_", "iso:")):
        return "GEOGRAPHY"

    return None


def _validate_availability_consistency(
    candidate_avail: Optional[datetime],
    source: SourceDocument,
    prefix: str,
) -> Optional[str]:
    """
    Validates that candidate availability does not predate the source version's verified availability.
    If source.availability_basis == "first_fetch", verified availability defaults to fetched_at.
    If source availability is unknown and candidate claims verified availability, flags missing evidence.
    """
    if candidate_avail is None:
        return None

    src_avail = source.available_at
    if src_avail is None:
        if source.availability_basis == "first_fetch":
            src_avail = source.fetched_at
        else:
            return f"{prefix}_AVAILABILITY_WITHOUT_SOURCE_EVIDENCE"

    cand = candidate_avail
    if cand.tzinfo is None and src_avail.tzinfo is not None:
        cand = cand.replace(tzinfo=timezone.utc)
    elif cand.tzinfo is not None and src_avail.tzinfo is None:
        src_avail = src_avail.replace(tzinfo=timezone.utc)

    if cand < src_avail:
        return f"{prefix}_PREDATES_SOURCE_AVAILABILITY"

    return None


def validate_candidate(candidate: Union[Fact, Observation], source: SourceDocument) -> QualityDecision:
    """
    Performs deterministic validation on candidate facts and observations.
    Enforces relational vocabulary, entity/edge namespaces, endpoint compatibility,
    temporal date sanity, interval ordering, and consistent source association.
    Returns QualityDecision with decision in accepted, needs_review, rejected, out_of_scope.
    """
    reason_codes: List[str] = []

    if isinstance(candidate, Observation):
        # 1. Validate observation period date format
        for field_name, p_val in [("period_start", candidate.period_start), ("period_end_exclusive", candidate.period_end_exclusive)]:
            try:
                text = str(p_val).strip()
                if date.fromisoformat(text).isoformat() != text:
                    raise ValueError("Expected YYYY-MM-DD")
            except (ValueError, TypeError, IndexError):
                return QualityDecision(
                    decision="rejected",
                    reason_codes=["MALFORMED_OBSERVATION_PERIOD"],
                    reviewer="automated_validator"
                )

        # 2. Validate interval ordering
        if str(candidate.period_start).strip() >= str(candidate.period_end_exclusive).strip():
            return QualityDecision(
                decision="rejected",
                reason_codes=["REVERSED_OBSERVATION_PERIOD"],
                reviewer="automated_validator"
            )

        # 3. Numeric validation
        if candidate.value is not None:
            if math.isnan(candidate.value) or math.isinf(candidate.value):
                return QualityDecision(
                    decision="rejected",
                    reason_codes=["NON_FINITE_OBSERVATION_VALUE"],
                    reviewer="automated_validator"
                )
        elif not candidate.missing_reason:
            reason_codes.append("NULL_VALUE_WITHOUT_MISSING_REASON")

        # 4. Measurement unit validation
        if not candidate.unit or not str(candidate.unit).strip():
            return QualityDecision(
                decision="rejected",
                reason_codes=["MISSING_MEASUREMENT_UNIT"],
                reviewer="automated_validator"
            )

        # 5. Source association and origin checks
        if candidate.data_origin != source.data_origin:
            return QualityDecision(
                decision="rejected",
                reason_codes=["DATA_ORIGIN_MISMATCH"],
                reviewer="automated_validator"
            )

        avail_issue = _validate_availability_consistency(candidate.available_at, source, "OBSERVATION")
        if avail_issue:
            reason_codes.append(avail_issue)

        if reason_codes:
            return QualityDecision(decision="needs_review", reason_codes=reason_codes, reviewer="automated_validator")
        return QualityDecision(decision="accepted", reason_codes=["VALID_OBSERVATION_CONTRACT"], reviewer="automated_validator")

    elif isinstance(candidate, Fact):
        # 1. Structural identifiers
        if not candidate.subject_id or not candidate.predicate:
            return QualityDecision(
                decision="rejected",
                reason_codes=["MALFORMED_FACT_IDENTIFIERS"],
                reviewer="automated_validator"
            )

        # 2. Supported relation predicate validation
        if candidate.predicate not in VALID_PREDICATES:
            return QualityDecision(
                decision="rejected",
                reason_codes=["UNSUPPORTED_PREDICATE"],
                reviewer="automated_validator"
            )

        # 3. Disallow edge/rel/fact IDs as subject or object unless predicate is MEASURES (edge-subject measurement)
        subj_lower = candidate.subject_id.strip().lower()
        if candidate.predicate != "MEASURES" and (subj_lower.startswith("edge_") or subj_lower.startswith("rel_") or subj_lower.startswith("fact_")):
            return QualityDecision(
                decision="rejected",
                reason_codes=["INVALID_SUBJECT_NAMESPACE"],
                reviewer="automated_validator"
            )

        if candidate.object_id:
            obj_lower = candidate.object_id.strip().lower()
            if candidate.predicate != "MEASURES" and (obj_lower.startswith("edge_") or obj_lower.startswith("rel_") or obj_lower.startswith("fact_")):
                return QualityDecision(
                    decision="rejected",
                    reason_codes=["INVALID_OBJECT_NAMESPACE"],
                    reviewer="automated_validator"
                )

        # 4. Temporal validity date validation and interval ordering
        dt_vf = None
        dt_vt = None
        for vf_name, vf_val in [("valid_from", candidate.valid_from), ("valid_to", candidate.valid_to)]:
            if vf_val is not None:
                if isinstance(vf_val, (datetime, date)):
                    if vf_name == "valid_from":
                        dt_vf = vf_val
                    else:
                        dt_vt = vf_val
                elif isinstance(vf_val, str):
                    try:
                        parsed_dt = datetime.fromisoformat(vf_val.strip().replace("Z", "+00:00"))
                        if vf_name == "valid_from":
                            dt_vf = parsed_dt
                        else:
                            dt_vt = parsed_dt
                    except (ValueError, TypeError, IndexError):
                        return QualityDecision(
                            decision="rejected",
                            reason_codes=["MALFORMED_VALIDITY_DATE"],
                            reviewer="automated_validator"
                        )
                else:
                    return QualityDecision(
                        decision="rejected",
                        reason_codes=["MALFORMED_VALIDITY_DATE"],
                        reviewer="automated_validator"
                    )

        if dt_vf is not None and dt_vt is not None:
            c_vf = dt_vf.replace(tzinfo=timezone.utc) if getattr(dt_vf, "tzinfo", None) is None else dt_vf
            c_vt = dt_vt.replace(tzinfo=timezone.utc) if getattr(dt_vt, "tzinfo", None) is None else dt_vt
            if c_vf >= c_vt:
                return QualityDecision(
                    decision="rejected",
                    reason_codes=["REVERSED_VALIDITY_INTERVAL"],
                    reviewer="automated_validator"
                )

        # 5. Source association and origin checks
        if candidate.source_id != source.source_id:
            return QualityDecision(
                decision="rejected",
                reason_codes=["SOURCE_ID_MISMATCH"],
                reviewer="automated_validator"
            )

        if candidate.data_origin != source.data_origin:
            return QualityDecision(
                decision="rejected",
                reason_codes=["DATA_ORIGIN_MISMATCH"],
                reviewer="automated_validator"
            )

        if source.published_at and source.fetched_at:
            pub_dt = source.published_at.replace(tzinfo=timezone.utc) if source.published_at.tzinfo is None else source.published_at
            fch_dt = source.fetched_at.replace(tzinfo=timezone.utc) if source.fetched_at.tzinfo is None else source.fetched_at
            if pub_dt > fch_dt:
                reason_codes.append("FUTURE_PUBLICATION_DATE")

        # 6. Endpoint type resolution and compatibility
        subj_type = resolve_entity_type(candidate.subject_id, candidate.payload_json, role="subject", predicate=candidate.predicate)
        obj_type = resolve_entity_type(candidate.object_id, candidate.payload_json, role="object", predicate=candidate.predicate) if candidate.object_id else None

        if subj_type is None:
            reason_codes.append("UNRESOLVED_SUBJECT_TYPE")
        if candidate.object_id and obj_type is None:
            reason_codes.append("UNRESOLVED_OBJECT_TYPE")

        if subj_type is not None and (obj_type is not None or candidate.object_id is None):
            endpoint_rules = ALLOWED_RELATION_ENDPOINTS.get(candidate.predicate)
            if endpoint_rules:
                if candidate.object_id is None:
                    if not endpoint_rules.get("allow_null_object", False):
                        return QualityDecision(
                            decision="rejected",
                            reason_codes=["MISSING_REQUIRED_OBJECT_ENDPOINT"],
                            reviewer="automated_validator"
                        )
                else:
                    if obj_type not in endpoint_rules["object_types"]:
                        return QualityDecision(
                            decision="rejected",
                            reason_codes=["INCOMPATIBLE_RELATION_ENDPOINTS"],
                            reviewer="automated_validator"
                        )
                if subj_type not in endpoint_rules["subject_types"]:
                    return QualityDecision(
                        decision="rejected",
                        reason_codes=["INCOMPATIBLE_RELATION_ENDPOINTS"],
                        reviewer="automated_validator"
                    )

        # 7. Availability consistency check for Facts (cannot predate verified source availability)
        avail_issue = _validate_availability_consistency(candidate.available_at, source, "FACT")
        if avail_issue:
            reason_codes.append(avail_issue)

        # 8. Legal rule completeness
        if candidate.predicate in ["MANDATES", "REGULATES", "AMENDS"]:
            if not candidate.object_id and not candidate.payload_json:
                reason_codes.append("LEGAL_RULE_MISSING_OBJECT_AND_PAYLOAD")

        if reason_codes:
            return QualityDecision(decision="needs_review", reason_codes=reason_codes, reviewer="automated_validator")
        return QualityDecision(decision="accepted", reason_codes=["VALID_FACT_CONTRACT"], reviewer="automated_validator")

    return QualityDecision(decision="out_of_scope", reason_codes=["UNRECOGNIZED_CANDIDATE_TYPE"], reviewer="automated_validator")
