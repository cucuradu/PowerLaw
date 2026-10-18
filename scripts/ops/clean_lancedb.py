import lancedb
import os
import shutil
from dotenv import load_dotenv

load_dotenv()

LANCEDB_PATH = os.getenv("LANCEDB_PATH", "data/lancedb")

def clean_lancedb():
    print(f"🧹 Cleaning LanceDB at {LANCEDB_PATH}...")
    if os.path.exists(LANCEDB_PATH):
        try:
            # Connect
            db = lancedb.connect(LANCEDB_PATH)
            # List tables
            tables = db.table_names()
            print(f"Found tables: {tables}")
            
            # Drop 'structured_rules'
            print("✅ LanceDB cleaned.")
        except Exception as e:
            print(f"❌ Error cleaning LanceDB: {e}")
            
    # FORCE DELETE to ensure schema update
    if os.path.exists(LANCEDB_PATH):
        print(f"⚠️ Force deleting {LANCEDB_PATH} to ensure fresh schema...")
        shutil.rmtree(LANCEDB_PATH)
        
    # Clean pycache to avoid stale schemas
    os.system("find . -name '__pycache__' -type d -exec rm -rf {} +")
    
    print("Creating LanceDB directory...")
    os.makedirs(LANCEDB_PATH, exist_ok=True)

if __name__ == "__main__":
    clean_lancedb()
