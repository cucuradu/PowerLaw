"""
Macro & Credit Risk Engine via Keyless FRED for PowerLaw.
Merged & adapted from cucuradu/ml-pipeline (Phase 7).

Pulls keyless macroeconomic & credit stress leading indicators:
1. Yield Curve Spread (10Y - 3M Treasury: DGS10 - DGS3MO) -> Recession & construction financing freezes
2. High-Yield Option-Adjusted Spread (HY OAS: BAMLH0A0HYM2) -> Corporate debt & contractor credit stress
3. National Financial Conditions Index (NFCI) -> Commercial real estate lending conditions
4. Initial Jobless Claims (ICSA) -> Macroeconomic labor health

Computes trailing percentile ranks (no look-ahead bias) and combines them
into a unified macro_risk score ∈ [0, 1] and crash_risk indicator.
"""

import logging
import io
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd

from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import GraphNode, GraphEdge, NodeStateRecord, NodeType, EdgeRelation

logger = logging.getLogger(__name__)

FRED_INDICATORS = {
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "DGS3MO": "3-Month Treasury Bill Secondary Market Rate",
    "BAMLH0A0HYM2": "ICE BofA US High Yield Index Option-Adjusted Spread",
    "NFCI": "Chicago Fed National Financial Conditions Index",
    "ICSA": "Initial Claims (Seasonally Adjusted)"
}

RISK_WEIGHTS = {"hy": 0.35, "curve": 0.25, "nfci": 0.25, "claims": 0.15}


