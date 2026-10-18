from src.schemas.lancedb import LawChunk
from src.utils.api_wrapper import get_embedding
import lancedb
import shutil
import os

DB_PATH = "data/debug_lancedb_tmp"

def debug_schema():
    print("🔍 Inspecting LawChunk fields...")
    for name, field in LawChunk.model_fields.items():
        print(f" - {name}: {field.annotation}")

    if "gnn_rule_json" not in LawChunk.model_fields:
        print("❌ CRITICAL: gnn_rule_json NOT found in LawChunk model fields!")
        return
    else:
        print("✅ gnn_rule_json found in model fields.")

    # Clean
    if os.path.exists(DB_PATH):
        shutil.rmtree(DB_PATH)

    print(f"\n🛠 Creating DB at {DB_PATH}...")
    db = lancedb.connect(DB_PATH)
    
    print("🛠 Creating Table...")
    table = db.create_table("test_table", schema=LawChunk)
    
    print("start schema")
    print(table.schema)
    print("end schema")

    # Create dummy row
    print("\n📝 Creating Dummy Row...")
    row = LawChunk(
        vector=[0.0] * 768,
        law_id="TEST",
        chunk_id="TEST_1",
        summary="Test",
        minio_path="s3://test",
        source_url="http://test",
        gnn_rule_json="{}",
        rule_type="REGULATORY",
        jurisdiction="EU",
        valid_from="2024",
        valid_to="2025",
        citations=[],
        entity_types_json="[]",
        numeric_constraints_json="[]"
    )
    
    print("📝 Adding Row...")
    try:
        table.add([row])
        print("✅ Success! Row added.")
    except Exception as e:
        print(f"❌ Failed to add row: {e}")

if __name__ == "__main__":
    debug_schema()
