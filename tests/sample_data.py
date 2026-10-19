"""Synthetic datasets for isolated automated tests only."""
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Union
import pandas as pd
import numpy as np

from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation,
    Strictness
)
from src.storage.graph_store import DualLayerGraphStore
from src.schemas.rule_schema import (
    GNNRule,
    Metadata,
    Scope,
    Timeline,
    Mechanism,
    Enforcement,
    ActionType,
    ActionOperator,
    LawType,
    ConditionalTrigger
)

logger = logging.getLogger(__name__)

RULE_FIXTURES: List[Dict[str, Any]] = [
    {
        "law_id": "32023R0956",
        "title": "Carbon Border Adjustment Mechanism (CBAM) Regulation",
        "chunk_id": "ART_6_CBAM_SURRENDER",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2023-10-01",
        "valid_to": "2034-12-31",
        "summary": "Importers of iron, steel, aluminum, cement, and fertilizers into the EU must purchase and surrender CBAM certificates reflecting weekly average EU ETS allowance prices to equalize carbon costs with domestic producers.",
        "recitals": "Regulation (EU) 2023/956 addresses the risk of carbon leakage resulting from increased EU climate ambition under Fit for 55 by ensuring imported industrial goods bear an equivalent carbon price to EU goods.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
        "rule": {
            "metadata": {
                "regulation_id": "32023R0956",
                "article_id": "ART_6",
                "law_type": "REGULATION",
                "parse_confidence": 0.98
            },
            "scope": {
                "regions": ["EU", "GLOBAL"],
                "industrial_sectors": ["C24", "C23", "C20", "D35"],
                "node_types": ["PRODUCER", "IMPORTER", "TRADER"],
                "affected_commodities": ["STEEL", "ALUMINUM", "CEMENT", "FERTILIZER", "HYDROGEN"]
            },
            "timeline": {
                "activation_date": "2023-10-01",
                "expiration_date": "2034-12-31",
                "phase_in_period_days": 820
            },
            "mechanism": {
                "target_variable": "embedded_carbon_price",
                "action_type": "ADJUSTMENT",
                "operator": "ADD_ABSOLUTE",
                "value": 68.5,
                "unit": "EUR/tCO2",
                "conditional_trigger": {
                    "variable": "origin_carbon_pricing",
                    "operator": "<",
                    "threshold": 68.5
                }
            },
            "enforcement": {
                "strictness": "HARD",
                "penalty_factor": 1.0
            },
            "summary": "Mandatory surrender of CBAM certificates equivalent to embedded emissions at weekly average EU ETS price.",
            "background_context": "Phase-in transitional period requires emissions reporting from October 2023; financial payment obligations commence 2026."
        }
    },
    {
        "law_id": "32003L0087",
        "title": "EU Emissions Trading System (ETS) Directive (Consolidated)",
        "chunk_id": "ART_12_ETS_ALLOWANCES",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2005-01-01",
        "valid_to": "2050-12-31",
        "summary": "Industrial installations and aviation operators must surrender one EU allowance (EUA) for each verified metric ton of CO2 equivalent emitted, under a linear reduction factor tightening the cap annually.",
        "recitals": "Directive 2003/87/EC establishes a system for greenhouse gas emission allowance trading within the Community in order to promote reductions of greenhouse gas emissions in a cost-effective and economically efficient manner.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32003L0087",
        "rule": {
            "metadata": {
                "regulation_id": "32003L0087",
                "article_id": "ART_12",
                "law_type": "DIRECTIVE",
                "parse_confidence": 0.99
            },
            "scope": {
                "regions": ["EU"],
                "industrial_sectors": ["D35", "C24", "C23", "C20", "H51"],
                "node_types": ["PRODUCER", "INFRASTRUCTURE"],
                "affected_commodities": ["CO2_ALLOWANCE", "ELECTRICITY", "STEEL"]
            },
            "timeline": {
                "activation_date": "2005-01-01",
                "expiration_date": "2050-12-31",
                "phase_in_period_days": 0
            },
            "mechanism": {
                "target_variable": "cap_allowance_annual_reduction",
                "action_type": "CONSTRAINT",
                "operator": "MIN_LIMIT",
                "value": 4.3,
                "unit": "PERCENT",
                "conditional_trigger": {
                    "variable": "annual_verified_emissions",
                    "operator": ">",
                    "threshold": 0.0
                }
            },
            "enforcement": {
                "strictness": "HARD",
                "penalty_factor": 1.0
            },
            "summary": "Mandatory surrender of EU allowances with an excess emissions fine of 100 EUR/t plus surrender deficit.",
            "background_context": "The 2023 revision under Fit for 55 accelerated the linear reduction factor to 4.3% annually and phases out free allocation to aviation and CBAM sectors."
        }
    },
    {
        "law_id": "32022R1032",
        "title": "Gas Storage Security of Supply Regulation",
        "chunk_id": "ART_6a_GAS_FILLING_TRAJECTORY",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2022-07-01",
        "valid_to": "2026-12-31",
        "summary": "Member States with underground gas storage facilities must achieve mandatory filling levels reaching at least 90% of capacity by 1 November each calendar year.",
        "recitals": "Regulation (EU) 2022/1032 amends Regulations (EU) 2017/1938 and (EC) No 715/2009 in response to the Russian invasion of Ukraine and gas supply weaponization, creating collective safety reserves.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R1032",
        "rule": {
            "metadata": {
                "regulation_id": "32022R1032",
                "article_id": "ART_6a",
                "law_type": "REGULATION",
                "parse_confidence": 0.97
            },
            "scope": {
                "regions": ["EU", "DE", "FR", "IT", "NL"],
                "industrial_sectors": ["D35", "B06"],
                "node_types": ["INFRASTRUCTURE", "TRADER", "GOVERNMENT"],
                "affected_commodities": ["GAS_TTF", "LNG"]
            },
            "timeline": {
                "activation_date": "2022-07-01",
                "expiration_date": "2026-12-31",
                "phase_in_period_days": 120
            },
            "mechanism": {
                "target_variable": "gas_storage_pct",
                "action_type": "CONSTRAINT",
                "operator": "MIN_LIMIT",
                "value": 90.0,
                "unit": "PERCENT",
                "conditional_trigger": {
                    "variable": "current_date_month",
                    "operator": "==",
                    "threshold": 11.0
                }
            },
            "enforcement": {
                "strictness": "HARD",
                "penalty_factor": 0.95
            },
            "summary": "Mandatory storage filling trajectory reaching 90% capacity by November 1 with intermediate intermediate milestones.",
            "background_context": "Establishes a burden-sharing mechanism for Member States without storage facilities and certification of storage system operators."
        }
    },
    {
        "law_id": "32023R1115",
        "title": "EU Deforestation Regulation (EUDR)",
        "chunk_id": "ART_3_DEFORESTATION_FREE_PROHIBITION",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2024-12-30",
        "valid_to": "2050-12-31",
        "summary": "Prohibits the placing on the EU market or export of cattle, cocoa, coffee, oil palm, rubber, soya and wood unless they are deforestation-free, legally produced, and covered by a geolocation due diligence statement.",
        "recitals": "Regulation (EU) 2023/1115 halts the EU market's contribution to global deforestation and forest degradation driven by agricultural expansion.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R1115",
        "rule": {
            "metadata": {
                "regulation_id": "32023R1115",
                "article_id": "ART_3",
                "law_type": "REGULATION",
                "parse_confidence": 0.96
            },
            "scope": {
                "regions": ["EU", "GLOBAL"],
                "industrial_sectors": ["A01", "C10", "H49"],
                "node_types": ["PRODUCER", "TRADER", "IMPORTER"],
                "affected_commodities": ["SOYA", "PALM_OIL", "CATTLE", "COFFEE", "COCOA", "WOOD"]
            },
            "timeline": {
                "activation_date": "2024-12-30",
                "expiration_date": "2050-12-31",
                "phase_in_period_days": 540
            },
            "mechanism": {
                "target_variable": "deforestation_due_diligence_compliance",
                "action_type": "CONSTRAINT",
                "operator": "SET_VALUE",
                "value": 1.0,
                "unit": "BOOLEAN",
                "conditional_trigger": {
                    "variable": "forest_degradation_post_2020",
                    "operator": "==",
                    "threshold": 1.0
                }
            },
            "enforcement": {
                "strictness": "HARD",
                "penalty_factor": 1.0
            },
            "summary": "Absolute ban on non-compliant commodities with maximum fines of at least 4% of total EU annual turnover.",
            "background_context": "Requires precise satellite plot geolocation coordinates for every commercial batch."
        }
    },
    {
        "law_id": "32024R1252",
        "title": "Critical Raw Materials Act (CRMA)",
        "chunk_id": "ART_5_STRATEGIC_PROJECT_TARGETS",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2024-05-23",
        "valid_to": "2040-12-31",
        "summary": "Sets benchmark quotas for EU domestic capacities by 2030: at least 10% of strategic raw materials extracted in the EU, 40% processed, 25% recycled, and not more than 65% from a single third country.",
        "recitals": "Regulation (EU) 2024/1252 establishes a framework for ensuring a secure and sustainable supply of critical raw materials for digital and net-zero green transitions.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1252",
        "rule": {
            "metadata": {
                "regulation_id": "32024R1252",
                "article_id": "ART_5",
                "law_type": "REGULATION",
                "parse_confidence": 0.95
            },
            "scope": {
                "regions": ["EU"],
                "industrial_sectors": ["B07", "C24", "C29", "J62"],
                "node_types": ["PRODUCER", "CONSUMER", "GOVERNMENT"],
                "affected_commodities": ["LITHIUM", "COBALT", "NICKEL", "RARE_EARTHS", "COPPER"]
            },
            "timeline": {
                "activation_date": "2024-05-23",
                "expiration_date": "2040-12-31",
                "phase_in_period_days": 180
            },
            "mechanism": {
                "target_variable": "domestic_extraction_share",
                "action_type": "CONSTRAINT",
                "operator": "MIN_LIMIT",
                "value": 10.0,
                "unit": "PERCENT",
                "conditional_trigger": {
                    "variable": "import_dependency_single_source",
                    "operator": ">",
                    "threshold": 65.0
                }
            },
            "enforcement": {
                "strictness": "SOFT",
                "penalty_factor": 0.65
            },
            "summary": "Enforces accelerated permitting deadlines: 27 months for extraction and 15 months for processing projects.",
            "background_context": "Mandates large companies to audit supply chains and stress-test strategic raw material dependencies."
        }
    },
    {
        "law_id": "32017R0852",
        "title": "Regulation on Mercury and Mercury Compounds",
        "chunk_id": "ART_8_MERCURY_INDUSTRIAL_BAN",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2018-01-01",
        "valid_to": "2050-12-31",
        "summary": "Prohibits the use of mercury in chlor-alkali production and imposes strict export, import and storage conditions for elemental mercury and mercury-added products.",
        "recitals": "Regulation (EU) 2017/852 implements the Minamata Convention on Mercury to protect human health and the environment from anthropogenic emissions.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0852",
        "rule": {
            "metadata": {
                "regulation_id": "32017R0852",
                "article_id": "ART_8",
                "law_type": "REGULATION",
                "parse_confidence": 0.99
            },
            "scope": {
                "regions": ["EU"],
                "industrial_sectors": ["C20", "E38"],
                "node_types": ["PRODUCER", "TRADER"],
                "affected_commodities": ["MERCURY", "CHLORINE", "CHEMICALS"]
            },
            "timeline": {
                "activation_date": "2018-01-01",
                "expiration_date": "2050-12-31",
                "phase_in_period_days": 0
            },
            "mechanism": {
                "target_variable": "mercury_chlor_alkali_usage",
                "action_type": "CONSTRAINT",
                "operator": "SET_VALUE",
                "value": 0.0,
                "unit": "TONNES",
                "conditional_trigger": {
                    "variable": "manufacturing_process",
                    "operator": "==",
                    "threshold": 1.0
                }
            },
            "enforcement": {
                "strictness": "HARD",
                "penalty_factor": 1.0
            },
            "summary": "Complete zero-tolerance ban on elemental mercury in chlor-alkali plants, mandating safe permanent underground storage.",
            "background_context": "Phase-out of dental amalgam and secondary industrial catalysts by 2025."
        }
    },
    {
        "law_id": "32014R0833",
        "title": "Restrictive Measures (Sanctions) - Energy & Trade Limits",
        "chunk_id": "ART_3j_SOLID_FOSSIL_FUELS",
        "rule_type": "REGULATORY",
        "jurisdiction": "EU",
        "valid_from": "2022-04-08",
        "valid_to": "2030-12-31",
        "summary": "Prohibits the direct or indirect purchase, import or transfer into the EU of coal, crude oil and petroleum products originating in Russia, and maritime services above the price cap.",
        "recitals": "Council Regulation (EU) 833/2014 concerning restrictive measures in view of actions destabilizing the situation in Ukraine, imposing trade restrictions on high-revenue export sectors.",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32014R0833",
        "rule": {
            "metadata": {
                "regulation_id": "32014R0833",
                "article_id": "ART_3j",
                "law_type": "REGULATION",
                "parse_confidence": 0.98
            },
            "scope": {
                "regions": ["EU", "GLOBAL"],
                "industrial_sectors": ["B05", "B06", "C19", "H50"],
                "node_types": ["IMPORTER", "TRADER", "CARRIER"],
                "affected_commodities": ["COAL", "CRUDE_OIL", "DIESEL"]
            },
            "timeline": {
                "activation_date": "2022-08-10",
                "expiration_date": "2030-12-31",
                "phase_in_period_days": 120
            },
            "mechanism": {
                "target_variable": "crude_oil_maritime_price_cap",
                "action_type": "CONSTRAINT",
                "operator": "MAX_LIMIT",
                "value": 60.0,
                "unit": "USD/BBL",
                "conditional_trigger": {
                    "variable": "origin_jurisdiction_sanctioned",
                    "operator": "==",
                    "threshold": 1.0
                }
            },
            "enforcement": {
                "strictness": "HARD",
                "penalty_factor": 1.0
            },
            "summary": "Strict import ban on coal and crude oil price cap mechanism at 60 USD/barrel.",
            "background_context": "Includes maritime transport, financing, and insurance bans for shipments above the threshold."
        }
    }
]


