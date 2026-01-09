import requests
import json
from pathlib import Path
from src.utils.minio_client import upload_law_pdf

# Configuration
EURLEX_BASE = "https://eur-lex.europa.eu"
BASE_DIR = Path("data/raw/laws")
BASE_DIR.mkdir(parents=True, exist_ok=True)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Exact logic from scraper, but targeted
def restore_law(celex_id):
    print(f"🚑 Restoring {celex_id}...")
    pdf_path = BASE_DIR / f"{celex_id}.pdf"
    
    # 1. Download
    pdf_url = f"{EURLEX_BASE}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex_id}"
    try:
        r = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code == 200 and len(r.content) > 1000 and r.content.startswith(b'%PDF'):
            with open(pdf_path, 'wb') as f:
                f.write(r.content)
            print(f"   ✅ Downloaded local: {pdf_path}")
            
            # 2. Metadata (dummy/minimal for now, just to have the file)
            # Or fetch SPARQL? Let's just create minimal so pipeline works.
            meta = {
                "celex_id": celex_id,
                "status": "in_force", # assumption for restoration
                "restored": True
            }
            with open(BASE_DIR / f"{celex_id}.json", 'w') as f:
                json.dump(meta, f, indent=2)
                
            # 3. Upload to MinIO
            uri = upload_law_pdf(pdf_path)
            print(f"   ✅ Uploaded to MinIO: {uri}")
            
        else:
            print(f"   ❌ Failed to download (Status {r.status_code})")
    except Exception as e:
        print(f"   ❌ Error: {e}")

if __name__ == "__main__":
    targets = ["32018R0093", "32017R0852"]
    for t in targets:
        restore_law(t)
