from src.utils.minio_client import get_minio_client, BUCKET_LAWS

def verify_structure():
    client = get_minio_client()
    print(f"🔍 Verifying contents of bucket: {BUCKET_LAWS}")
    
    # Check Archived
    print("\n📂 Checking 'archived/' folder:")
    archived_objs = client.list_objects(BUCKET_LAWS, prefix="archived/")
    a_count = 0
    for obj in archived_objs:
        a_count += 1
        if a_count <= 5:
            print(f"   - {obj['name']}")
    print(f"   (Total Archived: {a_count})")
    
    # Check In Force
    print("\n📂 Checking 'in_force/' folder:")
    in_force_objs = client.list_objects(BUCKET_LAWS, prefix="in_force/")
    i_count = 0
    for obj in in_force_objs:
        i_count += 1
        if i_count <= 5:
            print(f"   - {obj['name']}")
    print(f"   (Total In Force: {i_count})")

    # Check for specific user file if mentioned (32025R2067)
    target = "32025R2067.pdf"
    print(f"\n🕵️‍♂️ Searching for specific file: {target}")
    found = False
    
    all_objs = client.list_objects(BUCKET_LAWS)
    for obj in all_objs:
        if target in obj['name']:
            print(f"   ✅ FOUND: {obj['name']}")
            found = True
            
    if not found:
        print(f"   ❌ Not found yet (Scraper might still be downloading 2025...)")

if __name__ == "__main__":
    verify_structure()
