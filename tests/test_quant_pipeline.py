"""
Unit and Integration Tests for Merged Quantitative Modules (src/quant).

Tests:
1. Systemic Fragility & PCA Absorption Ratio (fragility.py)
2. Macro & Credit Risk Engine via FRED (macro_fred.py)
3. Construction Market Regime Classifier HMM (regime_hmm.py)
4. GARCH(1,1) Term-Structure Volatility Forecaster (volatility_garch.py)
"""

import pytest
import numpy as np
import pandas as pd

from src.storage.dashboard_data import get_graph_store
from src.quant.fragility import compute_systemic_fragility
from src.quant.macro_fred import MacroFREDEngine
from src.quant.regime_hmm import ConstructionRegimeHMM, detect_market_regime
from src.quant.volatility_garch import GARCHVolatilityEngine


@pytest.fixture
def populated_store():
    return get_graph_store()


def test_systemic_fragility(populated_store):
    """Verify PCA Absorption Ratio calculation over construction assets."""
    res = compute_systemic_fragility(populated_store)
    assert "absorption_ratio" in res
    assert "status" in res
    assert "variance_explained_pct" in res
    assert 0.0 <= res["absorption_ratio"] <= 1.0
    assert res["status"] in ["NORMAL", "ELEVATED", "FRAGILE"]


def test_macro_fred_engine(populated_store):
    """Verify keyless FRED macro risk computation and store ingestion."""
    engine = MacroFREDEngine()
    state = engine.compute_macro_risk()

    assert "macro_risk" in state
    assert "crash_risk" in state
    assert 0.0 <= state["macro_risk"] <= 1.0
    assert isinstance(state["crash_risk"], bool)

    # Ingest to store
    ingest_res = engine.ingest_to_store(populated_store)
    assert ingest_res["macro_risk"] == state["macro_risk"]
    assert "INDEX_MACRO_RISK" in populated_store.get_nodes_df()["id"].values


def test_construction_regime_hmm(populated_store):
    """Verify GaussianHMM market regime classification."""
    clf = ConstructionRegimeHMM()
    feat_df = clf.extract_features_from_store(populated_store)
    assert isinstance(feat_df, pd.DataFrame)

    res = clf.fit_predict(feat_df)
    assert "current_regime" in res
    assert res["current_regime"] in ["BULL_EXPANSION", "NEUTRAL_CONSOLIDATION", "CAUTION_VOLATILE", "DEFENSIVE_STRESS"]
    assert "confidence" in res
    assert 0.0 <= res["confidence"] <= 1.0
    assert "probabilities" in res

    # Sum of probabilities should be approximately 1.0
    prob_sum = sum(res["probabilities"].values())
    assert 0.98 <= prob_sum <= 1.02


def test_garch_volatility(populated_store):
    """Verify GARCH(1,1) forward volatility forecasting."""
    engine = GARCHVolatilityEngine()
    vol = engine.forecast_asset_volatility(populated_store, target_node_id="COMM_REBAR_STEEL")

    assert "node_id" in vol
    assert "garch_1d_pct" in vol
    assert "garch_5d_pct" in vol
    assert "term_structure" in vol
    assert vol["garch_1d_pct"] > 0.0
    assert vol["garch_5d_pct"] > 0.0
    assert vol["term_structure"] in ["RISING", "FALLING"]
