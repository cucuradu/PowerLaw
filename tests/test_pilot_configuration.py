"""Pilot settings and independent regional ADC clients."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.auth.credentials import AnonymousCredentials

from src.config import Settings, clear_settings_cache, get_settings
from src.utils import api_wrapper as api
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger
from google.genai import types


def test_total_euro_budget_is_distinct_from_run_budget(monkeypatch):
    monkeypatch.setenv("POWERLAW_EXTRACTION_TOTAL_BUDGET_EUR", "100")
    monkeypatch.setenv("POWERLAW_EXTRACTION_EUR_USD_RATE", "1.1225")
    monkeypatch.setenv("POWERLAW_EXTRACTION_BUDGET_USD", "1")
    clear_settings_cache()
    try:
        settings = get_settings()
        assert settings.extraction_total_budget_eur == 100
        assert settings.extraction_total_budget_usd == pytest.approx(101.025)
        assert settings.extraction_budget_usd == 1
    finally:
        clear_settings_cache()


@pytest.mark.parametrize("updates", [{"extraction_total_budget_eur": -1},
    {"extraction_eur_usd_rate": 0}, {"extraction_eur_usd_rate": float("nan")}])
def test_invalid_budget_conversion_cannot_enable_spending(updates):
    with pytest.raises(ValueError):
        Settings(**updates)


def test_generation_and_embedding_use_distinct_adc_regions(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "offline-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_CLOUD_EMBEDDING_LOCATION", "us-central1")
    monkeypatch.setattr(api, "_client", None)
    monkeypatch.setattr(api, "_embedding_client", None, raising=False)
    factory = MagicMock(side_effect=[SimpleNamespace(name="generation"), SimpleNamespace(name="embedding")])
    monkeypatch.setattr(api, "genai", SimpleNamespace(Client=factory))
    credentials = AnonymousCredentials()
    monkeypatch.setattr(api.google.auth, "default", lambda **kw: (credentials, "offline-project"))
    assert api.initialize_api().name == "generation"
    assert api.initialize_embedding_api().name == "embedding"
    assert api.initialize_embedding_api().name == "embedding"
    assert [c.kwargs["location"] for c in factory.call_args_list] == ["global", "us-central1"]
    assert all(c.kwargs["vertexai"] and c.kwargs["credentials"] is credentials
               and "api_key" not in c.kwargs for c in factory.call_args_list)


def test_budget_context_enforces_total_across_calls_and_new_runs(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "ledger.sqlite")
    token = api.set_ingestion_budget_context(ledger, "first", 1, input_price_usd_per_1k=1,
                                            output_price_usd_per_1k=1, total_budget_usd=1.5)
    try:
        ledger.reserve_cost("first", "one", 1, 1, price_known=True)
        ledger.settle_cost("first", "one", 1)
    finally:
        api.reset_ingestion_budget_context(token)
    token = api.set_ingestion_budget_context(ledger, "second", 1, input_price_usd_per_1k=1,
                                            output_price_usd_per_1k=1, total_budget_usd=1.5)
    try:
        with pytest.raises(BudgetError, match="total|overall|project"):
            ledger.reserve_cost("second", "two", .6, 1, price_known=True)
    finally:
        api.reset_ingestion_budget_context(token)


def test_truncated_json_is_not_accepted_as_a_complete_extraction():
    response = types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=types.FinishReason.MAX_TOKENS,
        content=types.Content(parts=[types.Part(text='{"rules":[]}')]))])
    with pytest.raises(ValueError, match="truncat|token"):
        api._response_text(response)


@pytest.mark.parametrize("limit", [-1, float("nan"), float("inf")])
def test_invalid_total_limit_cannot_bypass_authorization(tmp_path, limit):
    with pytest.raises(BudgetError):
        api.set_ingestion_budget_context(IngestionJobLedger(tmp_path / "jobs.sqlite"), "r", 1,
            input_price_usd_per_1k=1, output_price_usd_per_1k=1, total_budget_usd=limit)


@pytest.mark.parametrize("total,settled", [(510, True), (511, False), (None, False)])
def test_omitted_thoughts_need_a_matching_provider_total(tmp_path, monkeypatch, total, settled):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    client = MagicMock()
    client.models.count_tokens.return_value = types.CountTokensResponse(total_tokens=500)
    client.models.generate_content.return_value = SimpleNamespace(text="ok", usage_metadata=SimpleNamespace(
        prompt_token_count=500, candidates_token_count=10, thoughts_token_count=None, total_token_count=total))
    monkeypatch.setattr(api, "initialize_api", lambda: client)
    token = api.set_ingestion_budget_context(ledger, "r", 1, input_price_usd_per_1k=.0015,
                                            output_price_usd_per_1k=.0075, total_budget_usd=1)
    try:
        api.get_completion("fixture", max_output_tokens=32)
        assert (ledger.spending_summary()["unresolved_reservations"] == 0) is settled
        if settled:
            assert ledger.spent_cost("r") == pytest.approx(.000825)
    finally:
        api.reset_ingestion_budget_context(token)


def test_schema_bytes_cannot_bypass_a_small_input_budget(tmp_path, monkeypatch):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    client = MagicMock()
    client.models.count_tokens.return_value = types.CountTokensResponse(total_tokens=500)
    client.models.generate_content.return_value = SimpleNamespace(text="{}", usage_metadata=None)
    monkeypatch.setattr(api, "initialize_api", lambda: client)
    token = api.set_ingestion_budget_context(ledger, "r", .6, input_price_usd_per_1k=1,
                                            output_price_usd_per_1k=.0001, total_budget_usd=1)
    try:
        with pytest.raises(BudgetError):
            api.get_completion_with_json("fixture", response_schema={"type": "object", "description": "x" * 2000})
        client.models.generate_content.assert_not_called()
    finally:
        api.reset_ingestion_budget_context(token)
