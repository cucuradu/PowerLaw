"""Gemini 3.8 Flash on Vertex AI, authenticated with local ADC."""

import os
import json
import contextvars
import math
import uuid
from pathlib import Path
from typing import Any, List, Optional

import httpx
import google.auth
from google.auth.exceptions import DefaultCredentialsError
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from pydantic import BaseModel
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger

load_dotenv()

EMBEDDING_MODEL = "gemini-embedding-001"
LLM_FLASH_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# Existing use_pro callers select deeper reasoning on Flash, without a paid fallback.
LLM_PRO_MODEL = LLM_FLASH_MODEL
_client: Optional[genai.Client] = None
_embedding_client: Optional[genai.Client] = None
_budget_context: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar("powerlaw_ingestion_budget", default=None)


def set_ingestion_budget_context(ledger: IngestionJobLedger, run_id: str, budget_usd: float, *, input_price_usd_per_1k: Optional[float], output_price_usd_per_1k: Optional[float], embedding_price_usd_per_1k: Optional[float] = None, total_budget_usd: Optional[float] = None):
    """Bind paid SDK calls to one durable ingestion run; returns a reset token."""
    if total_budget_usd is None:
        from src.config import get_settings
        total_budget_usd = get_settings().extraction_total_budget_usd
    if not math.isfinite(total_budget_usd) or total_budget_usd < 0:
        raise BudgetError("total spending limit must be finite and non-negative")
    if total_budget_usd > 0:
        ledger.configure_spending_limit(total_budget_usd)
    return _budget_context.set({"ledger": ledger, "run_id": run_id, "budget": budget_usd, "input": input_price_usd_per_1k, "output": output_price_usd_per_1k, "embedding": embedding_price_usd_per_1k})


def reset_ingestion_budget_context(token) -> None:
    _budget_context.reset(token)


def _reserve_paid_attempt(operation: str, contents: Any, config: Optional[types.GenerateContentConfig] = None, *, embedding_count: int = 0) -> tuple[IngestionJobLedger, str, str, float]:
    context = _budget_context.get()
    if context is None:
        raise BudgetError("paid SDK call requires an ingestion budget context")
    values = (context["budget"], context["embedding"]) if embedding_count else (context["budget"], context["input"], context["output"])
    if not all(value is not None and math.isfinite(float(value)) and float(value) > 0 for value in values):
        raise BudgetError("ingestion paid embedding requires a positive budget and verified embedding price" if embedding_count else "ingestion paid extraction requires a positive budget and verified prices")
    if embedding_count:
        # Vertex gemini-embedding-001 accepts at most 2048 tokens per input.
        reserved = 2048 * embedding_count / 1000.0 * context["embedding"]
    else:
        counted_config = types.GenerationConfig(**{
            key: value for key, value in config.model_dump(exclude_none=True).items()
            if key in types.GenerationConfig.model_fields
        })
        count = initialize_api().models.count_tokens(
            model=LLM_FLASH_MODEL, contents=contents,
            config=types.CountTokensConfig(generation_config=counted_config),
        ).total_tokens
        if type(count) is not int or count < 0:
            raise BudgetError("complete request token count is unavailable")
        if config.response_schema is not None:
            # Live CountTokens omits schema overhead; UTF-8 bytes conservatively bound its text tokens.
            schema = config.response_schema.model_dump(mode="json", exclude_none=True)
            count += len(json.dumps(schema, ensure_ascii=False).encode("utf-8")) + 64
        # max_output_tokens caps visible output and thinking together, for one candidate.
        reserved = count / 1000.0 * context["input"] + config.max_output_tokens / 1000.0 * context["output"]
    request_id = f"{operation}:{uuid.uuid4().hex}"
    context["ledger"].reserve_cost(context["run_id"], request_id, reserved, context["budget"], price_known=True)
    return context["ledger"], context["run_id"], request_id, reserved


def _settle_reservation(
    reservation: Optional[tuple[IngestionJobLedger, str, str, float]],
    response: Optional[Any] = None,
    *, expected_embeddings: int = 0,
) -> None:
    if not reservation:
        return
    ledger, run_id, request_id, reserved_cost = reservation
    context = _budget_context.get()
    input_price = float(context["input"]) if context and context.get("input") is not None else 0.0
    output_price = float(context["output"]) if context and context.get("output") is not None else 0.0

    if response is None:
        # A dispatched request may have been billed even if its response was lost.
        return

    if expected_embeddings:
        embeddings = getattr(response, "embeddings", None) or []
        counts = [getattr(getattr(item, "statistics", None), "token_count", None) for item in embeddings]
        if len(counts) == expected_embeddings and all(_valid_token_count(value) for value in counts):
            ledger.settle_cost(run_id, request_id, sum(counts) / 1000.0 * float(context["embedding"]))
        return

    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        # Unknown usage: DO NOT settle as verified actual cost.
        # Preserve reservation as active/unresolved so ledger blocks new dispatch.
        return

    p_tokens = getattr(usage, "prompt_token_count", None)
    c_tokens = getattr(usage, "candidates_token_count", None)
    t_tokens = getattr(usage, "thoughts_token_count", None)
    total_tokens = getattr(usage, "total_token_count", None)
    if (t_tokens is None and all(_valid_token_count(value) for value in (p_tokens, c_tokens, total_tokens))
            and total_tokens == p_tokens + c_tokens):
        t_tokens = 0
    if not all(_valid_token_count(value) for value in (p_tokens, c_tokens, t_tokens)):
        return

    total_output_tokens = c_tokens + t_tokens
    calculated = (float(p_tokens) / 1000.0) * input_price + (float(total_output_tokens) / 1000.0) * output_price
    if math.isfinite(calculated) and calculated >= 0:
        ledger.settle_cost(run_id, request_id, float(calculated))


