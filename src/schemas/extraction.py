"""
Pydantic schemas for structured extraction across all pipelines
Enforces schema compliance via Google GenAI SDK
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Literal
from datetime import date

# ============ NEWS SCHEMAS ============


#from pydantic import BaseModel, Field
from typing import Literal, List, Optional, Union
from src.utils.taxonomy import ConceptCode

# --- Logic Primitives ---
class LogicTrigger(BaseModel):
    variable: str = Field(..., description="The monitored variable, e.g., 'gas_reserves_pct'")
    operator: str = Field(..., description="Mathematical operator: >, <, >=, <=, ==, !=")
    threshold: str = Field(..., description="Numeric threshold or Date string (ISO)")
    unit: str = Field(..., description="Unit of measurement, e.g., 'PERCENT', 'EUR_MWh'")

class LogicConsequence(BaseModel):
    set_value: str = Field(..., description="DEPRECATED") # Keeping structure clean requires schema reload. Better to rename to match intent.
    # Refactoring per User Request
    action_type: str = Field(..., description="Type: STATE_CHANGE, ALERT, FINE, SUBSIDY_CUT")
    target_variable: str
    numeric_value: str = Field(..., description="Numeric value to set/apply (as string)")
    unit: str = Field(..., description="Unit: 'PERCENT', 'EUR_MWh', 'STATUS_CODE', 'BOOLEAN'")

# --- Domain Models ---

class LogicGate(BaseModel):
    """
    Executable logic rule.
    Example: IF gas_reserves_pct < 50 THEN market_state = CRITICAL
    """
    trigger: LogicTrigger
    consequence: LogicConsequence

class StructuredImpact(BaseModel):
    """Economic impact with BOTH quantitative and qualitative signals"""
    
    # Asset & Metric (required)
    target_asset: str = Field(..., description="Affected asset: ELEC_DE_BASE, SECTOR_LOGISTICS, NATIONAL_SECURITY, etc.")
    metric: str = Field(..., description="PRICE, COST, SUPPLY, DEMAND, VOLUME, RISK")
    
    # QUALITATIVE (required - captures direction even without numbers)
    direction: str = Field(..., description="INCREASE, DECREASE, STABLE, or UNKNOWN")
    confidence: str = Field(..., description="HIGH (will/definitely), MEDIUM (could/may), LOW (might), SPECULATIVE (if/scenario)")
    evidence_text: Optional[str] = Field(None, description="Quote from source justifying classification")
    
    # QUANTITATIVE (optional - null if no explicit numbers in text)
    delta_value: Optional[float] = Field(None, description="Numeric change magnitude if stated")
    delta_unit: Optional[str] = Field(None, description="PCT, EUR, ABSOLUTE")
    absolute_price_target: Optional[float] = Field(None, description="Explicit price target if stated")
    
    # Temporal
    timeframe: str = Field(..., description="IMMEDIATE, 3_MONTHS, 1_YEAR, LONG_TERM")

    @field_validator('target_asset')
    @classmethod
    def validate_asset(cls, v):
        # Allow taxonomy concepts OR fallback sectors
        valid_concepts = {c.value for c in ConceptCode}
        fallback_sectors = {
            "SECTOR_LOGISTICS", "SECTOR_INDUSTRIAL", "SECTOR_AUTOMOTIVE",
            "NATIONAL_SECURITY", "MILITARY_ASSETS", "GRID_INFRASTRUCTURE",
            "ENERGY_GENERAL", "UNKNOWN"
        }
        all_valid = valid_concepts | fallback_sectors
        if v and v not in all_valid:
            raise ValueError(f"'{v}' not valid. Use ConceptCode or fallback: {fallback_sectors}")
        return v

class CausalLink(BaseModel):
    cause_concept: str = Field(..., description="The driving variable (e.g. CARBON_PRICE)")
    effect_concept: str = Field(..., description="The influenced variable (e.g. COAL_GENERATION)")
    
    # Math Verification Fields
    cause_change_pct: Optional[float] = Field(None, description="The percentage change of the cause (e.g. +10.0)")
    effect_change_pct: Optional[float] = Field(None, description="The percentage change of the effect (e.g. -2.0)")
    
    coefficient: float = Field(..., description="Calculated elasticity (Effect % / Cause %)")
    logic_type: Literal["PASS_THROUGH", "SUBSTITUTION", "ELASTICITY", "THRESHOLD"]
    timeframe: str = Field(..., description="SHORT_RUN, LONG_RUN")

    @field_validator('cause_concept', 'effect_concept')
    @classmethod
    def validate_concepts(cls, v):
        # Auto-clean the concept to standardise Node IDs
        # "Raw Material Prices" -> "RAW_MATERIAL_PRICES"
        import re
        v = v.strip().upper()
        v = v.replace(" ", "_")
        v = re.sub(r'[^A-Z0-9_]', '', v) # Remove parens, commas
        return v[:64] # Truncate to reasonable length

# --- Extraction Containers ---

class ArticleExtraction(BaseModel):
    """Law Chunk Extraction"""
    law_id: str = Field(description="CELEX ID")
    chunk_id: str = Field(description="Article/Section ID")
    jurisdiction: str = Field(description="EU, DE, FR, etc.")
    valid_from: str = Field(description="YYYY-MM-DD")
    valid_to: Optional[str] = Field(default="", description="YYYY-MM-DD or empty")
    semantic_summary: str = Field(..., description="1-sentence summary of the context/intent of these rules.")
    citations: List[str] = Field(default_factory=list, description="List of referenced Laws/Directives (e.g. 'Directive 2011/16/EU')")
    rule_type: Literal["REGULATORY", "PROCEDURAL", "DEFINITION"] = Field(default="REGULATORY", description="Is this a substantive rule, a procedural reference, or a definition?")
    # Schema Compatibility Fields (computed post-extraction)
    entity_types_json: str = Field(default="[]", description="JSON string of affected entities")
    numeric_constraints_json: str = Field(default="[]", description="JSON string of constraints")
    logic: List[LogicGate] = Field(default_factory=list)

class ReceptorSignal(BaseModel):
    # STRICT Taxonomy for Causes of Chaos (Events)
    primary_node: str = Field(..., description="The Root Cause Event (Source of Chaos)")
    severity: float = Field(..., description="0.0 to 1.0")

    # Validation removed to allow Graph Node IDs (e.g. DE_INDUSTRIAL_PRODUCTION)
    # @field_validator('primary_node')
    # ...

class NewsEntities(BaseModel):
    geography: List[str] = Field(default_factory=list)

# Note: StructuredImpact moved to line 42 with enhanced qualitative fields.
# Re-defining here for NewsExtraction to use correct version.
class StructuredImpactNews(BaseModel):
    """Economic impact with BOTH quantitative and qualitative signals - for News"""
    
    # Asset & Metric
    target_asset: str = Field("", description="Affected asset: ELEC_DE_BASE, SECTOR_LOGISTICS, NATIONAL_SECURITY")
    metric: str = Field("", description="PRICE, COST, SUPPLY, DEMAND, VOLUME, RISK")
    
    # QUALITATIVE (required - captures direction even without numbers)
    direction: str = Field("UNKNOWN", description="INCREASE, DECREASE, STABLE, or UNKNOWN")
    confidence: str = Field("MEDIUM", description="HIGH, MEDIUM, LOW, SPECULATIVE")
    evidence_text: Optional[str] = Field(None, description="Quote from source")
    
    # QUANTITATIVE (optional)
    delta_value: Optional[float] = Field(None, description="Numeric change if stated")
    delta_unit: Optional[str] = Field(None, description="PCT, EUR, ABSOLUTE")
    absolute_price_target: Optional[float] = Field(None, description="Explicit price target")
    
    timeframe: str = Field("IMMEDIATE", description="IMMEDIATE, 3_MONTHS, 1_YEAR, LONG_TERM")

class NewsExtraction(BaseModel):
    semantic_summary: str
    impact_date: Optional[str]
    receptor_signal: Optional[ReceptorSignal]
    structured_impact: Optional[StructuredImpactNews]  # Changed to enhanced version
    entities: Optional[NewsEntities]
    relevance_score: float

class PaperExtraction(BaseModel):
    """Research Paper Extraction - Basic (for causal links only)"""
    causal_links: List[CausalLink] = Field(default_factory=list)


# ============ ENHANCED PAPER EXTRACTION ============

class QuantitativeFinding(BaseModel):
    """
    Hard economic metrics extracted from text.
    Example: "Demand elasticity is -0.5" -> param="Elasticity", val=-0.5
    """
    parameter: str = Field(..., description="The economic parameter: 'Elasticity', 'Multiplier', 'Price Floor', 'Coefficient'")
    value: float = Field(..., description="Extracted numeric value. If text says 'High', use 1.0; 'Low' use 0.1.")
    unit: str = Field(..., description="Unit: 'PCT', 'EUR', 'USD', 'ABSOLUTE', 'INDEX'")
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(..., description="Confidence in this extraction")

class PaperExtraction(BaseModel):
    """
    Quantitative Research Extraction.
    Strictly filters out qualitative 'Vibe Checks'.
    """
    relevance_score: float = Field(..., description="0.0 (Irrelevant/Soft/Local) to 1.0 (Hard Econ/Finance/Industry)")
    geography: Literal["EU_27", "USA", "GLOBAL", "OTHER"] = Field(..., description="Primary focus region")
    topics: List[str] = Field(description="Tags: Economics, Finance, Energy, Policy, Technology (No Psychology/Sociology)")
    
    summary: str = Field(..., description="Concise executive summary of the research")
    
    key_findings: List[str] = Field(default_factory=list, description="Qualitative findings, optimization goals, or theoretical proofs (e.g. 'Bias is minimized', 'Efficiency increased'). Use this if no specific numbers exist.")
    quantitative_findings: List[QuantitativeFinding] = Field(default_factory=list, description="Extracted HARD metrics (Elasticity, Coefficients) ONLY. Do NOT invent numbers '1.0' for 'maximized'.")
    causal_links: List[CausalLink] = Field(default_factory=list, description="Causal relationships found")

class LawTOC(BaseModel):
    """Table of contents for hierarchical extraction"""
    law_id: str
    title: str
    articles: List[List[str]] = Field(
        description="List of [id, title] for each article"
    )

# ============ USAGE EXAMPLE ============

"""
from google import genai
import extraction_schemas

# Configure structured output
response = genai.GenerativeModel('gemini-2.0-flash').generate_content(
    prompt,
    generation_config=genai.GenerationConfig(
        response_mime_type="application/json",
        response_schema=extraction_schemas.NewsExtraction
    )
)

# Response is guaranteed to match schema
validated_data = NewsExtraction.model_validate_json(response.text)
"""
