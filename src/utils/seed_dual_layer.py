"""
Deterministic Rich Seed Generator for PowerLaw Dual-Layer System (Construction & Building Materials Niche).

Generates:
1. Topological graph nodes:
   - Laws: Construction Products Regulation (CPR 2024/1069), EPBD (2024/1275), CBAM (2023/956), ETS (2023/1534), Waste Framework Directive (2008/98/EC)
   - Actions: Digital Product Passports (DPP), Whole-Life Carbon (GWP) Calculations, Selective Demolition Quotas, Low-Carbon Concrete Procurement
   - Reactions: Rebar Price Spikes, Green Cement Premium, Scrap Steel Shortage, Mass Timber Substitution, Housing Permits Contraction
   - Sectors: F41 Buildings, F42 Civil Engineering, F43 Specialized Construction, C23 Cement/Concrete, C24 Structural Metals, C16 Timber
   - Commodities: Rebar Steel, Structural Beams, Portland Cement, Ready-Mix Concrete, Recycled Aggregates, CLT Timber, EU ETS Carbon
2. Directed causal edges (Mandates, Triggers, Regulates, Impacts, Causes)
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
    """Populates the dual-layer store with the Construction & Building Materials legal-economic ecosystem."""

    # ================= 1. GRAPH NODES =================
    nodes = [
        # --- CONSTRUCTION LAWS ---
        GraphNode(
            id="LAW_32024R1069_CPR",
            label="Construction Products Regulation (CPR 2024/1069)",
            node_type=NodeType.LAW,
            description="Revised EU Construction Products Regulation establishing harmonized rules for CE marking, mandatory Digital Product Passports (DPP), Environmental Product Declarations (EPD), and circular reuse criteria for structural steel, cement, concrete, and timber.",
            jurisdiction="EU",
            activation_date="2024-11-05",
            metadata={"celex_id": "32024R1069", "status": "IN_FORCE", "law_type": "REGULATION", "strictness": "HARD", "niche": "CONSTRUCTION"}
        ),
        GraphNode(
            id="LAW_32024L1275_EPBD",
            label="Energy Performance of Buildings Directive (EPBD 2024/1275)",
            node_type=NodeType.LAW,
            description="Mandates Zero-Emission Buildings (ZEB) for all new public buildings by 2028 and all private buildings by 2030, with mandatory calculation of Life-Cycle Global Warming Potential (GWP in kgCO2e/m²) and building renovation passports.",
            jurisdiction="EU",
            activation_date="2024-05-28",
            metadata={"celex_id": "32024L1275", "status": "IN_FORCE", "law_type": "DIRECTIVE", "strictness": "HARD", "niche": "CONSTRUCTION"}
        ),
        GraphNode(
            id="LAW_32023R0956_CBAM",
            label="EU CBAM (Regulation 2023/956)",
            node_type=NodeType.LAW,
            description="Carbon Border Adjustment Mechanism equalizing carbon prices of domestic EU construction materials and imports for structural steel (rebars, beams), cement, clinker, and aluminum.",
            jurisdiction="EU",
            activation_date="2023-05-10",
            metadata={"celex_id": "32023R0956", "status": "IN_FORCE", "law_type": "REGULATION", "strictness": "HARD", "niche": "CONSTRUCTION"}
        ),
        GraphNode(
            id="LAW_32023D1534_ETS",
            label="EU ETS Reform Directive (Directive 2023/959)",
            node_type=NodeType.LAW,
            description="Phases out free carbon allowances for cement kilns and primary blast furnace steel plants by 2034 while contracting overall EU allowances by 4.3% annually.",
            jurisdiction="EU",
            activation_date="2023-06-05",
            metadata={"celex_id": "32023D1534", "status": "IN_FORCE", "law_type": "DIRECTIVE", "strictness": "HARD", "niche": "ENERGY_CARBON"}
        ),
        GraphNode(
            id="LAW_32008L0098_WFD",
            label="Waste Framework Directive (Directive 2008/98/EC - CDW)",
            node_type=NodeType.LAW,
            description="Mandates minimum 70% material recovery, recycling, and preparation for re-use of non-hazardous Construction and Demolition Waste (CDW) across all EU Member States.",
            jurisdiction="EU",
            activation_date="2020-07-05",
            metadata={"celex_id": "32008L0098", "status": "IN_FORCE", "law_type": "DIRECTIVE", "strictness": "HARD", "niche": "CIRCULAR_ECONOMY"}
        ),
        GraphNode(
            id="LAW_32022R1032_GAS",
            label="Gas Storage Mandate (Regulation 2022/1032)",
            node_type=NodeType.LAW,
            description="Emergency regulation mandating underground gas storage facilities to be filled to 90%, stabilizing energy inputs for brick and ceramic tile firing.",
            jurisdiction="EU",
            activation_date="2022-06-29",
            metadata={"celex_id": "32022R1032", "status": "IN_FORCE", "law_type": "REGULATION", "strictness": "SOFT"}
        ),

        # --- CONSTRUCTION ACTIONS ---
        GraphNode(
            id="ACT_CPR_DIGITAL_PRODUCT_PASSPORT",
            label="Action: CPR Digital Product Passport Deployment",
            node_type=NodeType.ACTION,
            description="Mandatory digital passports and third-party verified EPD declarations covering global warming potential and recycled content for all commercial construction products.",
            jurisdiction="EU",
            activation_date="2024-12-01",
            metadata={"operator": "MAX_LIMIT", "penalty_factor": 0.9, "target_variable": "dpp_compliance_rate"}
        ),
        GraphNode(
            id="ACT_EPBD_WHOLE_LIFE_CARBON",
            label="Action: EPBD Whole-Life Carbon GWP Calculation",
            node_type=NodeType.ACTION,
            description="Mandatory calculation and reporting of embodied Life-Cycle Global Warming Potential (kgCO2e/m²) required to obtain building construction permits.",
            jurisdiction="EU",
            activation_date="2025-01-01",
            metadata={"operator": "MAX_LIMIT", "penalty_factor": 0.85, "target_variable": "embodied_gwp_kg_m2"}
        ),
        GraphNode(
            id="ACT_CBAM_IMPORTER_REPORTING",
            label="Action: CBAM Construction Steel & Cement Reporting",
            node_type=NodeType.ACTION,
            description="Quarterly filing of embedded greenhouse gas emissions for imported reinforcement bars, section beams, and Portland clinker.",
            jurisdiction="EU",
            activation_date="2023-10-01",
            metadata={"operator": "MAX_LIMIT", "penalty_factor": 0.8, "target_variable": "imported_clinker_steel_emissions"}
        ),
        GraphNode(
            id="ACT_CDW_SELECTIVE_DEMOLITION",
            label="Action: Mandatory Selective Demolition & Reuse",
            node_type=NodeType.ACTION,
            description="Obligatory on-site selective deconstruction audits to salvage structural steel elements and crushed concrete aggregates prior to demolition.",
            jurisdiction="EU",
            activation_date="2023-01-01",
            metadata={"operator": "MIN_LIMIT", "penalty_factor": 0.75, "target_variable": "cdw_recovery_percentage"}
        ),
        GraphNode(
            id="ACT_LOW_CARBON_CONCRETE_SWITCH",
            label="Action: Low-Carbon Concrete & Calcined Clay Transition",
            node_type=NodeType.ACTION,
            description="Civil engineering and public works contractors replacing high-emission CEM I clinker with blended CEM II/III cements and recycled concrete aggregates.",
            jurisdiction="EU",
            activation_date="2024-03-01",
            metadata={"operator": "SUB_PERCENTAGE", "penalty_factor": 0.7, "target_variable": "clinker_to_cement_ratio"}
        ),

        # --- CONSTRUCTION REACTIONS ---
        GraphNode(
            id="REACT_REBAR_STEEL_PRICE_SPIKE",
            label="Reaction: European Construction Rebar Price Volatility",
            node_type=NodeType.REACTION,
            description="Spikes in European construction rebar and structural beam prices driven by high domestic electricity tariffs, scrap tightness, and CBAM import friction.",
            jurisdiction="EU",
            activation_date="2023-11-15",
            metadata={"elasticity": 0.65, "market_direction": "INCREASE"}
        ),
        GraphNode(
            id="REACT_LOW_CARBON_CEMENT_PREMIUM",
            label="Reaction: Green Cement & Low-Carbon Concrete Premium",
            node_type=NodeType.REACTION,
            description="Blended cements (CEM II/C, CEM III) and recycled concrete mixtures command a +€15 to €35 per tonne green premium over conventional ready-mix concrete.",
            jurisdiction="EU",
            activation_date="2024-06-01",
            metadata={"elasticity": 0.55, "market_direction": "INCREASE"}
        ),
        GraphNode(
            id="REACT_RECYCLED_STEEL_SCRAP_SHORTAGE",
            label="Reaction: Ferrous Scrap Shortage & EAF Competition",
            node_type=NodeType.REACTION,
            description="Intense competition for European steel scrap as Electric Arc Furnace (EAF) mills seek low-carbon feedstock to satisfy green construction tenders.",
            jurisdiction="EU",
            activation_date="2024-02-10",
            metadata={"elasticity": -0.45, "market_direction": "DECREASE"}
        ),
        GraphNode(
            id="REACT_HOUSING_STARTS_COMPRESSION",
            label="Reaction: Residential Building Starts & Permits Contraction",
            node_type=NodeType.REACTION,
            description="Sharp drop in European residential building construction permits resulting from material price inflation and stricter regulatory compliance burdens.",
            jurisdiction="EU",
            activation_date="2023-09-01",
            metadata={"elasticity": -0.70, "market_direction": "DECREASE"}
        ),
        GraphNode(
            id="REACT_MASS_TIMBER_SUBSTITUTION",
            label="Reaction: Cross-Laminated Timber (CLT) Frame Substitution",
            node_type=NodeType.REACTION,
            description="Architects and developers substituting structural steel and reinforced concrete with Cross-Laminated Timber (CLT) to satisfy EPBD whole-life carbon caps.",
            jurisdiction="EU",
            activation_date="2024-04-15",
            metadata={"elasticity": 0.50, "market_direction": "INCREASE"}
        ),
        GraphNode(
            id="REACT_EUA_CARBON_PRICE_VOLATILITY",
            label="Reaction: Carbon Credit Pass-Through to Building Materials",
            node_type=NodeType.REACTION,
            description="Rising EU ETS carbon allowance costs directly inflating the marginal production cost of cement clinker and structural steel beams.",
            jurisdiction="EU",
            activation_date="2023-02-21",
            metadata={"elasticity": 0.80, "market_direction": "INCREASE"}
        ),

        # --- CONSTRUCTION & MATERIAL SECTORS (NACE Rev.2) ---
        GraphNode(
            id="F41_CONSTRUCTION_BUILDINGS",
            label="Sector: F41 Construction of Buildings",
            node_type=NodeType.SECTOR,
            description="General construction of residential, commercial, industrial, and institutional buildings.",
            jurisdiction="EU",
            metadata={"nace_code": "F41", "category": "CONSTRUCTION"}
        ),
        GraphNode(
            id="F42_CIVIL_ENGINEERING",
            label="Sector: F42 Civil Engineering & Infrastructure",
            node_type=NodeType.SECTOR,
            description="Construction of roads, railways, bridges, tunnels, utility networks, and heavy public infrastructure.",
            jurisdiction="EU",
            metadata={"nace_code": "F42", "category": "INFRASTRUCTURE"}
        ),
        GraphNode(
            id="F43_SPECIALISED_CONSTRUCTION",
            label="Sector: F43 Specialized Construction Activities",
            node_type=NodeType.SECTOR,
            description="Demolition and site preparation, electrical, plumbing, insulation, plastering, and structural steel erection.",
            jurisdiction="EU",
            metadata={"nace_code": "F43", "category": "SPECIALIZED_CONTRACTING"}
        ),
        GraphNode(
            id="C23_NON_METALLIC_MINERALS",
            label="Sector: C23 Cement, Concrete & Non-Metallic Minerals",
            node_type=NodeType.SECTOR,
            description="Manufacture of cement, clinker, lime, ready-mix concrete, precast concrete elements, and ceramic tiles.",
            jurisdiction="EU",
            metadata={"nace_code": "C23", "category": "BUILDING_MATERIALS"}
        ),
        GraphNode(
            id="C24_BASIC_METALS",
            label="Sector: C24 Basic Metals (Construction Steel)",
            node_type=NodeType.SECTOR,
            description="Manufacture of hot-rolled structural steel sections, reinforcement rebar, wire rod, and structural tubing.",
            jurisdiction="EU",
            metadata={"nace_code": "C24", "category": "HEAVY_METALS"}
        ),
        GraphNode(
            id="C16_MANUFACTURE_WOOD",
            label="Sector: C16 Engineered Wood & Timber Construction",
            node_type=NodeType.SECTOR,
            description="Manufacture of wood products, Cross-Laminated Timber (CLT), glulam beams, and prefabricated timber houses.",
            jurisdiction="EU",
            metadata={"nace_code": "C16", "category": "TIMBER_MATERIALS"}
        ),

        # --- CONSTRUCTION COMMODITIES ---
        GraphNode(
            id="COMM_REBAR_STEEL",
            label="Commodity: Construction Steel Rebar Ex-Works",
            node_type=NodeType.COMMODITY,
            description="Benchmark European reinforcement steel bar price per tonne for concrete structures.",
            jurisdiction="EU",
            metadata={"unit": "EUR/tonne", "ticker": "STEEL_REBAR_EU"}
        ),
        GraphNode(
            id="COMM_PORTLAND_CEMENT",
            label="Commodity: Bulk Portland Cement CEM I",
            node_type=NodeType.COMMODITY,
            description="Standard CEM I grey Portland cement bulk wholesale price benchmark across Northwest Europe.",
            jurisdiction="EU",
            metadata={"unit": "EUR/tonne", "ticker": "CEMENT_CEM1"}
        ),
        GraphNode(
            id="COMM_READY_MIX_CONCRETE",
            label="Commodity: Low-Carbon Ready-Mix Concrete C25/30",
            node_type=NodeType.COMMODITY,
            description="Delivered ready-mix structural concrete batch price per cubic meter.",
            jurisdiction="EU",
            metadata={"unit": "EUR/m3", "ticker": "CONCRETE_C2530"}
        ),
        GraphNode(
            id="COMM_RECYCLED_AGGREGATES",
            label="Commodity: Crushed Concrete Recycled Aggregates",
            node_type=NodeType.COMMODITY,
            description="Recycled concrete aggregate processed from demolition rubble for structural backfill and roadbases.",
            jurisdiction="EU",
            metadata={"unit": "EUR/tonne", "ticker": "RECYCLED_AGG_EU"}
        ),
        GraphNode(
            id="COMM_CLT_TIMBER",
            label="Commodity: Cross-Laminated Timber (CLT)",
            node_type=NodeType.COMMODITY,
            description="Engineered wood structural panel benchmark for commercial and multi-family residential building envelopes.",
            jurisdiction="EU",
            metadata={"unit": "EUR/m3", "ticker": "CLT_TIMBER"}
        ),
        GraphNode(
            id="CO2_ALLOWANCE",
            label="Commodity: EU ETS Allowance (EUA)",
            node_type=NodeType.COMMODITY,
            description="European Union Allowance driving marginal carbon emissions costs for cement clinker and steel production.",
            jurisdiction="EU",
            metadata={"unit": "EUR/tCO2", "ticker": "EUA_DEC"}
        )
    ]

    for n in nodes:
        store.add_node(n)

    # ================= 2. GRAPH EDGES =================
    edges = [
        # Laws -> Actions (MANDATES)
        GraphEdge(source_id="LAW_32024R1069_CPR", target_id="ACT_CPR_DIGITAL_PRODUCT_PASSPORT", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2024-11-05"),
        GraphEdge(source_id="LAW_32024L1275_EPBD", target_id="ACT_EPBD_WHOLE_LIFE_CARBON", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2024-05-28"),
        GraphEdge(source_id="LAW_32023R0956_CBAM", target_id="ACT_CBAM_IMPORTER_REPORTING", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2023-05-10"),
        GraphEdge(source_id="LAW_32008L0098_WFD", target_id="ACT_CDW_SELECTIVE_DEMOLITION", relation_type=EdgeRelation.MANDATES, weight=1.0, timestamp="2020-07-05"),
        GraphEdge(source_id="LAW_32024L1275_EPBD", target_id="ACT_LOW_CARBON_CONCRETE_SWITCH", relation_type=EdgeRelation.MANDATES, weight=0.9, timestamp="2024-05-28"),
        GraphEdge(source_id="LAW_32023D1534_ETS", target_id="ACT_LOW_CARBON_CONCRETE_SWITCH", relation_type=EdgeRelation.MANDATES, weight=0.85, timestamp="2023-06-05"),

        # Actions -> Reactions (TRIGGERS)
        GraphEdge(source_id="ACT_CBAM_IMPORTER_REPORTING", target_id="REACT_REBAR_STEEL_PRICE_SPIKE", relation_type=EdgeRelation.TRIGGERS, weight=0.88, timestamp="2023-10-01"),
        GraphEdge(source_id="ACT_CPR_DIGITAL_PRODUCT_PASSPORT", target_id="REACT_LOW_CARBON_CEMENT_PREMIUM", relation_type=EdgeRelation.TRIGGERS, weight=0.92, timestamp="2024-12-01"),
        GraphEdge(source_id="ACT_LOW_CARBON_CONCRETE_SWITCH", target_id="REACT_LOW_CARBON_CEMENT_PREMIUM", relation_type=EdgeRelation.TRIGGERS, weight=0.85, timestamp="2024-06-01"),
        GraphEdge(source_id="ACT_EPBD_WHOLE_LIFE_CARBON", target_id="REACT_MASS_TIMBER_SUBSTITUTION", relation_type=EdgeRelation.TRIGGERS, weight=0.80, timestamp="2025-01-01"),
        GraphEdge(source_id="ACT_EPBD_WHOLE_LIFE_CARBON", target_id="REACT_HOUSING_STARTS_COMPRESSION", relation_type=EdgeRelation.TRIGGERS, weight=0.78, timestamp="2024-06-01"),
        GraphEdge(source_id="ACT_CDW_SELECTIVE_DEMOLITION", target_id="REACT_RECYCLED_STEEL_SCRAP_SHORTAGE", relation_type=EdgeRelation.TRIGGERS, weight=0.72, timestamp="2023-06-01"),

        # Reactions -> Commodities & Sectors (IMPACTS)
        GraphEdge(source_id="REACT_REBAR_STEEL_PRICE_SPIKE", target_id="COMM_REBAR_STEEL", relation_type=EdgeRelation.IMPACTS, weight=0.95, timestamp="2023-11-15"),
        GraphEdge(source_id="REACT_REBAR_STEEL_PRICE_SPIKE", target_id="F41_CONSTRUCTION_BUILDINGS", relation_type=EdgeRelation.IMPACTS, weight=0.85, timestamp="2023-12-01"),
        GraphEdge(source_id="REACT_REBAR_STEEL_PRICE_SPIKE", target_id="F42_CIVIL_ENGINEERING", relation_type=EdgeRelation.IMPACTS, weight=0.88, timestamp="2023-12-01"),
        GraphEdge(source_id="REACT_LOW_CARBON_CEMENT_PREMIUM", target_id="COMM_PORTLAND_CEMENT", relation_type=EdgeRelation.IMPACTS, weight=0.90, timestamp="2024-06-01"),
        GraphEdge(source_id="REACT_LOW_CARBON_CEMENT_PREMIUM", target_id="COMM_READY_MIX_CONCRETE", relation_type=EdgeRelation.IMPACTS, weight=0.92, timestamp="2024-06-01"),
        GraphEdge(source_id="REACT_LOW_CARBON_CEMENT_PREMIUM", target_id="C23_NON_METALLIC_MINERALS", relation_type=EdgeRelation.IMPACTS, weight=0.82, timestamp="2024-06-01"),
        GraphEdge(source_id="REACT_MASS_TIMBER_SUBSTITUTION", target_id="COMM_CLT_TIMBER", relation_type=EdgeRelation.IMPACTS, weight=0.86, timestamp="2024-04-15"),
        GraphEdge(source_id="REACT_MASS_TIMBER_SUBSTITUTION", target_id="C16_MANUFACTURE_WOOD", relation_type=EdgeRelation.IMPACTS, weight=0.84, timestamp="2024-04-15"),
        GraphEdge(source_id="REACT_EUA_CARBON_PRICE_VOLATILITY", target_id="CO2_ALLOWANCE", relation_type=EdgeRelation.IMPACTS, weight=0.94, timestamp="2023-02-21"),
        GraphEdge(source_id="REACT_EUA_CARBON_PRICE_VOLATILITY", target_id="COMM_PORTLAND_CEMENT", relation_type=EdgeRelation.IMPACTS, weight=0.85, timestamp="2023-03-01"),

        # Laws -> Sectors (REGULATES)
        GraphEdge(source_id="LAW_32024R1069_CPR", target_id="C23_NON_METALLIC_MINERALS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2024-11-05"),
        GraphEdge(source_id="LAW_32024R1069_CPR", target_id="C24_BASIC_METALS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2024-11-05"),
        GraphEdge(source_id="LAW_32024L1275_EPBD", target_id="F41_CONSTRUCTION_BUILDINGS", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2024-05-28"),
        GraphEdge(source_id="LAW_32024L1275_EPBD", target_id="F43_SPECIALISED_CONSTRUCTION", relation_type=EdgeRelation.REGULATES, weight=1.0, timestamp="2024-05-28"),
        GraphEdge(source_id="LAW_32008L0098_WFD", target_id="F41_CONSTRUCTION_BUILDINGS", relation_type=EdgeRelation.REGULATES, weight=0.9, timestamp="2020-07-05"),
        GraphEdge(source_id="LAW_32008L0098_WFD", target_id="COMM_RECYCLED_AGGREGATES", relation_type=EdgeRelation.REGULATES, weight=0.88, timestamp="2020-07-05")
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
    prev_steel = 950.0
    prev_cement = 105.0
    prev_concrete = 115.0
    prev_timber = 440.0
    prev_aggregates = 18.0

    for idx, d_str in enumerate(months):
        year = int(d_str[:4])
        month = int(d_str[5:7])

        # 1. EU ETS Carbon Allowance
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
            notes=f"EEX monthly EUA reference auction settle at {c_val} EUR/t"
        ))
        prev_carbon = c_val

        # 2. Construction Steel Rebar
        if year == 2022:
            base_s = 920.0 + 140.0 * math.sin(min(month, 6) * 0.3)
        elif year == 2023:
            base_s = 760.0 - month * 9.0
        else:
            base_s = 635.0 + 12.0 * math.sin(idx * 0.25)
        s_val = round(base_s, 1)
        records.append(NodeStateRecord(
            node_id="COMM_REBAR_STEEL",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="DOMESTIC_OFFER",
            metric_name="rebar_ex_works",
            metric_value=s_val,
            delta=round(s_val - prev_steel, 1),
            unit="EUR/tonne",
            source_id="S&P_PLATTS_CONSTRUCTION",
            source_type="MARKET_INDEX",
            notes=f"North European steel rebar domestic ex-works at {s_val} EUR/t"
        ))
        prev_steel = s_val

        # 3. Portland Cement CEM I
        # Steadily rising with carbon costs and kiln fuel: €95 in 2022 -> €142 in 2025
        base_cem = 95.0 + (idx * 0.95) + (c_val * 0.18)
        cem_val = round(base_cem, 1)
        records.append(NodeStateRecord(
            node_id="COMM_PORTLAND_CEMENT",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="BULK_WHOLESALE",
            metric_name="cement_cem1_price",
            metric_value=cem_val,
            delta=round(cem_val - prev_cement, 1),
            unit="EUR/tonne",
            source_id="CEMENT_EUROPE_BENCHMARK",
            source_type="MARKET_INDEX",
            notes=f"Bulk CEM I Portland cement wholesale index at {cem_val} EUR/tonne"
        ))
        prev_cement = cem_val

        # 4. Low-Carbon Ready-Mix Concrete C25/30
        base_conc = 110.0 + (idx * 0.8) + (cem_val * 0.15)
        conc_val = round(base_conc, 1)
        records.append(NodeStateRecord(
            node_id="COMM_READY_MIX_CONCRETE",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="DELIVERED_BATCH",
            metric_name="ready_mix_concrete_price",
            metric_value=conc_val,
            delta=round(conc_val - prev_concrete, 1),
            unit="EUR/m3",
            source_id="ERMCO_READYMIX_INDEX",
            source_type="MARKET_INDEX",
            notes=f"Ready-mix structural concrete batch price at {conc_val} EUR/m3"
        ))
        prev_concrete = conc_val

        # 5. Cross-Laminated Timber (CLT)
        base_clt = 460.0 + 45.0 * math.sin(idx * 0.2) + (s_val * 0.08)
        clt_val = round(base_clt, 1)
        records.append(NodeStateRecord(
            node_id="COMM_CLT_TIMBER",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="COMMERCIAL_SUPPLY",
            metric_name="clt_timber_price",
            metric_value=clt_val,
            delta=round(clt_val - prev_timber, 1),
            unit="EUR/m3",
            source_id="TIMBER_TRADE_JOURNAL",
            source_type="MARKET_INDEX",
            notes=f"Cross-laminated timber structural panel quote at {clt_val} EUR/m3"
        ))
        prev_timber = clt_val

        # 6. Recycled Concrete Aggregates
        base_agg = 16.5 + (idx * 0.18)
        agg_val = round(base_agg, 2)
        records.append(NodeStateRecord(
            node_id="COMM_RECYCLED_AGGREGATES",
            node_type=NodeType.COMMODITY,
            timestamp=d_str,
            state_value="PROCESSED_SUPPLY",
            metric_name="recycled_aggregates_price",
            metric_value=agg_val,
            delta=round(agg_val - prev_aggregates, 2),
            unit="EUR/tonne",
            source_id="CIRCULAR_CONSTRUCTION_NETWORK",
            source_type="MARKET_INDEX",
            notes=f"Crushed concrete aggregate 0-32mm price at {agg_val} EUR/tonne"
        ))
        prev_aggregates = agg_val

        # 7. Low-Carbon Cement Green Premium (Reaction)
        if year >= 2023:
            prem_val = round(14.0 + (month * 0.8) + (c_val * 0.12), 2)
            records.append(NodeStateRecord(
                node_id="REACT_LOW_CARBON_CEMENT_PREMIUM",
                node_type=NodeType.REACTION,
                timestamp=d_str,
                state_value="ACTIVE_PREMIUM",
                metric_name="green_cement_premium",
                metric_value=prem_val,
                delta=0.8,
                unit="EUR/tonne",
                source_id="CIRCULAR_BUILDING_MONITOR",
                source_type="NEWS",
                notes=f"Low-carbon blended cement commands +{prem_val} EUR/t over conventional CEM I"
            ))

        # 8. Housing Starts Index (Reaction)
        base_starts = 100.0 - (idx * 0.65) + 6.0 * math.cos(idx * 0.5)
        starts_val = round(max(62.0, base_starts), 1)
        records.append(NodeStateRecord(
            node_id="REACT_HOUSING_STARTS_COMPRESSION",
            node_type=NodeType.REACTION,
            timestamp=d_str,
            state_value="CONTRACTION" if starts_val < 85 else "EXPANSION",
            metric_name="eu_housing_starts_index",
            metric_value=starts_val,
            delta=-0.65,
            unit="INDEX_2021_100",
            source_id="EUROSTAT_CONSTRUCTION_SURVEY",
            source_type="EUR_LEX",
            notes=f"Eurostat construction production & residential building permits index at {starts_val}"
        ))

    store.add_state_records_batch(records)
    store.save()
    print(f"[OK] Seeded Construction Ecosystem: {len(nodes)} Nodes, {len(edges)} Edges, {len(records)} Tabular Records.")


if __name__ == "__main__":
    s = DualLayerGraphStore()
    seed_database(s)
