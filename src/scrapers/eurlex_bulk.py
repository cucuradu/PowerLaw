#!/usr/bin/env python3
"""
EUR-Lex Bulk Downloader - SPARQL Edition
Uses the EU Publications Office SPARQL endpoint for reliable bulk extraction.
Downloads Regulations & Directives (2015-2025).
"""

import os
import time
import json
import requests
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from src.utils.minio_client import upload_law_pdf

# ... (existing imports)

def check_status_and_download(law: Dict) -> bool:
    """
    1. Check status (in force?) via scraping simple metadata
    2. Download PDF to appropriate folder
    3. Upload to MinIO
    """
    celex_id = law['celex_id']
    
    target_dir = IN_FORCE_DIR # Default
    is_in_force = True
    
    # ... (existing status check)
    url = f"{EURLEX_BASE}/legal-content/EN/TXT/?uri=CELEX:{celex_id}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if "no longer in force" in r.text.lower() or "repealed" in r.text.lower():
            target_dir = ARCHIVED_DIR
            is_in_force = False
    except:
        pass 
        
    pdf_path = target_dir / f"{celex_id}.pdf"
    
    # Download if not exists
    if not pdf_path.exists():
        pdf_url = f"{EURLEX_BASE}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex_id}"
        try:
            r = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000 and r.content.startswith(b'%PDF'):
                with open(pdf_path, 'wb') as f:
                    f.write(r.content)
                
                # Metadata
                with open(target_dir / f"{celex_id}.json", 'w') as f:
                    law['status'] = "in_force" if is_in_force else "archived"
                    json.dump(law, f, indent=2)
            else:
                return False
        except Exception as e:
            log(f"Failed download {celex_id}: {e}")
            return False

    # UPLOAD TO MINIO (Always ensure it's there)
    try:
        upload_law_pdf(pdf_path)
        # log(f"   Stored in MinIO: {celex_id}") 
    except Exception as e:
        log(f"   MinIO Upload Failed {celex_id}: {e}")
        
    return True
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from tqdm import tqdm

# Configuration
BASE_DIR = Path("data/raw/laws")
IN_FORCE_DIR = BASE_DIR / "in_force"
ARCHIVED_DIR = BASE_DIR / "archived"
REQUEST_DELAY = 1.0 
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Endpoints
SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
EURLEX_BASE = "https://eur-lex.europa.eu"

def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def ensure_directories():
    IN_FORCE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVED_DIR.mkdir(parents=True, exist_ok=True)