def _valid_token_count(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0 and int(value) == value


def _vertex_client(location: str) -> genai.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise ValueError("Set GOOGLE_CLOUD_PROJECT in your environment or private .env file.")
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        credentials=credentials,
        http_options=types.HttpOptions(
            timeout=600_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def initialize_api() -> genai.Client:
    """Initialize generation lazily with ADC; API keys are not required."""
    global _client
    if _client is None:
        _client = _vertex_client(os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip())
    return _client


def initialize_embedding_api() -> genai.Client:
    """The existing embedding model requires a regional endpoint, separate from Flash."""
    location = os.getenv("GOOGLE_CLOUD_EMBEDDING_LOCATION", "").strip()
    if not location or location == os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip():
        return initialize_api()
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = _vertex_client(location)
    return _embedding_client


def resolve_thinking_effort(thinking_level: Optional[str] = None, deep_reasoning: bool = False) -> str:
    """Keep the existing dynamic/low/medium/high reasoning preference."""
    level = (thinking_level or os.getenv("GEMINI_THINKING_LEVEL", "dynamic")).lower()
    if level == "dynamic":
        return "high" if deep_reasoning else "low"
    return level if level in ("low", "medium", "high") else "low"


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError) or (
        isinstance(exc, errors.APIError) and exc.code in (429, 500, 502, 503, 504)
    )


retry_config = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


def _generation_config(
    *, use_pro: bool, thinking_level: Optional[str] = None,
    max_output_tokens: Optional[int] = None, json_output: bool = False,
    response_schema: Optional[Any] = None,
) -> types.GenerateContentConfig:
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer")
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        response_schema = types.Schema.from_json_schema(json_schema=types.JSONSchema.model_validate(response_schema.model_json_schema()), api_option="VERTEX_AI", raise_error_on_unsupported_field=True)
    elif isinstance(response_schema, dict):
        response_schema = types.Schema.from_json_schema(json_schema=types.JSONSchema.model_validate(response_schema), api_option="VERTEX_AI", raise_error_on_unsupported_field=True)
    effort = resolve_thinking_effort(thinking_level, deep_reasoning=use_pro)
    # Gemini 2.5 uses token budgets; 3.x uses named thinking levels.
    thinking = (
        types.ThinkingConfig(thinking_budget={"low": 0, "medium": 1024, "high": 8192}[effort])
        if LLM_FLASH_MODEL.startswith("gemini-2.5-")
        else types.ThinkingConfig(thinking_level=effort.upper())
    )
    return types.GenerateContentConfig(
        temperature=0.1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=max_output_tokens,
        candidate_count=1,
        automatic_continuation=False,
        thinking_config=thinking,
        response_mime_type="application/json" if json_output else None,
        response_schema=response_schema,
        safety_settings=[
            types.SafetySetting(category=category, threshold="BLOCK_NONE")
            for category in (
                "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ],
    )


def _response_text(response: Any) -> str:
    if any(candidate.finish_reason == types.FinishReason.MAX_TOKENS
           for candidate in (getattr(response, "candidates", None) or [])):
        raise ValueError("Gemini response was truncated at its token limit; extraction is incomplete")
    text = response.text
    if not text or not text.strip():
        raise ValueError("Empty response from Gemini API")
    return text


@retry_config
def get_completion(
    prompt: str, use_pro: bool = False,
    max_output_tokens: Optional[int] = None, thinking_level: Optional[str] = None,
) -> str:
    """Send text to Gemini 3.8 Flash; use_pro selects higher thinking, not another model."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    config = _generation_config(use_pro=use_pro, thinking_level=thinking_level, max_output_tokens=1024 if max_output_tokens is None else max_output_tokens)
    reservation = _reserve_paid_attempt("completion", prompt, config)
    response = None
    try:
        response = initialize_api().models.generate_content(
            model=LLM_FLASH_MODEL,
            contents=prompt,
            config=config,
        )
        result = _response_text(response)
    finally:
        _settle_reservation(reservation, response)
    return result


@retry_config
def get_completion_with_json(
    prompt: str, use_pro: bool = False,
    response_schema: Optional[Any] = None, thinking_level: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    """Return JSON text, preserving the provided schema and its constraints."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    limit = 8192 if max_output_tokens is None else max_output_tokens
    config = _generation_config(use_pro=use_pro, thinking_level=thinking_level, max_output_tokens=limit, json_output=True, response_schema=response_schema)
    reservation = _reserve_paid_attempt("completion_json", prompt, config)
    response = None
    try:
        response = initialize_api().models.generate_content(
            model=LLM_FLASH_MODEL,
            contents=prompt,
            config=config,
        )
        result = _response_text(response)
    finally:
        _settle_reservation(reservation, response)
    return result


def _embedding_vectors(response: Any, expected_count: int) -> List[List[float]]:
    vectors = [list(item.values or []) for item in (response.embeddings or [])]
    if len(vectors) != expected_count or any(len(v) != 768 for v in vectors):
        raise ValueError("Gemini must return one 768-dimensional embedding per input")
    return vectors


@retry_config
def get_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
    """Return the existing 768-dimensional embedding shape."""
    if not text or not text.strip():
        raise ValueError("Input text cannot be empty")
    reservation = _reserve_paid_attempt("embedding", text, embedding_count=1)
    response = None
    try:
        response = initialize_embedding_api().models.embed_content(
            model=EMBEDDING_MODEL, contents=text,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=768, auto_truncate=False),
        )
        result = _embedding_vectors(response, 1)[0]
    finally:
        _settle_reservation(reservation, response, expected_embeddings=1)
    return result


@retry_config
def get_embeddings_batch(texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
    """Return embeddings for nonempty input strings in their original order."""
    valid_texts = [t for t in texts if t and t.strip()]
    if not valid_texts:
        raise ValueError("Input text list must contain at least one nonempty string")
    reservation = _reserve_paid_attempt("embeddings", valid_texts, embedding_count=len(valid_texts))
    response = None
    try:
        response = initialize_embedding_api().models.embed_content(
            model=EMBEDDING_MODEL, contents=valid_texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=768, auto_truncate=False),
        )
        result = _embedding_vectors(response, len(valid_texts))
    finally:
        _settle_reservation(reservation, response, expected_embeddings=len(valid_texts))
    return result


@retry_config
def upload_to_gemini(path: str, mime_type: str = "application/pdf") -> Any:
    """Return an inline document part; Vertex does not use AI Studio's Files API."""
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    # shortcut: bound inline documents to 20 MiB; use GCS URIs for larger PDFs.
    if file.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Vertex inline documents are limited to 20 MiB in this helper")
    return types.Part.from_bytes(data=file.read_bytes(), mime_type=mime_type)


@retry_config
def get_completion_multimodal(
    prompt: str, files: List[Any], use_pro: bool = True,
    response_schema: Optional[Any] = None,
) -> str:
    """Preserve PDF callers, with deeper Flash thinking for use_pro=True."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    contents = [prompt, *files]
    config = _generation_config(use_pro=use_pro, max_output_tokens=2048, json_output=response_schema is not None, response_schema=response_schema)
    reservation = _reserve_paid_attempt("multimodal", contents, config)
    response = None
    try:
        response = initialize_api().models.generate_content(
            model=LLM_FLASH_MODEL, contents=contents, config=config,
        )
        result = _response_text(response)
    finally:
        _settle_reservation(reservation, response)
    return result


def test_api_connection() -> bool:
    """Check model metadata without generation; this does not verify billing."""
    try:
        initialize_api().models.get(model=LLM_FLASH_MODEL)
        return True
    except (ValueError, DefaultCredentialsError, errors.APIError, httpx.HTTPError):
        return False


def check_connections(settings=None) -> dict[str, dict[str, str]]:
    """Return safe connection states without exposing credentials or making paid calls."""
    if settings is None:
        from src.config import get_settings
        settings = get_settings()
    result: dict[str, dict[str, str]] = {}
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        result["gemini"] = {"status": "missing", "reason": "GOOGLE_CLOUD_PROJECT_UNSET"}
    elif not settings.extraction_budget_usd or not settings.extraction_input_price_usd_per_1k or not settings.extraction_output_price_usd_per_1k:
        result["gemini"] = {"status": "configured_unverified", "reason": "PAID_BUDGET_OR_VERIFIED_PRICES_UNSET"}
    else:
        try:
            initialize_api().models.get(model=LLM_FLASH_MODEL)
            result["gemini"] = {"status": "working", "reason": "MODEL_METADATA_REACHABLE"}
        except (ValueError, DefaultCredentialsError, errors.APIError, httpx.HTTPError):
            result["gemini"] = {"status": "failed", "reason": "MODEL_METADATA_UNREACHABLE"}
    if settings.object_backend == "local":
        result["object_storage"] = {"status": "working", "reason": "EXPLICIT_LOCAL_ARCHIVE"}
    elif not settings.minio_endpoint or not settings.minio_access_key or not settings.minio_secret_key:
        result["object_storage"] = {"status": "missing", "reason": "OBJECT_STORAGE_CONFIGURATION_INCOMPLETE"}
    elif not settings.minio_secure:
        result["object_storage"] = {"status": "failed", "reason": "REMOTE_OBJECT_STORAGE_REQUIRES_TLS"}
    else:
        result["object_storage"] = {"status": "configured_unverified", "reason": "REMOTE_BUCKET_CHECK_NOT_REQUESTED"}
    return result


if __name__ == "__main__":
    print("Gemini model access:", "working" if test_api_connection() else "failed")
