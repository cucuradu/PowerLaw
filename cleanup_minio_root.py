from src.utils.minio_client import get_minio_client, BUCKET_LAWS

def cleanup_root():
    client = get_minio_client()
    print(f"🧹 Cleaning root files in: {BUCKET_LAWS}")
    
    objects = client.list_objects(BUCKET_LAWS)
    deleted = 0
    
    for obj in objects:
        name = obj['name']
        if "/" not in name:
            # It's a root file, delete it
            client.delete_object(BUCKET_LAWS, name)
            deleted += 1
            
    print(f"✅ Deleted {deleted} root files. Only folders should remain.")

if __name__ == "__main__":
    cleanup_root()
