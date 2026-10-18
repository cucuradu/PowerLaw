"""Tests for LanceDB and Pydantic schemas in src/schemas/lancedb.py."""

import json
from src.schemas.lancedb import LawChunk, NewsItem
from src.schemas.extraction import NewsEntities


def test_law_chunk_schema():
    """Verify that LawChunk model instantiates and exports json schema."""
    schema = LawChunk.model_json_schema()
    assert "properties" in schema
    assert "law_id" in schema["properties"]
    assert "vector" in schema["properties"]
    assert "gnn_rule_json" in schema["properties"]

    chunk = LawChunk(
        vector=[0.0] * 768,
        law_id="32024R9999",
        chunk_id="Article 1",
        summary="Test regulation chunk",
        minio_path="s3://raw/laws/32024R9999.pdf",
        source_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R9999",
        gnn_rule_json=json.dumps({"rule_id": "r1", "logic": "IF condition THEN action"}),
        rule_type="REGULATORY",
        jurisdiction="EU",
        valid_from="2024-01-01",
        valid_to="",
        citations=["32023R1234"],
    )
    assert chunk.law_id == "32024R9999"
    assert len(chunk.vector) == 768
    assert chunk.rule_type == "REGULATORY"


def test_news_item_schema():
    """Verify that NewsItem model instantiates with nested entities."""
    entities = NewsEntities(
        companies=["Company A"],
        sectors=["Energy"],
        geographies=["Germany"],
        commodities=["Electricity"],
    )
    news = NewsItem(
        vector=[0.0] * 768,
        news_id="news_001",
        semantic_summary="Power prices drop due to renewable surge",
        headline="German power prices decline",
        source_url="https://example.com/news/1",
        source_name="Energy Daily",
        entities=entities,
        actions_json="[]",
        reactions_json="[]",
    )
    assert news.news_id == "news_001"
    assert news.entities.sectors == ["Energy"]
    assert len(news.vector) == 768
