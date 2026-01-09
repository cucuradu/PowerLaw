import os
from pathlib import Path
from tqdm import tqdm
from src.utils.minio_client import upload_law_pdf, get_minio_client, BUCKET_LAWS

LAW_DIR = Path("data/raw/laws")

def sync():
    print("🚀 Syncing Local PDFs to MinIO...")
    pdfs = list(LAW_DIR.rglob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs.")
    
    client = get_minio_client()
    
    success = 0
    skipped = 0
    failed = 0
    
    existing_objects = set()
    for obj in client.list_objects(BUCKET_LAWS):
        # minio client vs wrapper dictionary handling
        if isinstance(obj, dict):
             existing_objects.add(obj.get('name'))
        else:
             existing_objects.add(obj.object_name)
    
    for pdf_path in tqdm(pdfs):
        celex_id = pdf_path.stem
        object_name = f"{celex_id}.pdf"
        
        if object_name in existing_objects:
            skipped += 1
            continue
            
        try:
            upload_law_pdf(pdf_path)
            success += 1
        except Exception as e:
            print(f"Failed {celex_id}: {e}")
            failed += 1
            
    print(f"\n✅ Sync Complete")
    print(f"Uploaded: {success}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")

if __name__ == "__main__":
    sync()
