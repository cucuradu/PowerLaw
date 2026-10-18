from src.utils.minio_client import get_minio_client, BUCKET_LAWS

client = get_minio_client()
print(f"Listing objects in {BUCKET_LAWS}...")
objects = client.list_objects(BUCKET_LAWS)
print(f"Found {len(objects)} objects. Deleting...")

for obj in objects:
    client.delete_object(BUCKET_LAWS, obj['name'])

print("✅ Bucket cleared.")
