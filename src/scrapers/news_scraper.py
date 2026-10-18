"""
Economic & Trade News Scraper & Event Extraction Pipeline for PowerLaw
Collects real-time and historical news related to EU trade, commodities, energy, and industry.

Extracts:
1. Event timestamp, Source URL, Headline, and Summary
2. Identified economic actors (companies, countries, sectors)
3. Extracted Actions (production cuts, capex, price revisions, etc.) and Reactions (price changes, supply shifts, etc.)
4. Persistence into LanceDB (NewsItem) and local disk cache
5. Dual-Layer Graph integration (Action/Reaction nodes + temporal tabular history)
6. Fallback offline seed generator for guaranteed offline execution
"""

import os
import re
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Union
import email.utils

import requests
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    HAS_BS4 = False
import xml.etree.ElementTree as ET
import numpy as np

# Schemas
from src.schemas.extraction import (
    NewsEntities,
    EconomicAction,
    EconomicReaction,
    StructuredEconomicEvent,
    ReceptorSignal,
    StructuredImpactNews,
    NewsExtraction
)
from src.schemas.lancedb import NewsItem

# Models and Graph Storage
from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)
from src.storage.graph_store import DualLayerGraphStore

try:
    import lancedb
    HAS_LANCEDB = True
except ImportError:
    HAS_LANCEDB = False

try:
    from src.utils.api_wrapper import get_embedding, get_completion_with_json, initialize_api
    HAS_GOOGLE_API = True
except Exception:
    HAS_GOOGLE_API = False

logger = logging.getLogger(__name__)

# ==========================================
# CURATED RSS FEEDS (EU Trade, Carbon, Energy, Commodities)
# ==========================================
DEFAULT_FEEDS = [
    {
        "name": "European Commission Press Corner",
        "url": "https://ec.europa.eu/commission/presscorner/api/rss?language=en",
        "category": "TRADE_POLICY"
    },
    {
        "name": "European Commission Single Market & Industry",
        "url": "https://single-market-economy.ec.europa.eu/rss_en",
        "category": "INDUSTRY_TRADE"
    },
    {
        "name": "European Commission Trade Policy",
        "url": "https://policy.trade.ec.europa.eu/rss_en",
        "category": "TRADE_POLICY"
    },
    {
        "name": "Google News EU Carbon Market & ETS",
        "url": "https://news.google.com/rss/search?q=EU+carbon+market+OR+EU+ETS&hl=en-US&gl=US&ceid=US:en",
        "category": "CARBON_ENERGY"
    },
    {
        "name": "Google News EU Trade Policy & CBAM",
        "url": "https://news.google.com/rss/search?q=EU+trade+policy+OR+CBAM+tariff&hl=en-US&gl=US&ceid=US:en",
        "category": "TRADE_POLICY"
    },
    {
        "name": "Google News European Energy Commodities",
        "url": "https://news.google.com/rss/search?q=European+gas+storage+OR+TTF+energy+market&hl=en-US&gl=US&ceid=US:en",
        "category": "ENERGY_COMMODITIES"
    },
    {
        "name": "Google News Metals & Raw Materials Europe",
        "url": "https://news.google.com/rss/search?q=European+steel+OR+aluminium+metals+industry&hl=en-US&gl=US&ceid=US:en",
        "category": "COMMODITIES_METALS"
    },
    {
        "name": "Google News Reuters Industrial & Commodities Europe",
        "url": "https://news.google.com/rss/search?q=Reuters+Europe+commodities+OR+metals+OR+energy&hl=en-US&gl=US&ceid=US:en",
        "category": "COMMODITIES_METALS"
    }
]

# ==========================================
# ENTITY & ACTOR VOCABULARY DICTIONARIES
# ==========================================
KNOWN_COMPANIES = {
    # Basic Metals / Steel / Aluminium
    "arcelormittal": "ArcelorMittal",
    "thyssenkrupp": "Thyssenkrupp",
    "salzgitter": "Salzgitter",
    "voestalpine": "Voestalpine",
    "tata steel": "Tata Steel",
    "ssab": "SSAB",
    "tenaris": "Tenaris",
    "trimet": "Trimet Aluminium",
    "norsk hydro": "Norsk Hydro",
    "rio tinto": "Rio Tinto",
    "glencore": "Glencore",
    "alcoa": "Alcoa",
    "aurubis": "Aurubis",
    
    # Chemicals & Fertilizer
    "basf": "BASF",
    "yara": "Yara International",
    "covestro": "Covestro",
    "evonik": "Evonik",
    "bayer": "Bayer",
    "solvay": "Solvay",
    "arkema": "Arkema",
    "wacker chemie": "Wacker Chemie",
    "ineos": "Ineos",
    "borealis": "Borealis",
    "oci": "OCI Global",
    
    # Energy / Utilities / Oil & Gas
    "rwe": "RWE",
    "uniper": "Uniper",
    "enel": "Enel",
    "totalenergies": "TotalEnergies",
    "total": "TotalEnergies",
    "shell": "Shell",
    "bp": "BP",
    "equinor": "Equinor",
    "engie": "Engie",
    "iberdrola": "Iberdrola",
    "edf": "EDF",
    "orsted": "Orsted",
    "vattenfall": "Vattenfall",
    "repsol": "Repsol",
    "eni": "Eni",
    "fortum": "Fortum",
    "cez": "CEZ",
    "e.on": "E.ON",
    "eon": "E.ON",
    
    # Transport & Logistics
    "maersk": "Maersk",
    "hapag-lloyd": "Hapag-Lloyd",
    "hapag lloyd": "Hapag-Lloyd",
    "cma cgm": "CMA CGM",
    "msc": "MSC",
    "kuehne+nagel": "Kuehne+Nagel",
    
    # Automotive & Clean Tech
    "volkswagen": "Volkswagen",
    "northvolt": "Northvolt",
    "stellantis": "Stellantis",
    "bmw": "BMW",
    "mercedes": "Mercedes-Benz",
    "renault": "Renault",
    "siemens energy": "Siemens Energy",
    "siemens": "Siemens Energy",
    "vestas": "Vestas",
    "alstom": "Alstom",
    "catl": "CATL",
    "albemarle": "Albemarle",
    "umicore": "Umicore"
}

KNOWN_COUNTRIES = {
    "germany": "Germany", "german": "Germany",
    "france": "France", "french": "France",
    "italy": "Italy", "italian": "Italy",
    "spain": "Spain", "spanish": "Spain",
    "poland": "Poland", "polish": "Poland",
    "netherlands": "Netherlands", "dutch": "Netherlands",
    "belgium": "Belgium", "belgian": "Belgium",
    "sweden": "Sweden", "swedish": "Sweden",
    "austria": "Austria", "austrian": "Austria",
    "denmark": "Denmark", "danish": "Denmark",
    "finland": "Finland", "finnish": "Finland",
    "portugal": "Portugal", "portuguese": "Portugal",
    "greece": "Greece", "greek": "Greece",
    "czech republic": "Czech Republic", "czechia": "Czech Republic", "czech": "Czech Republic",
    "romania": "Romania", "romanian": "Romania",
    "hungary": "Hungary", "hungarian": "Hungary",
    "ireland": "Ireland", "irish": "Ireland",
    "slovakia": "Slovakia", "slovak": "Slovakia",
    "bulgaria": "Bulgaria", "bulgarian": "Bulgaria",
    "croatia": "Croatia", "croatian": "Croatia",
    "lithuania": "Lithuania", "lithuanian": "Lithuania",
    "slovenia": "Slovenia", "slovenian": "Slovenia",
    "latvia": "Latvia", "latvian": "Latvia",
    "estonia": "Estonia", "estonian": "Estonia",
    "norway": "Norway", "norwegian": "Norway",
    "switzerland": "Switzerland", "swiss": "Switzerland",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "britain": "United Kingdom", "british": "United Kingdom",
    "united states": "United States", "usa": "United States", "us": "United States", "american": "United States",
    "china": "China", "chinese": "China",
    "ukraine": "Ukraine", "ukrainian": "Ukraine",
    "india": "India", "indian": "India",
    "japan": "Japan", "japanese": "Japan",
    "turkey": "Turkey", "turkish": "Turkey", "turkiye": "Turkey",
    "european union": "EU", "eu": "EU", "brussels": "EU"
}

SECTOR_KEYWORDS = {
    "C24_BASIC_METALS": ["steel", "aluminium", "aluminum", "copper", "zinc", "nickel", "smelter", "foundry", "metal", "hrc", "iron ore", "scrap metal"],
    "C20_CHEMICALS": ["chemical", "fertilizer", "ammonia", "polymer", "petrochemical", "plastics", "ethylene", "chlorine", "nitrogen"],
    "D35_ELECTRICITY_GAS": ["electricity", "natural gas", "gas storage", "ttf", "power plant", "grid", "lng", "pipeline", "megawatt", "mwh", "gigawatt"],
    "H50_WATER_TRANSPORT": ["shipping", "freight", "maritime", "vessel", "cargo", "container", "bunker", "port", "red sea", "suez"],
    "C23_NON_METALLIC_MINERALS": ["cement", "clinker", "glass", "ceramic", "lime", "concrete"],
    "C29_MOTOR_VEHICLES": ["automotive", "carmaker", "vehicle", "electric vehicle", "ev", "battery", "gigafactory"],
    "C27_ELECTRICAL_EQUIPMENT": ["turbine", "solar", "photovoltaic", "electrolyser", "inverter", "generator"],
    "C19_REFINED_PETROLEUM": ["refinery", "diesel", "gasoline", "bunker fuel", "crude oil", "brent", "petroleum"],
    "B07_MINING_METALS": ["critical raw materials", "lithium", "cobalt", "rare earth", "graphite", "mining", "extraction"],
    "A01_AGRICULTURE": ["grain", "wheat", "crop", "agriculture", "fertilizer consumption"]
}

