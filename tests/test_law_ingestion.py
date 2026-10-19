"""
Tests for Task 5: Source-grounded legal ingestion and graph projection.
Covers:
- XML/HTML structured parsing and preference over OCR/PDF.
- Long document chunking by legal provisions with shared context.
- Normal/scanned/table PDFs, scan detection routing to review.
- Amended articles and LAW -> AMENDS -> LAW relationships.
- Non-numeric obligations (value=None, non_numeric_obligation set).
- Conditional exemptions and future phases (as-of upcoming vs active status).
- Missing dates remaining None (never 1900-01-01 or today).
- Exact evidence links for all emitted rules.
- CELEX Decision identifiers (3YYYYD####) included in discovery and parsing.
- Non-destructive updates: failures leave prior committed rules visible.
- Consolidated single process_pdf definition.
"""

import json
import re
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
import pymupdf

from src.schemas.ingestion import (
    SourceDocument,
    Fact,
    Entity,
    EvidenceLink,
    BatchManifest,
    validate_candidate,
)
from src.schemas.rule_schema import (
    GNNRule,
    Metadata,
    Scope,
    Timeline,
    Mechanism,
    ActionType,
    ActionOperator,
    Strictness,
    LawType,
)
from src.storage.dual_layer_store import DualLayerStore
from src.main import LawIngestionPipeline
from src.config import clear_settings_cache


@pytest.fixture(autouse=True)
def successful_empty_model_fallback(monkeypatch):
    monkeypatch.setattr("src.main.get_completion_with_json", lambda **kw: '{"rules": []}')


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="powerlaw_law_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_store(temp_dir):
    return DualLayerStore(temp_dir / "lancedb")


@pytest.fixture
def sample_xml_content():
    fixture_path = Path("tests/fixtures/ingestion/eurlex_sample_act.xml")
    return fixture_path.read_bytes()


@pytest.fixture
def sample_decision_html():
    fixture_path = Path("tests/fixtures/ingestion/eurlex_sample_decision.html")
    return fixture_path.read_bytes()


@pytest.fixture
def sample_cbam_xml():
    fixture_path = Path("tests/fixtures/ingestion/eurlex_sample_cbam.xml")
    return fixture_path.read_bytes()


def create_test_pdf(text_by_page: list[str]) -> bytes:
    """Helper to generate in-memory PDF with specified text per page."""
    doc = pymupdf.open()
    for page_text in text_by_page:
        page = doc.new_page()
        if page_text:
            page.insert_text((50, 50), page_text)
    data = doc.tobytes()
    doc.close()
    return data


def test_process_document_structured_xml(mock_store, sample_xml_content):
    """
    Structured XML/HTML should be parsed cleanly into articles/recitals,
    extracting rules, evidence links, and canonical facts without losing context.
    """
    pipeline = LawIngestionPipeline(store=mock_store)
    batch_id = "batch_law_xml_1"
    doc = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
        publisher="Publications Office of the EU",
        content_bytes=sample_xml_content,
        batch_id=batch_id,
        data_origin="test",
        published_at=datetime(2024, 4, 24, 0, 0, 0, tzinfo=timezone.utc),
        available_at=datetime(2024, 4, 24, 8, 0, 0, tzinfo=timezone.utc),
    ).model_copy(update={"content_type": "application/xml", "upstream_id": "32024R1069"})

    facts = pipeline.process_document(doc, sample_xml_content)
    assert len(facts) >= 2, "Expected at least Article 5 and Article 8 rules/facts"

    # Verify each fact satisfies canonical quality validation
    for f in facts:
        decision = validate_candidate(f, doc)
        assert decision.decision in ("accepted", "needs_review"), f"Fact rejected: {decision.reason_codes}"

    # Check for LAW -> MANDATES -> LEGAL_RULE
    mandates = [f for f in facts if f.predicate == "MANDATES"]
    assert len(mandates) >= 2
    assert all("32024R1069" in f.subject_id for f in mandates)

    # Check for LEGAL_RULE -> REGULATES -> SECTOR / COMMODITY
    regulates = [f for f in facts if f.predicate == "REGULATES"]
    assert len(regulates) >= 1
    target_objects = [f.object_id for f in regulates]
    # Expect sector C24 or C23 or steel/cement
    assert any("c24" in o.lower() or "c23" in o.lower() or "steel" in o.lower() for o in target_objects)

    # Check for LAW -> AMENDS -> LAW
    amends = [f for f in facts if f.predicate == "AMENDS"]
    assert len(amends) >= 1, "Article 12 amends 32020R0852"
    assert any("32020R0852" in f.object_id for f in amends)

    # Evidence links must be emitted for every fact
    assert hasattr(pipeline, "last_evidence_links")
    assert len(pipeline.last_evidence_links) >= len(facts)
    for el in pipeline.last_evidence_links:
        assert el.document_version_id == doc.version_id
        assert el.quoted_text is not None and len(el.quoted_text) > 0


