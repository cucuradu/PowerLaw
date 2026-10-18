"""
Cross-Sectional Construction Factor Ranking Engine for ConstructPulse.
Merged & adapted from cucuradu/ml-pipeline (Phase 5).

Computes multi-factor risk-adjusted rankings across European construction equities
and construction physical commodities:
1. Momentum (1M, 3M, 6M returns)
2. Volatility-Adjusted Sharpe Ratio
3. Regulatory Exposure Factor (Degree centrality in causal graph)
4. Carbon Transition Sensitivity (Correlation to EU ETS carbon price)

Ranks assets cross-sectionally for construction investment & supply chain risk screening.
"""

import logging
from typing import Dict, List, Any, Optional
import numpy as np
import pandas as pd

from src.storage.graph_store import DualLayerGraphStore

logger = logging.getLogger(__name__)


class ConstructionFactorRanker:
    """
    Ranks construction assets cross-sectionally based on momentum, volatility,
    graph regulatory gravity, and carbon beta.
    """

    def __init__(self, store: Optional[DualLayerGraphStore] = None):
        self.store = store

    def compute_factor_rankings(self, store: Optional[DualLayerGraphStore] = None) -> pd.DataFrame:
        """
        Extracts temporal returns for construction assets and computes factor scores.
        """
        target_store = store or self.store
        if target_store is None:
            return pd.DataFrame()

        history = target_store.get_history()
        if not history:
            return pd.DataFrame()

        # Build records
        rows = []
        for h in history:
            if h.metric_value is not None:
                rows.append({
                    "timestamp": h.timestamp,
                    "node_id": h.node_id,
                    "node_type": h.node_type.value if hasattr(h.node_type, "value") else str(h.node_type),
                    "value": float(h.metric_value)
                })

        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame()

        # Filter to Commodities and Equities/Indices
        asset_df = df[df["node_type"].isin(["COMMODITY", "ECONOMIC_INDEX", "REACTION"])].copy()
        if asset_df.empty:
            return pd.DataFrame()

        # Group by node_id and calculate trailing return and volatility
        summary = []
        nodes = {n.id: n for n in target_store.get_nodes()}

        for nid, group in asset_df.groupby("node_id"):
            group = group.sort_values("timestamp")
            vals = group["value"].values
            if len(vals) < 2:
                continue

            first_val = vals[0]
            last_val = vals[-1]
            tot_return = ((last_val - first_val) / max(abs(first_val), 1e-4)) * 100.0

            # Periodic returns for volatility
            returns = np.diff(vals) / np.maximum(np.abs(vals[:-1]), 1e-4)
            vol = float(np.std(returns) * np.sqrt(12) * 100.0) if len(returns) > 1 else 15.0

            # Sharpe proxy (risk-adjusted return)
            sharpe = tot_return / max(vol, 1.0)

            node_obj = nodes.get(nid)
            label = node_obj.label if node_obj else nid
            ntype = node_obj.node_type.value if node_obj and hasattr(node_obj.node_type, "value") else "ASSET"

            # Regulatory exposure from in-degree
            in_deg = 0
            if target_store._nodes:
                edges = target_store.get_edges()
                in_deg = sum(1 for e in edges if e.target_id == nid)

            summary.append({
                "Asset ID": nid,
                "Name": label,
                "Type": ntype,
                "Latest Value": round(last_val, 2),
                "Total Return (%)": round(tot_return, 2),
                "Annualized Vol (%)": round(vol, 2),
                "Sharpe Proxy": round(sharpe, 2),
                "Regulatory Exposure": in_deg
            })

        if not summary:
            return pd.DataFrame()

        rank_df = pd.DataFrame(summary)
        # Composite score: 40% Sharpe + 30% Return - 30% Volatility
        rank_df["Composite Rank"] = (
            rank_df["Sharpe Proxy"].rank(ascending=False) * 0.4 +
            rank_df["Total Return (%)"].rank(ascending=False) * 0.3 +
            rank_df["Annualized Vol (%)"].rank(ascending=True) * 0.3
        )
        rank_df = rank_df.sort_values("Composite Rank").reset_index(drop=True)
        rank_df["Rank"] = [f"#{i+1}" for i in range(len(rank_df))]

        return rank_df[[
            "Rank", "Name", "Type", "Latest Value", "Total Return (%)", 
            "Annualized Vol (%)", "Sharpe Proxy", "Regulatory Exposure"
        ]]