# ==========================================
# BENCHMARK OFFLINE NEWS CORPUS (Seed Data)
# ==========================================
FALLBACK_NEWS_CORPUS = [
    {
        "news_id": "NEWS_2024_01_CBAM_PILOT",
        "title": "EU Importers Face First Mandatory CBAM Emissions Reporting Deadline",
        "published_date": "2024-01-31",
        "source_name": "EU Trade Monitor",
        "source_url": "https://policy.trade.ec.europa.eu/news/cbam-first-reporting-2024-01-31_en",
        "summary": "Importers of steel, aluminum, cement, and fertilizers into the EU must submit their initial quarterly embedded carbon emissions reports under the Carbon Border Adjustment Mechanism. ArcelorMittal and Thyssenkrupp warn of rising administrative and compliance costs.",
        "actors": {
            "companies": ["ArcelorMittal", "Thyssenkrupp"],
            "countries": ["Germany", "EU"],
            "sectors": ["C24_BASIC_METALS", "C20_CHEMICALS", "C23_NON_METALLIC_MINERALS"],
            "geography": ["EU", "Western Europe"]
        },
        "actions": [
            {
                "action_type": "REGULATORY_MANDATE",
                "actor": "European Commission",
                "target": "Embedded Carbon Emissions Reporting",
                "description": "Enforce initial mandatory quarterly reporting deadline under the Carbon Border Adjustment Mechanism (CBAM).",
                "magnitude": 100.0,
                "unit": "PERCENT_COMPLIANCE"
            }
        ],
        "reactions": [
            {
                "reaction_type": "COMPLIANCE_COST_SHIFT",
                "affected_entity": "C24_BASIC_METALS",
                "direction": "INCREASE",
                "magnitude": 8.5,
                "unit": "EUR/tonne",
                "timeframe": "IMMEDIATE",
                "confidence": 0.95
            }
        ],
        "receptor_signal": "CBAM_REPORTING_DEADLINE",
        "target_asset": "C24_BASIC_METALS",
        "metric": "COMPLIANCE_COST",
        "direction": "INCREASE",
        "delta_value": 8.5,
        "delta_unit": "EUR/tonne",
        "timeframe": "IMMEDIATE"
    },
    {
        "news_id": "NEWS_2024_03_ETS_CAP_REDUCTION",
        "title": "EU Carbon Allowance Price Fluctuates as Annual ETS Linear Reduction Factor Tightens to 4.3%",
        "published_date": "2024-03-15",
        "source_name": "Carbon Market Watch",
        "source_url": "https://euractiv.com/section/energy/news/ets-linear-reduction-2024",
        "summary": "The European Commission confirms the accelerated contraction of overall European emission allowances via the 4.3% linear reduction factor, driving heavy industrial compliance bidding among utilities RWE, Uniper, and Enel.",
        "actors": {
            "companies": ["RWE", "Uniper", "Enel"],
            "countries": ["Germany", "Italy", "EU"],
            "sectors": ["D35_ELECTRICITY_GAS", "C24_BASIC_METALS"],
            "geography": ["EU"]
        },
        "actions": [
            {
                "action_type": "REGULATORY_MANDATE",
                "actor": "European Commission",
                "target": "EU ETS Emission Cap",
                "description": "Accelerate annual linear reduction factor of emission allowances to 4.3%.",
                "magnitude": 4.3,
                "unit": "PERCENT"
            }
        ],
        "reactions": [
            {
                "reaction_type": "PRICE_CHANGE",
                "affected_entity": "CO2_ALLOWANCE",
                "direction": "INCREASE",
                "magnitude": 12.0,
                "unit": "EUR/tCO2",
                "timeframe": "3_MONTHS",
                "confidence": 0.9
            }
        ],
        "receptor_signal": "ETS_CAP_CONTRACTION",
        "target_asset": "CO2_ALLOWANCE",
        "metric": "PRICE",
        "direction": "INCREASE",
        "delta_value": 12.0,
        "delta_unit": "EUR/tCO2",
        "timeframe": "3_MONTHS"
    },
    {
        "news_id": "NEWS_2024_05_GAS_STORAGE_FILL",
        "title": "EU Underground Gas Storage Levels Reach 65% Ahead of Mandatory 90% Target",
        "published_date": "2024-05-20",
        "source_name": "Gas Infrastructure Europe",
        "source_url": "https://gie.eu/news/gas-storage-update-may-2024",
        "summary": "Member states continue mandatory injection obligations pursuant to the EU Gas Storage Regulation, buffering European energy markets against volatility as Equinor and TotalEnergies maintain steady pipeline deliveries.",
        "actors": {
            "companies": ["Equinor", "TotalEnergies", "Shell"],
            "countries": ["Germany", "Netherlands", "Norway", "EU"],
            "sectors": ["D35_ELECTRICITY_GAS"],
            "geography": ["Northwest Europe"]
        },
        "actions": [
            {
                "action_type": "REGULATORY_MANDATE",
                "actor": "EU Member States",
                "target": "Gas Storage Injections",
                "description": "Mandatory gas storage filling target reaching 90% ahead of winter heating season.",
                "magnitude": 90.0,
                "unit": "PERCENT"
            }
        ],
        "reactions": [
            {
                "reaction_type": "SUPPLY_SHIFT",
                "affected_entity": "GAS_TTF",
                "direction": "INCREASE",
                "magnitude": 65.0,
                "unit": "PERCENT",
                "timeframe": "1_YEAR",
                "confidence": 0.92
            }
        ],
        "receptor_signal": "GAS_INJECTION_MANDATE",
        "target_asset": "GAS_TTF",
        "metric": "SUPPLY",
        "direction": "INCREASE",
        "delta_value": 65.0,
        "delta_unit": "PERCENT",
        "timeframe": "1_YEAR"
    },
    {
        "news_id": "NEWS_2024_08_RED_SEA_SHIPPING",
        "title": "Red Sea Shipping Disruption Diverts Freight Around Cape of Good Hope, Increasing Shipping Bunker Costs",
        "published_date": "2024-08-10",
        "source_name": "Global Maritime Logistics",
        "source_url": "https://maritime-exec.com/news/red-sea-freight-rates-aug-2024",
        "summary": "Container carriers Maersk and Hapag-Lloyd report 14-day voyage extensions, driving up maritime fuel bunker expenses and container freight rates by 24.5% on Asia-Europe trade lanes.",
        "actors": {
            "companies": ["Maersk", "Hapag-Lloyd"],
            "countries": ["Denmark", "Germany", "EU"],
            "sectors": ["H50_WATER_TRANSPORT"],
            "geography": ["Global", "EU"]
        },
        "actions": [
            {
                "action_type": "FACILITY_SHUTDOWN",
                "actor": "Maersk & Hapag-Lloyd",
                "target": "Suez Transit Route",
                "description": "Reroute container vessels around Cape of Good Hope avoiding Bab-el-Mandeb strait.",
                "magnitude": 14.0,
                "unit": "DAYS_DELAY"
            }
        ],
        "reactions": [
            {
                "reaction_type": "PRICE_CHANGE",
                "affected_entity": "FREIGHT_RATES",
                "direction": "INCREASE",
                "magnitude": 24.5,
                "unit": "PERCENT",
                "timeframe": "IMMEDIATE",
                "confidence": 0.94
            }
        ],
        "receptor_signal": "SHIPPING_ROUTE_DISRUPTION",
        "target_asset": "H50_WATER_TRANSPORT",
        "metric": "COST",
        "direction": "INCREASE",
        "delta_value": 24.5,
        "delta_unit": "PERCENT",
        "timeframe": "IMMEDIATE"
    },
    {
        "news_id": "NEWS_2024_11_CRITICAL_RAW_MATERIALS",
        "title": "EU Critical Raw Materials Act Enters Enforcement: 10% Domestic Extraction Target by 2030",
        "published_date": "2024-11-05",
        "source_name": "Euractiv Industry",
        "source_url": "https://www.euractiv.com/section/circular-economy/news/crma-enforcement-2024",
        "summary": "New strategic permitting timelines and recycling quotas mandate automotive and renewable energy producers to secure domestic European rare earth and lithium supplies. Umicore and Albemarle announce new processing capex.",
        "actors": {
            "companies": ["Umicore", "Albemarle"],
            "countries": ["France", "Germany", "Belgium", "EU"],
            "sectors": ["B07_MINING_METALS", "C29_MOTOR_VEHICLES"],
            "geography": ["EU"]
        },
        "actions": [
            {
                "action_type": "CAPEX_INVESTMENT",
                "actor": "Umicore",
                "target": "Battery Materials & Lithium Refining",
                "description": "Allocate strategic capex for European battery materials processing and critical mineral recycling.",
                "magnitude": 500.0,
                "unit": "EUR_MILLION"
            }
        ],
        "reactions": [
            {
                "reaction_type": "DEMAND_CHANGE",
                "affected_entity": "LITHIUM",
                "direction": "INCREASE",
                "magnitude": 15.0,
                "unit": "PERCENT",
                "timeframe": "LONG_TERM",
                "confidence": 0.88
            }
        ],
        "receptor_signal": "CRITICAL_MINERAL_MANDATE",
        "target_asset": "LITHIUM",
        "metric": "DEMAND",
        "direction": "INCREASE",
        "delta_value": 15.0,
        "delta_unit": "PERCENT",
        "timeframe": "LONG_TERM"
    },
    {
        "news_id": "NEWS_2024_06_FERTILIZER_CURTAILMENT",
        "title": "Yara and BASF Curtail Ammonia Production as Natural Gas Feedstock Volatility Squeezes Margins",
        "published_date": "2024-06-18",
        "source_name": "Chemical Week Europe",
        "source_url": "https://chemweek.com/news/yara-basf-ammonia-curtailment-2024",
        "summary": "Chemical leaders Yara International and BASF announce a 20% curtailment in European ammonia and nitrogen fertilizer synthesis due to elevated Dutch TTF gas input costs, raising downstream European wholesale fertilizer prices by 18%.",
        "actors": {
            "companies": ["Yara International", "BASF"],
            "countries": ["Norway", "Germany", "EU"],
            "sectors": ["C20_CHEMICALS", "A01_AGRICULTURE"],
            "geography": ["Northwest Europe"]
        },
        "actions": [
            {
                "action_type": "PRODUCTION_CUT",
                "actor": "Yara International",
                "target": "European Ammonia Synthesis Plants",
                "description": "Curtail ammonia and nitrogen fertilizer output across European plants due to feedstock costs.",
                "magnitude": -20.0,
                "unit": "PERCENT"
            }
        ],
        "reactions": [
            {
                "reaction_type": "PRICE_CHANGE",
                "affected_entity": "FERTILIZER_NITROGEN",
                "direction": "INCREASE",
                "magnitude": 18.0,
                "unit": "PERCENT",
                "timeframe": "3_MONTHS",
                "confidence": 0.91
            }
        ],
        "receptor_signal": "FEEDSTOCK_GAS_VOLATILITY",
        "target_asset": "C20_CHEMICALS",
        "metric": "PRICE",
        "direction": "INCREASE",
        "delta_value": 18.0,
        "delta_unit": "PERCENT",
        "timeframe": "3_MONTHS"
    },
    {
        "news_id": "NEWS_2024_09_ALUMINIUM_SMELTER_IDLE",
        "title": "Trimet Aluminium and Norsk Hydro Idle Primary Smelter Capacity Amid Power Tariff Pressure",
        "published_date": "2024-09-22",
        "source_name": "Metals Daily",
        "source_url": "https://metalsdaily.com/news/trimet-hydro-idle-smelters-2024",
        "summary": "Trimet Aluminium and Norsk Hydro temporarily idle 30% of primary aluminium electrolysis potlines in France and Germany, reducing domestic European output by 150,000 tonnes as baseload electricity tariffs remain elevated.",
        "actors": {
            "companies": ["Trimet Aluminium", "Norsk Hydro"],
            "countries": ["France", "Germany", "Norway"],
            "sectors": ["C24_BASIC_METALS", "D35_ELECTRICITY_GAS"],
            "geography": ["Western Europe"]
        },
        "actions": [
            {
                "action_type": "PRODUCTION_CUT",
                "actor": "Trimet Aluminium",
                "target": "Electrolysis Potlines",
                "description": "Idle 30% of primary aluminium smelter capacity in response to elevated baseload power tariffs.",
                "magnitude": -30.0,
                "unit": "PERCENT"
            }
        ],
        "reactions": [
            {
                "reaction_type": "SUPPLY_SHIFT",
                "affected_entity": "ALUMINIUM_PRIMARY",
                "direction": "DECREASE",
                "magnitude": -150000.0,
                "unit": "TONNES",
                "timeframe": "3_MONTHS",
                "confidence": 0.93
            }
        ],
        "receptor_signal": "POWER_TARIFF_SMELTER_CURTAILMENT",
        "target_asset": "C24_BASIC_METALS",
        "metric": "SUPPLY",
        "direction": "DECREASE",
        "delta_value": -30.0,
        "delta_unit": "PERCENT",
        "timeframe": "3_MONTHS"
    },
    {
        "news_id": "NEWS_2024_10_GREEN_STEEL_CAPEX",
        "title": "Salzgitter and ArcelorMittal Secure €1.8B State Aid for Green Direct Reduced Iron Steel Transition",
        "published_date": "2024-10-14",
        "source_name": "Steel Times International",
        "source_url": "https://steeltimesint.com/news/salzgitter-arcelormittal-dri-green-steel-2024",
        "summary": "Germany and the European Commission finalize €1.8 billion in decarbonization subsidies for Salzgitter and ArcelorMittal to replace coal-fired blast furnaces with hydrogen-ready Direct Reduced Iron (DRI) shafts.",
        "actors": {
            "companies": ["Salzgitter", "ArcelorMittal"],
            "countries": ["Germany", "EU"],
            "sectors": ["C24_BASIC_METALS", "D35_ELECTRICITY_GAS"],
            "geography": ["Germany", "Central Europe"]
        },
        "actions": [
            {
                "action_type": "CAPEX_INVESTMENT",
                "actor": "Salzgitter",
                "target": "Hydrogen Direct Reduced Iron (DRI) Plant",
                "description": "Construct low-carbon DRI shaft and electric arc furnace infrastructure.",
                "magnitude": 1800.0,
                "unit": "EUR_MILLION"
            },
            {
                "action_type": "SUBSIDY_PROGRAM",
                "actor": "European Commission",
                "target": "Industrial Decarbonization Grant",
                "description": "Approve German state aid package under Important Projects of Common European Interest (IPCEI).",
                "magnitude": 1800.0,
                "unit": "EUR_MILLION"
            }
        ],
        "reactions": [
            {
                "reaction_type": "DEMAND_CHANGE",
                "affected_entity": "HYDROGEN_GREEN",
                "direction": "INCREASE",
                "magnitude": 25.0,
                "unit": "PERCENT",
                "timeframe": "LONG_TERM",
                "confidence": 0.89
            }
        ],
        "receptor_signal": "GREEN_STEEL_SUBSIDY_APPROVAL",
        "target_asset": "C24_BASIC_METALS",
        "metric": "SUPPLY",
        "direction": "INCREASE",
        "delta_value": 1800.0,
        "delta_unit": "EUR_MILLION",
        "timeframe": "LONG_TERM"
    },
    {
        "news_id": "NEWS_2024_12_SOLAR_STEEL_TARIFF",
        "title": "EU Imposes Definitive Anti-Dumping Duties of 35% on Imported Electrical Steel and Solar Fasteners",
        "published_date": "2024-12-02",
        "source_name": "EU Official Journal & Trade Brief",
        "source_url": "https://trade.ec.europa.eu/news/anti-dumping-steel-solar-2024",
        "summary": "Following an investigation confirming market distortion, the European Commission imposes definitive anti-dumping duties of 35% on grain-oriented electrical steel and solar mounting components from Asian exporters.",
        "actors": {
            "companies": ["Siemens Energy", "Vestas"],
            "countries": ["China", "EU", "Germany"],
            "sectors": ["C27_ELECTRICAL_EQUIPMENT", "C24_BASIC_METALS"],
            "geography": ["EU", "Asia"]
        },
        "actions": [
            {
                "action_type": "TARIFF_IMPOSITION",
                "actor": "European Commission",
                "target": "Grain-Oriented Electrical Steel",
                "description": "Impose 35% definitive trade defense anti-dumping duty on electrical steel imports.",
                "magnitude": 35.0,
                "unit": "PERCENT"
            }
        ],
        "reactions": [
            {
                "reaction_type": "PRICE_CHANGE",
                "affected_entity": "STEEL_ELECTRICAL",
                "direction": "INCREASE",
                "magnitude": 10.5,
                "unit": "PERCENT",
                "timeframe": "IMMEDIATE",
                "confidence": 0.96
            }
        ],
        "receptor_signal": "ANTI_DUMPING_STEEL_TARIFF",
        "target_asset": "C24_BASIC_METALS",
        "metric": "PRICE",
        "direction": "INCREASE",
        "delta_value": 35.0,
        "delta_unit": "PERCENT",
        "timeframe": "IMMEDIATE"
    },
    {
        "news_id": "NEWS_2025_01_REFINED_OIL_SANCTION",
        "title": "Maritime Insurance Caps Enforced on Seaborne Petroleum Shipments, Raising Refined Product Spreads",
        "published_date": "2025-01-18",
        "source_name": "Argus Petroleum Media",
        "source_url": "https://argusmedia.com/news/oil-refined-product-spreads-2025",
        "summary": "Strict verification requirements on maritime tanker insurance compliance tighten availability of compliant oil tankers, prompting TotalEnergies and BP to apply bunker and refinery product surcharges of €6.5 per barrel.",
        "actors": {
            "companies": ["TotalEnergies", "BP"],
            "countries": ["United Kingdom", "France", "EU"],
            "sectors": ["C19_REFINED_PETROLEUM", "H50_WATER_TRANSPORT"],
            "geography": ["North Sea", "Mediterranean"]
        },
        "actions": [
            {
                "action_type": "PRICE_REVISION",
                "actor": "TotalEnergies & BP",
                "target": "Refined Bunker & Diesel Products",
                "description": "Apply freight and maritime insurance compliance surcharges of €6.5 per barrel.",
                "magnitude": 6.5,
                "unit": "EUR/barrel"
            }
        ],
        "reactions": [
            {
                "reaction_type": "PRICE_CHANGE",
                "affected_entity": "BRENT_CRUDE_REFINED",
                "direction": "INCREASE",
                "magnitude": 6.5,
                "unit": "EUR/barrel",
                "timeframe": "IMMEDIATE",
                "confidence": 0.92
            }
        ],
        "receptor_signal": "TANKER_INSURANCE_COMPLIANCE_CAP",
        "target_asset": "C19_REFINED_PETROLEUM",
        "metric": "PRICE",
        "direction": "INCREASE",
        "delta_value": 6.5,
        "delta_unit": "EUR/barrel",
        "timeframe": "IMMEDIATE"
    }
]


