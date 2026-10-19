"""Offline compatibility tests for the Google GenAI SDK adapter."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import httpx
from google import genai as real_genai
from google.genai import errors, types
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field
from tenacity import wait_none
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "ignored-legacy-key")
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-cloud-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setattr("google.auth.default", MagicMock(return_value=(AnonymousCredentials(), "detected-project")))
    with patch("dotenv.load_dotenv", return_value=False):
        module = importlib.import_module("src.utils.api_wrapper")
        module = importlib.reload(module)
    client = MagicMock()
    client.models.count_tokens.return_value = types.CountTokensResponse(total_tokens=1000)
    client.models.generate_content.return_value.usage_metadata = SimpleNamespace(prompt_token_count=1000, candidates_token_count=1, thoughts_token_count=0)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(module, "genai", SimpleNamespace(Client=factory))
    monkeypatch.setattr(module, "_client", None, raising=False)
    for name in (
        "get_completion", "get_completion_with_json", "get_embedding",
        "get_embeddings_batch", "upload_to_gemini", "get_completion_multimodal",
    ):
        monkeypatch.setattr(getattr(module, name).retry, "wait", wait_none())
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.ensure_run("test-run", "TEST", 10.0)
    module.set_ingestion_budget_context(ledger, "test-run", 10.0, input_price_usd_per_1k=0.1, output_price_usd_per_1k=0.2, embedding_price_usd_per_1k=.1)
    return module, client, factory


def test_import_does_not_create_a_client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with patch("dotenv.load_dotenv", return_value=False), patch("google.genai.Client") as factory:
        import src.utils.api_wrapper as module
        importlib.reload(module)
        factory.assert_not_called()


def test_initialize_uses_adc_and_reuses_client(adapter):
    module, client, factory = adapter
    assert module.initialize_api() is client
    assert module.initialize_api() is client
    assert factory.call_count == 1
    assert factory.call_args.kwargs["vertexai"] is True
    assert factory.call_args.kwargs["project"] == "test-cloud-project"
    assert factory.call_args.kwargs["location"] == "us-central1"
    assert "api_key" not in factory.call_args.kwargs
    assert isinstance(factory.call_args.kwargs["credentials"], AnonymousCredentials)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_project_fails_without_key_fallback(adapter, monkeypatch, value):
    module, _, factory = adapter
    if value is None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    else:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", value)
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        module.initialize_api()
    factory.assert_not_called()


def test_text_prompt_uses_requested_flash_model(adapter):
    module, client, _ = adapter
    client.models.generate_content.return_value.text = "A useful answer"
    assert module.get_completion("Explain construction inflation", max_output_tokens=128) == "A useful answer"
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-3.8-flash"
    assert call["contents"] == "Explain construction inflation"
    assert call["config"].max_output_tokens == 128


def test_empty_prompt_and_empty_response_are_errors(adapter):
    module, client, _ = adapter
    with pytest.raises(ValueError):
        module.get_completion("   ")
    client.models.generate_content.assert_not_called()
    client.models.generate_content.return_value.text = None
    with pytest.raises(ValueError, match="Empty response"):
        module.get_completion("Hello")
    assert client.models.generate_content.call_count == 1


def test_structured_output_preserves_pydantic_constraints(adapter):
    module, client, _ = adapter

    class Result(BaseModel):
        confidence: float = Field(ge=0, le=1)

    client.models.generate_content.return_value.text = '{"confidence":0.8}'
    assert module.get_completion_with_json("Extract confidence", response_schema=Result) == '{"confidence":0.8}'
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema.properties["confidence"].maximum == 1
    assert Result.model_json_schema()["properties"]["confidence"]["maximum"] == 1


def test_single_and_batch_embeddings_preserve_dimensions_and_return_shapes(adapter):
    module, client, _ = adapter
    vector = [0.25] * 768
    client.models.embed_content.return_value.embeddings = [SimpleNamespace(values=vector, statistics=SimpleNamespace(token_count=7))]
    assert module.get_embedding("Cement") == vector
    config = client.models.embed_content.call_args.kwargs["config"]
    assert config.output_dimensionality == 768
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    client.models.embed_content.return_value.embeddings = [
        SimpleNamespace(values=vector, statistics=SimpleNamespace(token_count=7)), SimpleNamespace(values=vector, statistics=SimpleNamespace(token_count=7)),
    ]
    assert module.get_embeddings_batch(["Cement", "", "Steel"]) == [vector, vector]


def test_embedding_dimension_mismatch_is_rejected(adapter):
    module, client, _ = adapter
    client.models.embed_content.return_value.embeddings = [SimpleNamespace(values=[0.0] * 5)]
    with pytest.raises(ValueError, match="768"):
        module.get_embedding("Cement")


def test_upload_and_multimodal_use_inline_parts(adapter, tmp_path):
    module, client, _ = adapter
    path = tmp_path / "law.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    document = module.upload_to_gemini(str(path))
    assert document.inline_data.mime_type == "application/pdf"
    client.files.upload.assert_not_called()
    client.models.generate_content.return_value.text = '{"summary":"Law"}'
    assert module.get_completion_multimodal("Summarize", [document], use_pro=False, response_schema={"type":"object"}) == '{"summary":"Law"}'
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-3.8-flash"
    assert call["contents"] == ["Summarize", document]
    assert call["config"].response_schema is not None


def test_authentication_errors_are_not_retried(adapter):
    module, client, _ = adapter
    client.models.generate_content.side_effect = errors.ClientError(401, {"error":{"message":"Invalid key"}})
    with pytest.raises(errors.ClientError):
        module.get_completion("Hello")
    assert client.models.generate_content.call_count == 1


def test_transient_generation_errors_cannot_retry_unknown_cost(adapter):
    module, client, _ = adapter
    client.models.generate_content.side_effect = [
        errors.ServerError(503, {"error":{"message":"Unavailable"}}),
        SimpleNamespace(text="Recovered"),
    ]
    with pytest.raises(BudgetError, match="unresolved"):
        module.get_completion("Hello")
    assert client.models.generate_content.call_count == 1


def test_missing_adc_fails_without_key_fallback(adapter, monkeypatch):
    module, client, factory = adapter
    monkeypatch.setattr("google.auth.default", MagicMock(side_effect=DefaultCredentialsError("Run ADC login")))
    with pytest.raises(DefaultCredentialsError):
        module.initialize_api()
    factory.assert_not_called()


def test_vertex_pdf_uses_inline_bytes_without_files_service(adapter, monkeypatch, tmp_path):
    module, client, _ = adapter
    path = tmp_path / "law.pdf"
    content = b"%PDF-1.4\n"
    path.write_bytes(content)
    document = module.upload_to_gemini(str(path))
    assert document.inline_data.data == content
    assert document.inline_data.mime_type == "application/pdf"
    client.files.upload.assert_not_called()


def test_permission_failure_does_not_retry_or_fall_back(adapter):
    module, client, factory = adapter
    client.models.generate_content.side_effect = errors.ClientError(403, {"error":{"message":"Blocked API method"}})
    with pytest.raises(errors.ClientError):
        module.get_completion("Hello")
    assert client.models.generate_content.call_count == 1
    assert factory.call_count == 1


def test_ingestion_budget_context_blocks_unknown_or_zero_budget(adapter, tmp_path):
    module, client, _ = adapter
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.ensure_run("run", "TEST", 0.0)
    token = module.set_ingestion_budget_context(ledger, "run", 0.0, input_price_usd_per_1k=None, output_price_usd_per_1k=None)
    try:
        with pytest.raises(BudgetError):
            module.get_completion("paid attempt")
    finally:
        module.reset_ingestion_budget_context(token)
    client.models.generate_content.assert_not_called()


def test_ingestion_budget_reserves_each_paid_attempt(adapter, tmp_path):
    module, client, _ = adapter
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.ensure_run("run", "TEST", 1.0)
    client.models.generate_content.return_value.text = "ok"
    token = module.set_ingestion_budget_context(ledger, "run", 1.0, input_price_usd_per_1k=0.1, output_price_usd_per_1k=0.2)
    try:
        assert module.get_completion("paid attempt", max_output_tokens=10) == "ok"
    finally:
        module.reset_ingestion_budget_context(token)
    assert ledger.spent_cost("run") > 0


def test_paid_calls_without_context_are_blocked(adapter):
    module, client, _ = adapter
    token = module._budget_context.set(None)
    try:
        with pytest.raises(BudgetError):
            module.get_completion("blocked")
        with pytest.raises(BudgetError):
            module.get_embedding("blocked")
    finally:
        module._budget_context.reset(token)
    client.models.generate_content.assert_not_called()
    client.models.embed_content.assert_not_called()


def test_failed_paid_attempt_keeps_unknown_usage_unresolved(adapter):
    module, client, _ = adapter
    ledger = module._budget_context.get()["ledger"]
    client.models.generate_content.side_effect = errors.ClientError(401, {"error": {"message": "invalid"}})
    with pytest.raises(errors.ClientError):
        module.get_completion("will fail")
    with ledger._connection() as db:
        statuses = [row[0] for row in db.execute("SELECT status FROM cost_reservations WHERE run_id='test-run'").fetchall()]
    assert statuses == ["reserved"]


def test_reconciles_actual_usage_tokens(adapter):
    module, client, _ = adapter
    ledger = module._budget_context.get()["ledger"]
    response = SimpleNamespace(
        text="result",
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000,
            candidates_token_count=500,
            thoughts_token_count=100,
        ),
    )
    client.models.generate_content.return_value = response
    module.get_completion("calculate actual usage cost", max_output_tokens=1000)
    # input_price=0.1/1k, output_price=0.2/1k: 1000/1k*0.1 + 600/1k*0.2 = 0.1 + 0.12 = 0.22
    with ledger._connection() as db:
        row = db.execute("SELECT actual_usd FROM cost_reservations WHERE run_id='test-run' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert abs(row[0] - 0.22) < 1e-6


def test_unknown_usage_retains_conservative_reservation(adapter):
    module, client, _ = adapter
    ledger = module._budget_context.get()["ledger"]
    response = SimpleNamespace(text="result", usage_metadata=None)
    client.models.generate_content.return_value = response
    module.get_completion("unknown usage", max_output_tokens=1000)
    with ledger._connection() as db:
        row = db.execute("SELECT reserved_usd, actual_usd, status FROM cost_reservations WHERE run_id='test-run' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row[1] is None
    assert row[2] == "reserved"
    with pytest.raises(BudgetError, match="unresolved cost reservation"):
        module.get_completion("second call blocked")


def test_replaying_settled_request_id_raises_budget_error(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.ensure_run("run-1", "TEST", 5.0)
    ledger.reserve_cost("run-1", "req-1", 0.5, 5.0, price_known=True)
    ledger.settle_cost("run-1", "req-1", 0.5)
    with pytest.raises(BudgetError, match="status 'settled'"):
        ledger.reserve_cost("run-1", "req-1", 0.5, 5.0, price_known=True)


def test_non_finite_budget_and_cost_rejected(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    with pytest.raises(BudgetError, match="finite"):
        ledger.ensure_run("run-nan", "TEST", float("nan"))
    with pytest.raises(BudgetError, match="finite"):
        ledger.ensure_run("run-inf", "TEST", float("inf"))
    ledger.ensure_run("run-ok", "TEST", 10.0)
    with pytest.raises(BudgetError, match="finite"):
        ledger.reserve_cost("run-ok", "req-nan", float("nan"), 10.0, price_known=True)
    with pytest.raises(BudgetError, match="finite"):
        ledger.reserve_cost("run-ok", "req-nan2", 1.0, float("nan"), price_known=True)
    with pytest.raises(BudgetError, match="finite"):
        ledger.settle_cost("run-ok", "req-nan", float("nan"))


def test_connection_diagnostics_are_safe_and_budget_aware(adapter, monkeypatch):
    module, _, _ = adapter
    settings = SimpleNamespace(object_backend="local", extraction_budget_usd=0.0, extraction_input_price_usd_per_1k=None, extraction_output_price_usd_per_1k=None)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    result = module.check_connections(settings)
    assert result["gemini"]["status"] == "missing"
    assert result["object_storage"]["status"] == "working"
    assert all("key" not in json.dumps(result).lower() for key in ["key"])


def test_review_stays_on_flash_with_high_thinking(adapter):
    module, client, _ = adapter
    client.models.generate_content.return_value.text = "Reviewed"
    assert module.get_completion("Review a rule", use_pro=True) == "Reviewed"
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-3.8-flash"
    assert call["config"].thinking_config.thinking_level == types.ThinkingLevel.HIGH


def test_real_sdk_uses_vertex_project_route_and_ignores_inherited_api_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unused-ai-studio-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "unused-legacy-key")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}}],
        })

    with real_genai.Client(
        vertexai=True, project="test-cloud-project", location="us-central1",
        credentials=Credentials(token="test-access-token"),
        http_options=types.HttpOptions(
            api_version="v1", client_args={"transport": httpx.MockTransport(respond)},
        ),
    ) as client:
        response = client.models.generate_content(model="gemini-3.8-flash", contents="Hello")
    assert response.text == "OK"
    assert len(requests) == 1
    assert requests[0].url.host == "us-central1-aiplatform.googleapis.com"
    assert "/projects/test-cloud-project/" in requests[0].url.path
    assert "x-goog-api-key" not in requests[0].headers
