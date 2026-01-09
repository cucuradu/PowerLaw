import sys
from src.main import LawIngestionPipeline
from pathlib import Path
from tqdm import tqdm
import random

def ingest_batch_10():
    pipeline = LawIngestionPipeline()
    
    # Get all PDFs
    pdfs = pipeline.find_pdfs()
    
    # Filter for relevant laws (e.g. recent ones or sanctions for high impact)
    # FULL INGESTION: Process ALL PDFs
    target_pdfs = pdfs  # ALL 3500+ files
    
    print(f"🚀 Ingesting Batch of {len(target_pdfs)} Laws (Full Text Mode)...")
    
    success_count = 0
    for pdf_path in target_pdfs:
        try:
            print(f"\n📄 Processing {pdf_path.name}...")
            pipeline.process_pdf(pdf_path)
            success_count += 1
        except Exception as e:
            print(f"❌ Error processing {pdf_path.name}: {e}")

    print(f"\n✅ Batch Complete. Successfully ingested {success_count}/{len(target_pdfs)} laws.")

if __name__ == "__main__":
    ingest_batch_10()
