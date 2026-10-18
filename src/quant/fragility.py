"""
Systemic Fragility & Absorption Ratio for PowerLaw Construction Ecosystem.
Merged & adapted from cucuradu/ml-pipeline (Phase 6).

Implements the Kritzman, Li, Page & Rigobon (2010) Absorption Ratio:
    AR = λ_max / Σ λ_i = λ_max / n

Calculated over the cross-asset return correlation matrix of construction
stocks (Heidelberg, Holcim, Vinci, Saint-Gobain, Skanska, ArcelorMittal)
and commodities (Rebar Steel, Cement, Carbon Allowance EUA, TTF Gas).

Interpretation:
- AR ∈ [0.40, 0.65]: Normal, healthy, diversified construction supply chain.
- AR > 0.75: Extreme structural lockstep — a regulatory or energy shock cascades across all builders.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

from src.storage.graph_store import DualLayerGraphStore

logger = logging.getLogger(__name__)


def compute_systemic_fragility(
    store: DualLayerGraphStore,
    window_days: int = 60,
    min_assets: int = 3
) -> Dict[str, Any]:
    """
    Computes PCA Absorption Ratio and systemic fragility metrics from
    the behind-the-graph tabular history.

    Returns:
        Dict containing:
        - 'absorption_ratio': float ∈ [0, 1]
        - 'status': 'NORMAL', 'ELEVATED', 'FRAGILE'
        - 'variance_explained_pct': float
        - 'num_assets': int
        - 'window_days': int
        - 'top_contributors': List[Dict] (eigenvector loadings of dominant factor)
    """
    history_df = store.get_history_df()
    if history_df.empty:
        return {
            "absorption_ratio": 0.50,
            "status": "NORMAL",
            "variance_explained_pct": 50.0,
            "num_assets": 0,
            "error": "No history records found in store."
        }

    # Filter price metrics
    price_df = history_df[history_df["metric_name"].isin(["price", "rebar_ex_works", "cement_cem1_price", "eua_spot_price", "ttf_day_ahead"])].copy()
    if price_df.empty or "node_id" not in price_df.columns or "timestamp" not in price_df.columns:
        return {
            "absorption_ratio": 0.50,
            "status": "NORMAL",
            "variance_explained_pct": 50.0,
            "num_assets": 0,
            "error": "Insufficient price metrics."
        }

    # Pivot: dates as index, node_ids as columns
    price_df["timestamp"] = pd.to_datetime(price_df["timestamp"], errors="coerce")
    price_df = price_df.dropna(subset=["timestamp", "metric_value"])
    
    pivot = price_df.pivot_table(
        index="timestamp",
        columns="node_id",
        values="metric_value",
        aggfunc="last"
    ).sort_index()

    # Forward fill gaps up to 3 periods, then calculate log returns
    returns = np.log(pivot.ffill(limit=3) / pivot.ffill(limit=3).shift(1)).dropna(how="all")

    # Take trailing window
    returns_window = returns.tail(window_days).dropna(axis=1, thresh=max(5, int(len(returns) * 0.4)))

    num_assets = returns_window.shape[1]
    if num_assets < min_assets:
        return {
            "absorption_ratio": 0.52,
            "status": "NORMAL",
            "variance_explained_pct": 52.0,
            "num_assets": num_assets,
            "warning": f"Only {num_assets} assets available (< {min_assets}); returning default baseline."
        }

    # Correlation matrix & Eigenvalue decomposition
    corr = returns_window.corr().fillna(0.0).to_numpy()
    eigvals, eigvecs = np.linalg.eigh(corr)
    eigvals = np.clip(eigvals, 0.0, None)
    total_var = float(eigvals.sum())

    if total_var <= 0:
        return {"absorption_ratio": 0.50, "status": "NORMAL", "variance_explained_pct": 50.0, "num_assets": num_assets}

    max_idx = int(np.argmax(eigvals))
    lambda_max = float(eigvals[max_idx])
    ar = float(lambda_max / total_var)

    # Dominant eigenvector loadings
    dominant_vec = np.abs(eigvecs[:, max_idx])
    asset_names = list(returns_window.columns)
    contributions = []
    for name, weight in zip(asset_names, dominant_vec):
        contributions.append({
            "node_id": name,
            "eigenvector_weight": round(float(weight), 4)
        })
    contributions = sorted(contributions, key=lambda x: x["eigenvector_weight"], reverse=True)

    status = "NORMAL"
    if ar > 0.75:
        status = "FRAGILE"
    elif ar > 0.62:
        status = "ELEVATED"

    return {
        "absorption_ratio": round(ar, 4),
        "status": status,
        "variance_explained_pct": round(ar * 100.0, 2),
        "num_assets": num_assets,
        "window_days": window_days,
        "top_contributors": contributions[:6]
    }
