"""Synthetic news fixtures; application code must never import this module."""
from typing import List
from src.schemas.extraction import NewsEntities, EconomicAction, EconomicReaction, StructuredEconomicEvent

NEWS_FIXTURES = [
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



def make_news_events(count=10):
    events: List[StructuredEconomicEvent] = []

    # 1. Load curated benchmark items
    for item in NEWS_FIXTURES[:count]:
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

    return events
