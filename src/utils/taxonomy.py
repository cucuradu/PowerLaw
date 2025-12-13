"""
Law Variable Taxonomy for Stricter Extraction
Prevents LLM hallucination of variables like "GDP_EU"
"""

from enum import Enum

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
