import requests
import shutil
import json
from pathlib import Path
from datetime import datetime
from src.utils.minio_client import get_minio_client, BUCKET_LAWS, upload_law_pdf

# Configuration
ARCHIVED_DIR = Path("data/raw/laws/archived")
IN_FORCE_DIR = Path("data/raw/laws/in_force")
SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"

def get_end_dates(celex_ids):
    """Batch fetch end dates from SPARQL."""
    if not celex_ids: 
        return {}
        
    # Format for SPARQL VALUES
    values_str = " ".join([f'"{c}"' for c in celex_ids])
    
    query = f"""
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    SELECT ?celex ?end_date WHERE {{
        VALUES ?celex {{ {values_str} }}
        ?work cdm:resource_legal_id_celex ?celex .
        OPTIONAL {{ ?work cdm:resource_legal_date_end-of-validity ?end_date . }}
    }}
    """
    
    try:
        r = requests.get(SPARQL_ENDPOINT, params={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=30)
        data = r.json()
        results = {}
        for b in data.get("results", {}).get("bindings", []):
            celex = b["celex"]["value"]
            end_date = b.get("end_date", {}).get("value")
            results[celex] = end_date
        return results
    except Exception as e:
        print(f"❌ SPARQL Batch Error: {e}")
        return {}

def audit_and_fix():
    print("🕵️‍♂️ Starting Archive Audit...")
    
    # 1. List local archived files
    files = list(ARCHIVED_DIR.glob("*.pdf"))
    celex_map = {f.stem: f for f in files}
    all_celex = list(celex_map.keys())
    
    print(f"📂 Found {len(all_celex)} files in local 'archived' folder.")
    if not all_celex:
        return

    # 2. Query in batches
    batch_size = 50
    mistakes = []
    
    for i in range(0, len(all_celex), batch_size):
        batch = all_celex[i : i + batch_size]
        print(f"   Checking batch {i}-{i+len(batch)}...")
        
        dates = get_end_dates(batch)
        today = datetime.now().strftime("%Y-%m-%d")
        
        for celex in batch:
            end_date = dates.get(celex)
            
            # Logic: If end_date is None or Future -> Should be IN FORCE
            is_active = False
            if not end_date:
                is_active = True # No end date = Active
            elif end_date >= today:
                is_active = True # Future expiration = Active
            
            if is_active:
                mistakes.append((celex, end_date))

    print(f"\n🚨 Found {len(mistakes)} wrongly archived laws!")
    
    # 3. Fix them
    if mistakes:
        client = get_minio_client()
        print("🔧 Auto-fixing (Moving to In Force)...")
        
        for celex, edate in mistakes:
            print(f"   👉 Fixing {celex} (End Date: {edate or 'None/Active'})")
            
            # Paths
            old_pdf = ARCHIVED_DIR / f"{celex}.pdf"
            old_json = ARCHIVED_DIR / f"{celex}.json"
            new_pdf = IN_FORCE_DIR / f"{celex}.pdf"
            new_json = IN_FORCE_DIR / f"{celex}.json"
            
            # Move Local PDF
            if old_pdf.exists():
                shutil.move(str(old_pdf), str(new_pdf))
            
            # Move/Update Local JSON
            if old_json.exists():
                with open(old_json, 'r') as f:
                    meta = json.load(f)
                meta['status'] = "in_force"
                with open(new_json, 'w') as f:
                    json.dump(meta, f, indent=2)
                old_json.unlink() # Delete old
                
            # Fix MinIO
            minio_wrong = f"archived/{celex}.pdf"
            minio_right = f"in_force/{celex}.pdf"
            
            try:
                # Delete Wrong
                client.delete_object(BUCKET_LAWS, minio_wrong)
                # Upload Right
                if new_pdf.exists():
                    upload_law_pdf(new_pdf)
            except Exception as e:
                print(f"     ⚠️ MinIO Error: {e}")
                
    print("\n✅ Audit and Repair Complete.")

if __name__ == "__main__":
    audit_and_fix()