# ==========================================
# DETERMINISTIC VECTOR GENERATOR (Offline Fallback)
# ==========================================
def generate_deterministic_embedding(text: str, dim: int = 768) -> List[float]:
    """
    Generate a deterministic, normalized 768-dimensional float embedding from text.
    Provides a 100% reliable offline fallback when Google AI API is unavailable.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(h[:4], "big")
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


# ==========================================
# ECONOMIC NEWS SCRAPER & EXTRACTOR
# ==========================================
class EconomicNewsScraper:
    """
    Scrapes, parses, and extracts structured economic events from RSS feeds/APIs
    covering EU trade, carbon/energy, and commodity markets.
    
    Capabilities:
    - Live RSS parsing (RSS 2.0, Atom, RDF) with HTTP retry and timeout handling
    - Deterministic entity, actor, action, and reaction extraction
    - Optional LLM-assisted extraction via Gemini
    - LanceDB vector storage and tabular caching
    - DualLayerGraphStore integration (Action -> Reaction topology + behind-the-graph records)
    - Fallback offline seed generator for zero-internet environments
    """

    def __init__(
        self,
        data_dir: str = "data/raw/news",
        lancedb_path: Optional[str] = None,
        graph_store: Optional[DualLayerGraphStore] = None,
        use_llm: bool = False,
        offline: bool = False
    ):
        self.data_dir = Path(data_dir)
        from src.config import get_settings
        settings = get_settings()
        self.lancedb_path = Path(lancedb_path) if lancedb_path else settings.lancedb_path
        self.graph_store = graph_store
        self.use_llm = use_llm
        self.offline = offline
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 PowerLawBot/1.0"
        )

    # ----------------------------------------------------------------------
    # 1. RSS FEED FETCHING & PARSING
    # ----------------------------------------------------------------------
    def fetch_rss_feed(
        self,
        feed_url: str,
        source_name: str,
        max_items: int = 15,
        timeout: int = 10
    ) -> List[Dict[str, Any]]:
        """Fetch and parse an RSS or Atom feed with robust error handling."""
        raw_items = []
        try:
            headers = {"User-Agent": self.user_agent}
            resp = requests.get(feed_url, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch {feed_url}: HTTP status {resp.status_code}")
                return []

            # Try parsing with BeautifulSoup XML parser if available, else ElementTree
            if HAS_BS4 and BeautifulSoup is not None:
                soup = BeautifulSoup(resp.content, "xml")
                items = soup.find_all("item")
                if not items:
                    items = soup.find_all("entry")  # Atom feed format

                for item in items[:max_items]:
                    title_elem = item.find("title")
                    title_text = title_elem.text.strip() if title_elem else "Untitled"

                    link_elem = item.find("link")
                    link_text = ""
                    if link_elem:
                        link_text = link_elem.get("href") or link_elem.text.strip()

                    date_elem = item.find(["pubDate", "published", "updated", "date", "dc:date"])
                    date_str = date_elem.text.strip() if date_elem else ""
                    parsed_date = self._parse_date(date_str)

                    desc_elem = item.find(["description", "summary", "content", "content:encoded"])
                    desc_text = ""
                    if desc_elem:
                        try:
                            desc_soup = BeautifulSoup(desc_elem.text, "html.parser")
                            desc_text = desc_soup.get_text().strip()
                        except Exception:
                            desc_text = desc_elem.text.strip()

                    id_seed = f"{link_text}_{title_text}_{parsed_date}"
                    hash_id = abs(hash(id_seed)) % 10000000
                    article_id = f"NEWS_{parsed_date.replace('-', '_')}_{hash_id:07d}"

                    raw_items.append({
                        "news_id": article_id,
                        "title": title_text,
                        "published_date": parsed_date,
                        "source_name": source_name,
                        "source_url": link_text,
                        "summary": desc_text[:1200]
                    })
            else:
                # ElementTree standard library fallback
                root = ET.fromstring(resp.content)
                # Channel items (RSS) or root entries (Atom)
                et_items = root.findall(".//item")
                if not et_items:
                    # Strip namespace for Atom
                    et_items = [elem for elem in root.iter() if elem.tag.endswith("entry")]

                for item in et_items[:max_items]:
                    title_elem = next((e for e in item if e.tag.endswith("title")), None)
                    title_text = title_elem.text.strip() if title_elem is not None and title_elem.text else "Untitled"

                    link_elem = next((e for e in item if e.tag.endswith("link")), None)
                    link_text = ""
                    if link_elem is not None:
                        link_text = link_elem.attrib.get("href") or (link_elem.text.strip() if link_elem.text else "")

                    date_elem = next((e for e in item if any(e.tag.endswith(t) for t in ["pubDate", "published", "updated", "date"])), None)
                    date_str = date_elem.text.strip() if date_elem is not None and date_elem.text else ""
                    parsed_date = self._parse_date(date_str)

                    desc_elem = next((e for e in item if any(e.tag.endswith(t) for t in ["description", "summary", "content"])), None)
                    desc_text = ""
                    if desc_elem is not None and desc_elem.text:
                        desc_text = re.sub(r"<[^>]+>", " ", desc_elem.text).strip()

                    id_seed = f"{link_text}_{title_text}_{parsed_date}"
                    hash_id = abs(hash(id_seed)) % 10000000
                    article_id = f"NEWS_{parsed_date.replace('-', '_')}_{hash_id:07d}"

                    raw_items.append({
                        "news_id": article_id,
                        "title": title_text,
                        "published_date": parsed_date,
                        "source_name": source_name,
                        "source_url": link_text,
                        "summary": desc_text[:1200]
                    })
        except Exception as e:
            logger.warning(f"Error fetching feed {feed_url} ({source_name}): {e}")

        return raw_items

    def _parse_date(self, date_str: str) -> str:
        """Standardize various feed date strings to YYYY-MM-DD."""
        if not date_str or not isinstance(date_str, str):
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cleaned = date_str.strip()
        if not cleaned:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Try RFC 2822 / 822 format via standard library email.utils
        try:
            dt = email.utils.parsedate_to_datetime(cleaned)
            if dt:
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

        # 2. Try ISO 8601 format
        try:
            iso_str = cleaned.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

        # 3. Try strptime with common explicit patterns
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d %b %Y",
            "%d %B %Y",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%Y/%m/%d",
            "%Y.%m.%d"
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                continue

        # 4. Regex fallback for YYYY-MM-DD or YYYY/MM/DD
        m = re.search(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})', cleaned)
        if m:
            year, month, day = m.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # 5. Regex extraction for DD Mon YYYY (e.g. 15 Jul 2024)
        m_mon = re.search(r'(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})', cleaned)
        if m_mon:
            day, mon_name, year = m_mon.groups()
            try:
                dt = datetime.strptime(f"{day} {mon_name[:3]} {year}", "%d %b %Y")
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ----------------------------------------------------------------------
    # 2. EVENT EXTRACTION ENGINE (Actors, Actions, Reactions)
    # ----------------------------------------------------------------------
    def extract_actors(self, text: str) -> NewsEntities:
        """Identify companies, countries, sectors, and geography mentioned in text."""
        lower_text = f" {text.lower()} "
        
        # 1. Companies
        detected_companies = []
        for kw, company_name in KNOWN_COMPANIES.items():
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, lower_text):
                if company_name not in detected_companies:
                    detected_companies.append(company_name)

        # 2. Countries
        detected_countries = []
        for kw, country_name in KNOWN_COUNTRIES.items():
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, lower_text):
                if country_name not in detected_countries:
                    detected_countries.append(country_name)

        # 3. Sectors
        detected_sectors = []
        for sector_code, keywords in SECTOR_KEYWORDS.items():
            if any(re.search(r'\b' + re.escape(kw) + r'\b', lower_text) for kw in keywords):
                detected_sectors.append(sector_code)

        # 4. Geography
        geography = []
        if "EU" in detected_countries or any(c in detected_countries for c in ["Germany", "France", "Italy", "Spain", "Poland", "Netherlands", "Belgium"]):
            geography.append("EU")
        if any(c in detected_countries for c in ["Germany", "France", "Belgium", "Netherlands"]):
            geography.append("Western Europe")
        if any(c in detected_countries for c in ["United States", "China", "India"]):
            geography.append("Global")

        return NewsEntities(
            companies=detected_companies,
            countries=detected_countries,
            sectors=detected_sectors,
            geography=geography or ["EU"]
        )

    def extract_actions(self, title: str, summary: str, actors: NewsEntities) -> List[EconomicAction]:
        """Extract explicit commercial or regulatory actions from news text."""
        full_text = f"{title}. {summary}"
        actions: List[EconomicAction] = []
        primary_actor = (
            actors.companies[0] if actors.companies else
            actors.countries[0] if actors.countries else
            "Industry / Government"
        )

        action_patterns = [
            (
                r'\b(curtail(?:s|ed|ing)?|cut(?:s|ting)?(?:\s+[^.,;\n]{0,25})?\s+(?:output|production|capacity)|idle(?:s|d|ing)?|suspend(?:s|ed|ing)?\s+operations?|slash(?:es|ed|ing)?(?:\s+[^.,;\n]{0,25})?\s+(?:output|production|capacity)|halt(?:s|ed|ing)?\s+production|shut(?:s|ting)?\s+down)\b',
                "PRODUCTION_CUT"
            ),
            (
                r'\b(invest(?:s|ed|ing)?|allocate(?:s|d|ing)?\s+capex|build(?:s|ing)?\s+plant|expand(?:s|ed|ing)?\s+capacity|break(?:s)?\s+ground|fund(?:s|ed|ing)?\s+expansion|secure(?:s|d|ing)?(?:\s+[^.,;\n]{0,25})?\s+capex)\b',
                "CAPEX_INVESTMENT"
            ),
            (
                r'\b(raise(?:s|d|ing)?\s+prices?|hike(?:s|d|ing)?\s+prices?|cut(?:s|ting)?\s+prices?|price\s+revision|price\s+surcharge|apply(?:\s+[^.,;\n]{0,25})?\s+surcharges?)\b',
                "PRICE_REVISION"
            ),
            (
                r'\b(impose(?:s|d|ing)?(?:\s+[^.,;\n]{0,35})?\s+(?:tariffs?|dut(?:y|ies))|anti[- ]dumping|trade\s+sanction(?:s)?|cbam\s+obligation(?:s)?|customs\s+penalt(?:y|ies)|tariff\s+imposition)\b',
                "TARIFF_IMPOSITION"
            ),
            (
                r'\b(state\s+aid|approv(?:es|ed|ing)?\s+subsid(?:y|ies)|grant(?:s|ed|ing)?\s+funding|clean\s+transition\s+grant|secure(?:s|d|ing)?(?:\s+[^.,;\n]{0,25})?\s+state\s+aid)\b',
                "SUBSIDY_PROGRAM"
            ),
            (
                r'\b(mandate(?:s|d|ing)?|enforce(?:s|d|ing)?|linear\s+reduction\s+factor|storage\s+mandate|reporting\s+deadline)\b',
                "REGULATORY_MANDATE"
            ),
            (
                r'\b(sign(?:s|ed|ing)?\s+contract|offtake\s+deal|long[- ]term\s+agreement|procurement\s+deal)\b',
                "CONTRACT_SIGNING"
            ),
            (
                r'\b(close(?:s|d|ing)?\s+plant|shutter(?:s|ed|ing)?|mothball(?:s|ed|ing)?|decommission(?:s|ed|ing)?|idle(?:s|d|ing)?(?:\s+[^.,;\n]{0,25})?\s+(?:potline|furnace|smelter))\b',
                "FACILITY_SHUTDOWN"
            )
        ]

        # Break text into clauses / sentences for precise local extraction without splitting decimal numbers
        clauses = [c.strip() for c in re.split(r'(?<!\d)\.+(?!\d)|[;!\n]+|\band\s+(?=[A-Z])', full_text) if c.strip()]
        if not clauses:
            clauses = [full_text]

        seen_action_keys = set()

        for clause in clauses:
            # Check if a specific actor is named in this clause
            clause_lower = f" {clause.lower()} "
            clause_actor = primary_actor
            for kw, comp_name in KNOWN_COMPANIES.items():
                if re.search(r'\b' + re.escape(kw) + r'\b', clause_lower):
                    clause_actor = comp_name
                    break
            else:
                for kw, country_name in KNOWN_COUNTRIES.items():
                    if re.search(r'\b' + re.escape(kw) + r'\b', clause_lower):
                        clause_actor = country_name
                        break

            # Extract magnitude locally for this clause
            clause_mag, clause_unit = self._extract_magnitude(clause)

            for regex, act_type in action_patterns:
                m = re.search(regex, clause, re.IGNORECASE)
                if m:
                    action_key = (act_type, clause_actor)
                    if action_key not in seen_action_keys:
                        target_desc = actors.sectors[0] if actors.sectors else "Industrial Operations"
                        actions.append(EconomicAction(
                            action_type=act_type,
                            actor=clause_actor,
                            target=target_desc,
                            description=m.group(0).strip(),
                            magnitude=clause_mag,
                            unit=clause_unit
                        ))
                        seen_action_keys.add(action_key)

        # Fallback if no specific action pattern matched but actors exist
        if not actions and (actors.companies or actors.sectors or actors.countries):
            fallback_mag, fallback_unit = self._extract_magnitude(full_text)
            target_desc = actors.sectors[0] if actors.sectors else "Commercial Operations"
            actions.append(EconomicAction(
                action_type="COMMERCIAL_ADJUSTMENT",
                actor=primary_actor,
                target=target_desc,
                description=f"Market strategic adjustment by {primary_actor}",
                magnitude=fallback_mag,
                unit=fallback_unit
            ))

        return actions

    def _detect_direction(self, clause: str) -> str:
        """Detect direction (INCREASE, DECREASE, VOLATILE, STABLE) within a text clause."""
        inc_pat = r'\b(surge|surges|surged|surging|jump|jumps|jumped|jumping|rise|rises|rose|rising|hike|hikes|hiked|hiking|climb|climbs|climbed|climbing|increase|increases|increased|increasing|tighten|tightens|tightened|tightening|higher|peak|peaks|peaked|peaking|soar|soars|soared|soaring|gain|gains|gained|gaining|expand|expands|expanded|expanding|up)\b'
        dec_pat = r'\b(drop|drops|dropped|dropping|fall|falls|fell|falling|plunge|plunges|plunged|plunging|decrease|decreases|decreased|decreasing|slump|slumps|slumped|slumping|lower|lowers|lowered|lowering|ease|eases|eased|easing|decline|declines|declined|declining|retreat|retreats|retreated|retreating|cut|cuts|cutting|slash|slashes|slashed|slashing|curtail|curtails|curtailed|curtailing|shrink|shrinks|shrank|shrinking|down)\b'
        vol_pat = r'\b(volatile|volatility|fluctuate|fluctuates|fluctuated|fluctuating|wild\s+swings|uncertain|uncertainty)\b'

        has_inc = bool(re.search(inc_pat, clause, re.IGNORECASE))
        has_dec = bool(re.search(dec_pat, clause, re.IGNORECASE))
        has_vol = bool(re.search(vol_pat, clause, re.IGNORECASE))

        if has_vol and not (has_inc or has_dec):
            return "VOLATILE"
        if has_inc and not has_dec:
            return "INCREASE"
        if has_dec and not has_inc:
            return "DECREASE"
        if has_inc and has_dec:
            return "VOLATILE"
        return "STABLE"

    def extract_reactions(self, title: str, summary: str, actors: NewsEntities) -> List[EconomicReaction]:
        """Extract explicit market or system responses to shocks."""
        full_text = f"{title}. {summary}"
        reactions: List[EconomicReaction] = []

        reaction_patterns = [
            (r'\b(prices?|futures?|spot\s+rates?|allowance\s+costs?|tariff\s+costs?|spreads?)\b', "PRICE_CHANGE"),
            (r'\b(suppl(?:y|ies)|deficits?|gluts?|storages?|inventor(?:y|ies)|potlines?|electrolysis|outputs?|production|capacit(?:y|ies)|shipments?|deliver(?:y|ies))\b', "SUPPLY_SHIFT"),
            (r'\b(demands?|consumptions?|offtake|biddings?|buyings?|purchas(?:e|ing))\b', "DEMAND_CHANGE"),
            (r'\b(compliance\s+costs?|administrative\s+burdens?|carbon\s+costs?|surcharges?|penalt(?:y|ies))\b', "COMPLIANCE_COST_SHIFT"),
            (r'\b(volatilit(?:y|ies)|market\s+spreads?|uncertaint(?:y|ies)|fluctuations?)\b', "MARKET_VOLATILITY")
        ]

        # Break text into clauses / sentences without splitting decimal numbers
        clauses = [c.strip() for c in re.split(r'(?<!\d)\.+(?!\d)|[;!\n]+', full_text) if c.strip()]
        if not clauses:
            clauses = [full_text]

        seen_reaction_types = set()

        for clause in clauses:
            clause_upper = clause.upper()
            
            # Identify affected entity in clause
            affected_entity = "MARKET_GENERAL"
            if "GAS" in clause_upper or "TTF" in clause_upper:
                affected_entity = "GAS_TTF"
            elif "CARBON" in clause_upper or "ETS" in clause_upper or "ALLOWANCE" in clause_upper:
                affected_entity = "CO2_ALLOWANCE"
            elif "STEEL" in clause_upper:
                affected_entity = "STEEL_HRC"
            elif "ALUMINIUM" in clause_upper or "ALUMINUM" in clause_upper:
                affected_entity = "ALUMINIUM_PRIMARY"
            elif "SHIPPING" in clause_upper or "FREIGHT" in clause_upper:
                affected_entity = "FREIGHT_RATES"
            elif "FERTILIZER" in clause_upper or "AMMONIA" in clause_upper:
                affected_entity = "FERTILIZER_NITROGEN"
            elif "LITHIUM" in clause_upper or "BATTERY" in clause_upper:
                affected_entity = "LITHIUM_BATTERY"
            elif "HYDROGEN" in clause_upper:
                affected_entity = "HYDROGEN_GREEN"
            elif actors.sectors:
                affected_entity = actors.sectors[0]

            clause_dir = self._detect_direction(clause)
            clause_mag, clause_unit = self._extract_magnitude(clause)

            for regex, r_type in reaction_patterns:
                if re.search(regex, clause, re.IGNORECASE):
                    if r_type not in seen_reaction_types:
                        reactions.append(EconomicReaction(
                            reaction_type=r_type,
                            affected_entity=affected_entity,
                            direction=clause_dir,
                            magnitude=clause_mag,
                            unit=clause_unit,
                            timeframe="3_MONTHS" if clause_dir != "STABLE" else "IMMEDIATE",
                            confidence=0.88
                        ))
                        seen_reaction_types.add(r_type)

        if not reactions:
            overall_dir = self._detect_direction(full_text)
            overall_mag, overall_unit = self._extract_magnitude(full_text)
            default_asset = actors.sectors[0] if actors.sectors else "MARKET_GENERAL"
            reactions.append(EconomicReaction(
                reaction_type="MARKET_REACTION",
                affected_entity=default_asset,
                direction=overall_dir,
                magnitude=overall_mag,
                unit=overall_unit,
                timeframe="IMMEDIATE",
                confidence=0.8
            ))

        return reactions

    def _extract_magnitude(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """Extract numeric magnitude and unit (percent, EUR, EUR/t, tonnes, etc.)."""
        if not text:
            return None, None

        cleaned = text.strip()
        num_pat = r'([+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'

        # 1. Percent
        m_pct = re.search(num_pat + r'\s*(?:%|percent|percentage\s+points?)', cleaned, re.IGNORECASE)
        if m_pct:
            return float(m_pct.group(1).replace(',', '')), "PERCENT"

        # 2. EUR/t, EUR/tCO2, EUR/barrel, EUR/MWh, etc.
        m_eur_t = re.search(r'(?:€|EUR|USD|\$)\s*' + num_pat + r'\s*(?:\/|\s*per\s*)(?:t(?:onne)?s?|tCO2|barrel|bbl|MWh|kg)', cleaned, re.IGNORECASE)
        if not m_eur_t:
            m_eur_t = re.search(num_pat + r'\s*(?:€|EUR|USD|\$)\s*(?:\/|\s*per\s*)(?:t(?:onne)?s?|tCO2|barrel|bbl|MWh|kg)', cleaned, re.IGNORECASE)
        if m_eur_t:
            return float(m_eur_t.group(1).replace(',', '')), "EUR/UNIT"

        # 3. Billions (EUR or USD)
        m_bn = re.search(r'(?:€|EUR|USD|\$)\s*' + num_pat + r'\s*(?:billion|bn|B)\b', cleaned, re.IGNORECASE)
        if not m_bn:
            m_bn = re.search(num_pat + r'\s*(?:billion|bn|B)\s*(?:€|EUR|euros?|USD|dollars?)?\b', cleaned, re.IGNORECASE)
        if m_bn:
            return float(m_bn.group(1).replace(',', '')) * 1000.0, "EUR_MILLION"

        # 4. Millions (EUR or USD)
        m_mn = re.search(r'(?:€|EUR|USD|\$)\s*' + num_pat + r'\s*(?:million|mn|m|M)\b', cleaned, re.IGNORECASE)
        if not m_mn:
            m_mn = re.search(num_pat + r'\s*(?:million|mn|m|M)\s*(?:€|EUR|euros?|USD|dollars?)?\b', cleaned, re.IGNORECASE)
        if m_mn:
            return float(m_mn.group(1).replace(',', '')), "EUR_MILLION"

        # 5. Generic EUR / USD
        m_eur = re.search(r'(?:€|EUR)\s*' + num_pat, cleaned, re.IGNORECASE)
        if not m_eur:
            m_eur = re.search(num_pat + r'\s*(?:€|EUR|euros?)', cleaned, re.IGNORECASE)
        if m_eur:
            return float(m_eur.group(1).replace(',', '')), "EUR"

        # 6. Tonnes
        m_tonnes = re.search(num_pat + r'\s*(?:k|thousand)?\s*(?:tonnes?|tons?|metric\s+tons?)\b', cleaned, re.IGNORECASE)
        if m_tonnes:
            val = float(m_tonnes.group(1).replace(',', ''))
            if 'k' in m_tonnes.group(0).lower() or 'thousand' in m_tonnes.group(0).lower():
                val *= 1000.0
            return val, "TONNES"

        # 7. Days delay
        m_days = re.search(num_pat + r'\s*(?:days?|weeks?)\s*(?:delay|extension|voyage)?', cleaned, re.IGNORECASE)
        if m_days and ("delay" in cleaned.lower() or "transit" in cleaned.lower() or "voyage" in cleaned.lower()):
            val = float(m_days.group(1).replace(',', ''))
            unit = "DAYS_DELAY"
            if "week" in m_days.group(0).lower():
                val *= 7.0
            return val, unit

        return None, None

    def extract_with_llm(
        self,
        title: str,
        summary: str,
        pub_date: str,
        source_url: str,
        source_name: str,
        event_id: str
    ) -> Optional[StructuredEconomicEvent]:
        """
        Use Google Gemini API to semantically extract StructuredEconomicEvent when use_llm=True.
        Falls back to None on error or when offline.
        """
        if not HAS_GOOGLE_API or self.offline or not os.getenv("GOOGLE_API_KEY"):
            return None

        prompt = f"""
        Extract a structured economic event from the following news item:
        Headline: {title}
        Summary: {summary}
        Date: {pub_date}

        Format strictly as JSON with fields:
        {{
            "actors": {{
                "companies": ["..."],
                "countries": ["..."],
                "sectors": ["C24_BASIC_METALS", "C20_CHEMICALS", "D35_ELECTRICITY_GAS", etc.],
                "geography": ["EU", "Western Europe", "Global"]
            }},
            "actions": [
                {{
                    "action_type": "PRODUCTION_CUT" | "CAPEX_INVESTMENT" | "PRICE_REVISION" | "TARIFF_IMPOSITION" | "SUBSIDY_PROGRAM" | "FACILITY_SHUTDOWN" | "CONTRACT_SIGNING" | "REGULATORY_MANDATE",
                    "actor": "Name of company or government",
                    "target": "Target facility, sector, or commodity",
                    "description": "Brief description",
                    "magnitude": float or null,
                    "unit": "PERCENT" | "EUR_MILLION" | "EUR/UNIT" | "TONNES" | null
                }}
            ],
            "reactions": [
                {{
                    "reaction_type": "PRICE_CHANGE" | "SUPPLY_SHIFT" | "DEMAND_CHANGE" | "COMPLIANCE_COST_SHIFT" | "MARKET_VOLATILITY",
                    "affected_entity": "Asset or sector, e.g. GAS_TTF, CO2_ALLOWANCE, STEEL_HRC",
                    "direction": "INCREASE" | "DECREASE" | "STABLE" | "VOLATILE",
                    "magnitude": float or null,
                    "unit": "PERCENT" | "EUR_MILLION" | "EUR/UNIT" | "TONNES" | null,
                    "timeframe": "IMMEDIATE" | "3_MONTHS" | "1_YEAR" | "LONG_TERM",
                    "confidence": 0.9
                }}
            ],
            "receptor_signal": "Short uppercase root cause signal",
            "target_asset": "Primary affected asset"
        }}
        """
        try:
            raw_json = get_completion_with_json(prompt=prompt, use_pro=False)
            if not raw_json:
                return None
            data = json.loads(raw_json)
            actors_data = data.get("actors", {})
            actors = NewsEntities(
                companies=actors_data.get("companies", []),
                countries=actors_data.get("countries", []),
                sectors=actors_data.get("sectors", []),
                geography=actors_data.get("geography", ["EU"])
            )
            actions = [EconomicAction(**a) for a in data.get("actions", [])]
            reactions = [EconomicReaction(**r) for r in data.get("reactions", [])]
            return StructuredEconomicEvent(
                event_id=event_id,
                timestamp=pub_date,
                headline=title,
                source_url=source_url,
                source_name=source_name,
                summary=summary[:1000],
                actors=actors,
                actions=actions,
                reactions=reactions,
                receptor_signal=data.get("receptor_signal") or "ECONOMIC_EVENT",
                target_asset=data.get("target_asset") or (reactions[0].affected_entity if reactions else "GLOBAL_MARKET"),
                relevance_score=1.0
            )
        except Exception as e:
            logger.warning(f"LLM extraction failed for '{title}': {e}. Falling back to rule-based extractor.")
            return None

    def extract_structured_event(
        self,
        title: str,
        summary: str,
        source_url: str,
        source_name: str,
        pub_date: str,
        event_id: Optional[str] = None
    ) -> StructuredEconomicEvent:
        """
        Extract a comprehensive StructuredEconomicEvent from raw news inputs.
        """
        if not event_id:
            h = abs(hash(f"{source_url}_{title}_{pub_date}")) % 10000000
            event_id = f"NEWS_{pub_date.replace('-', '_')}_{h:07d}"

        # If LLM extraction requested and available, attempt it first
        if self.use_llm and not self.offline:
            llm_evt = self.extract_with_llm(
                title=title,
                summary=summary,
                pub_date=pub_date,
                source_url=source_url,
                source_name=source_name,
                event_id=event_id
            )
            if llm_evt:
                return llm_evt

        # 1. Identify economic actors
        actors = self.extract_actors(f"{title} {summary}")

        # 2. Extract actions and reactions
        actions = self.extract_actions(title, summary, actors)
        reactions = self.extract_reactions(title, summary, actors)

        # 3. Formulate root-cause Receptor Signal
        receptor_signal = "ECONOMIC_EVENT"
        if actions:
            act = actions[0]
            receptor_signal = f"{act.action_type}_{act.target}".upper().replace(" ", "_")[:64]

        # 4. Target Asset
        target_asset = reactions[0].affected_entity if reactions else "GLOBAL_MARKET"

        return StructuredEconomicEvent(
            event_id=event_id,
            timestamp=pub_date,
            headline=title,
            source_url=source_url,
            source_name=source_name,
            summary=summary[:1000],
            actors=actors,
            actions=actions,
            reactions=reactions,
            receptor_signal=receptor_signal,
            target_asset=target_asset,
            relevance_score=1.0
        )

    def _extract_signal_and_impact(self, title: str, summary: str) -> Dict[str, Any]:
        """Convenience method returning flattened signal and impact dictionary."""
        evt = self.extract_structured_event(
            title=title,
            summary=summary,
            source_url="",
            source_name="News",
            pub_date=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        primary_react = evt.reactions[0] if evt.reactions else None
        magnitude = primary_react.magnitude if primary_react and primary_react.magnitude is not None else 0.0
        return {
            "receptor_signal": evt.receptor_signal or "MACROECONOMIC_SHIFT",
            "target_asset": evt.target_asset or "GLOBAL_MARKET",
            "metric": primary_react.reaction_type if primary_react else "PRICE",
            "direction": primary_react.direction if primary_react else "STABLE",
            "delta_value": magnitude,
            "delta_unit": primary_react.unit or "PERCENT" if primary_react else "PERCENT",
            "timeframe": primary_react.timeframe if primary_react else "3_MONTHS"
        }

    def normalize_event(self, item: Union[StructuredEconomicEvent, Dict[str, Any]]) -> StructuredEconomicEvent:
        """
        Normalize an item (StructuredEconomicEvent or Dict) into a validated StructuredEconomicEvent.
        Preserves existing fields while extracting any missing attributes.
        """
        if isinstance(item, StructuredEconomicEvent):
            return item

        if isinstance(item, dict):
            event_id = item.get("event_id") or item.get("news_id")
            pub_date = item.get("timestamp") or item.get("published_date") or item.get("impact_date") or ""
            parsed_date = self._parse_date(pub_date)
            title = item.get("headline") or item.get("title") or "Untitled Event"
            summary = item.get("summary") or item.get("semantic_summary") or ""
            source_url = item.get("source_url") or ""
            source_name = item.get("source_name") or "News"

            # 1. Actors
            raw_actors = item.get("actors") or item.get("entities")
            if isinstance(raw_actors, NewsEntities):
                actors = raw_actors
            elif isinstance(raw_actors, dict):
                actors = NewsEntities(
                    companies=raw_actors.get("companies", []),
                    countries=raw_actors.get("countries", []),
                    sectors=raw_actors.get("sectors", []),
                    geography=raw_actors.get("geography", ["EU"])
                )
            else:
                actors = self.extract_actors(f"{title} {summary}")

            # 2. Actions
            raw_actions = item.get("actions", [])
            actions = []
            if raw_actions:
                for a in raw_actions:
                    if isinstance(a, EconomicAction):
                        actions.append(a)
                    elif isinstance(a, dict):
                        actions.append(EconomicAction(**a))
            else:
                actions = self.extract_actions(title, summary, actors)

            # 3. Reactions
            raw_reactions = item.get("reactions", [])
            reactions = []
            if raw_reactions:
                for r in raw_reactions:
                    if isinstance(r, EconomicReaction):
                        reactions.append(r)
                    elif isinstance(r, dict):
                        reactions.append(EconomicReaction(**r))
            else:
                reactions = self.extract_reactions(title, summary, actors)

            # 4. Receptor Signal & Target Asset
            receptor_signal = item.get("receptor_signal")
            if not receptor_signal and actions:
                act = actions[0]
                receptor_signal = f"{act.action_type}_{act.target}".upper().replace(" ", "_")[:64]

            target_asset = item.get("target_asset")
            if not target_asset and reactions:
                target_asset = reactions[0].affected_entity

            if not event_id:
                h = abs(hash(f"{source_url}_{title}_{parsed_date}")) % 10000000
                event_id = f"NEWS_{parsed_date.replace('-', '_')}_{h:07d}"

            return StructuredEconomicEvent(
                event_id=event_id,
                timestamp=parsed_date,
                headline=title,
                source_url=source_url,
                source_name=source_name,
                summary=summary[:1000],
                actors=actors,
                actions=actions,
                reactions=reactions,
                receptor_signal=receptor_signal or "ECONOMIC_EVENT",
                target_asset=target_asset or "GLOBAL_MARKET",
                relevance_score=float(item.get("relevance_score", 1.0))
            )

        raise TypeError(f"Cannot normalize event of type {type(item)}")

    # ----------------------------------------------------------------------
    # 3. SCRAPING PIPELINE & FEED COORDINATION
    # ----------------------------------------------------------------------
    def scrape_all_feeds(
        self,
        feeds: Optional[List[Dict[str, str]]] = None,
        max_per_feed: int = 15,
        save_to_db: bool = True
    ) -> List[StructuredEconomicEvent]:
        """
        Fetch all specified RSS feeds or fall back to offline seed corpus if network is unavailable or offline=True.
        """
        # If scraper was initialized with offline=True, do not make any network requests
        if self.offline:
            logger.info("Scraper is in offline mode. Generating offline seeds directly without network calls.")
            events = self.generate_offline_seeds(count=len(FALLBACK_NEWS_CORPUS), save_to_lancedb=save_to_db)
            if self.graph_store:
                self.ingest_news_into_graph_store(events)
            return events

        target_feeds = feeds or DEFAULT_FEEDS
        raw_articles = []

        for feed in target_feeds:
            feed_url = feed["url"]
            source_name = feed["name"]
            items = self.fetch_rss_feed(feed_url, source_name, max_items=max_per_feed)
            raw_articles.extend(items)

        events: List[StructuredEconomicEvent] = []

        if raw_articles:
            logger.info(f"Successfully scraped {len(raw_articles)} live articles from {len(target_feeds)} feeds.")
            for item in raw_articles:
                evt = self.extract_structured_event(
                    title=item["title"],
                    summary=item["summary"],
                    source_url=item["source_url"],
                    source_name=item["source_name"],
                    pub_date=item["published_date"],
                    event_id=item["news_id"]
                )
                events.append(evt)
        else:
            from src.config import get_settings
            settings = get_settings()
            if settings.mode == "demo" or self.offline:
                logger.info("Live RSS feeds returned 0 articles (or offline). Loading benchmark seed generator.")
                events = self.generate_offline_seeds(count=len(FALLBACK_NEWS_CORPUS), save_to_lancedb=False)
            else:
                logger.warning("Live RSS feeds returned 0 articles in production mode. Zero synthetic events generated.")
                return []

        # Cache events to disk
        self.cache_to_disk(events)

        # Save to LanceDB
        if save_to_db:
            self.save_to_lancedb(events)

        # Ingest to DualLayerGraphStore if attached
        if self.graph_store:
            self.ingest_news_into_graph_store(events)

        return events

    # ----------------------------------------------------------------------
    # 4. FALLBACK OFFLINE SEED GENERATOR
    # ----------------------------------------------------------------------
    def generate_offline_seeds(
        self,
        count: int = 10,
        save_to_lancedb: bool = True,
        output_file: Optional[Path] = None
    ) -> List[StructuredEconomicEvent]:
        """
        Generate curated benchmark and synthetic structured events for offline testing.
        Guarantees system operational viability without live network access.
        """
        events: List[StructuredEconomicEvent] = []

        # 1. Load curated benchmark items
        for item in FALLBACK_NEWS_CORPUS[:count]:
            actors_dict = item.get("actors", {})
            actors = NewsEntities(
                companies=actors_dict.get("companies", []),
                countries=actors_dict.get("countries", []),
                sectors=actors_dict.get("sectors", []),
                geography=actors_dict.get("geography", ["EU"])
            )

            actions = [
                EconomicAction(**act) for act in item.get("actions", [])
            ]
            reactions = [
                EconomicReaction(**react) for react in item.get("reactions", [])
            ]

            evt = StructuredEconomicEvent(
                event_id=item["news_id"],
                timestamp=item.get("published_date", "2024-01-01"),
                headline=item["title"],
                source_url=item.get("source_url", ""),
                source_name=item.get("source_name", "Curated Benchmark"),
                summary=item.get("summary", ""),
                actors=actors,
                actions=actions,
                reactions=reactions,
                receptor_signal=item.get("receptor_signal", "OFFLINE_SEED"),
                target_asset=item.get("target_asset", "MARKET"),
                relevance_score=1.0
            )
            events.append(evt)

        # 2. If count exceeds benchmark length, synthesize additional realistic events
        if count > len(events):
            additional_count = count - len(events)
            synthetic_templates = [
                ("ArcelorMittal", "Germany", "C24_BASIC_METALS", "PRODUCTION_CUT", -15.0, "PERCENT", "PRICE_CHANGE", "STEEL_HRC", "INCREASE", 6.0, "PERCENT"),
                ("BASF", "Germany", "C20_CHEMICALS", "CAPEX_INVESTMENT", 450.0, "EUR_MILLION", "SUPPLY_SHIFT", "C20_CHEMICALS", "INCREASE", 12.0, "PERCENT"),
                ("TotalEnergies", "France", "D35_ELECTRICITY_GAS", "PRICE_REVISION", 5.2, "EUR/MWh", "PRICE_CHANGE", "GAS_TTF", "INCREASE", 5.2, "EUR/MWh"),
                ("Maersk", "Denmark", "H50_WATER_TRANSPORT", "FACILITY_SHUTDOWN", 7.0, "DAYS_DELAY", "PRICE_CHANGE", "FREIGHT_RATES", "INCREASE", 14.0, "PERCENT"),
                ("Umicore", "Belgium", "B07_MINING_METALS", "CAPEX_INVESTMENT", 200.0, "EUR_MILLION", "DEMAND_CHANGE", "LITHIUM", "INCREASE", 8.0, "PERCENT")
            ]
            for i in range(additional_count):
                tmpl = synthetic_templates[i % len(synthetic_templates)]
                comp, country, sector, a_type, a_mag, a_unit, r_type, aff_asset, r_dir, r_mag, r_unit = tmpl
                synth_id = f"NEWS_SEED_{2024 + (i // 12):04d}_{(i % 12) + 1:02d}_{i:03d}"
                dt = f"{2024 + (i // 12):04d}-{(i % 12) + 1:02d}-15"
                headline = f"{comp} Announces {a_type.replace('_', ' ').title()} Impacting European {sector}"
                summary = f"{comp} in {country} executes a {a_type.lower()} with magnitude {a_mag} {a_unit}, leading to {r_type.lower()} in {aff_asset}."

                actors = NewsEntities(
                    companies=[comp],
                    countries=[country],
                    sectors=[sector],
                    geography=["EU"]
                )
                actions = [
                    EconomicAction(
                        action_type=a_type,
                        actor=comp,
                        target=sector,
                        description=f"Synthetic action {a_type}",
                        magnitude=a_mag,
                        unit=a_unit
                    )
                ]
                reactions = [
                    EconomicReaction(
                        reaction_type=r_type,
                        affected_entity=aff_asset,
                        direction=r_dir,
                        magnitude=r_mag,
                        unit=r_unit,
                        timeframe="3_MONTHS",
                        confidence=0.85
                    )
                ]
                events.append(StructuredEconomicEvent(
                    event_id=synth_id,
                    timestamp=dt,
                    headline=headline,
                    source_url=f"https://seed.powerlaw.internal/news/{synth_id.lower()}",
                    source_name="PowerLaw Seed Generator",
                    summary=summary,
                    actors=actors,
                    actions=actions,
                    reactions=reactions,
                    receptor_signal=f"SYNTHETIC_{a_type}_{sector}",
                    target_asset=aff_asset,
                    relevance_score=1.0
                ))

        # Cache to disk
        self.cache_to_disk(events, output_file)

        # Save to LanceDB
        if save_to_lancedb:
            self.save_to_lancedb(events)

        # Graph Store
        if self.graph_store:
            self.ingest_news_into_graph_store(events)

        return events

    # ----------------------------------------------------------------------
    # 5. STORAGE & LANCEDB INTEGRATION
    # ----------------------------------------------------------------------
    def _get_table_names(self, db) -> set:
        """Robustly retrieve table names across all LanceDB versions."""
        if hasattr(db, "list_tables"):
            try:
                res = db.list_tables()
                if hasattr(res, "tables"):
                    return set(res.tables)
                if isinstance(res, (list, set, tuple)):
                    return set(res)
            except Exception:
                pass
        if hasattr(db, "table_names"):
            try:
                return set(db.table_names())
            except Exception:
                pass
        return set()

    def save_to_lancedb(
        self,
        events: List[Union[StructuredEconomicEvent, Dict[str, Any]]],
        table_name: str = "news_items"
    ) -> int:
        """
        Persist structured economic events into LanceDB table conforming to NewsItem schema.
        Accepts StructuredEconomicEvent or dictionary items.
        Generates text embeddings via Google API or deterministic offline fallback.
        """
        if not HAS_LANCEDB:
            logger.warning("LanceDB is not installed or available.")
            return 0

        self.lancedb_path.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(self.lancedb_path))

        # Connect or create table
        existing_tables = self._get_table_names(db)
        if table_name in existing_tables:
            table = db.open_table(table_name)
            try:
                existing_df = table.to_pandas()
                existing_ids = set(existing_df["news_id"].unique()) if "news_id" in existing_df.columns else set()
            except Exception:
                existing_ids = set()
        else:
            table = db.create_table(table_name, schema=NewsItem)
            existing_ids = set()

        items_to_insert: List[NewsItem] = []
        for raw_evt in events:
            evt = self.normalize_event(raw_evt)
            if evt.event_id in existing_ids:
                continue

            # Compute embedding vector (768-dim)
            embedding_text = f"{evt.headline}. {evt.summary}"
            vector = None
            
            # Only use Google embedding API if explicitly enabled, API key is present, and not in offline mode
            can_use_google_api = (
                HAS_GOOGLE_API 
                and bool(os.getenv("GOOGLE_API_KEY")) 
                and not getattr(self, "offline", False)
            )
            if can_use_google_api:
                try:
                    vector = get_embedding(embedding_text)
                except Exception:
                    vector = None

            if vector is None or len(vector) != 768:
                vector = generate_deterministic_embedding(embedding_text, dim=768)

            # JSON serializations
            actions_json = json.dumps([a.model_dump() for a in evt.actions], ensure_ascii=False)
            reactions_json = json.dumps([r.model_dump() for r in evt.reactions], ensure_ascii=False)
            
            receptor_obj = ReceptorSignal(
                primary_node=evt.receptor_signal or "ECONOMIC_EVENT",
                severity=0.85
            )
            receptor_json = receptor_obj.model_dump_json()

            # StructuredImpact fallback
            primary_reaction = evt.reactions[0] if evt.reactions else None
            structured_impact = StructuredImpactNews(
                target_asset=evt.target_asset or "",
                metric=primary_reaction.reaction_type if primary_reaction else "PRICE",
                direction=primary_reaction.direction if primary_reaction else "STABLE",
                delta_value=primary_reaction.magnitude if primary_reaction else None,
                delta_unit=primary_reaction.unit if primary_reaction else None,
                timeframe=primary_reaction.timeframe if primary_reaction else "IMMEDIATE"
            )
            structured_impact_json = structured_impact.model_dump_json()

            item = NewsItem(
                vector=vector,
                news_id=evt.event_id,
                semantic_summary=evt.summary,
                headline=evt.headline,
                source_url=evt.source_url,
                source_name=evt.source_name,
                entities=evt.actors,
                actions_json=actions_json,
                reactions_json=reactions_json,
                receptor_signal_json=receptor_json,
                structured_impact_json=structured_impact_json,
                impact_date=evt.timestamp
            )
            items_to_insert.append(item)
            existing_ids.add(evt.event_id)

        if items_to_insert:
            table.add(items_to_insert)
            logger.info(f"Saved {len(items_to_insert)} new news events into LanceDB table '{table_name}'.")

        return len(items_to_insert)

    def load_from_lancedb(
        self,
        table_name: str = "news_items",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Read news records from LanceDB."""
        if not HAS_LANCEDB or not self.lancedb_path.exists():
            return []

        try:
            db = lancedb.connect(str(self.lancedb_path))
            existing_tables = self._get_table_names(db)
            if table_name not in existing_tables:
                return []
            tbl = db.open_table(table_name)
            df = tbl.to_pandas()
            if limit:
                df = df.head(limit)
            return df.to_dict(orient="records")
        except Exception as e:
            logger.warning(f"Error loading from LanceDB table '{table_name}': {e}")
            return []

    def cache_to_disk(
        self,
        events: List[Union[StructuredEconomicEvent, Dict[str, Any]]],
        output_file: Optional[Path] = None
    ) -> Path:
        """Cache extracted structured events as JSON to local storage."""
        target_path = output_file or (self.data_dir / "scraped_news.json")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        data = [self.normalize_event(e).model_dump() for e in events]
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return target_path

    # ----------------------------------------------------------------------
    # 6. DUAL-LAYER GRAPH STORE INTEGRATION
    # ----------------------------------------------------------------------
    def ingest_news_into_graph_store(
        self,
        events: List[Union[StructuredEconomicEvent, Dict[str, Any]]]
    ) -> None:
        """
        Map events into DualLayerGraphStore:
        - ACTION nodes for extracted actions
        - REACTION nodes for market reactions
        - TRIGGERS edge from Action -> Reaction
        - AFFECTS edge from Action -> Actor/Sector
        - IMPACTS edge from Reaction -> Target Asset / Affected Entity
        - Behind-the-graph temporal tabular records (NodeStateRecord)
        """
        if not self.graph_store:
            return

        for raw_item in events:
            evt = self.normalize_event(raw_item)

            pub_date = evt.timestamp
            headline = evt.headline
            target_asset = evt.target_asset or "MARKET"

            # 1. Action Nodes & Edges
            action_node_ids = []
            for idx, act in enumerate(evt.actions):
                act_node_id = f"ACTION_{evt.event_id}_{act.action_type}_{idx}"
                act_node = GraphNode(
                    id=act_node_id,
                    label=f"Action: {act.actor} -> {act.action_type}",
                    node_type=NodeType.ACTION,
                    description=act.description or headline,
                    jurisdiction="EU",
                    activation_date=pub_date,
                    metadata={
                        "actor": act.actor,
                        "action_type": act.action_type,
                        "target": act.target,
                        "magnitude": act.magnitude,
                        "unit": act.unit,
                        "news_id": evt.event_id
                    }
                )
                self.graph_store.add_node(act_node)
                action_node_ids.append(act_node_id)

                # Connect Action -> AFFECTS -> Sector nodes in the graph
                connected_sectors = set()
                # A) Direct target match
                if act.target in self.graph_store._nodes:
                    self.graph_store.add_edge(GraphEdge(
                        source_id=act_node_id,
                        target_id=act.target,
                        relation_type=EdgeRelation.AFFECTS,
                        weight=1.0,
                        timestamp=pub_date
                    ))
                    connected_sectors.add(act.target)

                # B) Link to identified sector nodes from event
                for sector_id in evt.actors.sectors:
                    if sector_id in self.graph_store._nodes and sector_id not in connected_sectors:
                        self.graph_store.add_edge(GraphEdge(
                            source_id=act_node_id,
                            target_id=sector_id,
                            relation_type=EdgeRelation.AFFECTS,
                            weight=1.0,
                            timestamp=pub_date
                        ))
                        connected_sectors.add(sector_id)

                # Tabular record for Action
                self.graph_store.add_state_record(NodeStateRecord(
                    node_id=act_node_id,
                    node_type=NodeType.ACTION,
                    timestamp=pub_date,
                    state_value=act.action_type,
                    metric_name="action_magnitude",
                    metric_value=float(act.magnitude or 1.0),
                    delta=float(act.magnitude or 0.0),
                    unit=act.unit or "COUNT",
                    source_id=evt.event_id,
                    source_type="NEWS",
                    confidence=0.9,
                    notes=f"Extracted action by {act.actor}"
                ))

            # 2. Reaction Nodes & Edges
            for idx, react in enumerate(evt.reactions):
                react_node_id = f"REACT_{evt.event_id}_{react.reaction_type}_{idx}"
                react_node = GraphNode(
                    id=react_node_id,
                    label=f"Reaction: {react.reaction_type} on {react.affected_entity}",
                    node_type=NodeType.REACTION,
                    description=f"{react.direction} in {react.affected_entity} ({react.magnitude} {react.unit})",
                    jurisdiction="EU",
                    activation_date=pub_date,
                    metadata={
                        "reaction_type": react.reaction_type,
                        "affected_entity": react.affected_entity,
                        "direction": react.direction,
                        "magnitude": react.magnitude,
                        "unit": react.unit,
                        "news_id": evt.event_id
                    }
                )
                self.graph_store.add_node(react_node)

                # Connect Action -> TRIGGERS -> Reaction
                for act_node_id in action_node_ids:
                    self.graph_store.add_edge(GraphEdge(
                        source_id=act_node_id,
                        target_id=react_node_id,
                        relation_type=EdgeRelation.TRIGGERS,
                        weight=1.0 if react.direction == "INCREASE" else -1.0,
                        timestamp=pub_date
                    ))

                # Connect Reaction -> IMPACTS -> Target Asset / Affected Entity
                impacted = False
                if react.affected_entity in self.graph_store._nodes:
                    self.graph_store.add_edge(GraphEdge(
                        source_id=react_node_id,
                        target_id=react.affected_entity,
                        relation_type=EdgeRelation.IMPACTS,
                        weight=1.0 if react.direction == "INCREASE" else -1.0,
                        timestamp=pub_date,
                        properties={"direction": react.direction, "magnitude": react.magnitude}
                    ))
                    impacted = True

                if target_asset in self.graph_store._nodes and target_asset != react.affected_entity:
                    self.graph_store.add_edge(GraphEdge(
                        source_id=react_node_id,
                        target_id=target_asset,
                        relation_type=EdgeRelation.IMPACTS,
                        weight=1.0 if react.direction == "INCREASE" else -1.0,
                        timestamp=pub_date,
                        properties={"direction": react.direction, "magnitude": react.magnitude}
                    ))
                    impacted = True

                if not impacted:
                    # Link to first sector node in graph if available
                    for sector_id in evt.actors.sectors:
                        if sector_id in self.graph_store._nodes:
                            self.graph_store.add_edge(GraphEdge(
                                source_id=react_node_id,
                                target_id=sector_id,
                                relation_type=EdgeRelation.IMPACTS,
                                weight=1.0 if react.direction == "INCREASE" else -1.0,
                                timestamp=pub_date,
                                properties={"direction": react.direction, "magnitude": react.magnitude}
                            ))
                            break

                # Tabular record for Reaction
                val = float(react.magnitude or (1.0 if react.direction == "INCREASE" else -1.0))
                self.graph_store.add_state_record(NodeStateRecord(
                    node_id=react_node_id,
                    node_type=NodeType.REACTION,
                    timestamp=pub_date,
                    state_value=react.direction,
                    metric_name=react.reaction_type.lower(),
                    metric_value=val,
                    delta=val if react.direction == "INCREASE" else -val,
                    unit=react.unit or "INDEX",
                    source_id=evt.event_id,
                    source_type="NEWS",
                    confidence=react.confidence,
                    notes=f"Reaction to {headline}"
                ))

        self.graph_store.save()
        logger.info(f"Ingested {len(events)} events into DualLayerGraphStore.")


