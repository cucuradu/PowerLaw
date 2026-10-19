"""
Construction Market Regime Classifier (HMM) for PowerLaw.
Merged & adapted from cucuradu/ml-pipeline (Phase 2).

Uses Gaussian Hidden Markov Models (GaussianHMM) to classify the construction
market into 4 regimes based on asset returns, realized volatility, and trend:
1. BULL_EXPANSION: Strong positive momentum, healthy order books, low volatility.
2. NEUTRAL_CONSOLIDATION: Flat price action, stable supply chain, baseline volatility.
3. CAUTION_VOLATILE: Elevated price swings, regulatory uncertainty, high volatility.
4. DEFENSIVE_STRESS: Downward contraction, high credit/macro stress, high volatility.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
try:
    from sklearn.preprocessing import StandardScaler
    from hmmlearn.hmm import GaussianHMM
    HAS_HMM = True
except ImportError:
    StandardScaler = None
    GaussianHMM = None
    HAS_HMM = False

from src.storage.graph_store import DualLayerGraphStore

logger = logging.getLogger(__name__)

REGIME_NAMES = ["BULL_EXPANSION", "NEUTRAL_CONSOLIDATION", "CAUTION_VOLATILE", "DEFENSIVE_STRESS"]


class ConstructionRegimeHMM:
    """
    Fits GaussianHMM on construction asset time-series and identifies
    the current macroeconomic and market regime.
    """
    def __init__(self, n_components: int = 4, random_state: int = 42):
        self.n_components = n_components
        self.random_state = random_state
        self.scaler = StandardScaler() if StandardScaler is not None else None
        self.model: Optional[Any] = None
        self.state_to_regime: Dict[int, str] = {}

    def extract_features_from_store(self, store: DualLayerGraphStore) -> pd.DataFrame:
        """
        Extracts multi-asset construction price series and calculates log return,
        realized volatility (21d), and trend (distance from 50d SMA).
        """
        hist = store.get_history_df()
        if hist.empty:
            return pd.DataFrame()

        price_df = hist[hist["metric_name"].isin(["price", "rebar_ex_works", "cement_cem1_price"])].copy()
        if price_df.empty:
            return pd.DataFrame()

        price_df["timestamp"] = pd.to_datetime(price_df["timestamp"], errors="coerce")
        price_df = price_df.dropna(subset=["timestamp", "metric_value"])

        pivot = price_df.pivot_table(index="timestamp", columns="node_id", values="metric_value", aggfunc="last").sort_index().ffill()
        
        # Aggregate composite construction index (mean normalized prices)
        norm_prices = pivot / pivot.iloc[0]
        composite = norm_prices.mean(axis=1)

        log_ret = np.log(composite / composite.shift(1)).fillna(0.0)
        rv = log_ret.rolling(window=10, min_periods=3).std().fillna(0.0) * np.sqrt(252) * 100.0
        sma50 = composite.rolling(window=20, min_periods=5).mean()
        trend_dist = ((composite - sma50) / sma50 * 100.0).fillna(0.0)

        feat_df = pd.DataFrame({
            "log_return": log_ret,
            "realized_vol": rv,
            "trend_dist": trend_dist
        }).dropna()

        return feat_df

    def fit_predict(self, feat_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Fits GaussianHMM and predicts current regime and state probabilities.
        """
        if not HAS_HMM or self.scaler is None or GaussianHMM is None or len(feat_df) < 15:
            return {
                "status": "unavailable",
                "current_regime": None,
                "confidence": None,
                "probabilities": {},
                "transition_matrix": [],
                "error": "Insufficient history or HMM dependency unavailable"
            }

        X = feat_df[["log_return", "realized_vol", "trend_dist"]].values
        X_scaled = self.scaler.fit_transform(X)

        self.model = GaussianHMM(
            n_components=self.n_components,
            covariance_type="diag",
            n_iter=150,
            random_state=self.random_state,
            min_covar=1e-3
        )
        self.model.fit(X_scaled)

        # Map hidden states to intuitive regime names based on trend and vol
        state_means = {}
        for s in range(self.n_components):
            # Unscale feature means
            unscaled = self.scaler.inverse_transform(self.model.means_[s:s+1])[0]
            state_means[s] = {"return": unscaled[0], "vol": unscaled[1], "trend": unscaled[2]}

        # Sort states by trend (descending)
        sorted_by_trend = sorted(state_means.keys(), key=lambda s: state_means[s]["trend"], reverse=True)
        # Highest trend = Bull, lowest = Defensive, middle split by vol
        bull_state = sorted_by_trend[0]
        defensive_state = sorted_by_trend[-1]
        remaining = [s for s in sorted_by_trend if s not in (bull_state, defensive_state)]
        
        if len(remaining) >= 2:
            # Sort remaining by vol: lower vol = Neutral, higher vol = Caution
            caution_state = max(remaining, key=lambda s: state_means[s]["vol"])
            neutral_state = min(remaining, key=lambda s: state_means[s]["vol"])
        else:
            neutral_state = remaining[0] if remaining else 1
            caution_state = 2

        self.state_to_regime = {
            bull_state: "BULL_EXPANSION",
            neutral_state: "NEUTRAL_CONSOLIDATION",
            caution_state: "CAUTION_VOLATILE",
            defensive_state: "DEFENSIVE_STRESS"
        }

        # Predict latest state probabilities
        posteriors = self.model.predict_proba(X_scaled)
        latest_probs = posteriors[-1]
        active_state = int(np.argmax(latest_probs))
        current_regime = self.state_to_regime.get(active_state, "NEUTRAL_CONSOLIDATION")
        confidence = float(latest_probs[active_state])

        regime_probs = {
            self.state_to_regime.get(s, f"STATE_{s}"): round(float(latest_probs[s]), 4)
            for s in range(self.n_components)
        }

        return {
            "current_regime": current_regime,
            "confidence": round(confidence, 3),
            "probabilities": regime_probs,
            "state_means": {self.state_to_regime.get(s, str(s)): {k: round(v, 2) for k, v in m.items()} for s, m in state_means.items()},
            "transition_matrix": np.round(self.model.transmat_, 3).tolist()
        }


def detect_market_regime(store: DualLayerGraphStore) -> Dict[str, Any]:
    """Convenience helper to extract features and detect current regime."""
    clf = ConstructionRegimeHMM()
    feat_df = clf.extract_features_from_store(store)
    return clf.fit_predict(feat_df)
