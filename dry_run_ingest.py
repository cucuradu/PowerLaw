from src.main import LawIngestionPipeline
from src.schemas.rule_schema import GNNRule
import json

def test_extraction():
    print("🚀 Initializing Pipeline...")
    pipeline = LawIngestionPipeline()
    
    # Sample Text: Sanctions
    sample_text = """
    Article 3j
    1. It shall be prohibited to purchase, import, or transfer, directly or indirectly, 
    coal and other solid fossil fuels, as listed in Annex XXII, if they originate in Russia 
    or are exported from Russia.
    2. The prohibition in paragraph 1 shall apply from 10 August 2022.
    """
    
    chunk = {
        "id": "Test_Sanctions_Art3j",
        "text": sample_text,
        "title": "Regulation (EU) 833/2014 Amendment"
    }
    
    print("\n📝 Extracting Rule from Sample Text...")
    rule = pipeline.process_chunk(chunk, "32014R0833")
    
    if rule:
        print("\n✅ Extraction Successful!")
        print(rule.model_dump_json(indent=2))
        
        # specific checks
        if rule.mechanism.action_type == "CONSTRAINT" and rule.mechanism.operator == "MAX_LIMIT":
            print("\nResult: CORRECT (Identified as Ban/Constraint)")
        else:
            print(f"\nResult: Review Needed (Type: {rule.mechanism.action_type})")
    else:
        print("\n❌ Extraction Failed (None returned).")

if __name__ == "__main__":
    test_extraction()
