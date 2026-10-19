import math
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationInfo
from typing import List, Optional, Any
from enum import Enum

# --- ENUMS ---
class LawType(str, Enum):
    REGULATION = "REGULATION"
    DIRECTIVE = "DIRECTIVE"
    TAX_CODE = "TAX_CODE"
    DECISION = "DECISION"

class ActionType(str, Enum):
    CONSTRAINT = "CONSTRAINT"   # Hard limit (Inequality) or qualitative ban
    ADJUSTMENT = "ADJUSTMENT"   # Modifies cost/price (Addition/Multiplication) - requires numeric value
    FIXING = "FIXING"           # Sets a specific value (Equality) - requires numeric value
    MANDATE = "MANDATE"         # Qualitative obligation / requirement
    PROHIBITION = "PROHIBITION" # Qualitative ban / embargo
    PROCEDURAL = "PROCEDURAL"   # Procedural requirement / reporting / certification

class ActionOperator(str, Enum):
    MAX_LIMIT = "MAX_LIMIT"       # <=
    MIN_LIMIT = "MIN_LIMIT"       # >=
    ADD_ABSOLUTE = "ADD_ABSOLUTE" # + value (Taxes)
    ADD_PERCENTAGE = "ADD_PERCENTAGE" # * (1 + value/100) (VAT)
    SUB_ABSOLUTE = "SUB_ABSOLUTE" # - value (Subsidies)
    SET_VALUE = "SET_VALUE"       # =
    EQUAL = "EQUAL"               # Alias for SET_VALUE
    APPLIES = "APPLIES"           # Applies rule / requirement
    PROHIBITS = "PROHIBITS"       # Prohibits activity / good
    REQUIRES = "REQUIRES"         # Requires action / reporting

class Strictness(str, Enum):
    HARD = "HARD" # Physics/Ban -> Infinite penalty
    SOFT = "SOFT" # Financial -> Finite penalty

# --- INDUSTRIAL CODES (NACE REV.2 SIMPLIFIED) ---
# We use string literals for Pydantic to allow the LLM some flexibility but guide it strongly.
# Ideally, we want the LLM to output "C24" or "C24_BASIC_METALS".

NACE_CODES_PROMPT = """
AVAILABLE INDUSTRIAL CODES (NACE Rev.2):
[AGRICULTURE]
A01: Crop/Animal Production
A03: Fishing/Aquaculture

[MINING & ENERGY]
B05: Coal/Lignite Mining
B06: Oil/Gas Extraction
B07: Metal Ores Mining
D35: Electricity, Gas, Steam, Air Conditioning

[MANUFACTURING]
C10: Food Products
C19: Coke & Refined Petroleum
C20: Chemicals & Chemical Products
C23: Non-metallic Mineral (Cement, Glass, Concrete)
C24: Basic Metals (Steel, Aluminum)
C25: Fabricated Metal Products
C29: Motor Vehicles (Automotive)

[TRANSPORT]
H49: Land Transport (Rail, Road)
H50: Water Transport (Shipping)
H51: Air Transport

[SERVICES]
J62: IT & Software
K64: Financial Services (Banking)
E38: Waste Collection & Treatment
"""

# --- SUB-MODELS ---

class Metadata(BaseModel):
    regulation_id: str = Field(..., description="CELEX ID or Law ID, e.g. 32017R0852")
    article_id: str = Field(..., description="Article/Section ID, e.g. ART_8")
    law_type: LawType
    parse_confidence: float = Field(..., ge=0.0, le=1.0)

class Scope(BaseModel):
    regions: List[str] = Field(..., description="List of ISO Country Codes (EU, DE, FR)")
    
    # Updated: Granular Codes
    industrial_sectors: List[str] = Field(..., description="List of NACE Codes (e.g., 'C24', 'D35', 'A01'). See prompt for allowed values.")
    
    node_types: List[str] = Field(..., description="Role in the graph: PRODUCER, CONSUMER, INFRASTRUCTURE, TRADER, GOVERNMENT, HOUSEHOLD")
    affected_commodities: List[str] = Field(default_factory=list, description="Specific goods: MERCURY, GAS_TTF, CO2, STEEL, WHEAT")

class Timeline(BaseModel):
    activation_date: Optional[str] = Field(default=None, description="Format YYYY-MM-DD, e.g. '2025-01-01', or null if indefinite/unspecified")
    expiration_date: Optional[str] = Field(default=None, description="Format YYYY-MM-DD or null if indefinite")
    phase_in_period_days: int = Field(0, description="Ramp-up period in days (0=Instant)")

class ConditionalTrigger(BaseModel):
    variable: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None

