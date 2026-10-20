"""
Law Variable Taxonomy for Stricter Extraction
Prevents LLM hallucination of variables like "GDP_EU"
"""

from enum import Enum

class ConceptCode(Enum):
    """
    Standard Taxonomy for Economic/Industrial Concepts.
    Used for Graph Nodes and Extraction Validation.
    """
    # Energy
    GAS_PRICE = "GAS_PRICE"
    ELEC_PRICE = "ELEC_PRICE"
    OIL_PRICE = "OIL_PRICE"
    CARBON_PRICE = "CARBON_PRICE"
    
    # Macro
    GDP = "GDP"
    INFLATION = "INFLATION" 
    INTEREST_RATE = "INTEREST_RATE"
    UNEMPLOYMENT = "UNEMPLOYMENT"
    
    # Industry
    STEEL_PRODUCTION = "STEEL_PRODUCTION"
    CEMENT_PRODUCTION = "CEMENT_PRODUCTION"
    CHEMICALS_PRODUCTION = "CHEMICALS_PRODUCTION"
    AUTO_PRODUCTION = "AUTO_PRODUCTION"
    
    # Grid
    GRID_LOAD = "GRID_LOAD"
    RENEWABLE_GENERATION = "RENEWABLE_GENERATION"
    COAL_GENERATION = "COAL_GENERATION"

    # Trade
    IMPORTS = "IMPORTS"
    EXPORTS = "EXPORTS"


class LawVariableCategory(Enum):
    BUDGET = "budget"
    EMISSION = "emission"
    ENERGY = "energy"
    PROCEDURAL = "procedural"
    THRESHOLD = "threshold"

class LawVariable(Enum):
    """Valid trigger/target variables for law logic gates"""
    
    # Budget & Financial
    BUDGET_ALLOCATION_PCT = "budget_allocation_pct"
    FUNDING_AMOUNT_EUR = "funding_amount_eur"
    SUBSIDY_RATE_PCT = "subsidy_rate_pct"
    PENALTY_AMOUNT_EUR = "penalty_amount_eur"
    
    # Emissions & Climate
    CO2_EMISSION_THRESHOLD_T = "co2_emission_threshold_t"
    CO2_REDUCTION_TARGET_PCT = "co2_reduction_target_pct"
    GHG_INTENSITY_KG_MWH = "ghg_intensity_kg_mwh"
    
    # Energy & Resources
    GAS_STORAGE_PCT = "gas_storage_pct"
    RENEWABLE_SHARE_PCT = "renewable_share_pct"
    ENERGY_EFFICIENCY_PCT = "energy_efficiency_pct"
    GRID_CAPACITY_MW = "grid_capacity_mw"
    
    # Industrial
    PRODUCTION_VOLUME = "production_volume"
    MARKET_SHARE_PCT = "market_share_pct"
    IMPORT_THRESHOLD = "import_threshold"
    
    # Procedural
    ENFORCEMENT_DATE = "enforcement_date"
    COMPLIANCE_DEADLINE = "compliance_deadline"
    REPORTING_PERIOD = "reporting_period"
    REVIEW_DATE = "review_date"
    
    # Generic Thresholds
    NUMERIC_THRESHOLD = "numeric_threshold"
    PERCENTAGE_THRESHOLD = "percentage_threshold"
    DATE_THRESHOLD = "date_threshold"


def get_valid_law_variables() -> list:
    """Return list of valid variable names for prompt injection"""
    return [v.value for v in LawVariable]


def validate_law_variable(variable: str) -> bool:
    """Check if variable is valid taxonomy"""
    valid = get_valid_law_variables()
    return variable in valid or variable.lower() in valid


def suggest_variable_mapping(hallucinated_var: str) -> str:
    """Suggest correct variable for common hallucinations"""
    mappings = {
        "gdp_eu": "budget_allocation_pct",
        "gdp": "budget_allocation_pct",
        "co2": "co2_emission_threshold_t",
        "carbon": "co2_emission_threshold_t",
        "emission": "co2_emission_threshold_t",
        "gas": "gas_storage_pct",
        "storage": "gas_storage_pct",
        "renewable": "renewable_share_pct",
        "energy": "energy_efficiency_pct",
    }
    lower_var = hallucinated_var.lower()
    for key, suggestion in mappings.items():
        if key in lower_var:
            return suggestion
    return "numeric_threshold"  # Fallback


# ---------------------------------------------------------------------------
# Sector and commodity entities shared by every source (config/sectors.json).
# Entities are immutable, so all adapters must build them here to agree on labels.
# ---------------------------------------------------------------------------
import json
from functools import lru_cache
from pathlib import Path

_SECTORS_PATH = Path(__file__).resolve().parents[2] / "config" / "sectors.json"


@lru_cache(maxsize=1)
def sector_config() -> dict:
    return json.loads(_SECTORS_PATH.read_text(encoding="utf-8"))


_SECTIONS = [("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39), ("F", 41, 43), ("G", 45, 47),
             ("H", 49, 53), ("I", 55, 56), ("J", 58, 63), ("K", 64, 66), ("L", 68, 68), ("M", 69, 75), ("N", 77, 82),
             ("O", 84, 84), ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93), ("S", 94, 96), ("T", 97, 98), ("U", 99, 99)]


def io_sector_code(code: str) -> str:
    """Map a NACE/CPA code (C2351, D35, F41) to the input-output node that contains it (C23, D, F)."""
    import re
    code = code.upper().replace(".", "").removeprefix("CPA_")
    sectors = sector_config()["sectors"]
    match = re.fullmatch(r"([A-U])?(\d{2})\d*", code)
    if code in sectors or not match:
        return code
    division = int(match.group(2))
    letter = next((s for s, lo, hi in _SECTIONS if lo <= division <= hi), match.group(1))
    for key in sorted(sectors, key=len):
        span = re.fullmatch(rf"{letter}(\d\d)(?:[-_](\d\d))?[A-Z]?", key)
        if span and int(span.group(1)) <= division <= int(span.group(2) or span.group(1)):
            return key
    return letter if letter in sectors else code


def sector_entity(code: str, *, batch_id: str, data_origin: str):
    from src.schemas.ingestion import Entity
    code = io_sector_code(code)
    info = sector_config()["sectors"].get(code, {})
    return Entity(entity_id=f"sec_{code}", entity_type="SECTOR", entity_subtype=info.get("kind"),
                  canonical_label=info.get("label", f"NACE {code}"), batch_id=batch_id, data_origin=data_origin)


def commodity_entity(name: str, *, batch_id: str, data_origin: str):
    from src.schemas.ingestion import Entity
    slug = name.strip().lower().replace(" ", "_")
    info = sector_config()["commodities"].get(slug, {})
    return Entity(entity_id=f"commodity_{slug}", entity_type="COMMODITY",
                  canonical_label=info.get("label", slug.replace("_", " ").capitalize()),
                  batch_id=batch_id, data_origin=data_origin)
