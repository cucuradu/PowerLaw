"""
Volatility Forecasting Engine (GARCH) for PowerLaw.
Merged & adapted from cucuradu/ml-pipeline (Phase 3).

Fits GARCH(1,1) models on construction commodity and equity return series
to forecast forward annualized volatility at 1-day and 5-day horizons.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

try:
    from arch import arch_model
    HAS_ARCH = True
except ImportError:
    HAS_ARCH = False

from src.storage.graph_store import DualLayerGraphStore

logger = logging.getLogger(__name__)


class GARCHVolatilityEngine:
    """
    Fits GARCH(1,1) on construction time series and forecasts term structure volatility.
    """
    def __init__(self, p: int = 1, q: int = 1):
        self.p = p
        self.q = q

    def forecast_asset_volatility(
        self,
        store: DualLayerGraphStore,
        target_node_id: str = "COMM_REBAR_STEEL"
    ) -> Dict[str, Any]:
        """
        Extracts price series for target_node_id from store and computes GARCH forward volatility.
        """
        hist = store.get_history_df()
        if hist.empty:
            return self._fallback_vol_result(target_node_id)

        node_hist = hist[hist["node_id"] == target_node_id].copy()
        if len(node_hist) < 15:
            # Fallback for sparse history
            return self._fallback_vol_result(target_node_id)

        node_hist["timestamp"] = pd.to_datetime(node_hist["timestamp"], errors="coerce")
        node_hist = node_hist.dropna(subset=["timestamp", "metric_value"]).sort_values("timestamp")
        
        prices = node_hist["metric_value"].astype(float).values
        returns = np.diff(np.log(prices))

        if len(returns) < 12 or not HAS_ARCH:
            rv = float(np.std(returns) * np.sqrt(252) * 100.0) if len(returns) > 2 else 22.5
            return {
                "node_id": target_node_id,
                "garch_1d_pct": round(rv, 2),
                "garch_5d_pct": round(rv * 1.05, 2),
                "term_structure": "RISING",
                "method": "Realized Volatility Fallback"
            }

        try:
            # Scale returns by 100 for optimizer stability
            scaled_ret = returns * 100.0
            am = arch_model(scaled_ret, vol="GARCH", p=self.p, q=self.q, mean="Constant", dist="normal")
            res = am.fit(disp="off", update_freq=0)

            forecasts = res.forecast(horizon=5, align="origin", reindex=False)
            var_path_scaled = forecasts.variance.iloc[-1].to_numpy()
            var_path = var_path_scaled / 10000.0

            daily_var_1d = float(var_path[0])
            daily_var_5d_avg = float(np.mean(var_path[:5]))

            ann_1d = float(np.sqrt(max(1e-6, daily_var_1d)) * np.sqrt(252) * 100.0)
            ann_5d = float(np.sqrt(max(1e-6, daily_var_5d_avg)) * np.sqrt(252) * 100.0)

            term = "RISING" if ann_5d > ann_1d else "FALLING"

            return {
                "node_id": target_node_id,
                "garch_1d_pct": round(ann_1d, 2),
                "garch_5d_pct": round(ann_5d, 2),
                "term_structure": term,
                "aic": round(float(res.aic), 2),
                "method": "GARCH(1,1)"
            }
        except Exception as e:
            logger.warning(f"GARCH fit failed for {target_node_id}: {e}")
            return self._fallback_vol_result(target_node_id)

    def _fallback_vol_result(self, node_id: str) -> Dict[str, Any]:
        return {
            "node_id": node_id,
            "garch_1d_pct": 24.50,
            "garch_5d_pct": 25.80,
            "term_structure": "RISING",
            "method": "Baseline Benchmark"
        }
