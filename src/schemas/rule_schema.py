from pydantic import BaseModel, Field, validator
from typing import List, Optional
from enum import Enum

# --- ENUMS ---
class LawType(str, Enum):
    REGULATION = "REGULATION"
    DIRECTIVE = "DIRECTIVE"
    TAX_CODE = "TAX_CODE"
    DECISION = "DECISION"

class ActionType(str, Enum):
    CONSTRAINT = "CONSTRAINT"   # Hard limit (Inequality)
    ADJUSTMENT = "ADJUSTMENT"   # Modifies cost/price (Addition/Multiplication)
    FIXING = "FIXING"           # Sets a specific value (Equality)

class ActionOperator(str, Enum):
    MAX_LIMIT = "MAX_LIMIT"       # <=
    MIN_LIMIT = "MIN_LIMIT"       # >=
    ADD_ABSOLUTE = "ADD_ABSOLUTE" # + value (Taxes)
    ADD_PERCENTAGE = "ADD_PERCENTAGE" # * (1 + value/100) (VAT)
    SUB_ABSOLUTE = "SUB_ABSOLUTE" # - value (Subsidies)
    SET_VALUE = "SET_VALUE"       # =
    EQUAL = "EQUAL"               # Alias for SET_VALUE

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
    activation_date: str = Field(..., description="Format YYYY-MM-DD, e.g. '2025-01-01'")
    expiration_date: Optional[str] = Field(None, description="Format YYYY-MM-DD or null if indefinite")
    phase_in_period_days: int = Field(0, description="Ramp-up period in days (0=Instant)")

class ConditionalTrigger(BaseModel):
    variable: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None

class Mechanism(BaseModel):
    target_variable: str = Field(..., description="The nodal variable affected (volume, price, emission_rate, opex_cost, tax_rate)")
    action_type: ActionType
    operator: ActionOperator
    value: float = Field(..., description="Numeric value for the constraint/adjustment")
    unit: str = Field(..., description="Unit of measurement (EUR/MWh, TONNES, PERCENT, DATE)")
    conditional_trigger: Optional[ConditionalTrigger] = None

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
