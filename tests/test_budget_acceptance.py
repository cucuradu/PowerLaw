"""Paid authorization regressions; provider calls are always offline doubles."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from google.genai import types
from google import genai
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field
from tenacity import wait_none

from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger
from src.utils import api_wrapper as api


@pytest.fixture
def paid(tmp_path, monkeypatch):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    client = MagicMock()
    client.models.count_tokens.return_value = types.CountTokensResponse(total_tokens=500)
    client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text="ok")]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=500, candidates_token_count=1, thoughts_token_count=0,
        ),
    )
    monkeypatch.setattr(api, "initialize_api", lambda: client)
    monkeypatch.setattr(api, "initialize_embedding_api", lambda: client)
    for name in ("get_completion", "get_completion_with_json", "get_completion_multimodal", "get_embedding", "get_embeddings_batch"):
        monkeypatch.setattr(getattr(api, name).retry, "wait", wait_none())
    token = api.set_ingestion_budget_context(ledger, "run", 10, input_price_usd_per_1k=1, output_price_usd_per_1k=1)
    yield ledger, client
    api.reset_ingestion_budget_context(token)


def test_ascii_prompt_cannot_dispatch_above_budget(paid):
    ledger, client = paid
    token = api.set_ingestion_budget_context(ledger, "ascii", .45, input_price_usd_per_1k=1, output_price_usd_per_1k=1)
    try:
        with pytest.raises(BudgetError):
            api.get_completion("x " * 500, max_output_tokens=1)
    finally:
        api.reset_ingestion_budget_context(token)
    client.models.generate_content.assert_not_called()


def test_lost_response_blocks_automatic_retry_and_reopened_run(paid):
    ledger, client = paid
    client.models.generate_content.side_effect = httpx.ReadTimeout("response lost")
    with pytest.raises(BudgetError, match="unresolved"):
        api.get_completion("attempted")
    assert client.models.generate_content.call_count == 1
    reopened = IngestionJobLedger(ledger.path)
    with pytest.raises(BudgetError, match="unresolved"):
        reopened.reserve_cost("run", "after-restart", .01, 10, price_known=True)
    with ledger._connection() as db:
        row = db.execute("SELECT actual_usd, status FROM cost_reservations").fetchone()
    assert tuple(row) == (None, "reserved")


def test_schema_pdf_and_named_thinking_share_counted_request_and_total_cap(paid):
    ledger, client = paid
    pdf = types.Part.from_bytes(data=b"%PDF-compressed", mime_type="application/pdf")
    schema = {"type": "object", "properties": {"finding": {"type": "string"}}}
    api.get_completion_multimodal("review", [pdf], use_pro=True, response_schema=schema)
    counted = client.models.count_tokens.call_args.kwargs
    generated = client.models.generate_content.call_args.kwargs
    assert counted["contents"] == generated["contents"] == ["review", pdf]
    assert counted["config"].generation_config.response_schema is not None
    assert generated["config"].max_output_tokens == 2048
    assert generated["config"].candidate_count == 1
    assert generated["config"].automatic_continuation is False
    with ledger._connection() as db:
        reserved = db.execute("SELECT reserved_usd FROM cost_reservations").fetchone()[0]
    assert reserved > 2.548  # Includes a schema bound beyond the provider's incomplete preflight count.


@pytest.mark.parametrize("count", [None, -1, 1.5, True])
def test_invalid_preflight_usage_never_dispatches(paid, count):
    _, client = paid
    client.models.count_tokens.return_value = SimpleNamespace(total_tokens=count)
    with pytest.raises(BudgetError):
        api.get_completion("invalid count")
    client.models.generate_content.assert_not_called()


@pytest.mark.parametrize("cap", [0, -1, 1.5, True])
def test_invalid_output_cap_never_dispatches(paid, cap):
    _, client = paid
    with pytest.raises((ValueError, BudgetError)):
        api.get_completion("invalid cap", max_output_tokens=cap)
    client.models.generate_content.assert_not_called()


def test_embedding_requires_its_own_verified_price(paid):
    _, client = paid
    with pytest.raises(BudgetError, match="embedding"):
        api.get_embedding("cement")
    client.models.embed_content.assert_not_called()


def test_missing_embedding_usage_blocks_next_dispatch(paid):
    ledger, client = paid
    token = api.set_ingestion_budget_context(ledger, "embed", 10, input_price_usd_per_1k=1, output_price_usd_per_1k=1, embedding_price_usd_per_1k=1)
    client.models.embed_content.return_value = types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=[0.] * 768)])
    try:
        assert len(api.get_embedding("cement")) == 768
        with pytest.raises(BudgetError, match="unresolved"):
            api.get_embedding("steel")
    finally:
        api.reset_ingestion_budget_context(token)
    with ledger._connection() as db:
        row = db.execute("SELECT reserved_usd, actual_usd, status FROM cost_reservations").fetchone()
    assert tuple(row) == (2.048, None, "reserved")


def test_embedding_settles_provider_usage_with_embedding_price(paid):
    ledger, client = paid
    token = api.set_ingestion_budget_context(ledger, "embed", 10, input_price_usd_per_1k=1, output_price_usd_per_1k=1, embedding_price_usd_per_1k=.1)
    client.models.embed_content.return_value = types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=[0.] * 768, statistics=types.ContentEmbeddingStatistics(token_count=7, truncated=False))])
    try:
        api.get_embedding("cement")
    finally:
        api.reset_ingestion_budget_context(token)
    assert ledger.spent_cost("embed") == pytest.approx(.0007)


def test_reserved_request_id_cannot_authorize_a_second_dispatch(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "ledger.sqlite")
    ledger.reserve_cost("r", "same", .1, 1, price_known=True)
    with pytest.raises(BudgetError):
        ledger.reserve_cost("r", "same", .1, 1, price_known=True)


def test_usage_above_authorized_bound_blocks_until_operator_reconciliation(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "ledger.sqlite")
    ledger.reserve_cost("r", "one", .1, 1, price_known=True)
    with pytest.raises(BudgetError, match="authorized"):
        ledger.settle_cost("r", "one", .2)
    assert ledger.spent_cost("r") == .2
    with pytest.raises(BudgetError):
        ledger.reserve_cost("r", "two", .1, 1, price_known=True)


def test_installed_sdk_counts_schema_and_pdf_before_generation(paid, monkeypatch):
    import json
    requests = []
    class Result(BaseModel):
        confidence: float = Field(ge=0, le=1)
    def respond(request):
        requests.append(request)
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 500})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"confidence":1}'}]}}], "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 1, "thoughtsTokenCount": 0}})
    with genai.Client(vertexai=True, project="offline", location="us-central1", credentials=Credentials(token="offline-fixture"), http_options=types.HttpOptions(client_args={"transport": httpx.MockTransport(respond)}, retry_options=types.HttpRetryOptions(attempts=1))) as client:
        monkeypatch.setattr(api, "initialize_api", lambda: client)
        pdf = types.Part.from_bytes(data=b"%PDF-test", mime_type="application/pdf")
        assert api.get_completion_multimodal("review", [pdf], response_schema=Result) == '{"confidence":1}'
    assert len(requests) == 2
    counted, generated = [json.loads(request.content) for request in requests]
    assert counted["contents"] == generated["contents"]
    assert counted["generationConfig"]["responseSchema"] == generated["generationConfig"]["responseSchema"]
    assert counted["generationConfig"]["responseSchema"]["properties"]["confidence"]["maximum"] == 1
