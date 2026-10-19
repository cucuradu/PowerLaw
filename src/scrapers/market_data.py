"""
Market Data & Stock Indicator Ingestion Pipeline for PowerLaw.

Pulls real historical and live market data (equities, commodity futures, carbon proxies, FX)
and maps them directly into the PowerLaw Dual-Layer Graph & Tabular Ledger:
- Graph Nodes: Corporate Equities (ArcelorMittal, Thyssenkrupp, RWE, BASF), Commodity Futures (TTF Gas), Carbon ETFs (KRBN)
- Graph Edges: Directed links (IMPACTS, AFFECTS, REGULATES) between Laws, Actions, Sectors, and Stocks
- Tabular History: Daily time-series records (Close Price, Volume, Daily Return %, Volatility)
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    yf = None
    HAS_YFINANCE = False

from src.storage.graph_store import DualLayerGraphStore
from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)

logger = logging.getLogger(__name__)


TRACKED_INSTRUMENTS = [
    {
        "ticker": "HEI.DE",
        "node_id": "STOCK_HEIDELBERG_MATERIALS",
        "label": "Heidelberg Materials AG (HEI.DE)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "Global cement, heavy building materials, and ready-mix concrete producer subject to EU ETS and CPR circularity rules.",
        "jurisdiction": "DE",
        "currency": "EUR",
        "sector_id": "C23_NON_METALLIC_MINERALS",
        "law_id": "LAW_32024R1069_CPR",
        "edge_relation": EdgeRelation.IMPACTS,
        "weight": 1.6
    },
    {
        "ticker": "HOLN.SW",
        "node_id": "STOCK_HOLCIM",
        "label": "Holcim Ltd (HOLN.SW)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "World leader in building solutions, recycled concrete, and low-carbon cement systems (ECOPact).",
        "jurisdiction": "EU",
        "currency": "CHF",
        "sector_id": "C23_NON_METALLIC_MINERALS",
        "law_id": "LAW_32024R1069_CPR",
        "edge_relation": EdgeRelation.IMPACTS,
        "weight": 1.6
    },
    {
        "ticker": "SGO.PA",
        "node_id": "STOCK_SAINT_GOBAIN",
        "label": "Compagnie de Saint-Gobain (SGO.PA)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "European leader in light and sustainable construction, thermal insulation, and plasterboard facade solutions.",
        "jurisdiction": "FR",
        "currency": "EUR",
        "sector_id": "F41_CONSTRUCTION_BUILDINGS",
        "law_id": "LAW_32024L1275_EPBD",
        "edge_relation": EdgeRelation.IMPACTS,
        "weight": 1.45
    },
    {
        "ticker": "DG.PA",
        "node_id": "STOCK_VINCI",
        "label": "VINCI SA (DG.PA)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "Europe's largest concession and civil engineering contractor executing major low-carbon infrastructure.",
        "jurisdiction": "FR",
        "currency": "EUR",
        "sector_id": "F41_CONSTRUCTION_BUILDINGS",
        "law_id": "LAW_32024L1275_EPBD",
        "edge_relation": EdgeRelation.IMPACTS,
        "weight": 1.5
    },
    {
        "ticker": "SKA-B.ST",
        "node_id": "STOCK_SKANSKA",
        "label": "Skanska AB (SKA-B.ST)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "Nordic and European construction contractor pioneering net-zero buildings and embodied carbon reductions.",
        "jurisdiction": "EU",
        "currency": "SEK",
        "sector_id": "F41_CONSTRUCTION_BUILDINGS",
        "law_id": "LAW_32024L1275_EPBD",
        "edge_relation": EdgeRelation.AFFECTS,
        "weight": 1.35
    },
    {
        "ticker": "MT",
        "node_id": "STOCK_ARCELORMITTAL",
        "label": "ArcelorMittal S.A. (MT)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "Primary supplier of European construction reinforcement rebar, wire rod, and structural section beams.",
        "jurisdiction": "EU",
        "currency": "EUR",
        "sector_id": "C24_BASIC_METALS",
        "law_id": "LAW_32023R0956_CBAM",
        "edge_relation": EdgeRelation.IMPACTS,
        "weight": 1.55
    },
    {
        "ticker": "CRH",
        "node_id": "STOCK_CRH",
        "label": "CRH plc (CRH)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "Building materials supplier specializing in infrastructure aggregates, asphalt, and concrete solutions.",
        "jurisdiction": "EU",
        "currency": "USD",
        "sector_id": "C23_NON_METALLIC_MINERALS",
        "law_id": "LAW_32024R1069_CPR",
        "edge_relation": EdgeRelation.IMPACTS,
        "weight": 1.4
    },
    {
        "ticker": "WIE.VI",
        "node_id": "STOCK_WIENERBERGER",
        "label": "Wienerberger AG (WIE.VI)",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "European producer of clay bricks, roof tiles, and ceramic building envelope systems for residential housing.",
        "jurisdiction": "EU",
        "currency": "EUR",
        "sector_id": "F41_CONSTRUCTION_BUILDINGS",
        "law_id": "LAW_32024L1275_EPBD",
        "edge_relation": EdgeRelation.AFFECTS,
        "weight": 1.3
    },
    {
        "ticker": "KRBN",
        "node_id": "INDEX_KRBN_CARBON",
        "label": "KraneShares Global Carbon Strategy ETF (KRBN)",
        "node_type": NodeType.COMMODITY,
        "description": "Global carbon allowance benchmark tracking EU ETS compliance prices which dictate cement clinker and steel production costs.",
        "jurisdiction": "GLOBAL",
        "currency": "USD",
        "sector_id": "C23_NON_METALLIC_MINERALS",
        "law_id": "LAW_32023D1534_ETS",
        "edge_relation": EdgeRelation.REGULATES,
        "weight": 1.75
    },
    {
        "ticker": "EURUSD=X",
        "node_id": "FX_EUR_USD",
        "label": "EUR / USD Foreign Exchange Rate",
        "node_type": NodeType.ECONOMIC_INDEX,
        "description": "Euro to US Dollar exchange rate directly impacting import parity and CBAM tariff calculations for imported steel & cement.",
        "jurisdiction": "GLOBAL",
        "currency": "USD",
        "sector_id": "C24_BASIC_METALS",
        "law_id": "LAW_32023R0956_CBAM",
        "edge_relation": EdgeRelation.AFFECTS,
        "weight": 1.15
    }
]


class MarketDataIngester:
    """
    Ingests financial equities, commodity benchmarks, and macroeconomic indicators,
    wiring them into the PowerLaw DualLayerGraphStore.
    """
    def __init__(self, store: DualLayerGraphStore):
        self.store = store

    def ingest_tracked_instruments(
        self,
        period: str = "1y",
        sample_step_days: int = 7
    ) -> Dict[str, Any]:
        """
        Fetches historical data for all tracked instruments via yfinance
        (without fabricated offline prices) and writes nodes, edges, and tabular history.

        Args:
            period: Time window (e.g. '6mo', '1y', '2y')
            sample_step_days: Sampling frequency (default: weekly snapshots for clean GNN graphs)
        """
        results = {
            "nodes_added": 0,
            "edges_added": 0,
            "history_rows_added": 0,
            "instruments_processed": []
        }

        for item in TRACKED_INSTRUMENTS:
            ticker = item["ticker"]
            node_id = item["node_id"]
            label = item["label"]
            node_type = item["node_type"]
            jurisdiction = item["jurisdiction"]
            desc = item["description"]
            currency = item["currency"]

            # 1. Ensure Graph Node exists
            node = GraphNode(
                id=node_id,
                label=label,
                node_type=node_type,
                description=desc,
                jurisdiction=jurisdiction,
                activation_date="2022-01-01",
                metadata={"ticker": ticker, "currency": currency}
            )
            self.store.add_node(node)
            results["nodes_added"] += 1

            # 2. Ensure Graph Edges exist (Connect to Law & Sector)
            law_id = item.get("law_id")
            if law_id:
                edge_law = GraphEdge(
                    source_id=law_id,
                    target_id=node_id,
                    relation_type=item.get("edge_relation", EdgeRelation.IMPACTS),
                    weight=item.get("weight", 1.0),
                    properties={"description": f"{law_id} regulates and drives market price dynamics for {label}."}
                )
                self.store.add_edge(edge_law)
                results["edges_added"] += 1

            sector_id = item.get("sector_id")
            if sector_id:
                edge_sec = GraphEdge(
                    source_id=node_id,
                    target_id=sector_id,
                    relation_type=EdgeRelation.AFFECTS,
                    weight=item.get("weight", 1.0) * 0.9,
                    properties={"description": f"Valuation and costs of {label} affect financial conditions in {sector_id}."}
                )
                self.store.add_edge(edge_sec)
                results["edges_added"] += 1

            # 3. Fetch Time Series History
            from src.config import get_settings
            settings = get_settings()

            df = self._fetch_history(ticker, period)
            if df is None or df.empty:
                if "instruments_failed" not in results:
                    results["instruments_failed"] = []
                results["instruments_failed"].append(ticker)
                df = None

            # Sample records
            records_count = 0
            if df is not None and not df.empty:
                df = df.iloc[::sample_step_days] if len(df) > sample_step_days else df

                batch_records = []
                for dt, row in df.iterrows():
                    dt_str = str(dt)[:10]
                    close_val = float(row.get("Close", 0.0))
                    volume_val = float(row.get("Volume", 0.0))
                    ret_val = float(row.get("Return_Pct", 0.0))

                    rec = NodeStateRecord(
                        node_id=node_id,
                        node_type=node_type,
                        timestamp=dt_str,
                        state_value="ACTIVE",
                        metric_name="price",
                        metric_value=close_val,
                        delta=round(ret_val, 2),
                        unit=currency,
                        source_id=ticker,
                        source_type="MARKET_INDEX",
                        notes=f"{label} closing price: {close_val:.2f} {currency} (Weekly Return: {ret_val:+.2f}%)"
                    )
                    batch_records.append(rec)

                if batch_records:
                    self.store.add_state_records_batch(batch_records)
                    records_count = len(batch_records)

            results["history_rows_added"] += records_count
            results["instruments_processed"].append({"ticker": ticker, "records": records_count})

        # Save to disk
        self.store.save()
        failed_count = len(results.get("instruments_failed", []))
        if failed_count == len(TRACKED_INSTRUMENTS):
            results["status"] = "unavailable"
        elif failed_count > 0:
            results["status"] = "partial_failure"
        else:
            results["status"] = "success"
        return results

    def _fetch_history(self, ticker: str, period: str) -> Optional[pd.DataFrame]:
        """Fetches historical price data from yfinance."""
        if not HAS_YFINANCE:
            return None
        try:
            df = yf.download(ticker, period=period, progress=False)
            if df is None or df.empty:
                return None
            
            # Flatten multi-index columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]

            if "Close" not in df.columns:
                return None

            df["Return_Pct"] = df["Close"].pct_change().fillna(0.0) * 100.0
            return df
        except Exception as e:
            logger.warning(f"Failed to fetch market data for {ticker} from yfinance: {e}")
            return None
