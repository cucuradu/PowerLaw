#!/usr/bin/env python3
"""
EUR-Lex History Scraper (2018-2025)
Builds a comprehensive legal history for Green Energy & Industrial sectors.

Scope:
- Years: 2018-2025
- Directory Codes: 12 (Energy), 15 (Environment), 09 (Taxation), 08 (Competition), 02 (Customs)
- Types: Regulations (R), Directives (L)

Logic:
1. Search EUR-Lex by Directory Code + Year
2. Parse Search Results to get CELEX IDs
3. METADATA FIRST: Scrape HTML Notice Page for 'Date of entry into force'
4. Download PDF only if metadata is valid
5. Save JSON sidecar with dates

Author: Python Web Scraping Engineer
Date: 2025-12-01
"""

import os
import sys
import time
import json
import requests
import random
from pathlib import Path
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from datetime import datetime

# Configuration
YEARS = range(2015, 2026)  # 2015 to 2025
DIRECTORY_CODES = [
    "12",    # Energy
    "15",    # Environment
    "09",    # Taxation
    "08",    # Competition
    "02",    # Customs / Trade
    "13",    # Industrial Policy / Internal Market
    "10",    # Economic & Monetary Policy
]
ALLOWED_TYPES = ["R", "L", "D"]  # R=Regulation, L=Directive, D=Decision
BASE_URL = "https://eur-lex.europa.eu"
OUTPUT_DIR = Path("data/raw/laws")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def log(message: str) -> None:
    """Simple console logger with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

class EurLexScraper:
    """Scrapes EUR-Lex for legal history"""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
    
    def search_laws(self, year: int, code: str) -> List[str]:
        """
        Search for laws by Year and Directory Code.
        Returns a list of CELEX IDs.
        
        Note: EUR-Lex search pages are complex. 
        This uses a simplified query to the search endpoint.
        """
        log(f"🔎 Searching: Year {year}, Code {code}")
        
        # Construct search URL (Advanced Search simulation)
        # We filter by Domain=EU Law and Legislation, Year, and Directory Code
        # Note: This is a best-effort construction. EUR-Lex URLs are very dynamic.
        # A more robust way often involves iterating pages, but we'll try to get the first page of results.
        
        # Using the 'advanced search' parameters often results in a complex URL.
        # We will try a direct GET request to the search controller.
        
        params = {
            "scope": "EURLEX",
            "text": "",
            "lang": "en",
            "type": "quick",
            "DTS_DOM": "EU_LAW",
            "DTS_SUBDOM": "LEGISLATION",
            "DD_YEAR": str(year),
            "CC_1_CODED": code
        }
        
        try:
            response = self.session.get(f"{BASE_URL}/search.html", params=params, timeout=10)
            if response.status_code != 200:
                log(f"  ❌ Search failed: HTTP {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Extract CELEX IDs from results
            # EUR-Lex results usually have metadata blocks. 
            # We look for elements containing CELEX info.
            celex_ids = []
            
            # This selector is based on common EUR-Lex structure
            # It might need adjustment if EUR-Lex changes their DOM
            results = soup.find_all("div", class_="SearchResult")
            
            for result in results:
                # Find the CELEX label
                # Usually inside a definition list or specific span
                # We'll look for the link to the legal content which contains the CELEX
                title_link = result.find("a", class_="title")
                if title_link and "uri=CELEX:" in title_link.get("href", ""):
                    href = title_link["href"]
                    # Extract CELEX from URI
                    # Format: ./legal-content/EN/TXT/?uri=CELEX:32023R0956
                    try:
                        celex = href.split("CELEX:")[1].split("&")[0]
                        
                        # Filter by Type (R or L)
                        # CELEX structure: 3 (Sector) + Year + Type + Number
                        # e.g., 32023R0956 -> Type is 'R'
                        if len(celex) > 5:
                            law_type = celex[5] # 3 + 4 digits (year) = index 5
                            if law_type in ALLOWED_TYPES:
                                celex_ids.append(celex)
                    except IndexError:
                        continue
            
            log(f"  ✅ Found {len(celex_ids)} relevant laws (R/L)")
            return list(set(celex_ids)) # Deduplicate
            
        except Exception as e:
            log(f"  ❌ Search error: {e}")
            return []

    def get_metadata(self, celex: str) -> Optional[Dict[str, str]]:
        """
        Scrape HTML Notice Page for metadata.
        CRITICAL: Must extract 'Date of entry into force'.
        """
        url = f"{BASE_URL}/legal-content/EN/ALL/?uri=CELEX:{celex}"
        
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, "html.parser")
            metadata = {"celex": celex, "url": url}
            
            # Extract Title
            title_tag = soup.find("p", class_="doc-title")
            if title_tag:
                metadata["title"] = title_tag.get_text(strip=True)
            
            # Helper to find dates in the definition list
            # EUR-Lex uses <dl>, <dt>, <dd> for metadata
            def find_date(label_text):
                dt = soup.find("dt", string=lambda t: t and label_text in t)
                if dt:
                    dd = dt.find_next_sibling("dd")
                    if dd:
                        # Text often contains "Date of entry into force: 01/01/2023"
                        text = dd.get_text(strip=True)
                        # Extract date pattern DD/MM/YYYY
                        import re
                        match = re.search(r'\d{2}/\d{2}/\d{4}', text)
                        if match:
                            # Convert to YYYY-MM-DD
                            d = datetime.strptime(match.group(0), "%d/%m/%Y")
                            return d.strftime("%Y-%m-%d")
                return None

            # 1. Date of document
            metadata["date_document"] = find_date("Date of document")
            
            # 2. Date of entry into force (Priority)
            metadata["date_entry_force"] = find_date("Date of entry into force")
            
            # 3. End of validity
            metadata["date_end_validity"] = find_date("End of validity")
            
            # Do not invent entry into force from document date
            return metadata
            
        except Exception as e:
            log(f"  ⚠️  Metadata scrape failed for {celex}: {e}")
            return None

    def download_pdf(self, celex: str) -> bool:
        """Download PDF file"""
        pdf_path = self.output_dir / f"{celex}.pdf"
        url = f"{BASE_URL}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex}"
        
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200 and len(response.content) > 1000:
                with open(pdf_path, "wb") as f:
                    f.write(response.content)
                return True
            return False
        except Exception:
            return False

    def run(self):
        log("🚀 Starting EUR-Lex History Scraper")
        log(f"Scope: {YEARS.start}-{YEARS.stop-1}, Codes: {DIRECTORY_CODES}")
        
        total_downloaded = 0
        
        for year in YEARS:
            for code in DIRECTORY_CODES:
                # 1. Search
                celex_ids = self.search_laws(year, code)
                
                for celex in celex_ids:
                    pdf_path = self.output_dir / f"{celex}.pdf"
                    json_path = self.output_dir / f"{celex}.json"
                    
                    # Skip if exists
                    if pdf_path.exists() and json_path.exists():
                        continue
                    
                    log(f"📄 Processing {celex}...")
                    
                    # 2. Metadata First
                    metadata = self.get_metadata(celex)
                    
                    if not metadata:
                        log(f"  ❌ No metadata found. Skipping download.")
                        continue
                        
                    if not metadata.get("date_entry_force"):
                        log(f"  ⚠️  No entry date found. Skipping.")
                        continue
                    
                    # 3. Download PDF
                    if self.download_pdf(celex):
                        # 4. Save Metadata
                        with open(json_path, "w") as f:
                            json.dump(metadata, f, indent=2)
                        
                        log(f"  ✅ Downloaded {celex} (Entry: {metadata['date_entry_force']})")
                        total_downloaded += 1
                    else:
                        log(f"  ❌ PDF download failed")
                    
                    # Politeness delay
                    time.sleep(2)
                
                # Delay between searches
                time.sleep(2)
        
        log(f"\n✨ Scraper finished. Total new documents: {total_downloaded}")

if __name__ == "__main__":
    scraper = EurLexScraper(OUTPUT_DIR)
    scraper.run()
