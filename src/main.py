#!/usr/bin/env python3
"""
Strict Law Ingestion Pipeline (Phase 1.5 Executable)
Architecture: PDF -> MinIO -> Atomic LogicGate Extraction -> LanceDB (LawChunk)
"""

import os
import sys
import json
import re
import fitz  # PyMuPDF
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

from src.utils.api_wrapper import get_embedding, get_completion_with_json
from src.utils.minio_client import upload_law_pdf
# NEW IMPORTS
from src.schemas.lancedb import LawChunk
from src.schemas.extraction import ArticleExtraction, LogicGate, LogicTrigger, LogicConsequence
from src.utils.taxonomy import get_valid_law_variables

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
LANCEDB_PATH = Path(os.getenv("LANCEDB_PATH", "./storage/lancedb"))
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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def get_eurlex_url(celex_id: str) -> str:
    """Generate EUR-Lex URL from CELEX ID for traceability"""
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex_id}"

class LawIngestionPipeline:
    def __init__(self):
        self.setup_db()
        
    def setup_db(self):
        self.db = lancedb.connect(str(LANCEDB_PATH))
        
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

    def process_chunk(self, chunk: Dict[str, str]) -> Optional[ArticleExtraction]:
        # Strict Taxonomy Injection
        from src.utils.taxonomy import ConceptCode
        valid_concepts = ", ".join([code.value for code in ConceptCode])
        valid_law_vars = ", ".join(get_valid_law_variables())


        # Noise Pre-Filter (Save Token Costs & Ensure Purity)
        NOISE_TRIGGERS = [
            "procedure for the committee",
            "committee procedure",
            "exercise of the delegation",
            "conferred on the commission",
            "entry into force",
            "addressee",
            "done at brussels"
        ]
        
        lower_text = chunk['text'].lower()
        if any(trigger in lower_text for trigger in NOISE_TRIGGERS):
            log(f"⏩ Skipped Noise Chunk {chunk['id']} (Trigger in text)")
            return None

        # Build table context if available
        tables_context = ""
        if chunk.get('tables'):
            tables_context = f"""
        
        TABLES EXTRACTED FROM PDF (Use these for numeric values):
        {chunk['tables'][:8000]}
        """

        prompt = f"""
        Analyze this text from EU Law {chunk['title']} (ID: {chunk['id']}):
        
        {chunk['text']}
        {tables_context}
        
        EXTRACT FORMAT (JSON):
        {{
          "status": "SUCCESS" or "SKIPPED",
          "reason": "Why skipped (e.g. 'Purely administrative', 'Delegated Act boilerplate')",
          "summary": "Concise natural language description of the rule (e.g. 'Requires shipping companies to report CO2 emissions annually').",
          "logic_type": "REGULATORY" (Obligation/Ban), "PROCEDURAL" (only for compliance steps), "DEFINITION",
          "entities": ["ACTOR", "COMMODITY"], (e.g. ["SHIPPING_COMPANY", "CO2"], ["IMPORTER", "STEEL"])
          "constraints": [
            {{
              "name": "variable_name",
              "operator": "EQUAL" | "GREATER_THAN" | "LESS_THAN",
              "value": numeric_value or "YYYY-MM-DD",
              "unit": "kg" | "EUR" | "%" | "DATE" | "BOOLEAN"
            }}
          ],
          "commodity_code": "String (CN/HS code if present)",
          "confidence": 0.0 to 1.0
        }}

        RULES:
        1. **SUMMARY IS CRITICAL**: Provide a high-quality, standalone sentence describing the rule. This will be used for semantic search.
        
        2. **USE TABLE DATA**: If the article references "targets" or "thresholds" and a table is provided above, extract the ACTUAL values from the table. Do NOT guess.

        RULES:
        1. **NO REFERENCE DEFINITIONS (CRITICAL)**: 
           - **SKIP** types that just say "X is defined in Directive Y". 
           - **SKIP** "For the purposes of this Regulation, the definitions in Article 2 shall apply."
           - **ONLY** extract definitions if they contain concrete parameters *in this text* (e.g. " 'SME' means an enterprise with < 250 employees").
           - If a rule relies on a definition, extract the **RULE**, not the definition pointer.

        2. **NO GENERIC ENTITIES**:
           - FORBIDDEN: ["OPERATOR", "ECONOMIC_OPERATOR", "UNDERTAKING", "MEMBER_STATE", "RELEVANT_SECTOR", "THIRD_COUNTRY"].
           - REQUIRED: Specific sectors (e.g. "SHIPPING_COMPANY", "AIRCRAFT_OPERATOR", "STEEL_PRODUCER").
           - If text says "operator", infer the sector from context (e.g. if 'emissions', use 'EMITTER' or 'INSTALLATION').
        
        3. **NUMERIC CONSTRAINTS ONLY**:
           - Extract LIMITS (quotas, thresholds, prices, fines).
           - **DATES**: Must be SINGLE ISO-8601 string ("2017-08-31"). **NEVER** split dates into Day/Month/Year constraints.
           - **MISSING VALUES (TABLES)**: If a value is referenced (e.g. "set out in Annex") but NOT visible in text, extract as **-1** (Unknown/Reference). **DO NOT GUESS 0**.
           - IGNORE: CN codes (e.g. 1509 10 20), Regulation numbers (2015/757), meaningless headers.
           - DIRECTION: "Up to X" = MAX (LESS_THAN_EQUAL). "At least X" = MIN (GREATER_THAN_EQUAL).

        4. **NOISE FILTERING (CRITICAL)**:
           - **SKIP** (Status="SKIPPED") if the text is about:
             - **Delegated Acts / Implementing Acts**: "The Commission is empowered to adopt delegated acts...", "Power to adopt...", "Assisted by a Committee...".
             - **Committee Procedures**: "Examination procedure", "Committee establishment".
             - **Administrative Reporting**: Internal reports from Commission to Parliament (unless it's a business reporting obligation).
             - **Entry into Force**: "This Regulation shall enter into force..."
             - **Digital/Social Governance**: "Terrorist content", "CSAM", "Digital Services Act", "Cybersecurity" (unless explicit Energy/Grid context).
           - KEEP only rules that impose costs, obligations, or limits on ECONOMIC ACTORS.

        5. **GRANULARITY**:
           - If text lists specific goods (e.g. "tomatoes, cucumbers"), create a rule for the category but tag specific entities.
        """
        try:
            # We use a Dict for the response schema because the new structure is dynamic
            json_str = get_completion_with_json(prompt, response_schema=None) 
            
            # Use json_repair to handle malformed outputs (unclosed lists, missing quotes)
            data_raw = json.loads(repair_json(json_str))
            
            # 1. Handle List vs Dict output
            if isinstance(data_raw, list):
                if not data_raw: return None
                data = data_raw[0] # Take first extraction if multiple
            else:
                data = data_raw

            # Handle NOISE/SKIPPED
            if data.get("status") == "SKIPPED":
                return None
            
            # Map new schema to existing ArticleExtraction model
            logic_gates = []
            constraints = data.get("constraints", [])
            entities = data.get("entities", [])
            raw_type = data.get("logic_type", "REGULATORY")
            
            # === QUALITY FILTER 1: Reject Empty Entities ===
            if not entities or len(entities) == 0:
                log(f"⏩ Skipped {chunk['id']}: Empty entities (Generic/Noise)")
                return None

            # Map granular types to DB types
            # LanceDB schema only accepts: REGULATORY, PROCEDURAL, DEFINITION
            type_map = {
                "OBLIGATION": "REGULATORY",
                "PROHIBITION": "REGULATORY",
                "TARIFF_RULE": "REGULATORY",
                "TARIFF": "REGULATORY", # LLM alias
                "QUOTA": "REGULATORY",
                "SUBSIDY": "REGULATORY",
                "THRESHOLD": "REGULATORY",
                "DEFINITION": "DEFINITION",
                "PROCEDURAL": "PROCEDURAL", 
                "LEGISLATIVE_UPDATE": "PROCEDURAL"
            }
            db_rule_type = type_map.get(raw_type.upper(), "REGULATORY") # Default to REGULATORY

            # If no constraints but valid economic rule, create a Metadata gate
            if not constraints:
                constraints = [{"name": "compliance", "value": 1.0, "unit": "BOOLEAN", "operator": "EQUAL"}]
            
            # === QUALITY FILTER 2: Reject Sole Compliance (Noise) ===
            if len(constraints) == 1 and constraints[0].get("name") == "compliance":
                log(f"⏩ Skipped {chunk['id']}: Sole compliance=1.0 (No real constraints)")
                return None

            for const in constraints:
                val_str = str(const.get("value", 0.0))
                unit_str = const.get("unit", "NONE")
                
                # === QUALITY FILTER 3: Skip Negative Values (Hallucination) ===
                try:
                    val_num = float(val_str)
                    if val_num < 0 and unit_str not in ["DATE", "DATE_RECURRING"]:
                        log(f"⏩ Skipped constraint in {chunk['id']}: Negative value {val_num} (Invalid)")
                        continue
                except ValueError:
                    pass # Non-numeric value (e.g., date string), OK to proceed
                
                # Check for Recurring Date (YYYY)
                if "YYYY" in val_str:
                    unit_str = "DATE_RECURRING"

                gate = LogicGate(
                    trigger=LogicTrigger(
                        variable=const.get("name", "unknown"),
                        operator=const.get("operator", "EQUAL"),
                        threshold=val_str,
                        unit=unit_str
                    ),
                    consequence=LogicConsequence(
                        action_type=raw_type, 
                        target_variable=const.get("name", "unknown"),
                        numeric_value=val_str,
                        unit=unit_str,
                        set_value=f"{val_str} {unit_str}"
                    )
                )
                logic_gates.append(gate)
            
            # === QUALITY FILTER 4: Reject if all constraints were filtered out ===
            if not logic_gates:
                log(f"⏩ Skipped {chunk['id']}: All constraints were invalid/filtered")
                return None

            # Use LLM summary or fallback to structured representation
            summary = data.get("summary")
            if not summary or len(summary) < 10:
                summary = f"Type: {raw_type} | Entities: {entities} | Commodity: {data.get('commodity_code', 'N/A')} | Constraints: {constraints}"

            extraction = ArticleExtraction(
                law_id=chunk['id'].split('_')[0],
                chunk_id=data.get("id", chunk['id']),
                jurisdiction="EU",
                valid_from="2024-01-01",
                semantic_summary=summary,
                rule_type=db_rule_type,
                entity_types_json=json.dumps(entities),
                numeric_constraints_json=json.dumps(constraints),
                logic=logic_gates,
                citations=[]
            )
            
            return extraction

        except Exception as e:
            log(f"⚠️ Extraction error for {chunk['id']}: {e}")
            return None

    def ingest_law_from_chunks(self, law_id: str, chunks: List[Dict[str, str]], minio_uri: str):
        """
        Core Logic: Process list of text chunks, filter/merge types, and write to LanceDB.
        Shared by PDF pipeline and Bulk JSON pipeline.
        """
        # 0. Idempotency: Delete existing entries for this law to prevent duplicates
        try:
            self.table.delete(f"law_id = '{law_id}'")
            # log(f"🔄 Cleared existing rows for {law_id} (Overwriting)")
        except Exception:
            pass # Table might not exist or ID not found

        # 1. Collect Extractions
        substantive_items = []
        procedural_items = []
        
        for chunk in chunks:
            # Check for title/text structure
            if 'text' not in chunk: continue
            
            ext = self.process_chunk(chunk)
            if ext:
                if ext.rule_type == "PROCEDURAL":
                    procedural_items.append(ext)
                else:
                    substantive_items.append(ext)

        new_rows = []

        # 2. Process Substantive
        for ext in substantive_items:
            for gate in ext.logic:
                # Construct Vector Text
                rule_text = (f"IF {gate.trigger.variable} {gate.trigger.operator} {gate.trigger.threshold} {gate.trigger.unit} "
                             f"THEN {gate.consequence.action_type} {gate.consequence.target_variable} = {gate.consequence.numeric_value} {gate.consequence.unit}")
                
                # ID Formatting: {law_id}_{art_id}
                raw_id = ext.chunk_id if ext.chunk_id else "Substantive"
                # Ensure prefix presence
                if not raw_id.startswith(law_id):
                    final_id = f"{law_id}_{raw_id}"
                else:
                    final_id = raw_id
                
                # Auto-fill deprecated
                gate.consequence.set_value = f"{gate.consequence.numeric_value} {gate.consequence.unit}"

                row = LawChunk(
                    vector=get_embedding(rule_text),
                    law_id=law_id,
                    chunk_id=final_id,
                    summary=ext.semantic_summary,
                    minio_path=minio_uri,
                    source_url=get_eurlex_url(law_id),
                    logic=gate,
                    jurisdiction=ext.jurisdiction or "EU",
                    valid_from=ext.valid_from or "2024-01-01",
                    valid_to=ext.valid_to or "9999-12-31",
                    rule_type=ext.rule_type,
                    citations=ext.citations,
                    entity_types_json=ext.entity_types_json,
                    numeric_constraints_json=ext.numeric_constraints_json
                )
                new_rows.append(row)

        # 3. Aggregate Procedural
        if procedural_items:
            all_summaries = [p.semantic_summary for p in procedural_items]
            all_citations = set()
            for p in procedural_items:
                all_citations.update(p.citations)
            
            merged_summary = "Administrative & Procedural Provisions: " + "; ".join(all_summaries)
            merged_citations = list(all_citations)
            
            noop_gate = LogicGate(
                trigger=LogicTrigger(variable="sys_procedure", operator="==", threshold="1.0", unit="BOOLEAN"),
                consequence=LogicConsequence(action_type="METADATA", target_variable="citations", numeric_value="0.0", unit="NONE", set_value="0.0 NONE")
            )
            
            row = LawChunk(
                vector=get_embedding(merged_summary),
                law_id=law_id,
                chunk_id=f"{law_id}_Annex_Procedure", # Standardized ID
                summary=merged_summary,
                minio_path=minio_uri,
                source_url=get_eurlex_url(law_id),
                logic=noop_gate,
                jurisdiction="EU",
                valid_from="2024-01-01", 
                valid_to="9999-12-31",
                rule_type="PROCEDURAL",
                citations=merged_citations,
                entity_types_json="[]",
                numeric_constraints_json="[]"
            )
            new_rows.append(row)
            
        if new_rows:
            self.table.add(new_rows)
            log(f"✅ Ingested {len(substantive_items)} rules + {1 if procedural_items else 0} procedural annex from {law_id}")

    def process_law_entry(self, entry: Dict):
        # Legacy/Simple Bulk Support
        # Now wrapping it as a single chunk to use shared logic
        law_id = entry['id']
        chunk = {
            "id": f"{law_id}_Full",
            "title": entry.get("title", "Full Text"),
            "text": entry['text']
        }
        minio_path = f"s3://laws/{law_id}.pdf"
        self.ingest_law_from_chunks(law_id, [chunk], minio_path)

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

        chunks = self.extract_chunks(pdf_path)
        if not chunks:
            return
        
        # 4. Inject Table Data into Each Chunk
        for chunk in chunks:
            chunk['tables'] = tables_markdown
            
        self.ingest_law_from_chunks(celex_id, chunks, minio_uri)


    def run(self):
        log("🚀 Starting Executable Law Ingestion")
        pdf_files = self.find_pdfs()
        existing = self.get_existing_ids()
        log(f"Found {len(pdf_files)} PDFs. Scanning for Domain Keywords (Energy, Gas, Carbon)...")
        
        # Domain Filter
        KEYWORDS = ["energy", "electricity", "gas", "carbon", "emission", "climate", "power", "renewable", "steel", "hydrogen"]
        EXCLUDED_KEYWORDS = ["terrorist content", "digital services act", "sexual abuse", "hosting service", "social media", "content moderation", "online platform"]
        
        filtered_files = []
        for pdf_path in tqdm(pdf_files, desc="Filtering"):
            if self.get_celex_id(pdf_path) in existing:
                continue
                
            try:
                # Fast Scan: Check first 2 pages
                doc = fitz.open(pdf_path)
                text = ""
                for i, page in enumerate(doc):
                    if i > 2: break
                    text += page.get_text().lower()
                doc.close()
                
                # Check Exclusions First
                if any(ex in text for ex in EXCLUDED_KEYWORDS):
                    # log(f"🚫 Excluded {pdf_path.name} (Blacklisted Keyword)")
                    continue

                if any(kw in text for kw in KEYWORDS):
                    filtered_files.append(pdf_path)
            except Exception:
                continue

        log(f"📉 Filtered: {len(filtered_files)} relevant laws (from {len(pdf_files)} total). Processing ALL...")
        
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            list(tqdm(executor.map(self.process_pdf, filtered_files), total=len(filtered_files)))

        log("🎉 Ingestion Complete")

if __name__ == "__main__":
    LawIngestionPipeline().run()