class Mechanism(BaseModel):
    target_variable: str = Field(..., description="The nodal variable affected (volume, price, emission_rate, opex_cost, tax_rate, compliance_status)")
    action_type: ActionType
    operator: ActionOperator
    value: Optional[float] = Field(default=None, description="Numeric value for the constraint/adjustment, or None for qualitative obligations")
    unit: str = Field(default="", description="Unit of measurement (EUR/MWh, TONNES, PERCENT, DATE, TEXT, NONE)")
    non_numeric_obligation: Optional[str] = Field(default=None, description="Explicit qualitative obligation description without fabricated quantities")
    condition: Optional[str] = Field(default=None, description="Governing condition or threshold clause")
    exception: Optional[str] = Field(default=None, description="Explicit statutory exception or exemption clause")
    evidence_anchor: Optional[str] = Field(default=None, description="Article/paragraph or text span anchor")
    conditional_trigger: Optional[ConditionalTrigger] = None

    @field_validator("value", mode="before")
    @classmethod
    def validate_numeric_not_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("Mechanism value cannot be a boolean")
        return v

    @model_validator(mode="after")
    def validate_mechanism(self) -> "Mechanism":
        # 1. Action / operator compatibility
        numeric_adjustment_ops = {
            ActionOperator.ADD_ABSOLUTE,
            ActionOperator.ADD_PERCENTAGE,
            ActionOperator.SUB_ABSOLUTE,
        }
        fixing_ops = {
            ActionOperator.SET_VALUE,
            ActionOperator.EQUAL,
        }
        mandate_ops = {
            ActionOperator.REQUIRES,
            ActionOperator.APPLIES,
        }
        prohibition_ops = {
            ActionOperator.PROHIBITS,
            ActionOperator.MAX_LIMIT,
        }
        procedural_ops = {
            ActionOperator.REQUIRES,
            ActionOperator.APPLIES,
        }

        constraint_ops = {
            ActionOperator.MAX_LIMIT,
            ActionOperator.MIN_LIMIT,
            ActionOperator.SET_VALUE,
            ActionOperator.EQUAL,
        }

        if self.action_type == ActionType.ADJUSTMENT:
            if self.operator not in numeric_adjustment_ops:
                raise ValueError(f"ActionType.ADJUSTMENT is incompatible with operator {self.operator}")
        elif self.action_type == ActionType.FIXING:
            if self.operator not in fixing_ops:
                raise ValueError(f"ActionType.FIXING is incompatible with operator {self.operator}")
        elif self.action_type == ActionType.CONSTRAINT:
            if self.operator not in constraint_ops:
                raise ValueError(f"ActionType.CONSTRAINT is incompatible with operator {self.operator}")
        elif self.action_type == ActionType.MANDATE:
            if self.operator not in mandate_ops:
                raise ValueError(f"ActionType.MANDATE is incompatible with operator {self.operator}")
        elif self.action_type == ActionType.PROHIBITION:
            if self.operator not in prohibition_ops:
                raise ValueError(f"ActionType.PROHIBITION is incompatible with operator {self.operator}")
        elif self.action_type == ActionType.PROCEDURAL:
            if self.operator not in procedural_ops:
                raise ValueError(f"ActionType.PROCEDURAL is incompatible with operator {self.operator}")

        # 2. Enforce numeric requirement for ADJUSTMENT and FIXING (runs even when value is omitted)
        if self.action_type in (ActionType.ADJUSTMENT, ActionType.FIXING):
            if self.value is None or isinstance(self.value, bool):
                raise ValueError(f"ActionType '{self.action_type}' requires a numeric value")

        # 3. Finite numeric value check whenever value is provided
        if self.value is not None:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or math.isnan(self.value) or math.isinf(self.value):
                raise ValueError(f"Mechanism value must be a finite numeric value, got {self.value}")

        return self

class Enforcement(BaseModel):
    strictness: Strictness
    penalty_factor: float = Field(..., description="Lambda coefficient (1.0 = Strict/Ban, 0.1 = Soft target)")

# --- ROOT MODEL ---

class GNNRule(BaseModel):
    metadata: Metadata
    scope: Scope
    timeline: Timeline
    mechanism: Mechanism
    enforcement: Enforcement
    summary: str = Field(..., description="Natural language summary of the rule")
    background_context: Optional[str] = Field(None, description="Context from Recitals describing intent")

class GNNRuleList(BaseModel):
    rules: List[GNNRule]


def rule_to_fact(rule: GNNRule, source_id: str = "EURLEX_CELLAR", batch_id: str = "batch_001", data_origin: str = "live"):
    """
    Converts a structured GNNRule into a canonical Fact contract,
    preserving non-numeric obligations, conditions, exceptions, and evidence anchors.
    """
    from src.schemas.ingestion import Fact
    import json
    
    subject_id = f"LAW_{rule.metadata.regulation_id}"
    predicate = "MANDATES"
    if rule.mechanism.action_type in (ActionType.PROHIBITION,):
        predicate = "REGULATES"
    elif rule.mechanism.action_type in (ActionType.ADJUSTMENT, ActionType.FIXING):
        predicate = "REGULATES"
    
    payload = {
        "article_id": rule.metadata.article_id,
        "law_type": rule.metadata.law_type.value,
        "action_type": rule.mechanism.action_type.value,
        "target_variable": rule.mechanism.target_variable,
        "operator": rule.mechanism.operator.value,
        "value": rule.mechanism.value,
        "unit": rule.mechanism.unit,
        "non_numeric_obligation": rule.mechanism.non_numeric_obligation or rule.summary,
        "condition": rule.mechanism.condition,
        "exception": rule.mechanism.exception,
        "evidence_anchor": rule.mechanism.evidence_anchor or rule.metadata.article_id,
        "sectors": rule.scope.industrial_sectors,
        "regions": rule.scope.regions,
        "strictness": rule.enforcement.strictness.value,
    }
    
    valid_from = None
    if rule.timeline.activation_date:
        try:
            valid_from = datetime.fromisoformat(rule.timeline.activation_date).replace(tzinfo=timezone.utc)
        except Exception:
            try:
                valid_from = datetime.strptime(rule.timeline.activation_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                valid_from = None

    valid_to = None
    if rule.timeline.expiration_date:
        try:
            valid_to = datetime.fromisoformat(rule.timeline.expiration_date).replace(tzinfo=timezone.utc)
        except Exception:
            try:
                valid_to = datetime.strptime(rule.timeline.expiration_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                valid_to = None

    return Fact(
        subject_id=subject_id,
        predicate=predicate,
        object_id=f"ACT_{rule.metadata.article_id}",
        payload_json=json.dumps(payload, sort_keys=True),
        valid_from=valid_from,
        valid_to=valid_to,
        source_id=source_id,
        batch_id=batch_id,
        data_origin=data_origin
    )
