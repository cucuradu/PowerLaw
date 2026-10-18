#!/usr/bin/env python3
"""
Strict Law Ingestion Pipeline (Phase 1.5 Executable)
Architecture: PDF -> MinIO -> Atomic LogicGate Extraction -> LanceDB (LawChunk)
"""

import os
import sys
import json
import re
import pymupdf as fitz  # PyMuPDF
from json_repair import repair_json
import lancedb
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# Add project root to path
# sys.path.append(str(Path(__file__).parent.parent.parent)) # Removed for standalone repo

from src.utils.api_wrapper import get_embedding, get_completion_with_json, upload_to_gemini, get_completion_multimodal
from src.utils.minio_client import upload_law_pdf
# NEW IMPORTS
from src.schemas.lancedb import LawChunk
from src.schemas.rule_schema import GNNRule, GNNRuleList
from src.prompts.rule_extraction import RULES_EXTRACTION_SYSTEM_PROMPT
from src.prompts.rule_validation import RULE_REVIEW_PROMPT, validate_rule_python

# Date Conversion Helper
import datetime

def to_timestamp(date_str: str) -> int:
    """Convert YYYY-MM-DD string to Unix timestamp."""
    if not date_str:
        return None
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except:
        return None

# Table Extraction
import pdfplumber

def extract_tables_from_pdf(pdf_path: Path) -> str:
    """
    Extract all tables from a PDF using pdfplumber.
    Returns a markdown-formatted string of all tables.
    """
    tables_md = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_tables = page.extract_tables()
                for j, table in enumerate(page_tables):
                    if not table or len(table) < 2:
                        continue
                    # Convert to markdown
                    headers = table[0]
                    md = f"\n### Table {i+1}.{j+1}\n"
                    md += "| " + " | ".join(str(h) if h else "" for h in headers) + " |\n"
                    md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
                    for row in table[1:]:
                        md += "| " + " | ".join(str(c) if c else "" for c in row) + " |\n"
                    tables_md.append(md)
    except Exception as e:
        pass  # If table extraction fails, continue without tables
    return "\n".join(tables_md)

# Load environment variables
load_dotenv()

# Configuration
def get_configured_db_path() -> Path:
    from src.config import get_settings
    return get_settings().lancedb_path

LANCEDB_PATH = get_configured_db_path()
LAW_DIR = Path("data/raw/laws")
TABLE_NAME = "structured_rules"
WORKERS = 4
CHUNK_PATTERN = re.compile(r'(?:^|\n)(?:Art(?:icle|\.)?|Annex|Appendix)\s*[\d\w]+', re.IGNORECASE)

# Laws that require manual curation (table extraction fails or consistently hallucinates)
BLOCKLIST = {
    "32022R2301",  # Gas Storage Targets - Tables not extractable, LLM hallucinates 5%
    "32023R2869",  # ESAP Portal - Just PDF upload obligations, no economic impact
    "32023R0066",  # Dual-Use Items - Generic list updates, no specifics
}

def log(message: str) -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def get_eurlex_url(celex_id: str) -> str:
    """Generate EUR-Lex URL from CELEX ID for traceability"""
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex_id}"