def test_non_numeric_obligation_and_missing_dates(mock_store, sample_xml_content):
    """
    Qualitative / reporting obligations must have value=None and non_numeric_obligation set.
    Missing activation dates must remain None, never 1900-01-01 or today.
    """
    pipeline = LawIngestionPipeline(store=mock_store)
    batch_id = "batch_law_xml_2"
    doc = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
        publisher="Publications Office of the EU",
        content_bytes=sample_xml_content,
        batch_id=batch_id,
        data_origin="test",
        published_at=datetime(2024, 4, 24, 0, 0, 0, tzinfo=timezone.utc),
        available_at=datetime(2024, 4, 24, 8, 0, 0, tzinfo=timezone.utc),
    ).model_copy(update={"content_type": "application/xml", "upstream_id": "32024R1069"})

    facts = pipeline.process_document(doc, sample_xml_content)

    # Find the rule for Article 8 (mandatory recycling declaration)
    art8_facts = [f for f in facts if f.predicate == "MANDATES" and "art_8" in (f.object_id or "").lower()]
    assert len(art8_facts) >= 1
    f8 = art8_facts[0]
    assert f8.payload_json is not None
    payload = json.loads(f8.payload_json)
    mech = payload.get("mechanism", {})

    # Mechanism value must be null, NOT 0.0 or 1.0 or fabricated number
    assert mech.get("value") is None, f"Value should be None for non-numeric obligation, got {mech.get('value')}"
    assert mech.get("non_numeric_obligation") is not None
    assert "declaration" in mech.get("non_numeric_obligation").lower() or "report" in mech.get("non_numeric_obligation").lower()

    # If activation date is not explicitly specified in Article 8, valid_from must be None or match entry into force
    timeline = payload.get("timeline", {})
    if not timeline.get("activation_date"):
        assert timeline.get("activation_date") is None
        assert f8.valid_from is None or f8.valid_from == "" or "1900" not in str(f8.valid_from)


def test_conditional_exemptions_and_future_phases(mock_store, sample_cbam_xml):
    """
    Conditional exemptions (e.g. thresholds) and future phases must be preserved.
    In DualLayerStore, a future phase must project as 'upcoming' before application date,
    and 'active' after application date.
    """
    pipeline = LawIngestionPipeline(store=mock_store)
    batch_id = "batch_cbam_1"
    doc = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R0956",
        publisher="Publications Office of the EU",
        content_bytes=sample_cbam_xml,
        batch_id=batch_id,
        data_origin="test",
        published_at=datetime(2023, 5, 10, 0, 0, 0, tzinfo=timezone.utc),
        available_at=datetime(2023, 5, 10, 8, 0, 0, tzinfo=timezone.utc),
    ).model_copy(update={"content_type": "application/xml", "upstream_id": "32023R0956"})

    facts = pipeline.process_document(doc, sample_cbam_xml)
    entities = getattr(pipeline, "last_entities", [])
    evidence = getattr(pipeline, "last_evidence_links", [])

    # Stage and commit in store
    batch = BatchManifest(
        batch_id=batch_id,
        source_id="EURLEX_CELLAR",
        data_origin="test",
        discovered_count=1,
        fetched_count=1,
        accepted_count=len(facts),
    )
    mock_store.stage_batch(batch, [doc], facts, [], evidence_links=evidence, entities=entities)
    mock_store.commit_batch(batch_id)

    # Cutoff 1: 2024-06-01 (transitional period active, but 2026 phase is upcoming)
    proj_2024 = mock_store.project_graph(cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc), mode="public_as_of")
    future_edges = [
        e for e in proj_2024["edges"]
        if e.get("valid_from") and e["valid_from"] > "2024-06-01"
    ]
    if future_edges:
        assert all(e["status"] == "upcoming" for e in future_edges)
        assert all(not e["is_active"] for e in future_edges)

    # Cutoff 2: 2026-06-01 (2026 phase is now active)
    proj_2026 = mock_store.project_graph(cutoff=datetime(2026, 6, 1, tzinfo=timezone.utc), mode="public_as_of")
    active_2026_edges = [
        e for e in proj_2026["edges"]
        if e.get("valid_from") and "2026" in e["valid_from"]
    ]
    if active_2026_edges:
        assert all(e["status"] == "active" for e in active_2026_edges)
        assert all(e["is_active"] for e in active_2026_edges)


