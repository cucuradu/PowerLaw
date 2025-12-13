#!/usr/bin/env python3
"""
EUR-Lex Law Downloader - Direct CELEX Approach
Downloads specific EU regulations by CELEX ID

This is a simplified but reliable approach for downloading known EU laws
related to Energy, Steel, and Carbon Emissions.

Author: Python Web Scraping Engineer
Date: 2025-12-01
"""

import os
import time
import json
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
LAW_DIR = Path("data/raw/laws")
LANGUAGE = "en"
REQUEST_DELAY = 2
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Known relevant EU regulations (CELEX IDs)
# These are real, important EU laws on carbon, energy, and steel
TARGET_LAWS = [
    {
        "celex_id": "32023R0956",
        "title": "Carbon Border Adjustment Mechanism (CBAM)",
        "date": "2023-05-10"
    },
    {
        "celex_id": "32023D1534",
        "title": "Emissions Trading System (ETS) Reform 2023",
        "date": "2023-07-26"
    },
    {
        "celex_id": "32018L2002",
        "title": "Energy Efficiency Directive",
        "date": "2018-12-11"
    },
    {
        "celex_id": "32021R1119",
        "title": "European Climate Law",
        "date": "2021-06-30"
    },
    {
        "celex_id": "32022L2464",
        "title": "Corporate Sustainability Reporting Directive (CSRD)",
        "date": "2022-12-14"
    }
]

EURLEX_BASE = "https://eur-lex.europa.eu"


def log(message: str) -> None:
    """Simple console logger with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def download_law(celex_id: str, title: str, date: str) -> bool:
    """
    Download law PDF directly using CELEX ID
    
    Args:
        celex_id: CELEX ID
        title: Document title
        date: Adoption date
    
    Returns:
        True if successful
    """
    pdf_path = LAW_DIR / f"{celex_id}.pdf"
    json_path = LAW_DIR / f"{celex_id}.json"
    
    # Check if already exists
    if pdf_path.exists():
        log(f"  ⏭️  {celex_id}.pdf already exists, skipping")
        return True
    
    # Direct PDF URL construction
    pdf_url = f"{EURLEX_BASE}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex_id}"
    
    log(f"  📥 Downloading {celex_id}...")
    log(f"     {title}")
    
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf"
    }
    
    try:
        response = requests.get(pdf_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Verify PDF content
        if len(response.content) < 1000:
            log(f"  ❌ Response too small, might be an error page")
            return False
        
        # Save PDF
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        
        log(f"  ✅ Saved {celex_id}.pdf ({len(response.content):,} bytes)")
        
        # Create metadata
        metadata = {
            "celex_id": celex_id,
            "title": title,
            "adoption_date": date,
            "language": LANGUAGE,
            "downloaded_at": datetime.now().isoformat(),
            "source_url": f"{EURLEX_BASE}/legal-content/EN/TXT/?uri=CELEX:{celex_id}"
        }
        
        with open(json_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        log(f"  ✅ Saved metadata: {celex_id}.json")
        return True
        
    except Exception as e:
        log(f"  ❌ Download failed: {e}")
        return False


def main():
    """Main entry point"""
    log("🚀 EUR-Lex Law Downloader (Direct CELEX Approach)")
    log(f"{'='*60}\n")
    
    # Ensure output directory exists
    LAW_DIR.mkdir(parents=True, exist_ok=True)
    
    log(f"Output directory: {LAW_DIR}")
    log(f"Language: {LANGUAGE.upper()}")
    log(f"Laws to download: {len(TARGET_LAWS)}")
    log(f"\n{'='*60}\n")
    
    downloaded = 0
    skipped = 0
    failed = 0
    
    for law in TARGET_LAWS:
        log(f"\n📄 {law['celex_id']}")
        
        success = download_law(law['celex_id'], law['title'], law['date'])
        
        if success:
            if (LAW_DIR / f"{law['celex_id']}.pdf").stat().st_mtime > time.time() - 60:
                downloaded += 1
            else:
                skipped += 1
        else:
            failed += 1
        
        # Politeness delay
        time.sleep(REQUEST_DELAY)
    
    # Summary
    log(f"\n{'='*60}")
    log("🎉 DOWNLOAD COMPLETE")
    log(f"{'='*60}")
    log(f"  Downloaded: {downloaded}")
    log(f"  Skipped (already exists): {skipped}")
    log(f"  Failed: {failed}")
    log(f"  Total: {len(TARGET_LAWS)}")
    
    if downloaded > 0 or skipped > 0:
        log(f"\n💡 Next step: Run 'python src/ingestion/ingest_laws.py' to process PDFs")


if __name__ == "__main__":
    main()