# ============================================================================
# SEED GRAPH BUILDER
# ============================================================================

def populate_fixture_graph(store: DualLayerGraphStore) -> None:
    """Populate the DualLayerGraphStore with a rich, coherent legal-economic topology and time-series."""
    logger.info("Initializing Seed Data for DualLayerGraphStore...")

    # 1. LAW NODES
    for item in RULE_FIXTURES:
        law_node = GraphNode(
            id=f"LAW_{item['law_id']}",
            label=f"{item['law_id']}: {item['title'][:32]}...",
            node_type=NodeType.LAW,
            description=item["summary"],
            jurisdiction=item["jurisdiction"],
            activation_date=item["valid_from"],
            metadata={
                "celex_id": item["law_id"],
                "title": item["title"],
                "source_url": item["source_url"],
                "valid_to": item["valid_to"]
            }
        )
        store.add_node(law_node)

    # 2. SECTOR NODES (NACE Rev. 2)
    nace_sectors = [
        ("C24_BASIC_METALS", "NACE C24: Manufacture of Basic Metals (Steel, Aluminum)", "Basic metals smelting and casting"),
        ("D35_ELECTRICITY_GAS", "NACE D35: Electricity, Gas, Steam & Air Conditioning", "Energy generation and gas network distribution"),
        ("A01_AGRICULTURE", "NACE A01: Crop and Animal Production", "Primary agricultural cultivation, soy, livestock"),
        ("H50_WATER_TRANSPORT", "NACE H50: Water Transport & Maritime Shipping", "Container shipping and bulk carriers"),
        ("C20_CHEMICALS", "NACE C20: Manufacture of Chemicals & Fertilizers", "Industrial chemical synthesis and fertilizers"),
        ("C23_NON_METALLIC_MINERALS", "NACE C23: Non-metallic Mineral Products", "Cement, clinker, lime, glass and ceramics"),
        ("C29_AUTOMOTIVE", "NACE C29: Manufacture of Motor Vehicles", "Passenger vehicles, EV manufacturing and batteries"),
        ("B06_OIL_GAS_EXTRACTION", "NACE B06: Extraction of Crude Petroleum & Natural Gas", "Upstream exploration and drilling"),
        ("B07_METAL_ORES_MINING", "NACE B07: Mining of Metal Ores", "Mining of iron, bauxite, lithium and nickel ores")
    ]
    for sid, label, desc in nace_sectors:
        store.add_node(GraphNode(
            id=sid,
            label=label,
            node_type=NodeType.SECTOR,
            description=desc,
            jurisdiction="EU",
            activation_date="2020-01-01",
            metadata={"nace_code": sid.split("_")[0]}
        ))

    # 3. COMMODITY & ECONOMIC INDEX NODES
    commodities = [
        ("CO2_ALLOWANCE", "EU ETS Carbon Allowance (EUA)", NodeType.COMMODITY, "EUR/tCO2"),
        ("GAS_TTF", "Dutch Title Transfer Facility (TTF) Natural Gas", NodeType.COMMODITY, "EUR/MWh"),
        ("STEEL_COIL", "Hot-Rolled Coil Steel (EU Domestic)", NodeType.COMMODITY, "EUR/tonne"),
        ("ALUMINUM_PRIMARY", "Primary LME Grade Aluminum", NodeType.COMMODITY, "USD/tonne"),
        ("LITHIUM_BATTERY", "Battery-Grade Lithium Hydroxide", NodeType.COMMODITY, "USD/tonne"),
        ("SOYA_BEANS", "Imported Deforestation-Free Soybeans", NodeType.COMMODITY, "EUR/tonne"),
        ("BALTIC_DRY_INDEX", "Baltic Dry Maritime Freight Index", NodeType.ECONOMIC_INDEX, "POINTS"),
        ("EU_ENERGY_INFLATION", "EU Harmonized Energy Inflation Index", NodeType.ECONOMIC_INDEX, "INDEX")
    ]
    for cid, label, ntype, unit in commodities:
        store.add_node(GraphNode(
            id=cid,
            label=label,
            node_type=ntype,
            description=f"Market benchmark for {label}",
            jurisdiction="EU",
            activation_date="2020-01-01",
            metadata={"unit": unit}
        ))

    # 4. ACTION NODES (Mandated Compliance Actions)
    actions = [
        ("ACT_CBAM_SURRENDER", "Mandatory CBAM Certificate Surrender", "LAW_32023R0956", "2023-10-01", "Importers must surrender verified carbon border certificates"),
        ("ACT_ETS_SURRENDER", "Mandatory Annual EUA Allowance Surrender", "LAW_32003L0087", "2005-01-01", "Surrender verified carbon certificates annually by Sept 30"),
        ("ACT_GAS_90PCT_INJECT", "Mandatory 90% Gas Storage Trajectory", "LAW_32022R1032", "2022-07-01", "Storage operators must inject to meet strict seasonal milestones"),
        ("ACT_EUDR_DILIGENCE", "Mandatory Satellite Geolocation Statement", "LAW_32023R1115", "2024-12-30", "Upload verifiable plot-level coordinate proof to EU portal"),
        ("ACT_CRMA_STRATEGIC", "Fast-Track Strategic Extraction Permitting", "LAW_32024R1252", "2024-05-23", "Member States must grant one-stop-shop approvals within 27 months"),
        ("ACT_MERCURY_PHASEOUT", "Total Industrial Mercury Prohibition", "LAW_32017R0852", "2018-01-01", "Cease all chlor-alkali mercury cell operations immediately"),
        ("ACT_SANCTIONS_PRICE_CAP", "60 USD Maritime Crude Price Cap Enforcement", "LAW_32014R0833", "2022-12-05", "Deny maritime insurance to vessels loaded with oil above cap")
    ]
    for aid, label, law_id, act_date, desc in actions:
        store.add_node(GraphNode(
            id=aid,
            label=label,
            node_type=NodeType.ACTION,
            description=desc,
            jurisdiction="EU",
            activation_date=act_date,
            metadata={"mandating_law": law_id}
        ))
        # Edge: LAW -> ACTION (MANDATES)
        store.add_edge(GraphEdge(
            source_id=law_id,
            target_id=aid,
            relation_type=EdgeRelation.MANDATES,
            weight=1.0,
            timestamp=act_date,
            properties={"strictness": "HARD"}
        ))

    # 5. REACTION NODES (Economic & Market Reactions)
    reactions = [
        ("REACT_CBAM_METALS_COST", "Surge in Embedded Carbon Import Costs", "ACT_CBAM_SURRENDER", "2023-11-01", "Third-country steel and aluminum suppliers factor in carbon fees"),
        ("REACT_ETS_CARBON_PRICE", "EUA Price Tightening Under Shrinking Cap", "ACT_ETS_SURRENDER", "2022-01-15", "Carbon market participants bid up EUA prices under 4.3% cap contraction"),
        ("REACT_GAS_STORAGE_BUFFER", "Strategic Gas Buffer Mitigates TTF Spikes", "ACT_GAS_90PCT_INJECT", "2022-11-01", "EU reaches 95% storage, cooling speculative wholesale gas spikes"),
        ("REACT_EUDR_SOY_COMPLIANCE", "Supply Chain Audit Premium on Soy & Palm", "ACT_EUDR_DILIGENCE", "2024-06-01", "Agricultural traders incur traceability validation costs"),
        ("REACT_LITHIUM_CAPEX_SURGE", "Capital Influx into European Critical Lithium Projects", "ACT_CRMA_STRATEGIC", "2024-07-01", "Mining consortia advance French and Portuguese lithium refineries"),
        ("REACT_MERCURY_MEMBRANE_SWAP", "Capital Conversion to Membrane Cell Technology", "ACT_MERCURY_PHASEOUT", "2018-06-01", "Chlorine producers invest in zero-mercury electrochemical cells"),
        ("REACT_OIL_FREIGHT_REROUTING", "Shadow Tanker Fleet & Voyage Route Divergence", "ACT_SANCTIONS_PRICE_CAP", "2023-01-10", "Urals crude redirected to India and China, boosting bunker fuel ton-miles")
    ]
    for rid, label, act_id, act_date, desc in reactions:
        store.add_node(GraphNode(
            id=rid,
            label=label,
            node_type=NodeType.REACTION,
            description=desc,
            jurisdiction="GLOBAL",
            activation_date=act_date,
            metadata={"trigger_action": act_id}
        ))
        # Edge: ACTION -> REACTION (TRIGGERS)
        store.add_edge(GraphEdge(
            source_id=act_id,
            target_id=rid,
            relation_type=EdgeRelation.TRIGGERS,
            weight=1.0,
            timestamp=act_date,
            properties={"elasticity": 0.85}
        ))

    # 6. TOPOLOGICAL CROSS-LAYER EDGES
    cross_edges = [
        # Actions -> Sectors (AFFECTS)
        ("ACT_CBAM_SURRENDER", "C24_BASIC_METALS", EdgeRelation.AFFECTS, 0.95),
        ("ACT_CBAM_SURRENDER", "C23_NON_METALLIC_MINERALS", EdgeRelation.AFFECTS, 0.90),
        ("ACT_ETS_SURRENDER", "D35_ELECTRICITY_GAS", EdgeRelation.AFFECTS, 1.0),
        ("ACT_ETS_SURRENDER", "C24_BASIC_METALS", EdgeRelation.AFFECTS, 0.88),
        ("ACT_GAS_90PCT_INJECT", "D35_ELECTRICITY_GAS", EdgeRelation.AFFECTS, 0.98),
        ("ACT_EUDR_DILIGENCE", "A01_AGRICULTURE", EdgeRelation.AFFECTS, 0.92),
        ("ACT_CRMA_STRATEGIC", "C29_AUTOMOTIVE", EdgeRelation.AFFECTS, 0.80),
        ("ACT_CRMA_STRATEGIC", "B07_METAL_ORES_MINING", EdgeRelation.AFFECTS, 0.95),
        ("ACT_SANCTIONS_PRICE_CAP", "H50_WATER_TRANSPORT", EdgeRelation.AFFECTS, 0.91),

        # Reactions -> Commodities (IMPACTS)
        ("REACT_CBAM_METALS_COST", "STEEL_COIL", EdgeRelation.IMPACTS, 0.85),
        ("REACT_CBAM_METALS_COST", "ALUMINUM_PRIMARY", EdgeRelation.IMPACTS, 0.75),
        ("REACT_ETS_CARBON_PRICE", "CO2_ALLOWANCE", EdgeRelation.IMPACTS, 1.0),
        ("REACT_GAS_STORAGE_BUFFER", "GAS_TTF", EdgeRelation.IMPACTS, -0.70),
        ("REACT_EUDR_SOY_COMPLIANCE", "SOYA_BEANS", EdgeRelation.IMPACTS, 0.60),
        ("REACT_LITHIUM_CAPEX_SURGE", "LITHIUM_BATTERY", EdgeRelation.IMPACTS, 0.80),
        ("REACT_OIL_FREIGHT_REROUTING", "BALTIC_DRY_INDEX", EdgeRelation.IMPACTS, 0.72),

        # Laws -> Sectors (REGULATES)
        ("LAW_32023R0956", "C24_BASIC_METALS", EdgeRelation.REGULATES, 1.0),
        ("LAW_32003L0087", "D35_ELECTRICITY_GAS", EdgeRelation.REGULATES, 1.0),
        ("LAW_32022R1032", "D35_ELECTRICITY_GAS", EdgeRelation.REGULATES, 1.0),
        ("LAW_32023R1115", "A01_AGRICULTURE", EdgeRelation.REGULATES, 1.0),
        ("LAW_32024R1252", "B07_METAL_ORES_MINING", EdgeRelation.REGULATES, 1.0),
        ("LAW_32017R0852", "C20_CHEMICALS", EdgeRelation.REGULATES, 1.0),
        ("LAW_32014R0833", "H50_WATER_TRANSPORT", EdgeRelation.REGULATES, 1.0),

        # Cross-law Amendments or Cascades
        ("LAW_32023R0956", "LAW_32003L0087", EdgeRelation.AMENDS, 0.9),
        ("REACT_ETS_CARBON_PRICE", "REACT_CBAM_METALS_COST", EdgeRelation.CAUSES, 0.85)
    ]
    for src, tgt, rel, w in cross_edges:
        store.add_edge(GraphEdge(
            source_id=src,
            target_id=tgt,
            relation_type=rel,
            weight=w,
            timestamp="2023-01-01",
            properties={"link_type": "topological_interlock"}
        ))

    # 7. BEHIND-THE-GRAPH TABULAR TIME-SERIES HISTORY (2022 - 2025)
    # Generate historical observations for nodes
    history_records: List[NodeStateRecord] = []

    # Historical monthly timestamps
    dates = [
        "2022-01-15", "2022-04-15", "2022-07-15", "2022-10-15",
        "2023-01-15", "2023-04-15", "2023-07-15", "2023-10-15",
        "2024-01-15", "2024-04-15", "2024-07-15", "2024-10-15",
        "2025-01-15", "2025-04-15"
    ]

    # Series 1: CO2_ALLOWANCE (EUA Price EUR/t)
    co2_prices = [84.5, 80.2, 85.0, 77.2, 89.4, 96.2, 88.0, 82.5, 63.4, 69.1, 68.0, 66.5, 71.0, 74.5]
    prev_co2 = co2_prices[0]
    for d, p in zip(dates, co2_prices):
        delta = round(p - prev_co2, 2)
        prev_co2 = p
        state = "PEAK" if p > 90 else ("ELEVATED" if p > 75 else "MODERATE")
        history_records.append(NodeStateRecord(
            node_id="CO2_ALLOWANCE",
            node_type=NodeType.COMMODITY,
            timestamp=d,
            state_value=state,
            metric_name="carbon_price",
            metric_value=p,
            delta=delta,
            unit="EUR/tCO2",
            source_id="ICE_ENDEX_EUA",
            source_type="MARKET_INDEX",
            confidence=0.99,
            notes="Daily settlement price on European Energy Exchange"
        ))

    # Series 2: GAS_TTF (Dutch TTF Gas EUR/MWh)
    ttf_prices = [85.0, 105.0, 160.0, 125.0, 65.0, 42.0, 31.0, 46.0, 30.0, 31.5, 34.0, 40.2, 44.0, 38.5]
    prev_ttf = ttf_prices[0]
    for d, p in zip(dates, ttf_prices):
        delta = round(p - prev_ttf, 2)
        prev_ttf = p
        state = "CRITICAL" if p > 100 else ("VOLATILE" if p > 50 else "STABILIZED")
        history_records.append(NodeStateRecord(
            node_id="GAS_TTF",
            node_type=NodeType.COMMODITY,
            timestamp=d,
            state_value=state,
            metric_name="gas_price_ttf",
            metric_value=p,
            delta=delta,
            unit="EUR/MWh",
            source_id="THE_NETHERLANDS_TTF",
            source_type="MARKET_INDEX",
            confidence=0.99,
            notes="Front-month European benchmark natural gas contract"
        ))

    # Series 3: ACT_GAS_90PCT_INJECT (Gas Storage Percentage)
    storage_pcts = [45.2, 32.0, 64.5, 93.8, 81.2, 56.4, 82.0, 99.2, 78.5, 62.0, 85.5, 95.2, 82.0, 61.5]
    for d, pct in zip(dates, storage_pcts):
        state = "TARGET_MET" if pct >= 90.0 else ("FILLING" if pct >= 60.0 else "DEFICIT")
        history_records.append(NodeStateRecord(
            node_id="ACT_GAS_90PCT_INJECT",
            node_type=NodeType.ACTION,
            timestamp=d,
            state_value=state,
            metric_name="storage_fill_pct",
            metric_value=pct,
            delta=0.0,
            unit="PERCENT",
            source_id="GIE_AGSI_PLUS",
            source_type="EUR_LEX",
            confidence=0.98,
            notes="Gas Infrastructure Europe Aggregate Gas Storage Inventory"
        ))

    # Series 4: C24_BASIC_METALS (Compliance & Carbon Cost Index)
    c24_costs = [100.0, 104.5, 112.0, 118.0, 125.0, 132.0, 136.5, 142.0, 138.0, 141.0, 144.5, 148.0, 151.0, 153.5]
    for d, c in zip(dates, c24_costs):
        state = "HIGH_COMPLIANCE" if c > 130 else "MODERATE_BURDEN"
        history_records.append(NodeStateRecord(
            node_id="C24_BASIC_METALS",
            node_type=NodeType.SECTOR,
            timestamp=d,
            state_value=state,
            metric_name="compliance_cost_index",
            metric_value=c,
            delta=round(c - 100.0, 2),
            unit="INDEX",
            source_id="EUROSTAT_NACE_C24",
            source_type="EXPERT_AUDIT",
            confidence=0.92,
            notes="Indexed composite cost of carbon and CBAM monitoring compliance"
        ))

    # Series 5: ACT_CBAM_SURRENDER (State progression: PROPOSED -> ACTIVE -> REPORTING -> ENFORCED)
    cbam_states = [
        ("2022-01-15", "PROPOSED", 10.0),
        ("2022-07-15", "POLITICAL_AGREEMENT", 35.0),
        ("2023-01-15", "FORMAL_ADOPTION", 60.0),
        ("2023-10-15", "TRANSITIONAL_REPORTING", 85.0),
        ("2024-04-15", "FIRST_EMISSIONS_SUBMISSION", 90.0),
        ("2024-10-15", "AUDIT_REFINEMENT", 95.0),
        ("2025-01-15", "FULL_PRE_ENFORCEMENT", 100.0)
    ]
    for d, st_val, val in cbam_states:
        history_records.append(NodeStateRecord(
            node_id="ACT_CBAM_SURRENDER",
            node_type=NodeType.ACTION,
            timestamp=d,
            state_value=st_val,
            metric_name="implementation_readiness_pct",
            metric_value=val,
            delta=val,
            unit="PERCENT",
            source_id="CELEX:32023R0956",
            source_type="EUR_LEX",
            confidence=1.0,
            notes="Implementation lifecycle milestone tracker"
        ))

    # Series 6: BALTIC_DRY_INDEX (Maritime Shipping Index)
    bdi_values = [1850, 2140, 1980, 1420, 1120, 1380, 1150, 1820, 1640, 1720, 1890, 1940, 1780, 1810]
    for d, v in zip(dates, bdi_values):
        state = "HIGH_FREIGHT" if v > 1800 else "NORMAL_FREIGHT"
        history_records.append(NodeStateRecord(
            node_id="BALTIC_DRY_INDEX",
            node_type=NodeType.ECONOMIC_INDEX,
            timestamp=d,
            state_value=state,
            metric_name="freight_rate_points",
            metric_value=float(v),
            delta=0.0,
            unit="POINTS",
            source_id="BALTIC_EXCHANGE",
            source_type="MARKET_INDEX",
            confidence=0.95,
            notes="Global dry bulk maritime shipping freight benchmark"
        ))

    # Series 7: REACT_LITHIUM_CAPEX_SURGE (Investment Index)
    lithium_capex = [100.0, 105.0, 110.0, 120.0, 135.0, 150.0, 170.0, 195.0, 220.0, 245.0, 275.0, 310.0, 335.0, 360.0]
    for d, v in zip(dates, lithium_capex):
        history_records.append(NodeStateRecord(
            node_id="REACT_LITHIUM_CAPEX_SURGE",
            node_type=NodeType.REACTION,
            timestamp=d,
            state_value="CAPITAL_ACCELERATION",
            metric_name="strategic_raw_material_capex",
            metric_value=v,
            delta=round(v - 100.0, 1),
            unit="INDEX",
            source_id="CRMA_MONITOR",
            source_type="NEWS",
            confidence=0.90,
            notes="Quarterly capital expenditures tracking European battery supply projects"
        ))

    store.add_state_records_batch(history_records)
    store.save()
    logger.info(f"Seed Data populated: {len(store.get_nodes())} nodes, {len(store.get_edges())} edges, {len(store.get_history())} history records.")



def get_fixture_graph_store(storage_dir):
    """Populate explicitly supplied test storage; never loaded by application code."""
    store = DualLayerGraphStore(storage_dir=storage_dir)
    populate_fixture_graph(store)
    import lancedb
    from tests.news_sample_data import NEWS_FIXTURES
    rows = []
    for item in RULE_FIXTURES:
        rows.append({
            "law_id": item["law_id"], "chunk_id": item["chunk_id"],
            "summary": item["summary"], "source_url": item["source_url"],
            "gnn_rule_json": json.dumps(item["rule"]), "rule_type": item["rule_type"],
            "jurisdiction": item["jurisdiction"], "valid_from": item["valid_from"],
            "valid_to": item["valid_to"],
        })
    lancedb.connect(storage_dir).create_table("structured_rules", data=pd.DataFrame(rows), mode="overwrite")
    news_path = Path(storage_dir).parent / "raw/news/scraped_news.json"
    news_path.parent.mkdir(parents=True, exist_ok=True)
    news_path.write_text(json.dumps(NEWS_FIXTURES), encoding="utf-8")
    return store