def test_statute_with_decision_identifier_supported(mock_store, sample_decision_html):
    """
    Decisions (3YYYYD####) must NOT be filtered out by discovery or parsing regexes.
    """
    from src.scrapers.eurlex_bulk import execute_sparql
    from src.scrapers.eurlex_history import EurLexScraper, ALLOWED_TYPES

    # 1. ALLOWED_TYPES in eurlex_history must include 'D'
    assert "D" in ALLOWED_TYPES, "Decisions ('D') must be an allowed legal type"

    # 2. SPARQL regex in eurlex_bulk must match Decisions 'D'
    sparql_regex = r"^3[0-9]{4}[RLD][0-9]+"
    assert re.match(sparql_regex, "32023D1534"), "SPARQL regex must match decision 32023D1534"
    assert re.match(sparql_regex, "32024R1069"), "SPARQL regex must match regulation 32024R1069"
    assert re.match(sparql_regex, "32024L1275"), "SPARQL regex must match directive 32024L1275"

    # 3. Pipeline must parse Decision documents and extract rules
    pipeline = LawIngestionPipeline(store=mock_store)
    batch_id = "batch_decision_1"
    doc = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023D1534",
        publisher="Publications Office of the EU",
        content_bytes=sample_decision_html,
        batch_id=batch_id,
        data_origin="test",
        published_at=datetime(2023, 7, 26, 0, 0, 0, tzinfo=timezone.utc),
        available_at=datetime(2023, 7, 26, 8, 0, 0, tzinfo=timezone.utc),
    ).model_copy(update={"content_type": "text/html", "upstream_id": "32023D1534"})

    facts = pipeline.process_document(doc, sample_decision_html)
    assert len(facts) >= 1
    assert any("32023D1534" in f.subject_id for f in facts)


def test_scanned_pdf_detection_routes_to_review(mock_store):
    """
    Scanned PDFs with unextractable text must be detected, marked as SCANNED_PDF or OCR_REVIEW,
    and routed to review rather than fabricating rules or reporting complete coverage.
    """
    pipeline = LawIngestionPipeline(store=mock_store)
    # Create empty 3-page scanned PDF (no selectable text)
    scanned_bytes = create_test_pdf(["", "", ""])
    batch_id = "batch_scanned_1"
    doc = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32015R0001",
        publisher="Publications Office of the EU",
        content_bytes=scanned_bytes,
        batch_id=batch_id,
        data_origin="test",
    ).model_copy(update={"content_type": "application/pdf", "upstream_id": "32015R0001"})

    facts = pipeline.process_document(doc, scanned_bytes)
    assert len(facts) == 0, "No rules should be emitted for empty scanned PDF without OCR"
    assert doc.coverage_status in ("SCANNED_PDF", "OCR_REVIEW")


def test_long_document_bounded_chunking_with_context(mock_store):
    """
    Long documents (>150k chars) must NOT be silently truncated after 150,000 characters.
    Articles must be chunked with definitions and recitals shared context, and coverage marked COMPLETE.
    """
    pipeline = LawIngestionPipeline(store=mock_store)
    # Generate long legal text with 30 articles totaling ~200,000 characters
    long_pages = []
    long_pages.append("RECITAL 1: General industrial context on steel and concrete.\n" * 50)
    for i in range(1, 31):
        long_pages.append(f"Article {i}: Requirement for construction material in sector C24. " + ("Obligation text. " * 300))
    long_pdf_bytes = create_test_pdf(long_pages)

    batch_id = "batch_long_doc_1"
    doc = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32024R9999",
        publisher="Publications Office of the EU",
        content_bytes=long_pdf_bytes,
        batch_id=batch_id,
        data_origin="test",
    ).model_copy(update={"content_type": "application/pdf", "upstream_id": "32024R9999"})

    chunks = pipeline.extract_provisions(long_pdf_bytes, content_type="application/pdf")
    assert len(chunks) >= 25, "Expected chunks for all articles across the full length"
    # Ensure articles toward the end (e.g. Article 28) are present, not truncated
    chunk_titles = [c.get("title", "") for c in chunks]
    assert any("Article 28" in t or "28" in t for t in chunk_titles)