class MacroFREDEngine:
    """
    Keyless Federal Reserve Economic Data (FRED) fetcher and risk aggregator.
    """
    def __init__(self, start_date: str = "2021-01-01", timeout: int = 15):
        self.start_date = start_date
        self.timeout = timeout

    def fetch_fred_series(self, code: str) -> Optional[pd.Series]:
        """Fetches a single series from the keyless FRED graph CSV endpoint."""
        url = (
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"
            f"&cosd={self.start_date}"
        )
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "PowerLaw/1.0 (MacroEngine)"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw_data = resp.read()
                df = pd.read_csv(io.BytesIO(raw_data))
                if df.shape[1] >= 2:
                    idx = pd.to_datetime(df.iloc[:, 0], errors="coerce")
                    val = pd.to_numeric(df.iloc[:, 1], errors="coerce")
                    ser = pd.Series(val.to_numpy(), index=idx).dropna().sort_index()
                    if not ser.empty:
                        return ser
            except Exception as e:
                time.sleep(1.0 * (attempt + 1))
        return None

    def compute_macro_risk(self) -> Dict[str, Any]:
        """
        Fetches leading series, computes trailing percentiles, and blends into macro_risk.
        Falls back to realistic current economic values if network is unavailable.
        """
        dgs10 = self.fetch_fred_series("DGS10")
        dgs3m = self.fetch_fred_series("DGS3MO")
        hy_oas = self.fetch_fred_series("BAMLH0A0HYM2")
        nfci = self.fetch_fred_series("NFCI")
        claims = self.fetch_fred_series("ICSA")

        # Fallback offline defaults if network unreachable
        if any(s is None for s in [dgs10, dgs3m, hy_oas]):
            from src.config import get_settings
            settings = get_settings()
            if settings.mode == "demo":
                return self._generate_fallback_macro_state()
            return {
                "status": "unavailable",
                "macro_risk": None,
                "crash_risk": None,
                "yield_curve_spread": None,
                "hy_oas_spread": None,
                "nfci_index": None,
                "error": "FRED series unreachable and synthetic fallback is disabled in production"
            }

        # 1. Yield Curve (10Y - 3M)
        common_idx = dgs10.index.intersection(dgs3m.index)
        yield_curve = (dgs10.loc[common_idx] - dgs3m.loc[common_idx]).dropna()
        # Inversion = high risk: invert so higher = worse
        curve_stress = -yield_curve

        # 2. Trailing Percentile Ranks (756 trading days ~ 3y)
        pct_curve = self._trailing_pct_rank(curve_stress).iloc[-1]
        pct_hy = self._trailing_pct_rank(hy_oas).iloc[-1]
        pct_nfci = self._trailing_pct_rank(nfci).iloc[-1] if nfci is not None else 0.40
        pct_claims = self._trailing_pct_rank(claims.rolling(4).mean()).iloc[-1] if claims is not None else 0.35

        # 3. Composite Macro Risk
        macro_risk = (
            RISK_WEIGHTS["hy"] * pct_hy +
            RISK_WEIGHTS["curve"] * pct_curve +
            RISK_WEIGHTS["nfci"] * pct_nfci +
            RISK_WEIGHTS["claims"] * pct_claims
        )
        macro_risk = float(np.clip(macro_risk, 0.0, 1.0))

        latest_curve = float(yield_curve.iloc[-1])
        latest_hy = float(hy_oas.iloc[-1])
        crash_risk = bool(macro_risk > 0.70 or (latest_curve < 0.0 and latest_hy > 4.5))

        return {
            "macro_risk": round(macro_risk, 4),
            "crash_risk": crash_risk,
            "yield_curve_spread": round(latest_curve, 3),
            "hy_oas_spread": round(latest_hy, 2),
            "nfci_index": round(float(nfci.iloc[-1]), 3) if nfci is not None else -0.5,
            "percentiles": {
                "hy_oas_pct": round(float(pct_hy), 3),
                "yield_curve_pct": round(float(pct_curve), 3),
                "nfci_pct": round(float(pct_nfci), 3),
                "claims_pct": round(float(pct_claims), 3)
            },
            "source": "FRED (Federal Reserve Bank of St. Louis)"
        }

    def _trailing_pct_rank(self, s: pd.Series, window: int = 756) -> pd.Series:
        """Trailing window percentile rank without look-ahead bias."""
        return s.rolling(window, min_periods=30).apply(
            lambda w: (w <= w[-1]).mean(), raw=True
        )

    def _generate_fallback_macro_state(self) -> Dict[str, Any]:
        """Robust offline defaults based on current economic baseline."""
        return {
            "macro_risk": 0.425,
            "crash_risk": False,
            "yield_curve_spread": 0.185,
            "hy_oas_spread": 3.45,
            "nfci_index": -0.48,
            "percentiles": {
                "hy_oas_pct": 0.40,
                "yield_curve_pct": 0.48,
                "nfci_pct": 0.38,
                "claims_pct": 0.44
            },
            "source": "FRED Baseline (Simulated / Fallback)"
        }

    def ingest_to_store(self, store: DualLayerGraphStore) -> Dict[str, Any]:
        """Writes macro risk and credit nodes into the DualLayerGraphStore."""
        state = self.compute_macro_risk()
        now_str = datetime.now().strftime("%Y-%m-%d")

        # 1. Macro Risk Composite Node
        node_macro = GraphNode(
            id="INDEX_MACRO_RISK",
            label="Macroeconomic & Credit Risk Composite",
            node_type=NodeType.ECONOMIC_INDEX,
            description="Leading credit spread (HY OAS), yield curve inversion (10Y-3M), and financial tightness index.",
            jurisdiction="GLOBAL",
            activation_date="2022-01-01",
            metadata={"source": state["source"], "crash_risk": state["crash_risk"]}
        )
        store.add_node(node_macro)

        # 2. Edges connecting Macro Risk to Construction Sectors & Equities
        for target_id in ["F41_CONSTRUCTION_BUILDINGS", "F42_CIVIL_ENGINEERING", "C23_NON_METALLIC_MINERALS"]:
            edge = GraphEdge(
                source_id="INDEX_MACRO_RISK",
                target_id=target_id,
                relation_type=EdgeRelation.AFFECTS,
                weight=1.3,
                properties={"description": "System-wide credit conditions dictate construction lending and commercial development."}
            )
            store.add_edge(edge)

        # 3. Tabular History Record
        rec = NodeStateRecord(
            node_id="INDEX_MACRO_RISK",
            node_type=NodeType.ECONOMIC_INDEX,
            timestamp=now_str,
            state_value="CRASH_RISK" if state["crash_risk"] else "NORMAL",
            metric_name="macro_risk_score",
            metric_value=state["macro_risk"],
            delta=0.0,
            unit="PROBABILITY",
            source_id="FRED_COMPOSITE",
            source_type="MARKET_INDEX",
            notes=f"Composite Macro Risk: {state['macro_risk']:.2f} (Yield Curve: {state['yield_curve_spread']:+.2f}%, HY OAS: {state['hy_oas_spread']}%)"
        )
        store.add_state_records_batch([rec])
        store.save()

        return state
