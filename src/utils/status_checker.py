#!/usr/bin/env python3
"""
Law Status Checker - EUR-Lex API
Checks if laws are still active, amended, or repealed.
Updates LanceDB with current status.

Strategy:
1. Fetch latest law metadata from EUR-Lex (RSS or API)
2. Compare with existing laws in LanceDB
3. Update status: ACTIVE → REPEALED, ACTIVE → AMENDED
4. Flag new laws for ingestion
"""

import os
import sys
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv
import lancedb
from tqdm import tqdm

sys.path.append(str(Path(__file__).parent.parent.parent))
load_dotenv()

try:
    from src.config import get_settings
    DEFAULT_LANCEDB = str(get_settings().lancedb_path)
except Exception:
    DEFAULT_LANCEDB = "./data/production/lancedb"

LANCEDB_PATH = Path(os.getenv("LANCEDB_PATH", DEFAULT_LANCEDB))

# EUR-Lex RSS Feeds for recent changes
EURLEX_FEEDS = {
    "energy": "https://eur-lex.europa.eu/content/rss/legislation-by-subject.en.xml?subject=12.04.00",
    "environment": "https://eur-lex.europa.eu/content/rss/legislation-by-subject.en.xml?subject=15.10.00",
    "trade": "https://eur-lex.europa.eu/content/rss/legislation-by-subject.en.xml?subject=11.00.00",
}


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_existing_laws() -> Dict[str, Dict]:
    """Get existing laws from LanceDB"""
    db = lancedb.connect(str(LANCEDB_PATH))
    
    if "logic_rules" not in db.table_names():
        log("⚠️ No logic_rules table found")
        return {}
    
    df = db.open_table("logic_rules").to_pandas()
    
    # Group by law_id
    laws = {}
    for law_id in df["law_id"].unique():
        law_df = df[df["law_id"] == law_id]
        laws[law_id] = {
            "rule_count": len(law_df),
            "valid_from": law_df["valid_from"].iloc[0],
            "valid_to": law_df["valid_to"].iloc[0],
        }
    
    return laws


def fetch_eurlex_rss(feed_url: str) -> List[Dict]:
    """Fetch recent laws from EUR-Lex RSS"""
    try:
        import feedparser
        
        headers = {"User-Agent": "PolicyPilot/1.0 (research)"}
        response = requests.get(feed_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            log(f"⚠️ RSS fetch failed: {response.status_code}")
            return []
        
        feed = feedparser.parse(response.content)
        
        entries = []
        for entry in feed.entries:
            # Extract CELEX ID from link
            celex = None
            link = entry.get("link", "")
            if "CELEX:" in link:
                celex = link.split("CELEX:")[-1].split("&")[0]
            
            entries.append({
                "title": entry.get("title", ""),
                "link": link,
                "celex": celex,
                "published": entry.get("published", ""),
            })
        
        return entries
        
    except Exception as e:
        log(f"❌ RSS error: {e}")
        return []


def check_law_status(celex_id: str) -> Optional[str]:
    """Check if a specific law is still in force via EUR-Lex"""
    # EUR-Lex search API (basic)
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex_id}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 404:
            return "NOT_FOUND"
        
        # Check for repeal indicators in page
        text = response.text.lower()
        if "repealed" in text or "no longer in force" in text:
            return "REPEALED"
        elif "amended" in text:
            return "AMENDED"
        else:
            return "ACTIVE"
            
    except Exception as e:
        log(f"⚠️ Status check failed for {celex_id}: {e}")
        return None


def run():
    """Main check loop"""
    log("🔍 Law Status Checker Started")
    
    # Get existing laws
    existing_laws = get_existing_laws()
    log(f"📚 Found {len(existing_laws)} laws in LanceDB")
    
    # Check recent EUR-Lex RSS
    log("📡 Checking EUR-Lex RSS feeds...")
    new_laws = []
    for name, url in EURLEX_FEEDS.items():
        entries = fetch_eurlex_rss(url)
        log(f"   {name}: {len(entries)} entries")
        for entry in entries:
            if entry["celex"] and entry["celex"] not in existing_laws:
                new_laws.append(entry)
    
    if new_laws:
        log(f"\n🆕 Found {len(new_laws)} potentially new laws:")
        for law in new_laws[:5]:
            log(f"   • {law['celex']}: {law['title'][:60]}...")
    
    # TODO: Batch status check and LanceDB update
    # For MVP, just report findings
    
    log("\n✅ Check complete")


if __name__ == "__main__":
    run()
