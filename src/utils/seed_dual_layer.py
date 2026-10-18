"""
Deterministic Rich Seed Generator for PowerLaw Dual-Layer System.
Generates:
1. Topological graph nodes (Laws, Actions, Reactions, Sectors, Commodities)
2. Complex directed multi-edges (Mandates, Triggers, Impacts, Regulates)
3. Multi-year behind-the-graph temporal tabular history (2022 - 2025 monthly data)
"""

import math
from datetime import datetime, timezone
from typing import List, Dict, Any

from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)
from src.storage.graph_store import DualLayerGraphStore


def seed_database(store: DualLayerGraphStore) -> None:
    """Populates the dual-layer store with realistic EU legal-economic graph and tabular history."""

    # ================= 1. GRAPH NODES =================
    nodes = [
        # --- LAWS ---
        GraphNode(
            id="LAW_32023R0956_CBAM",
            label="EU CBAM (Regulation 2023/956)",
            node_type=NodeType.LAW,
            description="Carbon Border Adjustment Mechanism equalizing carbon price of domestic EU products and imports for steel, cement, aluminum, fertilizers, electricity, and hydrogen.",
            jurisdiction="EU",
            activation_date="2023-05-10",
            metadata={"celex_id": "32023R0956", "status": "IN_FORCE", "law_type": "REGULATION", "strictness": "HARD"}
        ),
        GraphNode(
            id="LAW_32023D1534_ETS",
            label="EU ETS Reform 2023 (Directive 2023/959)",
            node_type=NodeType.LAW,
            description="Revision of the EU Emissions Trading System tightening the emissions cap (-62% by 2030) and phasing out free allowances for trade-exposed industries.",
            jurisdiction="EU",
            activation_date="2023-06-05",
            metadata={"celex_id": "32023D1534", "status": "IN_FORCE", "law_type": "DIRECTIVE", "strictness": "HARD"}
        ),
        GraphNode(
            id="LAW_32022R1032_GAS",
            label="Gas Storage Mandate (Regulation 2022/1032)",
            node_type=NodeType.LAW,
            description="Emergency regulation mandating underground gas storage facilities in member states to be filled to at least 90% by November 1 each year.",
            jurisdiction="EU",
            activation_date="2022-06-29",
            metadata={"celex_id": "32022R1032", "status": "IN_FORCE", "law_type": "REGULATION", "strictness": "HARD"}
        ),
        GraphNode(
            id="LAW_32024R1252_CRMA",
            label="Critical Raw Materials Act (Regulation 2024/1252)",
            node_type=NodeType.LAW,
            description="Framework for ensuring a secure and sustainable supply of critical and strategic raw materials for Europe's green and digital transition.",
            jurisdiction="EU",
            activation_date="2024-05-23",
            metadata={"celex_id": "32024R1252", "status": "IN_FORCE", "law_type": "REGULATION", "strictness": "SOFT"}
        ),
        GraphNode(
            id="LAW_32018L2002_EED",
            label="Energy Efficiency Directive (Directive 2018/2002)",
            node_type=NodeType.LAW,
            description="Sets binding energy efficiency targets and annual final energy consumption reduction obligations across industry, transport, and building sectors.",
            jurisdiction="EU",
            activation_date="2018-12-11",
            metadata={"celex_id": "32018L2002", "status": "IN_FORCE", "law_type": "DIRECTIVE", "strictness": "SOFT"}
        ),

        # --- ACTIONS ---
        GraphNode(
            id="ACT_CBAM_IMPORTER_REPORTING",
            label="Action: CBAM Importer Quarterly Reporting",
            node_type=NodeType.ACTION,
            description="Mandatory quarterly filing of direct and indirect greenhouse gas emissions embedded in imported goods without financial adjustments during transitional phase.",
            jurisdiction="EU",
            activation_date="2023-10-01",
            metadata={"operator": "MAX_LIMIT", "penalty_factor": 0.8, "target_variable": "embedded_emissions_declaration"}
        ),
        GraphNode(
            id="ACT_ETS_CAP_LINEAR_REDUCTION",
            label="Action: ETS Linear Reduction Tightening",
            node_type=NodeType.ACTION,
            description="Linear Reduction Factor (LRF) increase from 2.2% to 4.3% per year (and 4.4% from 2028), shrinking total allowable EUA volume annually.",
            jurisdiction="EU",
            activation_date="2024-01-01",
            metadata={"operator": "SUB_ABSOLUTE", "penalty_factor": 1.0, "target_variable": "allowance_cap_volume"}
        ),
        GraphNode(
            id="ACT_ETS_FREE_ALLOWANCE_PHASEOUT",
            label="Action: ETS Free Allocation Phase-Out",
            node_type=NodeType.ACTION,
            description="Systematic decline of benchmark free allocation for CBAM sectors starting at 2.5% in 2026, reaching 100% phase-out by 2034.",
            jurisdiction="EU",
            activation_date="2026-01-01",
            metadata={"operator": "SUB_PERCENTAGE", "penalty_factor": 1.0, "target_variable": "free_allocation_factor"}
        ),
        GraphNode(
            id="ACT_GAS_90PCT_STORAGE_FILLING",
            label="Action: Mandatory 90% Gas Storage Injection",
            node_type=NodeType.ACTION,
            description="Enforcement of intermediate filling trajectory milestones across EU transmission system operators and storage operators.",
            jurisdiction="EU",
            activation_date="2022-07-01",
            metadata={"operator": "MIN_LIMIT", "penalty_factor": 0.9, "target_variable": "storage_fill_pct"}
        ),
        GraphNode(
            id="ACT_CRMA_STRATEGIC_PERMITTING",
            label="Action: Strategic Mining Permitting Fast-Track",
            node_type=NodeType.ACTION,
            description="Time-cap of 27 months for extraction permits and 15 months for processing permits for designated European strategic projects.",
            jurisdiction="EU",
            activation_date="2024-07-01",
            metadata={"operator": "MAX_LIMIT", "penalty_factor": 0.6, "target_variable": "permitting_duration_months"}
        ),

        # --- REACTIONS ---
        GraphNode(
            id="REACT_TURKISH_INDIAN_STEEL_PREMIUM",
            label="Reaction: Non-EU Steel CBAM Surcharge",
            node_type=NodeType.REACTION,
            description="Turkish, Indian, and Chinese steel mills adjust billing terms to incorporate CBAM compliance certification costs and emissions reporting surcharges.",
            jurisdiction="GLOBAL",
            activation_date="2023-11-15",
            metadata={"elasticity": 0.35, "market_direction": "INCREASE"}
        ),
        GraphNode(
            id="REACT_EUA_CARBON_PRICE_VOLATILITY",
            label="Reaction: EUA Carbon Allowance Price Spikes",
            node_type=NodeType.REACTION,
            description="Speculative hedge fund and industrial compliance purchasing causes carbon credit volatility, peaking at over €100/t before macroeconomic moderation.",
            jurisdiction="EU",
            activation_date="2023-02-21",
            metadata={"elasticity": 0.85, "market_direction": "INCREASE"}
        ),
        GraphNode(
            id="REACT_FERTILIZER_CURTAILMENT",
            label="Reaction: Ammonia Fertilizer Production Curtailment",
            node_type=NodeType.REACTION,
            description="European nitrogen fertilizer producers curtail synthetic ammonia synthesis when wholesale natural gas inputs exceed economic thresholds.",
            jurisdiction="EU",
            activation_date="2022-08-15",
            metadata={"elasticity": -0.65, "market_direction": "DECREASE"}
        ),
        GraphNode(
            id="REACT_TTF_GAS_STORAGE_BUFFER",
            label="Reaction: Winter Gas Price Cushioning",
            node_type=NodeType.REACTION,
            description="High underground storage fill rates decouple European winter spot prices from panic premiums and dampen extreme TTF volatility.",
            jurisdiction="EU",
            activation_date="2022-11-01",
            metadata={"elasticity": -0.45, "market_direction": "STABLE"}
        ),
        GraphNode(
            id="REACT_SCRAP_METAL_DOMESTIC_RETENTION",
            label="Reaction: Scrap Metal Re-routing & EAF Utilization",
            node_type=NodeType.REACTION,
            description="European steelmakers expand Electric Arc Furnace (EAF) capacity and retain recycled steel scrap internally to avoid CBAM exposure.",
            jurisdiction="EU",
            activation_date="2024-02-10",
            metadata={"elasticity": 0.40, "market_direction": "INCREASE"}
        ),
        GraphNode(
            id="REACT_CLEAN_ENERGY_PPA_SURGE",
            label="Reaction: Industrial Clean Power Purchase Agreements",
            node_type=NodeType.REACTION,
            description="Basic metals and chemicals manufacturing conglomerates lock in multi-year solar/wind PPAs to suppress indirect Scope 2 carbon charges.",
            jurisdiction="EU",
            activation_date="2023-04-18",
            metadata={"elasticity": 0.55, "market_direction": "INCREASE"}
        ),

        # --- SECTORS (NACE Rev.2) ---
        GraphNode(
            id="C24_BASIC_METALS",
            label="Sector: C24 Basic Metals (Steel & Al)",
            node_type=NodeType.SECTOR,
            description="Manufacture of basic iron, steel, ferro-alloys, aluminum, and non-ferrous metals.",
            jurisdiction="EU",
            metadata={"nace_code": "C24", "category": "HEAVY_INDUSTRY"}
        ),
        GraphNode(
            id="C20_CHEMICALS",
            label="Sector: C20 Chemicals & Fertilizers",
            node_type=NodeType.SECTOR,
            description="Manufacture of basic chemicals, fertilizers, nitrogen compounds, and synthetic polymers.",
            jurisdiction="EU",
            metadata={"nace_code": "C20", "category": "PROCESS_INDUSTRY"}
        ),
        GraphNode(
            id="C23_NON_METALLIC_MINERALS",
            label="Sector: C23 Cement, Lime & Glass",
            node_type=NodeType.SECTOR,
            description="Manufacture of cement, clinker, lime, plaster, and glass products.",
            jurisdiction="EU",
            metadata={"nace_code": "C23", "category": "MINERALS"}
        ),
        GraphNode(
            id="D35_ELECTRICITY_GAS",
            label="Sector: D35 Electricity & Gas Supply",
            node_type=NodeType.SECTOR,
            description="Electric power generation, transmission, distribution, and gaseous fuels manufacturing.",
            jurisdiction="EU",
            metadata={"nace_code": "D35", "category": "UTILITIES"}
        ),
        GraphNode(
            id="H50_WATER_TRANSPORT",
            label="Sector: H50 Maritime & Coastal Transport",
            node_type=NodeType.SECTOR,
            description="Maritime cargo container freight and coastal bulk vessel transport.",
            jurisdiction="EU",
            metadata={"nace_code": "H50", "category": "LOGISTICS"}
        ),

        # --- COMMODITIES ---
        GraphNode(
            id="CO2_ALLOWANCE",
            label="Commodity: EU ETS Allowance (EUA)",
            node_type=NodeType.COMMODITY,
            description="European Union Allowance permitting the emission of one tonne of carbon dioxide equivalent.",
            jurisdiction="EU",
            metadata={"unit": "EUR/tCO2", "ticker": "EUA_DEC"}
        ),
        GraphNode(
            id="STEEL_REBAR",
            label="Commodity: Steel Rebar (Hot-Rolled)",
            node_type=NodeType.COMMODITY,
            description="Reinforcing steel bar benchmark for commercial and industrial construction.",
            jurisdiction="EU",
            metadata={"unit": "EUR/tonne", "ticker": "STEEL_REBAR_EU"}
        ),
        GraphNode(
            id="GAS_TTF",
            label="Commodity: Dutch TTF Natural Gas",
            node_type=NodeType.COMMODITY,
            description="Title Transfer Facility benchmark virtual trading point for European natural gas.",
            jurisdiction="EU",
            metadata={"unit": "EUR/MWh", "ticker": "TTF_NATGAS"}
        ),
        GraphNode(
            id="FERTILIZER_UREA",
            label="Commodity: Granular Urea Fertilizer",
            node_type=NodeType.COMMODITY,
            description="Solid nitrogenous fertilizer benchmark commodity.",
            jurisdiction="GLOBAL",
            metadata={"unit": "EUR/tonne", "ticker": "UREA_FOB"}
        )
    ]

    for n in nodes:
        store.add_node(n)

    # ================= 2. GRAPH EDGES =================
    edges = [
        # Laws -> Actions (MANDATES)
        GraphEdge(source_id="LAW_32023R0956_CBAM", target_id="ACT_CBAM_IMPORTER_REPORTING", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2023-05-10"),
        GraphEdge(source_id="LAW_32023D1534_ETS", target_id="ACT_ETS_CAP_LINEAR_REDUCTION", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2023-06-05"),
        GraphEdge(source_id="LAW_32023D1534_ETS", target_id="ACT_ETS_FREE_ALLOWANCE_PHASEOUT", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2023-06-05"),
        GraphEdge(source_id="LAW_32022R1032_GAS", target_id="ACT_GAS_90PCT_STORAGE_FILLING", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2022-06-29"),
        GraphEdge(source_id="LAW_32024R1252_CRMA", target_id="ACT_CRMA_STRATEGIC_PERMITTING", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2024-05-23"),

        # Actions -> Reactions (TRIGGERS)
        GraphEdge(source_id="ACT_CBAM_IMPORTER_REPORTING", target_id="REACT_TURKISH_INDIAN_STEEL_PREMIUM", relation_type=EdgeRelation.TRIGGERS, weight=0.85, timestamp="2023-10-01"),
        GraphEdge(source_id="ACT_ETS_CAP_LINEAR_REDUCTION", target_id="REACT_EUA_CARBON_PRICE_VOLATILITY", relation_type=EdgeRelation.TRIGGERS, weight=0.90, timestamp="2024-01-01"),
        GraphEdge(source_id="ACT_GAS_90PCT_STORAGE_FILLING", target_id="REACT_TTF_GAS_STORAGE_BUFFER", relation_type=EdgeRelation.TRIGGERS, weight=0.95, timestamp="2022-07-01"),
        GraphEdge(source_id="ACT_ETS_FREE_ALLOWANCE_PHASEOUT", target_id="REACT_CLEAN_ENERGY_PPA_SURGE", relation_type=EdgeRelation.TRIGGERS, weight=0.75, timestamp="2024-03-01"),
        GraphEdge(source_id="ACT_CBAM_IMPORTER_REPORTING", target_id="REACT_SCRAP_METAL_DOMESTIC_RETENTION", relation_type=EdgeRelation.TRIGGERS, weight=0.70, timestamp="2024-01-15"),

        # Reactions -> Reactions (CAUSES)
        GraphEdge(source_id="REACT_TTF_GAS_STORAGE_BUFFER", target_id="REACT_FERTILIZER_CURTAILMENT", relation_type=EdgeRelation.CAUSES, weight=0.80, timestamp="2022-08-15"),
        GraphEdge(source_id="REACT_TURKISH_INDIAN_STEEL_PREMIUM", target_id="REACT_SCRAP_METAL_DOMESTIC_RETENTION", relation_type=EdgeRelation.CAUSES, weight=0.65, timestamp="2024-02-01"),

        # Reactions -> Commodities / Sectors (IMPACTS)
        GraphEdge(source_id="REACT_TURKISH_INDIAN_STEEL_PREMIUM", target_id="STEEL_REBAR", relation_type=EdgeRelation.IMPACTS, weight=0.78, timestamp="2023-11-15"),
        GraphEdge(source_id="REACT_EUA_CARBON_PRICE_VOLATILITY", target_id="CO2_ALLOWANCE", relation_type=EdgeRelation.IMPACTS, weight=0.92, timestamp="2023-02-21"),
        GraphEdge(source_id="REACT_EUA_CARBON_PRICE_VOLATILITY", target_id="C24_BASIC_METALS", relation_type=EdgeRelation.IMPACTS, weight=0.65, timestamp="2023-03-01"),
        GraphEdge(source_id="REACT_TTF_GAS_STORAGE_BUFFER", target_id="GAS_TTF", relation_type=EdgeRelation.IMPACTS, weight=0.88, timestamp="2022-11-01"),
        GraphEdge(source_id="REACT_FERTILIZER_CURTAILMENT", target_id="FERTILIZER_UREA", relation_type=EdgeRelation.IMPACTS, weight=0.84, timestamp="2022-09-01"),
        GraphEdge(source_id="REACT_CLEAN_ENERGY_PPA_SURGE", target_id="D35_ELECTRICITY_GAS", relation_type=EdgeRelation.IMPACTS, weight=0.70, timestamp="2023-05-01"),
        GraphEdge(source_id="REACT_SCRAP_METAL_DOMESTIC_RETENTION", target_id="C24_BASIC_METALS", relation_type=EdgeRelation.IMPACTS, weight=0.72, timestamp="2024-02-15"),

        # Laws / Actions -> Sectors / Commodities (REGULATES / AFFECTS)
        GraphEdge(source_id="LAW_32023R0956_CBAM", target_id="C24_BASIC_METALS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2023-05-10"),
        GraphEdge(source_id="LAW_32023R0956_CBAM", target_id="C20_CHEMICALS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2023-05-10"),
        GraphEdge(source_id="LAW_32023R0956_CBAM", target_id="C23_NON_METALLIC_MINERALS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2023-05-10"),
        GraphEdge(source_id="LAW_32023D1534_ETS", target_id="D35_ELECTRICITY_GAS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2023-06-05"),
        GraphEdge(source_id="LAW_32023D1534_ETS", target_id="H50_WATER_TRANSPORT", relation_type=EdgeRelation.REGULATES, weight=0.9, timestamp="2023-06-05"),
        GraphEdge(source_id="LAW_32018L2002_EED", target_id="C24_BASIC_METALS", relation_type=EdgeRelation.REGULATES, weight=0.8, timestamp="2018-12-11")
    ]

    for e in edges:
        store.add_edge(e)

    # ================= 3. BEHIND-THE-GRAPH TABULAR HISTORY =================
    # Generate realistic monthly data points from 2022-01 to 2025-12 (48 months)
    records: List[NodeStateRecord] = []

    months = []
    for y in [2022, 2023, 2024, 2025]:
        for m in range(1, 13):
            months.append(f"{y}-{m:02d}-15")

    prev_carbon = 82.0
    prev_gas = 75.0
    prev_steel = 950.0
    prev_urea = 580.0

    for idx, d_str in enumerate(months):
        year = int(d_str[:4])
        month = int(d_str[5:7])

        # 1. CO2 Allowance
        # Historical pattern: €80 in early 2022 -> €100 in Feb 2023 -> dip to €55 in early 2024 -> €70 in 2025
        if year == 2022:
            base_c = 75.0 + 8.0 * math.sin(idx * 0.4)
        elif year == 2023:
            base_c = 95.0 - (month * 2.0)
        elif year == 2024:
            base_c = 58.0 + (month * 1.2)
        else:
            base_c = 72.0 + 3.0 * math.cos(idx * 0.3)
        c_val = round(base_c, 2)
        records.append(NodeStateRecord(
            node_id="CO2_ALLOWANCE",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="ACTIVE_TRADING",
            metric_name="eua_spot_price",
            metric_value=c_val,
            delta=round(c_val - prev_carbon, 2),
            unit="EUR/tCO2",
            source_id="EEX_SPOT_INDEX",
            source_type="MARKET_INDEX",
            confidence=1.0,
            notes=f"EEX monthly EUA reference auction settle at {c_val} EUR/t"
        ))
        prev_carbon = c_val

        # 2. Dutch TTF Gas
        # Historical pattern: €80 -> €230 (Aug 2022) -> €40 (2023) -> €32-€40 (2024-2025)
        if year == 2022:
            if month <= 7:
                base_g = 80.0 + month * 18.0
            elif month == 8:
                base_g = 235.0  # Peak
            else:
                base_g = 235.0 - (month - 8) * 35.0
        elif year == 2023:
            base_g = 60.0 - min(month * 2.5, 25.0)
        else:
            base_g = 34.0 + 5.0 * math.sin(month * 0.5)
        g_val = round(base_g, 2)
        records.append(NodeStateRecord(
            node_id="GAS_TTF",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="MARKET_SETTLED",
            metric_name="ttf_day_ahead",
            metric_value=g_val,
            delta=round(g_val - prev_gas, 2),
            unit="EUR/MWh",
            source_id="ICE_ENDEX_TTF",
            source_type="MARKET_INDEX",
            confidence=1.0,
            notes=f"ICE Endex TTF benchmark monthly average at {g_val} EUR/MWh"
        ))
        prev_gas = g_val

        # 3. Steel Rebar
        # €1050 peak in 2022 -> gradual cooling to €620 in 2024
        if year == 2022:
            base_s = 900.0 + 150.0 * math.sin(min(month, 6) * 0.3)
        elif year == 2023:
            base_s = 740.0 - month * 8.0
        else:
            base_s = 620.0 + 10.0 * math.sin(idx * 0.2)
        s_val = round(base_s, 1)
        records.append(NodeStateRecord(
            node_id="STEEL_REBAR",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="DOMESTIC_OFFER",
            metric_name="rebar_ex_works",
            metric_value=s_val,
            delta=round(s_val - prev_steel, 1),
            unit="EUR/tonne",
            source_id="S&P_PLATTS_STEEL",
            source_type="MARKET_INDEX",
            confidence=0.95,
            notes=f"North European steel rebar domestic ex-works at {s_val} EUR/t"
        ))
        prev_steel = s_val

        # 4. Mandatory Gas Storage Filling (Action)
        # Seasonal: Low in spring (35-50%), Peaks in Oct/Nov (92-98%)
        storage_pct = round(50.0 + 44.0 * math.sin((month - 4) * (math.pi / 6)), 1)
        storage_pct = max(28.0, min(99.0, storage_pct))
        records.append(NodeStateRecord(
            node_id="ACT_GAS_90PCT_STORAGE_FILLING",
            node_type=NodeType.ACTION,
            timestamp=d_str,
            state_value="COMPLIANT" if storage_pct >= 85.0 else "INJECTION_PHASE",
            metric_name="eu_storage_fill_pct",
            metric_value=storage_pct,
            delta=0.0,
            unit="PERCENT",
            source_id="GIE_AGSI_PLUS",
            source_type="EUR_LEX",
            confidence=1.0,
            notes=f"Aggregated Gas Storage Inventory (AGSI+) reported EU fill at {storage_pct}%"
        ))

        # 5. CBAM Importer Declarations (Action)
        if year >= 2023 and month >= 10:
            decl_count = float(450 + (year - 2023) * 320 + month * 40)
            records.append(NodeStateRecord(
                node_id="ACT_CBAM_IMPORTER_REPORTING",
                node_type=NodeType.ACTION,
                timestamp=d_str,
                state_value="REPORTING_ACTIVE",
                metric_name="active_cbam_declarants",
                metric_value=decl_count,
                delta=40.0,
                unit="DECLARANTS",
                source_id="EC_TAXUD_CBAM_REGISTRY",
                source_type="EUR_LEX",
                confidence=0.98,
                notes=f"EU Customs portal registered {int(decl_count)} authorized CBAM reporting entities"
            ))

        # 6. Turkish & Indian Steel CBAM Surcharge (Reaction)
        if year >= 2024:
            surcharge = round(12.0 + (month * 1.4) + (c_val * 0.15), 2)
            records.append(NodeStateRecord(
                node_id="REACT_TURKISH_INDIAN_STEEL_PREMIUM",
                node_type=NodeType.REACTION,
                timestamp=d_str,
                state_value="SURCHARGE_LEVIED",
                metric_name="cbam_compliance_premium",
                metric_value=surcharge,
                delta=1.4,
                unit="EUR/tonne",
                source_id="MEPS_INTERNATIONAL_NEWS",
                source_type="NEWS",
                confidence=0.92,
                notes=f"Import steel cargo quote includes verified CBAM data fee: +{surcharge} EUR/t"
            ))

        # 7. Ammonia Fertilizer Curtailment (Reaction)
        if year == 2022 and 6 <= month <= 10:
            curtailment_rate = 68.0  # Acute curtailment during 2022 crisis
        elif year == 2022:
            curtailment_rate = 35.0
        elif year == 2023:
            curtailment_rate = 18.0
        else:
            curtailment_rate = 9.0
        records.append(NodeStateRecord(
            node_id="REACT_FERTILIZER_CURTAILMENT",
            node_type=NodeType.REACTION,
            timestamp=d_str,
            state_value="HIGH_CURTAILMENT" if curtailment_rate > 30 else "NORMAL_OPERATION",
            metric_name="eu_ammonia_curtailment_pct",
            metric_value=float(curtailment_rate),
            delta=0.0,
            unit="PERCENT",
            source_id="FERTILIZERS_EUROPE_SURVEY",
            source_type="NEWS",
            confidence=0.90,
            notes=f"Estimated European synthetic nitrogen synthesis reduction: {curtailment_rate}%"
        ))

    store.add_state_records_batch(records)
    store.save()
    print(f"[OK] Seeded DualLayerGraphStore: {len(nodes)} Nodes, {len(edges)} Edges, {len(records)} Tabular Records.")


if __name__ == "__main__":
    s = DualLayerGraphStore()
    seed_database(s)
