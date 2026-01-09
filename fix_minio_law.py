from src.utils.minio_client import get_minio_client, BUCKET_LAWS, upload_law_pdf
from pathlib import Path

def fix_minio():
    client = get_minio_client()
    target_wrong = "archived/32025R2067.pdf"
    target_right = "in_force/32025R2067.pdf"
    
    # 1. Check existence
    print("🔍 Checking MinIO state:")
    has_wrong = client.object_exists(BUCKET_LAWS, target_wrong)
    has_right = client.object_exists(BUCKET_LAWS, target_right)
    
    print(f"Old (Wrong): {has_wrong}")
    print(f"New (Right): {has_right}")
    
    # 2. Delete Wrong
    if has_wrong:
        print(f"🗑️ Deleting stale file: {target_wrong}")
        client.delete_object(BUCKET_LAWS, target_wrong)
        
    # 3. Ensure Right
    if not has_right:
        print(f"📤 Uploading correct file to: {target_right}")
        local_path = Path("data/raw/laws/in_force/32025R2067.pdf")
        if local_path.exists():
            # Use our helper which infers prefix from parent directory "in_force"
            upload_law_pdf(local_path)
            print("✅ Upload complete.")
        else:
            print("❌ Local file missing, cannot fix!")
    else:
        print("✅ Correct file already exists in MinIO.")

if __name__ == "__main__":
    fix_minio()
