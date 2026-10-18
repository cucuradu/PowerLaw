"""
Canonical Ingestion Contracts & Schemas for PowerLaw / ConstructPulse.
Implements the canonical data models, SHA-256 stable identity, versioning,
evidence linking, and quality decision interfaces from Task 2.
"""

import math
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field, model_validator


def stable_id(namespace: str, *parts: str) -> str:
    """
    Generates a deterministic 64-character SHA-256 stable identity string
    prefixed with the namespace.
    """
    normalized_tokens = [str(namespace).strip().lower()]
    for p in parts:
        normalized_tokens.append(str(p).strip())
    raw_key = ":".join(normalized_tokens)
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"{namespace}_{digest}"


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
        data_origin: str = "live"
    ) -> "SourceDocument":
        """Factory constructor computing deterministic content hash and version IDs."""
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        doc_id = stable_id("doc", source_id, canonical_url)
        ver_id = stable_id("docver", doc_id, sha256)
        now_utc = datetime.now(timezone.utc)
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
            available_at=published_at or now_utc,
            fetched_at=now_utc,
            recorded_at=now_utc,
            batch_id=batch_id,
            data_origin=data_origin
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
    valid_from: Optional[datetime] = Field(default=None, description="Application date [from")
    valid_to: Optional[datetime] = Field(default=None, description="Expiration date to)")
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
            self.fact_id = stable_id("fact", self.subject_id, self.predicate, self.object_id or "")
        if not self.fact_version_id:
            payload_str = self.payload_json or ""
            self.fact_version_id = stable_id("factver", self.fact_id, payload_str, str(self.is_retracted))
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

    @model_validator(mode="after")
    def populate_id(self) -> "EvidenceLink":
        if not self.evidence_id:
            self.evidence_id = stable_id("evi", self.assertion_version_id, self.document_version_id, self.selector_type)
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
    geography_id: str = Field(default="DE", description="DE, EU, etc.")
    classification_version: Optional[str] = Field(default=None, description="NACE Rev.2, etc.")
    base_period: Optional[str] = Field(default=None, description="e.g. 2021=100")
    adjustment: Optional[str] = Field(default="NSA", description="NSA (None), CA (Calendar), SA (Seasonal)")
    measurement_kind: str = Field(default="PRODUCER_PRICE_INDEX")
    available_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
            vintage_token = self.vintage_id or (self.available_at.isoformat() if self.available_at else "initial")
            val_token = str(self.value) if self.value is not None else f"missing_{self.missing_reason}"
            self.revision_id = stable_id("obsrev", self.observation_id, vintage_token, val_token, str(self.is_retracted))
        return self


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
    committed_at: Optional[datetime] = None
    manifest_sha256: Optional[str] = None


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


def validate_candidate(candidate: Union[Fact, Observation], source: SourceDocument) -> QualityDecision:
    """
    Performs deterministic validation on candidate facts and observations.
    Returns QualityDecision with decision in accepted, needs_review, rejected, out_of_scope.
    """
    reason_codes: List[str] = []

    if isinstance(candidate, Observation):
        # Numeric validation
        if candidate.value is not None:
            if math.isnan(candidate.value) or math.isinf(candidate.value):
                return QualityDecision(
                    decision="rejected",
                    reason_codes=["NON_FINITE_OBSERVATION_VALUE"],
                    reviewer="automated_validator"
                )
        elif not candidate.missing_reason:
            reason_codes.append("NULL_VALUE_WITHOUT_MISSING_REASON")

        if not candidate.unit:
            return QualityDecision(
                decision="rejected",
                reason_codes=["MISSING_MEASUREMENT_UNIT"],
                reviewer="automated_validator"
            )

        if reason_codes:
            return QualityDecision(decision="needs_review", reason_codes=reason_codes)
        return QualityDecision(decision="accepted", reason_codes=["VALID_OBSERVATION_CONTRACT"])

    elif isinstance(candidate, Fact):
        if not candidate.subject_id or not candidate.predicate:
            return QualityDecision(
                decision="rejected",
                reason_codes=["MALFORMED_FACT_IDENTIFIERS"],
                reviewer="automated_validator"
            )

        if candidate.predicate in ["MANDATES", "REGULATES", "AMENDS"]:
            if not candidate.object_id and not candidate.payload_json:
                reason_codes.append("LEGAL_RULE_MISSING_OBJECT_AND_PAYLOAD")

        if reason_codes:
            return QualityDecision(decision="needs_review", reason_codes=reason_codes)
        return QualityDecision(decision="accepted", reason_codes=["VALID_FACT_CONTRACT"])

    return QualityDecision(decision="out_of_scope", reason_codes=["UNRECOGNIZED_CANDIDATE_TYPE"])
