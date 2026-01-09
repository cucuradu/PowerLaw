import logging
import sys
from pathlib import Path
from src.main import LawIngestionPipeline

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

def test_single_multimodal():
    pipeline = LawIngestionPipeline()
    law_id = "31996R2100"
    pdf_path = Path(f"data/raw/laws/{law_id}.pdf")
    
    print(f"🧪 Testing Multimodal Extraction for {law_id}...")
    
    
    # Force call extract_rules_multimodal directly
    # Pipeline already initialized
    
    try:
        rules = pipeline.extract_rules_multimodal(pdf_path, law_id)
        print(f"\n✅ Extracted {len(rules)} rules:")
        for r in rules:
            print(f"- {r.summary[:80]}...")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_single_multimodal()