# ==========================================
# CLI ENTRYPOINT
# ==========================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="PowerLaw Economic News Scraper & Event Extraction Pipeline")
    parser.add_argument("--offline", action="store_true", help="Run strictly offline using benchmark seed generator")
    parser.add_argument("--seed", action="store_true", help="Generate and ingest offline seeds into LanceDB")
    parser.add_argument("--count", type=int, default=10, help="Number of seed items to generate")
    parser.add_argument("--feeds", action="store_true", help="Fetch and process live RSS feeds")
    parser.add_argument("--db-path", type=str, default=None, help="LanceDB database directory path (defaults to centralized settings)")
    parser.add_argument("--output", type=str, default="data/raw/news/scraped_news.json", help="Output JSON path")
    parser.add_argument("--verify", action="store_true", help="Verify LanceDB news table records")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

    is_offline = bool(args.offline or args.seed)
    scraper = EconomicNewsScraper(
        lancedb_path=args.db_path,
        data_dir=str(Path(args.output).parent),
        offline=is_offline
    )

    if args.verify:
        records = scraper.load_from_lancedb(limit=10)
        print(f"\n[VERIFY] LanceDB Verification: Found {len(records)} records in 'news_items'")
        for r in records[:5]:
            print(f" - [{r.get('impact_date')}] {r.get('headline')} (ID: {r.get('news_id')})")
        return

    if args.seed or args.offline:
        print(f"\n[SEED] Generating {args.count} offline seed events...")
        events = scraper.generate_offline_seeds(count=args.count, save_to_lancedb=True, output_file=Path(args.output))
        print(f"[OK] Generated {len(events)} structured economic events.")
        print(f"[CACHE] Cached JSON at: {args.output}")
        print(f"[LANCEDB] Persisted into LanceDB: {scraper.lancedb_path} (table 'news_items')")
        return

    # Default / Feeds mode
    print("\n[SCRAPER] Running News Scraper Pipeline...")
    events = scraper.scrape_all_feeds(save_to_db=True)
    print(f"[OK] Pipeline complete: Processed {len(events)} events.")
    print(f"[CACHE] Cached JSON at: {args.output}")
    print(f"[LANCEDB] Persisted into LanceDB: {args.db_path} (table 'news_items')")


if __name__ == "__main__":
    main()
