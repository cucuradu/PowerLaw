from src.scrapers.eurlex_bulk import execute_sparql, filter_by_keywords

def inspect_rejections():
    print("🔍 Analyzing Rejections for 2024...")
    laws = execute_sparql(2024)
    print(f"Total Laws in 2024: {len(laws)}")
    
    accepted = filter_by_keywords(laws)
    accepted_ids = {l['celex_id'] for l in accepted}
    
    rejected = [l for l in laws if l['celex_id'] not in accepted_ids]
    
    print(f"Accepted: {len(accepted)}")
    print(f"Rejected: {len(rejected)}")
    
    print("\n--- SAMPLE REJECTED LAWS ---")
    # specific common topics to ignore?
    # We want to see if we missed anything substantial.
    for i, law in enumerate(rejected[:30]):
        topics = law.get('topics', 'None')
        print(f"❌ [{law['celex_id']}] {law['title'][:80]}...")
        # print first 3 topics
        print(f"    Topics: {topics[:100]}...")

if __name__ == "__main__":
    inspect_rejections()
