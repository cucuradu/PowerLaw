from typing import Dict, List, Any, Optional
from lancedb.pydantic import LanceModel, Vector
from src.schemas.extraction import LogicGate, StructuredImpact, StructuredImpactNews, CausalLink, NewsEntities, ReceptorSignal
from src.utils.taxonomy import ConceptCode
from pydantic import field_validator
from typing import List

# 1. LAWS (Logic Gates)
class LawChunk(LanceModel):
    vector: Vector(768)
    law_id: str             # CELEX ID (e.g. 32024R9999)
    chunk_id: str           # Article ID (e.g. Article 5)
    summary: str            # Semantic summary (Context & Intent)
    minio_path: str         # Path to raw PDF in MinIO
    source_url: str         # EUR-Lex URL for traceability
    
    # Native Nesting
    logic: LogicGate 
    
    jurisdiction: str
    valid_from: str
    valid_to: str           # e.g. "2030-01-01" or "" if indefinite
    
    # Metadata Refinement
    rule_type: str          # REGULATORY, PROCEDURAL, DEFINITION
    citations: List[str]    # Referenced CELEX IDs or Directive Names
    
    # Schema Compatibility Fields
    entity_types_json: str  # JSON string of entity/product tags
    numeric_constraints_json: str # JSON string of constraints

# 2. NEWS (Structured Impact)
# Renamed from NewsEvent to NewsItem to avoid confusion with Neo4j "Events"
class NewsItem(LanceModel):
    vector: Vector(768)
    news_id: str
    
    # --- SEARCH LAYER ---
    semantic_summary: str   # Renamed from summary per request
    
    # --- CONTEXT LAYER ---
    source_url: str         # Renamed from minio_path (can serve as both or URL)
    source_name: str        # e.g. "Reuters", "Financial Times"
    entities: NewsEntities  # Nested Pydantic Model instead of Dict
    
    # --- CAUSAL LAYER (GNN Input) ---
    receptor_signal_json: str   # JSON of ReceptorSignal
    
    # --- MARKET LAYER (GNN Output) ---
    structured_impact_json: str # JSON of StructuredImpactNews 
    
    impact_date: str        # Kept for sorting

# 3A. RESEARCH PAPERS (Document Layer + Economic Insights)
class ResearchPaper(LanceModel):
    vector: Vector(768)
    paper_id: str
    title: str
    abstract: str
    published_date: str
    publication: str              # e.g. "RePEc - Energy Economics"
    source_url: str               # Original URL (arXiv, RePEc, DOI)
    minio_path: str               # Raw text storage path
    
    # Enhanced Economic Insights (JSON serialized for LanceDB compatibility)
    quantitative_findings_json: Optional[str] = None      # JSON of List[QuantitativeFinding]
    causal_links_json: Optional[str] = None               # JSON of List[CausalLink]
    
    # Legacy/Optional Context
    key_findings: Optional[List[str]] = None          # 3-5 concrete findings
    technology_implications: Optional[List[str]] = None  # ["Hydrogen", "CCS", "Renewables"]



# 3B. ECONOMIC PARAMETERS (Causal Layer - Extracted from Papers)
class EconomicParameter(LanceModel):
    vector: Vector(768)
    parameter_id: str
    paper_id: str           # Foreign Key to ResearchPaper
    
    # Flattened Causal Link
    cause_concept: str
    effect_concept: str
    coefficient: float
    logic_type: str
    
    # Context (optional, for quick lookup without join)
    semantic_context: str   # Short text describing the specific finding

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

# 3C. MARKET INDEXES (Time Series Layer)
class MarketIndex(LanceModel):
    vector: Vector(768) # Embedding of description/name
    ticker: str         # e.g. SPX, GAS_TTF
    date: str
    value: float
    units: str
    description: str    # e.g. "Dutch TTF Natural Gas Futures"

# 4. EXPERT FEEDBACK (Ground Truth)
class ExpertCorrection(LanceModel):
    vector: Vector(768) # Embedding of the reasoning
    correction_id: str
    summary: str        # Reasoning text
    # MinIO path not strictly necessary for corrections as they are pure metadata, 
    # but could point to a screenshot or evidence file if needed. Skipped for now.
    timestamp: str
    
    metric: str
    original_value: float
    corrected_value: float
    
    expert_id: str
    reasoning: str
    status: str # PENDING, APPROVED, REJECTED
