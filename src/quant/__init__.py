"""
Quantitative Financial & Risk Intelligence Package for PowerLaw.
Merged & adapted from cucuradu/ml-pipeline.

Modules:
- fragility: Systemic Fragility & PCA Absorption Ratio (Kritzman 2010)
- macro_fred: Keyless Federal Reserve (FRED) Macro & Credit Risk Engine
- regime_hmm: Hidden Markov Model (HMM) Construction Market Regime Classifier
- volatility_garch: GARCH(1,1) Term-Structure Volatility Forecaster
"""

from src.quant.fragility import compute_systemic_fragility
from src.quant.macro_fred import MacroFREDEngine
from src.quant.regime_hmm import ConstructionRegimeHMM, detect_market_regime
from src.quant.volatility_garch import GARCHVolatilityEngine
from src.quant.ranking import ConstructionFactorRanker

__all__ = [
    "compute_systemic_fragility",
    "MacroFREDEngine",
    "ConstructionRegimeHMM",
    "detect_market_regime",
    "GARCHVolatilityEngine",
    "ConstructionFactorRanker"
]
