# Law Scraper & Extraction Engine

A standalone tool for scraping EU Law (EUR-Lex), filtering relevant regulations, and extracting logic gates (IF-THEN rules) into LanceDB.

## 🚀 Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment**:
   Copy `.env.example` to `.env` (or create one):
   ```bash
   GOOGLE_API_KEY=your_key
   LANCEDB_PATH=./storage/lancedb
   ```

## 🏃 Usage

### Run the Pipeline
This will scan `data/raw/laws` (you need to populate this or configure scrapers), filter relevant PDFs, and ingest them.

```bash
# Run from repository root
python -m src.main
```

## 📁 Structure

- `src/scrapers`: Scripts to fetch scraping data.
- `src/main.py`: Core logic for Ingestion, Filtration, and Rule Extraction.
- `src/schemas`: Data models (LanceDB) and Extraction Schemas.
- `src/utils`: Helpers for API, MinIO, and Taxonomy.

## 🛠 Integration
Data is stored in `storage/lancedb`. You can mount this folder in other projects or copy the LanceDB files.
