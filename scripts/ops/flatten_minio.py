from src.utils.minio_client import get_minio_client, BUCKET_LAWS

def flatten_minio():
    client = get_minio_client()
    print(f"🚜 Flattening bucket: {BUCKET_LAWS}")
    
    objects = client.list_objects(BUCKET_LAWS)
    moved = 0
    
    from minio.commonconfig import CopySource
    
    for obj in objects:
        name = obj['name']
        if "/" in name:
            # It's in a folder, move it to root
            folder, filename = name.split("/", 1)
            new_name = filename
            
            # Copy
            print(f"   Moving {name} -> {new_name}")
            client.client.copy_object(
                BUCKET_LAWS,
                new_name,
                CopySource(BUCKET_LAWS, name)
            )
            # Delete old
            client.delete_object(BUCKET_LAWS, name)
            moved += 1
            
    print(f"✅ Flattened {moved} objects.")

if __name__ == "__main__":
    flatten_minio()