def execute_sparql(year: int) -> List[Dict]:
    """Execute SPARQL query to get laws and their EuroVoc topics."""
    query = f"""
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    
    SELECT DISTINCT ?celex ?title ?date ?end_date (GROUP_CONCAT(?eurovoc_uri;separator="|") as ?topics)
    WHERE {{
      ?work cdm:resource_legal_id_celex ?celex .
      ?work cdm:work_date_document ?date .
      ?work cdm:resource_legal_type ?type .
      
      FILTER (?date >= "{year}-01-01"^^xsd:date && ?date <= "{year}-12-31"^^xsd:date)
      FILTER (REGEX(?celex, "^3[0-9]{{4}}[RL][0-9]{{4}}"))
      
      OPTIONAL {{ ?work cdm:work_is_about_concept_eurovoc ?eurovoc_uri . }}
      OPTIONAL {{ ?work cdm:resource_legal_date_end-of-validity ?end_date . }}
      
      # Optional Title (fallback)
      OPTIONAL {{ ?work cdm:work_title ?title . FILTER(LANG(?title) = "en") }}
    }}
    GROUP BY ?celex ?title ?date ?end_date
    LIMIT 20000
    """
    
    headers = {"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT}
    try:
        response = requests.get(SPARQL_ENDPOINT, params={"query": query}, headers=headers, timeout=60)
        data = response.json()
        results = []
        for b in data.get('results', {}).get('bindings', []):
            end_date = b.get('end_date', {}).get('value')
            results.append({
                "celex_id": b['celex']['value'],
                "title": b.get('title', {}).get('value', f"EU Law {b['celex']['value']}"),
                "date": b['date']['value'],
                "year": year,
                "end_date": end_date,
                "topics": b.get('topics', {}).get('value', "")
            })
        return results
    except Exception as e:
        log(f"❌ SPARQL error: {e}")
        return []

def filter_by_keywords(laws: List[Dict]) -> List[Dict]:
    """
    Filter laws by RELEVANT EuroVoc domains.
    We check if the law's topics match our target list.
    """
    # Topics to filter by (EuroVoc IDs)
    # Enlarged to include Environment, Climate, Health as per user interest in missed laws.
    ENVIRONMENT_TOPICS = [
        "2825", # environment
        "5463", # climate change policy
        "5315", # waste management
        "862",  # pollution control
        "5794", # nature reserve
        "3558", # biodiversity
        "763",  # pollution
        "2807", # environmental policy
    ]
    
    # New domains requested: Taxes, Economy-altering changes
    EXPANDED_TOPICS = [
        # Consumer Protection
        "2836", # consumer protection
        
        # Social Policy / Labor
        "2517", # social policy
        "3346", # European social policy
        
        # Customs / Tariffs
        "3156", # customs regulations
        "4380", # customs tariff
        "4600", # customs union
        "4031", # trade policy (already in base, but explicit here)
        
        # Digital Economy
        "7219", # digital technology
        "c_6f7bbe63", # digital single market
        "4404", # data protection (inferred)
        
        # Transport (Logistics costs)
        "2457", # common transport policy
        "2494", # transport policy
        "4509", # transport law
    ]
    
    # New: Sanctions & Foreign Affairs (Found during audit of 2024 rejections)
    SANCTIONS_TOPICS = [
        "3870", # international sanctions / restrictive measures
        "5876", # Russia (often target of sanctions)
        "5877", # Ukraine
        "5946", # restrictive measure (EU)
        "6930", # economic sanctions
        "2337", # Common Foreign and Security Policy (PESC)
        "3483", # International relations
        "3584", # Trade restriction
        # Trade Defense / Specific Restrictions
        "3732", # anti-dumping duty? / restriction
        "2173", # common customs tariff (related)
        "3191", # import policy
    ]
    
    # Agriculture & Fisheries (Huge economic sector)
    AGRI_TOPICS = [
        "2965", # agricultural product
        "2734", # animal feeding (agri input)
        "1802", # fisheries
        "2437", # fisheries policy
        "2879", # fishing agreement
        "2735", "1425", # food safety / additives
    ]
    
    TARGET_TOPICS = [
        "5806", "5207", "5211", "6821",  # Industrial structures, policy
        "2036", "4809",                  # Energy
        "3633", "3634",                  # Coal, Mining
        "5824", "6816",                  # Industrial hazards, tech
        "4308",                          # Competition
        "2441", "4031",                  # Trade
        "3236",                          # Internal market
        "6006", "6806", "6811", "6826",  # Agri-food / Production
        "2446", "3131",                  # Taxation
    ] + ENVIRONMENT_TOPICS + EXPANDED_TOPICS + SANCTIONS_TOPICS + AGRI_TOPICS
    
    filtered = []
    for law in laws:
        topics = law.get('topics', '')
        # Check if ANY target topic ID is present in the topics string
        if any(f"eurovoc.europa.eu/{tid}" in topics for tid in TARGET_TOPICS):
            filtered.append(law)
            
    return filtered

def check_status_and_download(law: Dict) -> bool:
    """
    1. Check status (in force?) using SPARQL end_date
    2. Download PDF to 'in_force' folder (Unified storage)
    3. Upload to MinIO
    """
    celex_id = law['celex_id']
    end_date = law.get('end_date')
    
    # Unified directory per user request (Flat structure)
    target_dir = BASE_DIR 
    is_in_force = True
    
    # Just update metadata status, don't move file
    if end_date:
        today = datetime.now().strftime("%Y-%m-%d")
        if end_date < today:
            is_in_force = False
            
    pdf_path = target_dir / f"{celex_id}.pdf"
    
    if pdf_path.exists():
        # UPLOAD TO MINIO even if exists locally, just to be sure it's in sync
        try:
             # Logic to infer path inside upload_law_pdf handles folders
             upload_law_pdf(pdf_path)
        except:
             pass
        return False # Skipped download, but synced
        
    # Download PDF
    pdf_url = f"{EURLEX_BASE}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex_id}"
    try:
        r = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code == 200 and len(r.content) > 1000 and r.content.startswith(b'%PDF'):
            with open(pdf_path, 'wb') as f:
                f.write(r.content)
            
            # Metadata
            with open(target_dir / f"{celex_id}.json", 'w') as f:
                law['status'] = "in_force" if is_in_force else "archived"
                json.dump(law, f, indent=2)
                
            return True
    except Exception as e:
        log(f"Failed download {celex_id}: {e}")
        
    return False

def main():
    log("🚀 EUR-Lex Scraper (Expanded Scope 1990-2025)")
    ensure_directories()
    
    # Step 3: Download PDFs (Parallel)
    log("\n📥 Phase 3: Downloading PDFs (Parallel 10x)...")
    
    total_downloaded = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    # Flatten list of all laws to download
    # Actually we do year by year to manage memory better or just one big pool?
    # Let's do year by year to keep the log structured
    pass # logic is below in main loop

    
    for year in range(1990, 2026):
        log(f"📅 Scanning {year}...")
        laws = execute_sparql(year)
        relevant = filter_by_keywords(laws)
        log(f"   Found {len(laws)} docs -> {len(relevant)} relevant")
        
        if not relevant:
            continue
            
        with ThreadPoolExecutor(max_workers=10) as executor:
            # map returns iterator, we want progress
            futures = [executor.submit(check_status_and_download, law) for law in relevant]
            
            for f in tqdm(as_completed(futures), total=len(relevant), desc=f"Downloading {year}"):
                if f.result():
                    total_downloaded += 1
            
    log(f"🎉 Complete. Total Downloaded: {total_downloaded}")

if __name__ == "__main__":
    main()
