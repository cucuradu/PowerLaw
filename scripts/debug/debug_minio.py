from src.utils.minio_client import get_minio_client, BUCKET_LAWS

def inspect_minio():
    client = get_minio_client()
    print(f"📦 Inspecting bucket: {BUCKET_LAWS}")
    
    objects = client.list_objects(BUCKET_LAWS)
    count = 0
    folders = set()
    root_files = 0
    
    for obj in objects:
        count += 1
        name = obj['name']
        if "/" in name:
            folders.add(name.split("/")[0])
        else:
            root_files += 1
            if root_files < 5:
                print(f"  Root file (sample): {name}")
                
    print(f"\nTotal Objects: {count}")
    print(f"Root Files: {root_files}")
    print(f"Folders found: {folders}")

if __name__ == "__main__":
    inspect_minio()
