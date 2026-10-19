"""
Economic & Trade News Scraper & Event Extraction Pipeline for PowerLaw
Collects real-time and historical news related to EU trade, commodities, energy, and industry.

Extracts:
1. Event timestamp, Source URL, Headline, and Summary
2. Identified economic actors (companies, countries, sectors)
3. Extracted Actions (production cuts, capex, price revisions, etc.) and Reactions (price changes, supply shifts, etc.)
4. Persistence into LanceDB (NewsItem) and local disk cache
5. Dual-Layer Graph integration (Action/Reaction nodes + temporal tabular history)
6. Explicit offline mode without fabricated data
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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from src.schemas.ingestion import SourceDocument, Fact, stable_id

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
    "umicore": "Umicore",

    # Public Authorities & Institutions
    "european commission": "European Commission",
    "commission": "European Commission",
    "council": "Council of the European Union",
    "parliament": "European Parliament",
    "destatis": "Destatis"
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
    @staticmethod
    def _canonical_url(url: str) -> str:
        parts = urlsplit((url or "").strip())
        if not parts.scheme or not parts.netloc:
            return (url or "").strip()
        ignored = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref", "mc_cid", "mc_eid"}
        query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in ignored))
        path = parts.path.rstrip("/") if parts.path and parts.path != "/" else (parts.path or "/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))

    def _article_identity(self, source_url: str, title: str, published_date: str, upstream_id: str = "") -> str:
        canonical = self._canonical_url(source_url)
        key_parts = [p for p in (upstream_id.strip(), canonical) if p]
        if not key_parts:
            key_parts = [(title or "").strip(), published_date or ""]
        natural_key = "|".join(key_parts)
        return f"NEWS_{hashlib.sha256(natural_key.encode('utf-8')).hexdigest()[:32]}"

    def _event_identity(self, actor: str, action_type: str, target: str, date_str: str, magnitude: Optional[float] = None, location: str = "") -> str:
        loc_str = location.strip().lower() if location else ""
        natural_key = f"{actor.strip().lower()}|{action_type.strip().upper()}|{target.strip().lower()}|{loc_str}|{date_str.strip()}"
        return f"EVENT_{hashlib.sha256(natural_key.encode('utf-8')).hexdigest()[:32]}"

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

                    guid_elem = item.find(["guid", "id"])
                    upstream_id = guid_elem.text.strip() if guid_elem and guid_elem.text else ""
                    article_id = self._article_identity(link_text, title_text, parsed_date, upstream_id)

                    raw_items.append({
                        "news_id": article_id,
                        "title": title_text,
                        "published_date": parsed_date,
                        "source_name": source_name,
                        "source_url": link_text,
                        "upstream_id": upstream_id,
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

                    guid_elem = next((e for e in item if e.tag.endswith(("guid", "id"))), None)
                    upstream_id = guid_elem.text.strip() if guid_elem is not None and guid_elem.text else ""
                    article_id = self._article_identity(link_text, title_text, parsed_date, upstream_id)

                    raw_items.append({
                        "news_id": article_id,
                        "title": title_text,
                        "published_date": parsed_date,
                        "source_name": source_name,
                        "source_url": link_text,
                        "upstream_id": upstream_id,
                        "summary": desc_text[:1200]
                    })
        except Exception as e:
            logger.warning(f"Error fetching feed {feed_url} ({source_name}): {e}")

        return raw_items

    def _parse_date(self, date_str: str) -> str:
        """Standardize various feed date strings to YYYY-MM-DD."""
        if not date_str or not isinstance(date_str, str):
            return ""

        cleaned = date_str.strip()
        if not cleaned:
            return ""

        # 1. Try RFC 2822 / 822 format via standard library email.utils
        try:
            dt = email.utils.parsedate_to_datetime(cleaned)
            if dt:
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc)
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

        # 2. Try ISO 8601 format
        try:
            iso_str = cleaned.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
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

        return ""

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
                r'\b(impose(?:s|d|ing)?(?:\s+[^.,;\n]{0,35})?\s+(?:tariffs?|dut(?:y|ies))|(?:import\s+)?dut(?:y|ies)|tariffs?|anti[- ]dumping|trade\s+sanction(?:s)?|cbam\s+obligation(?:s)?|customs\s+penalt(?:y|ies)|tariff\s+(?:quota|imposition|rate|review))\b',
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
            clause_lower = f" {clause.lower()} "

            # Check for negation
            is_negated = bool(re.search(r'\b(will\s+not|not\s+(?:close|shut|halt|idle|curtail|cut|impose|raise|invest)|does\s+not|deni(?:es|ed)|reject(?:s|ed)|no\s+plans?\s+to|no\s+action)\b', clause_lower))
            is_tentative = bool(re.search(r'\b(weighs?\s+plans?|considers?|evaluat(?:es?|ing)|explor(?:es?|ing)|potential(?:ly)?)\b', clause_lower))

            if is_negated:
                continue

            # Check if a specific actor is named in this clause
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

            # Extract location if mentioned in clause
            loc_m = re.search(r'\b(?:in|at|near|for|plant in|site in|facility in)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', clause)
            clause_loc = loc_m.group(1).strip() if loc_m else ""
            if clause_loc and clause_loc in KNOWN_COUNTRIES:
                clause_loc = KNOWN_COUNTRIES[clause_loc]

            # Extract magnitude locally for this clause
            clause_mag, clause_unit = self._extract_magnitude(clause)
            if is_tentative:
                clause_mag = None

            for regex, act_type in action_patterns:
                m = re.search(regex, clause, re.IGNORECASE)
                if m:
                    base_target = actors.sectors[0] if actors.sectors else "Industrial Operations"
                    target_desc = f"{base_target} - {clause_loc}" if clause_loc else base_target
                    action_key = (act_type, clause_actor, target_desc, clause_mag, clause_unit)
                    if action_key not in seen_action_keys:
                        desc = f"Tentative/planned: {m.group(0).strip()}" if is_tentative else m.group(0).strip()
                        actions.append(EconomicAction(
                            action_type=act_type,
                            actor=clause_actor,
                            target=target_desc,
                            description=desc,
                            magnitude=clause_mag,
                            unit=clause_unit
                        ))
                        seen_action_keys.add(action_key)

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
            (r'\b(suppl(?:y|ies)(?!\s+chains?)|deficits?|gluts?|storages?|inventor(?:y|ies)|potlines?|electrolysis|outputs?|production|capacit(?:y|ies)|shipments?|deliver(?:y|ies))\b', "SUPPLY_SHIFT"),
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
            clause_lower = f" {clause.lower()} "
            is_negated = bool(re.search(r'\b(will\s+not|not\s+(?:drop|fall|decline|rise|surge|jump|cut|curtail)|does\s+not|deni(?:es|ed)|reject(?:s|ed)|no\s+(?:cuts?|drops?|falls?|declines?|surges?|changes?)|without\s+(?:changes?|moves?))\b', clause_lower))
            if is_negated:
                continue

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
                    # Guard: Do not emit fabricated reactions on commentary/general discussion
                    if clause_dir == "STABLE" and clause_mag is None:
                        if not re.search(r'\b(remain(?:s|ed)?\s+stable|stabl(?:e|ity|ized?))\b', clause, re.IGNORECASE) or affected_entity == "MARKET_GENERAL":
                            continue
                    reaction_key = (r_type, affected_entity, clause_dir, clause_mag, clause_unit)
                    if reaction_key not in seen_reaction_types:
                        reactions.append(EconomicReaction(
                            reaction_type=r_type,
                            affected_entity=affected_entity,
                            direction=clause_dir,
                            magnitude=clause_mag,
                            unit=clause_unit,
                            timeframe="3_MONTHS" if clause_dir != "STABLE" else "IMMEDIATE",
                            confidence=0.88
                        ))
                        seen_reaction_types.add(reaction_key)

        return reactions

    def _extract_magnitude(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """Extract numeric magnitude and unit (percent, EUR, EUR/t, tonnes, etc.)."""
        if not text:
            return None, None

        cleaned = text.strip()
        num_pat = r'([+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'

        # 1. Percentage points vs Percent
        m_pp = re.search(num_pat + r'\s*(?:percentage\s+points?|pp\b)', cleaned, re.IGNORECASE)
        if m_pp:
            return float(m_pp.group(1).replace(',', '')), "PERCENTAGE_POINTS"
        m_pct = re.search(num_pat + r'\s*(?:%|percent\b)', cleaned, re.IGNORECASE)
        if m_pct:
            return float(m_pct.group(1).replace(',', '')), "PERCENT"

        # 2. EUR/t, EUR/tCO2, USD/t, etc.
        m_eur_t = re.search(r'(€|EUR|USD|\$)\s*' + num_pat + r'\s*(?:\/|\s*per\s*)(?:t(?:onne)?s?|tCO2|barrel|bbl|MWh|kg)', cleaned, re.IGNORECASE)
        if not m_eur_t:
            m_eur_t = re.search(num_pat + r'\s*(€|EUR|USD|\$)\s*(?:\/|\s*per\s*)(?:t(?:onne)?s?|tCO2|barrel|bbl|MWh|kg)', cleaned, re.IGNORECASE)
        if m_eur_t:
            curr = m_eur_t.group(1) if m_eur_t.group(1) in ('€', 'EUR', 'USD', '$') else m_eur_t.group(2)
            num_str = m_eur_t.group(2) if m_eur_t.group(1) in ('€', 'EUR', 'USD', '$') else m_eur_t.group(1)
            is_usd = curr in ('USD', '$')
            return float(num_str.replace(',', '')), ("USD/UNIT" if is_usd else "EUR/UNIT")

        # 3. Billions (EUR or USD)
        m_bn = re.search(r'(€|EUR|USD|\$)\s*' + num_pat + r'\s*(?:billion|bn|B)\b', cleaned, re.IGNORECASE)
        if not m_bn:
            m_bn = re.search(num_pat + r'\s*(?:billion|bn|B)\s*(€|EUR|euros?|USD|\$|dollars?)?\b', cleaned, re.IGNORECASE)
        if m_bn:
            txt = m_bn.group(0).upper()
            is_usd = 'USD' in txt or '$' in txt or 'DOLLAR' in txt
            num_m = re.search(num_pat, m_bn.group(0))
            return float(num_m.group(1).replace(',', '')) * 1000.0, ("USD_MILLION" if is_usd else "EUR_MILLION")

        # 4. Millions (EUR or USD)
        m_mn = re.search(r'(€|EUR|USD|\$)\s*' + num_pat + r'\s*(?:million|mn|m|M)\b', cleaned, re.IGNORECASE)
        if not m_mn:
            m_mn = re.search(num_pat + r'\s*(?:million|mn|m|M)\s*(€|EUR|euros?|USD|\$|dollars?)?\b', cleaned, re.IGNORECASE)
        if m_mn:
            txt = m_mn.group(0).upper()
            is_usd = 'USD' in txt or '$' in txt or 'DOLLAR' in txt
            num_m = re.search(num_pat, m_mn.group(0))
            return float(num_m.group(1).replace(',', '')), ("USD_MILLION" if is_usd else "EUR_MILLION")

        # 5. Generic EUR / USD
        m_usd = re.search(r'(?:USD|\$)\s*' + num_pat, cleaned, re.IGNORECASE) or re.search(num_pat + r'\s*(?:USD|\$|dollars?)', cleaned, re.IGNORECASE)
        if m_usd:
            return float(re.search(num_pat, m_usd.group(0)).group(1).replace(',', '')), "USD"
        m_eur = re.search(r'(?:€|EUR)\s*' + num_pat, cleaned, re.IGNORECASE) or re.search(num_pat + r'\s*(?:€|EUR|euros?)', cleaned, re.IGNORECASE)
        if m_eur:
            return float(re.search(num_pat, m_eur.group(0)).group(1).replace(',', '')), "EUR"

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
        if not HAS_GOOGLE_API or self.offline or not os.getenv("GOOGLE_CLOUD_PROJECT"):
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
        # If LLM extraction requested and available, attempt it first
        if self.use_llm and not self.offline:
            llm_evt = self.extract_with_llm(
                title=title,
                summary=summary,
                pub_date=pub_date,
                source_url=source_url,
                source_name=source_name,
                event_id=event_id or self._article_identity(source_url, title, pub_date)
            )
            if llm_evt:
                return llm_evt

        # 1. Identify economic actors
        actors = self.extract_actors(f"{title} {summary}")

        # 2. Extract actions and reactions
        actions = self.extract_actions(title, summary, actors)
        reactions = self.extract_reactions(title, summary, actors)

        if not event_id:
            if actions:
                act = actions[0]
                event_id = self._event_identity(act.actor, act.action_type, act.target, pub_date, act.magnitude)
            else:
                event_id = f"EVENT_{hashlib.sha256((title.strip() + '|' + pub_date).encode('utf-8')).hexdigest()[:32]}"

        # 3. Formulate root-cause Receptor Signal
        receptor_signal = None
        if actions:
            act = actions[0]
            receptor_signal = f"{act.action_type}_{act.target}".upper().replace(" ", "_")[:64]

        # 4. Target Asset
        target_asset = reactions[0].affected_entity if reactions else None

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
                event_id = self._article_identity(source_url, title, parsed_date)

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
                receptor_signal=receptor_signal,
                target_asset=target_asset,
                relevance_score=float(item.get("relevance_score", 1.0))
            )

        raise TypeError(f"Cannot normalize event of type {type(item)}")

    def extract_document_events(self, document: SourceDocument, text: str) -> List[Fact]:
        """Convert one archived source document into evidence-backed event facts."""
        title = text.splitlines()[0][:300] if text.strip() else ""
        pub_date = document.published_at.isoformat() if document.published_at else ""
        event = self.extract_structured_event(
            title=title,
            summary=text,
            source_url=document.canonical_url,
            source_name=document.publisher,
            pub_date=pub_date,
        )
        facts: List[Fact] = []
        doc_available = document.available_at or document.published_at or document.recorded_at
        article_id = self._article_identity(document.canonical_url, title, pub_date, document.upstream_id or "")

        # Check for stated occurrence date in text
        m_occ = re.search(r'\b(?:on|dated|as of|effective)\s+(\d{4}-\d{2}-\d{2})\b', text, re.IGNORECASE)
        if m_occ:
            valid_from = f"{m_occ.group(1)}T00:00:00+00:00"
        else:
            valid_from = event.timestamp or None

        if not hasattr(self, "_known_news_facts"):
            self._known_news_facts = {}

        retractions: List[Fact] = []

        for action in event.actions:
            act_event_id = self._event_identity(action.actor, action.action_type, action.target, event.timestamp, action.magnitude)
            f_action = Fact(
                subject_id=act_event_id,
                predicate="ABOUT",
                object_id=action.target or None,
                payload_json=json.dumps({
                    "kind": "action",
                    "event_id": act_event_id,
                    "article_id": article_id,
                    "canonical_url": document.canonical_url,
                    "document_version_id": document.version_id,
                    "action": action.model_dump(),
                    "evidence_text": text
                }, sort_keys=True),
                available_at=doc_available,
                valid_from=valid_from,
                source_id=document.source_id,
                quality_state="needs_review" if not action.description else "accepted",
                batch_id=document.batch_id,
                data_origin=document.data_origin,
            )
            old_f = self._known_news_facts.get(act_event_id)
            if old_f and old_f.fact_version_id != f_action.fact_version_id:
                retractions.append(old_f.model_copy(update={
                    "is_retracted": True,
                    "retraction_reason": "Superseded by news correction",
                    "available_at": doc_available,
                    "batch_id": document.batch_id,
                }))
            self._known_news_facts[act_event_id] = f_action
            facts.append(f_action)

        for reaction in event.reactions:
            react_event_id = f"REACT_{hashlib.sha256((event.event_id + '|' + reaction.reaction_type + '|' + reaction.affected_entity).encode('utf-8')).hexdigest()[:32]}"
            f_react = Fact(
                subject_id=react_event_id,
                predicate="IMPACTS",
                object_id=reaction.affected_entity or None,
                payload_json=json.dumps({
                    "kind": "reaction",
                    "event_id": event.event_id,
                    "article_id": article_id,
                    "canonical_url": document.canonical_url,
                    "document_version_id": document.version_id,
                    "reaction": reaction.model_dump(),
                    "evidence_text": text
                }, sort_keys=True),
                available_at=doc_available,
                valid_from=valid_from,
                source_id=document.source_id,
                quality_state="accepted" if reaction.magnitude is not None or reaction.direction != "STABLE" else "needs_review",
                batch_id=document.batch_id,
                data_origin=document.data_origin,
            )
            old_r = self._known_news_facts.get(react_event_id)
            if old_r and old_r.fact_version_id != f_react.fact_version_id:
                retractions.append(old_r.model_copy(update={
                    "is_retracted": True,
                    "retraction_reason": "Superseded by news correction",
                    "available_at": doc_available,
                    "batch_id": document.batch_id,
                }))
            self._known_news_facts[react_event_id] = f_react
            facts.append(f_react)

        facts.extend(retractions)
        return facts

    def cluster_event_candidates(self, facts: List[Fact], cutoff: datetime) -> List[Fact]:
        """Deduplicate event candidates by stable event identity at a cutoff."""
        latest: Dict[str, Fact] = {}
        for fact in facts:
            available = fact.available_at or fact.recorded_at
            if available and available > cutoff:
                continue
            payload = json.loads(fact.payload_json or "{}")
            event_id = payload.get("event_id") or fact.subject_id
            kind = payload.get("kind", "")
            action_type = payload.get("action", {}).get("action_type", "") if isinstance(payload.get("action"), dict) else ""
            reaction_type = payload.get("reaction", {}).get("reaction_type", "") if isinstance(payload.get("reaction"), dict) else ""
            candidate_key = f"{event_id}:{fact.predicate}:{kind}:{action_type}:{reaction_type}:{fact.object_id or ''}"
            previous = latest.get(candidate_key)
            if previous is None:
                latest[candidate_key] = fact
            else:
                prev_time = previous.recorded_at or previous.available_at
                curr_time = fact.recorded_at or fact.available_at
                if curr_time and prev_time and curr_time > prev_time:
                    latest[candidate_key] = fact
        return list(latest.values())

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
        Fetch live RSS feeds. Offline or empty sources return no events.
        """
        # If scraper was initialized with offline=True, do not make any network requests
        if self.offline:
            return []

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
            logger.warning("No live news articles available; no events generated.")
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
        Stores only real embeddings; unavailable embeddings skip the optional vector projection.
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
                and bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
                and not getattr(self, "offline", False)
            )
            if can_use_google_api:
                try:
                    vector = get_embedding(embedding_text)
                except Exception:
                    vector = None

            if vector is None or len(vector) != 768:
                logger.warning("Skipping vector projection for %s: real embedding unavailable; source event remains in canonical storage/cache.", evt.event_id)
                continue

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
                if act.magnitude is None:
                    continue
                self.graph_store.add_state_record(NodeStateRecord(
                    node_id=act_node_id,
                    node_type=NodeType.ACTION,
                    timestamp=pub_date,
                    state_value=act.action_type,
                    metric_name="action_magnitude",
                    metric_value=float(act.magnitude),
                    delta=float(act.magnitude),
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
                if len(action_node_ids) == 1:
                    self.graph_store.add_edge(GraphEdge(
                        source_id=action_node_ids[0],
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
                if react.magnitude is None:
                    continue
                val = float(react.magnitude)
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
    parser.add_argument("--offline", action="store_true", help="Disable network access; returns no new events")
    parser.add_argument("--feeds", action="store_true", help="Fetch and process live RSS feeds")
    parser.add_argument("--db-path", type=str, default=None, help="LanceDB database directory path (defaults to centralized settings)")
    parser.add_argument("--output", type=str, default="data/raw/news/scraped_news.json", help="Output JSON path")
    parser.add_argument("--verify", action="store_true", help="Verify LanceDB news table records")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

    is_offline = bool(args.offline)
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

    # Default / Feeds mode
    print("\n[SCRAPER] Running News Scraper Pipeline...")
    events = scraper.scrape_all_feeds(save_to_db=True)
    print(f"[OK] Pipeline complete: Processed {len(events)} events.")
    print(f"[CACHE] Cached JSON at: {args.output}")
    print(f"[LANCEDB] Persisted into LanceDB: {args.db_path} (table 'news_items')")


if __name__ == "__main__":
    main()