class LawIngestionPipeline:
    def __init__(self):
        self.setup_db()
        
    def setup_db(self):
        self.db = lancedb.connect(str(get_configured_db_path()))
        
        if TABLE_NAME in self.db.table_names():
            self.table = self.db.open_table(TABLE_NAME)
            log(f"✅ Connected to table '{TABLE_NAME}'")
        else:
            # LanceModel automatically derives schema
            self.table = self.db.create_table(TABLE_NAME, schema=LawChunk)
            log(f"✅ Created new table '{TABLE_NAME}' with Strict Schema")

    def get_existing_ids(self) -> set:
        try:
            # We filter by 'law_id' (was law_celex)
            return set(self.table.to_pandas()["law_id"].unique())
        except Exception:
            return set()

    def find_pdfs(self) -> List[Path]:
        all_files = list(LAW_DIR.rglob("*"))
        pdfs = list(LAW_DIR.rglob("*.pdf"))
        others = len(all_files) - len(pdfs)
        if others > 0:
            log(f"ℹ️ Found {len(pdfs)} PDFs in data/raw/laws. Ignoring {others} non-PDF files (JSON, etc) as per strict Raw-Only policy.")
        return pdfs

    def get_celex_id(self, pdf_path: Path) -> str:
        return pdf_path.stem

    def extract_chunks(self, pdf_path: Path) -> List[Dict[str, str]]:
        chunks = []
        try:
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            matches = list(CHUNK_PATTERN.finditer(text))
            celex_id = self.get_celex_id(pdf_path)
            
            if not matches:
                if len(text) > 50:
                    chunks.append({"id": f"{celex_id}_Full", "text": text, "title": "Full Text"})
                return chunks

            for i, match in enumerate(matches):
                start = match.start()
                end = matches[i+1].start() if i+1 < len(matches) else len(text)
                
                chunk_header = match.group(0).strip()
                chunk_text = text[start:end].strip()
                if chunk_text.startswith(chunk_header):
                    chunk_text = chunk_text[len(chunk_header):].strip()
                
                # Standardize Title and ID
                # User Request: "Chunk ID sa specifice art sau anex si nr"
                # Pattern match to extract type and number
                # Header usually: "Article 5" or "Article 5."
                
                type_match = re.search(r'(Article|Art|Annex|Appendix)[^\d]*(\d+\w?)', chunk_header, re.IGNORECASE)
                if type_match:
                    c_type = type_match.group(1).capitalize()
                    if c_type.startswith("Art"): c_type = "Article"
                    c_num = type_match.group(2)
                    unique_id = f"{celex_id}_{c_type}_{c_num}"
                    title = f"{c_type} {c_num}"
                else:
                    # Fallback
                    clean_header = re.sub(r'[^\w]', '', chunk_header)
                    unique_id = f"{celex_id}_{clean_header}"
                    title = chunk_header.replace("\n", " ")
                
                if len(chunk_text) >= 30:
                    chunks.append({"id": unique_id, "text": chunk_text, "title": title})
                    
        except Exception as e:
            log(f"❌ Error reading {pdf_path.name}: {e}")
            
        return chunks

    def extract_rules_from_full_text(self, full_text: str, law_id: str) -> List[GNNRule]:
        """
        Two-Stage Extraction Pipeline:
        1. FLASH: Fast extraction of draft rules (cheap, ~$0.002)
        2. PRO: Review and fix logical errors in JSON (smart, ~$0.003)
        3. PYTHON: Deterministic fixes (free)
        
        Total cost: ~$0.005 per law (vs $0.04 for Pro-only = 87% savings)
        """
        try:
            # Context Limit Check
            truncated_text = full_text[:150000]
            
            # ============ STEP 1: FLASH DRAFT ============
            extraction_prompt = f"{RULES_EXTRACTION_SYSTEM_PROMPT}\n\n"
            extraction_prompt += f"# FULL LAW TEXT (ID: {law_id})\n"
            extraction_prompt += f"'''{truncated_text}'''\n\n"
            extraction_prompt += "EXTRACT ALL RELEVANT ECONOMIC RULES NOW:"

            # Flash model for initial extraction (fast, cheap)
            draft_response = get_completion_with_json(
                prompt=extraction_prompt,
                use_pro=False,  # FLASH
                response_schema=GNNRuleList
            )
            
            # Parse draft
            try:
                draft_rules = GNNRuleList.model_validate_json(draft_response)
            except Exception:
                parsed = json.loads(repair_json(draft_response))
                draft_rules = GNNRuleList.model_validate(parsed)
            
            # If no rules found, skip Pro review
            if not draft_rules.rules:
                log(f"ℹ️ Flash found 0 rules for {law_id}, skipping Pro review")
                return []
            
            log(f"📝 Flash draft: {len(draft_rules.rules)} rules for {law_id}")
            
            # ============ STEP 2: PRO REVIEW ============
            # Pro receives ONLY the JSON (small input = cheap Pro call)
            review_prompt = RULE_REVIEW_PROMPT.format(
                rules_json=draft_rules.model_dump_json(indent=2)
            )
            
            try:
                reviewed_response = get_completion_with_json(
                    prompt=review_prompt,
                    use_pro=True,  # PRO for reasoning
                    response_schema=GNNRuleList
                )
                
                # Parse reviewed rules
                try:
                    reviewed_rules = GNNRuleList.model_validate_json(reviewed_response)
                except Exception:
                    parsed = json.loads(repair_json(reviewed_response))
                    reviewed_rules = GNNRuleList.model_validate(parsed)
                
                log(f"✅ Pro reviewed: {len(reviewed_rules.rules)} rules for {law_id}")
                rules_to_process = reviewed_rules.rules
                
            except Exception as e:
                # FALLBACK: Pro review failed, use Flash draft with Python fixes
                log(f"⚠️ Pro review failed for {law_id}: {e}. Using Flash draft.")
                rules_to_process = draft_rules.rules
            
            # ============ STEP 3: PYTHON POST-PROCESSING ============
            final_rules = []
            for rule in rules_to_process:
                # Apply deterministic Python fixes
                rule_dict = rule.model_dump()
                rule_dict = validate_rule_python(rule_dict, law_id)
                final_rules.append(GNNRule.model_validate(rule_dict))
            
            return final_rules

        except Exception as e:
            log(f"⚠️ Extraction error for {law_id}: {e}")
            return []

    def ingest_law_full_text(self, law_id: str, chunks: List[Dict[str, str]], minio_uri: str, metadata: Dict = None):
        """
        New Logic: Consolidate text chunks and extract rules holistically.
        """
        # 0. Idempotency
        try:
            self.table.delete(f"law_id = '{law_id}'")
        except Exception:
            pass 

        # 1. Aggregate Text
        full_text = ""
        # Sort chunks if possible to maintain order? Usually they are ordered.
        for chunk in chunks:
            if chunk.get('text'):
                full_text += f"\n\n--- {chunk.get('title', 'Section')} ---\n"
                full_text += chunk['text']
                if chunk.get('tables'):
                    full_text += f"\n[TABLE DATA]:\n{chunk['tables']}\n"
        
        if len(full_text) < 100:
            log(f"⏩ Skipped {law_id}: Too short ({len(full_text)} chars)")
            return

        # 2. LLM Extraction (ONE CALL)
        rules = self.extract_rules_from_full_text(full_text, law_id)
        
        if not rules:
            log(f"⚠️ No rules found in {law_id}")
            return

        # 3. Store Rules
        new_rows = []
        for i, rule in enumerate(rules):
            # Enforce consistency
            if rule.metadata.regulation_id != law_id:
                rule.metadata.regulation_id = law_id
            
            # ECU→EUR Conversion (1:1 rate, effective Jan 1, 1999)
            # Only check for pre-1999 regulations (CELEX format: 3YYYYRxxxx)
            try:
                year = int(law_id[1:5]) if law_id[0] == '3' else 2000
                if year < 1999 and rule.mechanism.unit and "ECU" in rule.mechanism.unit:
                    rule.mechanism.unit = rule.mechanism.unit.replace("ECU", "EUR")
            except (ValueError, IndexError):
                pass  # Skip if year extraction fails

            # Vector Text
            mech = rule.mechanism
            scope = rule.scope
            vector_text = (
                f"{mech.action_type} on {mech.target_variable} ({mech.operator} {mech.value} {mech.unit}). "
                f"Sectors: {', '.join(scope.industrial_sectors)}. Nodes: {', '.join(scope.node_types)}."
            )

            row = LawChunk(
                vector=get_embedding(vector_text),
                law_id=law_id,
                chunk_id=f"{rule.metadata.article_id}_{i}", # Auto-increment if multiple rules per article
                summary=rule.summary, # Use Natural Language Summary
                minio_path=minio_uri,
                source_url=get_eurlex_url(law_id),
                gnn_rule_json=rule.model_dump_json(),
                
                # Metadata filters
                rule_type=rule.metadata.law_type.value,
                jurisdiction="EU",
                valid_from=rule.timeline.activation_date,  # String format YYYY-MM-DD
                valid_to=rule.timeline.expiration_date or "9999-12-31",
                citations=[],
                entity_types_json="[]",
                numeric_constraints_json="[]"
            )
            new_rows.append(row)

        if new_rows:
            self.table.add(new_rows)
            log(f"✅ Ingested {len(new_rows)} rules for {law_id}")


    def run_bulk(self, config_path: str):
        if not os.path.exists(config_path):
            print("Config not found.")
            return
            
        with open(config_path, 'r') as f:
            laws = json.load(f)
            
        print(f"Processing {len(laws)} laws...")
        for law in laws:
            self.process_law_entry(law)

    def check_relevance(self, text: str, law_id: str) -> bool:
        """
        Smart Filter: Ask LLM if this law is relevant to the economic graph.
        Costs 1 call per PDF but saves ~20 calls if the law is irrelevant.
        """
        # Truncate text to save tokens (First 3000 chars covers Title + Recitals)
        sample = text[:3000]
        
        prompt = f"""
        Analyze the relevance of this EU Law ({law_id}) for an Industrial Economic Graph (Energy, Commodities, Manufacturing, Trade).

        TEXT SAMPLE:
        {sample}...

        TASK:
        Return JSON with "is_relevant" (boolean) and "reason".
        
        CRITERIA:
        - **RELEVANT (True)**: Regulations on Energy, Climate (CO2), Industry (Steel/Chemicals), Trade (Imports/Exports), Transport, or Environmental Compliance.
        - **IRRELEVANT (False)**: 
            - Digital Services / Social Media / Content Moderation / Copyright.
            - Terrorist Content / Civil Security / Police Cooperation.
            - Purely Administrative Protocols (e.g. appointing a specific committee member).
            - Judicial cooperation in civil matters.
            - Food safety (unless it impacts industrial production costs).
            - Banking/Finance (unless strict capital requirements for energy).

        FORMAT:
        {{
            "is_relevant": true/false,
            "reason": "Short explanation"
        }}
        """
        try:
            resp = get_completion_with_json(prompt)
            data = json.loads(repair_json(resp))
            is_rel = data.get("is_relevant", False)
            if not is_rel:
                log(f"🚫 Smart Filter Rejected {law_id}: {data.get('reason')}")
            else:
                log(f"✅ Smart Filter Approved {law_id}")
            return is_rel
        except Exception as e:
            log(f"⚠️ Smart Filter Failed for {law_id}, defaulting to True: {e}")
            return True

    def process_pdf(self, pdf_path: Path):
        celex_id = self.get_celex_id(pdf_path)
        
        # 0. Check Blocklist (requires manual curation)
        if celex_id in BLOCKLIST:
            log(f"🚫 Blocklisted {celex_id} (Requires manual curation)")
            return
        
        # 1. Extract Text for Smart Filter
        try:
            doc = fitz.open(pdf_path)
            # Get first 2 pages for context
            text_sample = ""
            for i, page in enumerate(doc):
                if i > 1: break
                text_sample += page.get_text()
            doc.close()
            
            # 2. Run Smart Filter
            if not self.check_relevance(text_sample, celex_id):
                return
                
        except Exception as e:
            log(f"⚠️ Error reading PDF for filter {celex_id}: {e}")
            return

        # 3. Extract Tables from PDF
        tables_markdown = extract_tables_from_pdf(pdf_path)
        if tables_markdown:
            log(f"📊 Extracted tables from {celex_id} ({len(tables_markdown)} chars)")

        try:
            minio_uri = upload_law_pdf(pdf_path)
        except Exception:
            minio_uri = f"file://{pdf_path}"

    def extract_rules_multimodal(self, pdf_path: Path, law_id: str) -> List[GNNRule]:
        """
        FULL PRO MULTIMODAL EXTRACTION
        Uploads PDF to Gemini 1.5 Pro and asks for rules directly.
        Best for: Tables, Complex Layouts, Large Docs where text chunking fails.
        Cost: Higher (~$0.50 for large docs) but accurate.
        """
        try:
            log(f"🧠 Starting Multimodal Extraction (Pro 1.5) for {law_id}...")
            
            # Upload PDF
            # Check file size (limit to 20MB for API safety, though limit is higher)
            if pdf_path.stat().st_size > 50 * 1024 * 1024:
                log(f"⚠️ PDF too large for Multimodal ({pdf_path.stat().st_size} bytes). Skipping.")
                return []
                
            uploaded_file = upload_to_gemini(str(pdf_path))
            
            # Construct Multimodal Prompt
            # Reuse system prompt but simplified for "Look at this file"
            prompt = f"{RULES_EXTRACTION_SYSTEM_PROMPT}\n\n"
            prompt += f"# TARGET DOCUMENT\n"
            prompt += f"Attached is the full text of Regulation/Directive {law_id}.\n"
            prompt += "Analyze the visual structure, especially TABLES and ANNEXES.\n"
            prompt += "EXTRACT ALL RELEVANT ECONOMIC RULES NOW (in JSON)."

            response_text = get_completion_multimodal(
                prompt=prompt,
                files=[uploaded_file],
                use_pro=True,
                response_schema=GNNRuleList
            )
            
            # Parse
            try:
                rules_list = GNNRuleList.model_validate_json(response_text)
            except Exception:
                parsed = json.loads(repair_json(response_text))
                rules_list = GNNRuleList.model_validate(parsed)
                
            log(f"✅ Multimodal extracted: {len(rules_list.rules)} rules for {law_id}")
            
            # Helper cleanup - though we rely on LRU cache or file expiration usually
            # genai.delete_file(uploaded_file.name) # If needed
            
            # Python Post-Processing (Reuse existing logic)
            final_rules = []
            for rule in rules_list.rules:
                rule_dict = rule.model_dump()
                rule_dict = validate_rule_python(rule_dict, law_id)
                final_rules.append(GNNRule.model_validate(rule_dict))
                
            return final_rules

        except Exception as e:
            log(f"❌ Multimodal Extraction Failed for {law_id}: {e}")
            return []

    def ingest_rules(self, law_id: str, rules: List[GNNRule], minio_uri: str):
        """
        Ingest list of GNNRule objects into LanceDB
        """
        if not rules:
            return
            
        # 0. Idempotency
        try:
            self.table.delete(f"law_id = '{law_id}'")
        except Exception:
            pass 

        new_rows = []
        for i, rule in enumerate(rules):
            # Enforce consistency
            if rule.metadata.regulation_id != law_id:
                rule.metadata.regulation_id = law_id
            
            # ECU→EUR Conversion
            try:
                year = int(law_id[1:5]) if law_id[0] == '3' else 2000
                if year < 1999 and rule.mechanism.unit and "ECU" in rule.mechanism.unit:
                    rule.mechanism.unit = rule.mechanism.unit.replace("ECU", "EUR")
            except (ValueError, IndexError):
                pass

            # Vector Text
            mech = rule.mechanism
            scope = rule.scope
            vector_text = (
                f"{mech.action_type} on {mech.target_variable} ({mech.operator} {mech.value} {mech.unit}). "
                f"Sectors: {', '.join(scope.industrial_sectors)}. Nodes: {', '.join(scope.node_types)}."
            )

            row = LawChunk(
                vector=get_embedding(vector_text),
                law_id=law_id,
                chunk_id=f"{rule.metadata.article_id}_{i}", 
                summary=rule.summary,
                minio_path=minio_uri,
                source_url=get_eurlex_url(law_id),
                gnn_rule_json=rule.model_dump_json(),
                rule_type=rule.metadata.law_type.value,
                jurisdiction="EU",
                valid_from=rule.timeline.activation_date,
                valid_to=rule.timeline.expiration_date or "9999-12-31",
                citations=[],
                entity_types_json="[]",
                numeric_constraints_json="[]"
            )
            new_rows.append(row)
            
        if new_rows:
            self.table.add(new_rows)
            log(f"✅ Ingested {len(new_rows)} rules for {law_id}")


    def process_pdf(self, pdf_path: Path):
        celex_id = self.get_celex_id(pdf_path)
        
        # 0. Check Blocklist
        if celex_id in BLOCKLIST:
            log(f"🚫 Blocklisted {celex_id}")
            return
        
        # 1. Extract Text for Smart Filter
        try:
            doc = fitz.open(pdf_path)
            text_sample = ""
            full_text_extract = "" # Extract all text here for hybrid
            for i, page in enumerate(doc):
                text = page.get_text()
                full_text_extract += text
                if i < 2:
                    text_sample += text
            doc.close()
            
            # 2. Run Smart Filter
            if not self.check_relevance(text_sample, celex_id):
                return
                
        except Exception as e:
            log(f"⚠️ Error reading PDF {celex_id}: {e}")
            return

        # 3. Extract Tables
        tables_markdown = extract_tables_from_pdf(pdf_path)
        if tables_markdown:
            log(f"📊 Extracted tables from {celex_id} ({len(tables_markdown)} chars)")

        try:
            minio_uri = upload_law_pdf(pdf_path)
        except Exception:
            minio_uri = f"file://{pdf_path}"

        # ============ ROUTING LOGIC ============
        rules = []
        use_multimodal = False
        
        # Condition A: Heavy Tables -> Go directly to Multimodal
        if len(tables_markdown) > 3000:
            log(f"🔄 Routing {celex_id} to PRO MULTIMODAL (Heavy Tables detected)")
            use_multimodal = True
        else:
            # Condition B: Try Hybrid First
            log(f"🔄 Routing {celex_id} to HYBRID FLASH (Text-based)")
            chunks = self.extract_chunks(pdf_path) # Just for logic reuse if needed, but we use full_text_extract now
            # Add table context if small
            if tables_markdown:
                full_text_extract += f"\n\n[TABLE DATA]:\n{tables_markdown}"
                
            rules = self.extract_rules_from_full_text(full_text_extract, celex_id)
            
            # Condition C: Failure Fallback
            # If Hybrid returned 0 rules BUT Smart Filter was True -> Likely parse error or complex layout
            if not rules:
                log(f"⚠️ Hybrid found 0 rules for relevant law {celex_id}. Fallback to PRO MULTIMODAL.")
                use_multimodal = True

        # Execute Multimodal if flagged
        if use_multimodal:
            rules = self.extract_rules_multimodal(pdf_path, celex_id)

        # Ingest
        if rules:
            self.ingest_rules(celex_id, rules, minio_uri)
        else:
            log(f"⚠️ No rules extracted for {celex_id} (Attempted {'Multimodal' if use_multimodal else 'Hybrid'})")


    def run(self):
        log("🚀 Starting Executable Law Ingestion")
        pdf_files = self.find_pdfs()
        existing = self.get_existing_ids()
        log(f"Found {len(pdf_files)} PDFs. Processing...")
        
        # Domain Filter - RELAXED (Trusting Scraper + Smart Filter)
        # We process everything that the scraper downloaded, relying on Smart Filter for final check.
        filtered_files = []
        for pdf_path in tqdm(pdf_files, desc="Scanning"):
            if self.get_celex_id(pdf_path) in existing:
                continue
            filtered_files.append(pdf_path)

        log(f"📉 Filtered: {len(filtered_files)} relevant laws (from {len(pdf_files)} total). Processing ALL...")
        
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            list(tqdm(executor.map(self.process_pdf, filtered_files), total=len(filtered_files)))

        log("🎉 Ingestion Complete")

if __name__ == "__main__":
    LawIngestionPipeline().run()