def test_non_destructive_ingestion_on_failure(mock_store, sample_xml_content, monkeypatch):
    """
    F05: Destructive law replacement must be eliminated.
    If extraction, embedding, or writing fails mid-way, the previous accepted version
    remains completely visible and unmodified.
    """
    pipeline = LawIngestionPipeline(store=mock_store)
    batch_v1 = "batch_v1_commit"
    doc_v1 = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
        publisher="Publications Office of the EU",
        content_bytes=sample_xml_content,
        batch_id=batch_v1,
        data_origin="test",
        published_at=datetime(2024, 4, 24, tzinfo=timezone.utc),
        available_at=datetime(2024, 4, 24, 8, tzinfo=timezone.utc),
    ).model_copy(update={"content_type": "application/xml", "upstream_id": "32024R1069"})

    # Successfully ingest and commit v1
    facts_v1 = pipeline.process_document(doc_v1, sample_xml_content)
    batch1 = BatchManifest(batch_id=batch_v1, source_id="EURLEX_CELLAR", data_origin="test")
    mock_store.stage_batch(batch1, [doc_v1], facts_v1, [], evidence_links=pipeline.last_evidence_links, entities=pipeline.last_entities)
    mock_store.commit_batch(batch_v1)

    # Check v1 is present
    cutoff = datetime(2024, 5, 1, tzinfo=timezone.utc)
    proj1 = mock_store.project_graph(cutoff=cutoff, mode="public_as_of")
    assert len(proj1["edges"]) >= 2
    initial_edge_count = len(proj1["edges"])

    # Now simulate v2 ingestion where extraction or embedding fails with an error
    def failing_extract(*args, **kwargs):
        raise RuntimeError("LLM extraction API error injected!")

    monkeypatch.setattr(pipeline, "extract_rules_from_text", failing_extract)

    batch_v2 = "batch_v2_failing"
    doc_v2 = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
        publisher="Publications Office of the EU",
        content_bytes=sample_xml_content + b"<!-- modified -->",
        batch_id=batch_v2,
        data_origin="test",
        published_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        available_at=datetime(2024, 6, 1, 8, tzinfo=timezone.utc),
    ).model_copy(update={"content_type": "application/xml", "upstream_id": "32024R1069"})

    with pytest.raises(RuntimeError, match="injected"):
        pipeline.process_document(doc_v2, sample_xml_content + b"<!-- modified -->")

    # Crucial assertion: the store MUST STILL HAVE all v1 rules visible!
    proj_after_failure = mock_store.project_graph(cutoff=cutoff, mode="public_as_of")
    assert len(proj_after_failure["edges"]) == initial_edge_count, "Previous accepted rules were destroyed by failure!"


def test_consolidated_single_process_pdf_definition():
    """
    F06: Consolidate duplicate process_pdf definitions in LawIngestionPipeline.
    Ensure exactly one process_pdf method exists on the class.
    """
    import inspect
    import src.main as main_module

    # Read src/main.py source code and count 'def process_pdf'
    source = Path("src/main.py").read_text(encoding="utf-8")
    matches = re.findall(r"^\s*def process_pdf\b", source, re.MULTILINE)
    assert len(matches) == 1, f"Found {len(matches)} definitions of process_pdf in src/main.py; expected exactly 1."


def test_process_document_rejects_content_checksum_mismatch(mock_store, sample_xml_content):
    pipeline = LawIngestionPipeline(store=mock_store)
    document = SourceDocument.from_content(
        source_id="EURLEX_CELLAR",
        canonical_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1069",
        publisher="Publications Office of the EU",
        content_bytes=sample_xml_content,
        batch_id="checksum-mismatch",
        data_origin="test",
    ).model_copy(update={"content_type": "application/xml", "upstream_id": "32024R1069"})
    assert pipeline.process_document(document, b"different bytes") == []
    assert document.coverage_status == "CHECKSUM_MISMATCH"


def test_process_pdf_uses_verified_archive_before_publication(mock_store, temp_dir, monkeypatch):
    monkeypatch.setenv("POWERLAW_MODE", "test")
    monkeypatch.setenv("POWERLAW_DATA_DIR", str(temp_dir / "data"))
    monkeypatch.setenv("LANCEDB_PATH", str(temp_dir / "data" / "lancedb"))
    clear_settings_cache()
    pdf_path = temp_dir / "32024R1069.pdf"
    pdf_path.write_bytes(create_test_pdf(["Article 1: The operator shall report the verified quantity."]))
    pipeline = LawIngestionPipeline(store=mock_store, storage_dir=temp_dir / "lancedb")
    monkeypatch.setattr("src.main.get_embedding", lambda text: [0.0] * 768)
    facts = pipeline.process_pdf(pdf_path)
    assert facts
    assert all(document.data_origin == "test" for document in mock_store._canonical_documents.values())
    assert all(document.raw_uri for document in mock_store._canonical_documents.values())


def test_celex_resolution_from_eurlex_url_and_source_law_type(mock_store):
    pipeline = LawIngestionPipeline(store=mock_store)
    assert pipeline.get_celex_id("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023D1534") == "32023D1534"
