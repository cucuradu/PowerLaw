"""Recoverable, archive-first ingestion runner."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
import uuid
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from itertools import islice
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import requests

from src.config import get_settings, load_data_sources_registry
from src.schemas.ingestion import BatchManifest, Entity, EntityAlias, EvidenceLink, Fact, Observation, SeriesDefinition, SourceDocument, stable_id
from src.storage.dual_layer_store import DualLayerStore
from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger, JobLeaseError
from src.utils.minio_client import EvidenceArchive


class FetchError(RuntimeError):
    """A bounded HTTP acquisition failed."""


class ArchiveError(RuntimeError, OSError):
    """Evidence could not be durably archived and verified."""


class DiskFloorError(ArchiveError):
    """Available disk space is below configured disk floor."""


SourceItem = dict[str, Any]
Discoverer = Callable[[str, Optional[str], Optional[str], int], Iterable[SourceItem]]
Parser = Callable[[SourceItem, bytes, SourceDocument], Any]

_DISCOVERERS: dict[str, Discoverer] = {}
_PARSERS: dict[str, Parser] = {}
_TRANSIENT_STATUS = {429, 502, 503, 504}


def register_source(source_id: str, discover: Discoverer, parse: Parser) -> None:
    """Register source-specific discovery and parsing adapters."""
    _DISCOVERERS[source_id] = discover
    _PARSERS[source_id] = parse


def _safe_error(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)([?&](?:key|token|api[_-]?key|access[_-]?token|signature)=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^,\s]+", r"\1[REDACTED]", text)
    for name in ("GOOGLE_API_KEY", "GENESIS_API_TOKEN", "MINIO_SECRET_KEY", "MINIO_ACCESS_KEY"):
        secret = __import__("os").environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:500]


def _source_policy(source_id: str, settings, *, injected: bool) -> dict[str, Any]:
    try:
        entry = load_data_sources_registry().get("sources", {}).get(source_id)
    except (FileNotFoundError, ValueError) as exc:
        raise FetchError("source registry is unavailable or invalid") from exc
    if entry is None:
        if settings.mode == "test" and injected:
            return {"source_id": source_id, "attribution": source_id}
        raise FetchError("source is not registered")
    if settings.mode == "production" and (not entry.get("enabled") or not entry.get("rights_checked") or entry.get("access_fee", 1) != 0 or entry.get("requires_paid_subscription")):
        raise FetchError("source is disabled or fails free-source policy")
    if not injected and source_id not in _DISCOVERERS:
        raise FetchError("source has no registered discovery adapter")
    if not injected and source_id not in _PARSERS:
        raise FetchError("source has no registered parser adapter")
    return entry


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("URL is not an approved HTTP(S) endpoint")
    if parsed.username or parsed.password:
        raise FetchError("credentials in URLs are not allowed")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _host_is_public(hostname: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except OSError as exc:
        raise FetchError("host resolution failed") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return False
    return True


def _retry_delay(response, attempt: int) -> float:
    value = response.headers.get("Retry-After")
    if value:
        try:
            return min(max(float(value), 0.0), 30.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                return min(max((retry_at - datetime.now(retry_at.tzinfo)).total_seconds(), 0.0), 30.0)
            except (TypeError, ValueError) as exc:
                raise FetchError("malformed Retry-After header") from exc
    return min(2.0 ** attempt, 8.0)


def bounded_fetch(url: str, *, settings=None, session=None, timeout: tuple[float, float] = (10.0, 30.0),
                  max_bytes: int = 25 * 1024 * 1024, max_redirects: int = 3, retries: int = 3,
                  total_timeout: float = 120.0, allowed_hosts: Optional[set[str]] = None,
                  request_method: str = "GET", request_data: Optional[dict] = None,
                  auth_source: Optional[str] = None,
                  headers: Optional[dict[str, str]] = None) -> tuple[bytes, str, str, int]:
    """Fetch an approved public URL with bounded retries, redirects and bytes."""
    settings = settings or get_settings()
    current = _safe_url(url)
    client = session or requests.Session()
    deadline = time.monotonic() + total_timeout
    req_headers = {"User-Agent": "PowerLaw-ingestion/1.0"}
    if headers:
        req_headers.update(headers)
    headers = req_headers
    try:
        if request_method not in {"GET", "POST"}:
            raise FetchError("unsupported acquisition method")
        if auth_source:
            if auth_source != "DESTATIS_GENESIS" or urlparse(current).hostname != "genesis.destatis.de" or request_method != "POST":
                raise FetchError("invalid source authentication contract")
            token = __import__("os").environ.get("GENESIS_API_TOKEN")
            if not token:
                raise FetchError("GENESIS_API_TOKEN is required for Destatis acquisition")
            headers.update({"username": token, "Content-Type": "application/x-www-form-urlencoded"})
        for redirect_count in range(max_redirects + 1):
            if time.monotonic() >= deadline:
                raise FetchError("total acquisition timeout exceeded")
            parsed = urlparse(current)
            if not _host_is_public(parsed.hostname or ""):
                raise FetchError("private or local redirect target rejected")
            if allowed_hosts is not None and (parsed.hostname or "").lower() not in allowed_hosts:
                raise FetchError("URL is outside the registered source endpoints")
            last_error = None
            redirected = False
            for attempt in range(retries + 1):
                if time.monotonic() >= deadline:
                    raise FetchError("total acquisition timeout exceeded")
                response = None
                try:
                    request_kwargs = {"headers": headers, "timeout": timeout, "stream": True, "allow_redirects": False}
                    if request_method == "POST":
                        response = client.post(current, data=request_data or {}, **request_kwargs)
                    else:
                        response = client.get(current, **request_kwargs)
                    if response.status_code in _TRANSIENT_STATUS:
                        if attempt >= retries:
                            raise FetchError(f"transient HTTP failure {response.status_code}")
                        time.sleep(min(_retry_delay(response, attempt), max(0.0, deadline - time.monotonic())))
                        continue
                    if 300 <= response.status_code < 400:
                        if auth_source or request_method == "POST":
                            raise FetchError("authenticated or POST acquisition redirects are not allowed")
                        location = response.headers.get("Location")
                        if not location or redirect_count >= max_redirects:
                            raise FetchError("redirect limit exceeded or redirect location missing")
                        current = _safe_url(urljoin(current, location))
                        redirected = True
                        break
                    if response.status_code >= 400:
                        raise FetchError(f"HTTP failure {response.status_code}")
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            declared_length = int(content_length)
                        except ValueError as exc:
                            raise FetchError("malformed Content-Length header") from exc
                        if declared_length < 0:
                            raise FetchError("malformed Content-Length header")
                        if declared_length > max_bytes:
                            raise FetchError("download exceeds configured maximum size")
                    chunks = []
                    total = 0
                    def _stream():
                        nonlocal total
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                response.close()
                                raise FetchError("download exceeds configured maximum size")
                            chunks.append(chunk)
                        return chunks

                    reader = ThreadPoolExecutor(max_workers=1)
                    future = reader.submit(_stream)
                    try:
                        chunks = future.result(timeout=max(0.0, deadline - time.monotonic()))
                    except FutureTimeout as exc:
                        future.cancel()
                        response.close()
                        reader.shutdown(wait=False, cancel_futures=True)
                        raise FetchError("total acquisition timeout exceeded") from exc
                    except Exception:
                        response.close()
                        raise
                    finally:
                        if not future.done():
                            reader.shutdown(wait=False, cancel_futures=True)
                        else:
                            reader.shutdown(wait=True, cancel_futures=True)
                    content = b"".join(chunks)
                    content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip().lower()
                    prefix = content[:512].lstrip().lower()
                    if ("pdf" in content_type or current.lower().split("?", 1)[0].endswith(".pdf")) and (prefix.startswith(b"<html") or b"<html" in prefix or prefix.startswith(b"<!doctype")):
                        raise FetchError("HTML error response disguised as PDF")
                    return content, current, content_type, response.status_code
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt >= retries:
                        raise FetchError("request failed after bounded retries") from exc
                    time.sleep(min(2.0 ** attempt, 8.0, max(0.0, deadline - time.monotonic())))
                finally:
                    if response is not None:
                        response.close()
            if redirected:
                continue
            if last_error:
                raise FetchError("request failed after bounded retries") from last_error
            raise FetchError("request failed")
    finally:
        if session is None:
            client.close()
    raise FetchError("redirect limit exceeded")


def archive_document(document: SourceDocument, content: bytes, *, archive: Optional[EvidenceArchive] = None) -> SourceDocument:
    """Archive bytes and return the document with a verified raw URI."""
    digest = hashlib.sha256(content).hexdigest()
    if digest != document.content_sha256:
        raise ArchiveError("document checksum does not match content")
    archive = archive or EvidenceArchive()
    try:
        uri, archived_digest = archive.put(content, content_type=document.content_type)
    except Exception as exc:
        raise ArchiveError("evidence archive failed") from exc
    if archived_digest != digest:
        raise ArchiveError("evidence archive returned an unexpected checksum")
    return document.model_copy(update={"raw_uri": uri, "byte_count": len(content)})


def _default_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    del source_id, since, until, limit
    return iter(())


def _default_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    del item, content, document
    raise FetchError("source parser is unavailable")


def _parse_result(result: Any) -> tuple[list[Fact], list[Observation], list[EvidenceLink], list[Entity], list[SeriesDefinition], int, int, int]:
    if isinstance(result, dict):
        if "status" not in result:
            raise FetchError("parser result is missing an explicit status")
        status = str(result["status"])
        if status not in {"accepted", "needs_review", "rejected", "out_of_scope"}:
            raise FetchError("parser returned an invalid processing status")
        accepted = int(result.get("accepted_count", 1 if status == "accepted" else 0))
        review = int(result.get("review_count", 1 if status == "needs_review" else 0))
        rejected = int(result.get("rejected_count", 1 if status in {"rejected", "out_of_scope"} else 0))
        if min(accepted, review, rejected) < 0:
            raise FetchError("parser returned a negative processing count")
        return (
            [Fact.model_validate(value) for value in result.get("facts", [])],
            [Observation.model_validate(value) for value in result.get("observations", [])],
            [EvidenceLink.model_validate(value) for value in result.get("evidence_links", [])],
            [Entity.model_validate(value) for value in result.get("entities", [])],
            [SeriesDefinition.model_validate(value) for value in result.get("series", [])],
            accepted, review, rejected,
        )
    raise TypeError("source parser must return a mapping with an explicit status")


def _content_type(item: SourceItem, fetched_type: str) -> str:
    value = str(item.get("content_type") or fetched_type or "application/octet-stream").lower()
    return value.split(";", 1)[0].strip()


def _registered_hosts(entry: dict[str, Any]) -> set[str]:
    hosts = set()
    for endpoint in (entry.get("endpoints") or {}).values():
        parsed = urlparse(str(endpoint))
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def _reconfirm_unchanged_observations(store: DualLayerStore, observations: list[Observation],
                                      evidence: list[EvidenceLink]) -> tuple[list[Observation], list[EvidenceLink]]:
    """Drop re-fetched rows equal to their latest committed revision when the provider gives no vintage.

    Without a vintage, fetch time was the only thing distinguishing them, so each run created a duplicate
    revision. The new document's evidence is re-pointed at the existing revision ("reconfirmed").
    """
    candidates = {o.observation_id for o in observations if not o.vintage_id}
    if not candidates:
        return observations, evidence
    latest: dict[str, Observation] = {}
    for row in store.query_observation_revisions(datetime.now(timezone.utc), "system_as_of"):
        if row.observation_id in candidates:
            previous = latest.get(row.observation_id)
            if previous is None or store._available(row) > store._available(previous):
                latest[row.observation_id] = row

    def same(a: Observation, b: Observation) -> bool:
        fields = ("value", "missing_reason", "unit", "source_unit", "is_retracted", "provider_status", "quality_state")
        return all(getattr(a, f) == getattr(b, f) for f in fields)

    kept, repoint = [], {}
    for obs in observations:
        previous = latest.get(obs.observation_id)
        if not obs.vintage_id and previous is not None and same(obs, previous):
            repoint[obs.revision_id] = previous.revision_id
        else:
            kept.append(obs)
    links = [EvidenceLink.model_validate({**link.model_dump(exclude={"evidence_id"}),
                                          "assertion_version_id": repoint[link.assertion_version_id]})
             if link.assertion_version_id in repoint else link for link in evidence]
    return kept, links


def run_ingestion(source_id: str, *, limit: int, since: str | None = None, until: str | None = None,
                  dry_run: bool = False, settings=None, ledger: Optional[IngestionJobLedger] = None,
                  store: Optional[DualLayerStore] = None, archive: Optional[EvidenceArchive] = None,
                  discover: Optional[Discoverer] = None, parse: Optional[Parser] = None,
                  fetch: Callable[..., tuple[bytes, str, str, int]] = bounded_fetch,
                  run_id: Optional[str] = None,
                  budget_run_id: Optional[str] = None,
                  max_series: Optional[int] = None) -> dict[str, Any]:
    """Run one durable collection operation; pass ``run_id`` only to resume it."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if max_series is not None and (type(max_series) is not int or max_series < 1):
        raise ValueError("max_series must be a positive integer")
    settings = settings or get_settings()
    ledger = ledger or IngestionJobLedger(settings.sqlite_path)
    injected = discover is not None and (parse is not None or settings.mode == "test")
    policy = _source_policy(source_id, settings, injected=injected)
    run_id = run_id or f"run_{uuid.uuid4().hex}"
    batch_id = stable_id("batch", run_id)
    auth_run_id = ledger.bind_budget_run(run_id, source_id, float(getattr(settings, "extraction_budget_usd", 0.0)), budget_run_id)
    report: dict[str, Any] = {"run_id": run_id, "batch_id": batch_id, "source_id": source_id, "discovered": 0, "fetched": 0, "staged": 0, "accepted": 0, "review": 0, "rejected": 0, "duplicate": 0, "committed": 0, "source_errors": [], "bytes": 0, "cost_usd": ledger.spent_cost(run_id), "dry_run": dry_run}
    token = ledger.acquire_lease(source_id, run_id)
    archive = archive or EvidenceArchive(settings)
    canonical_store = store or DualLayerStore(settings.lancedb_path)
    completed = False
    try:
        discoverer = discover or _DISCOVERERS[source_id]
        parser = parse or _PARSERS[source_id]
        saved_cursor = ledger.cursor(source_id, str(policy.get("cursor_key", "default")))
        effective_since = since if since is not None else saved_cursor
        items = ledger.work_items(run_id, ("discovered", "fetched", "failed", "parsed"))
        if not items:
            for index, item in enumerate(islice(discoverer(source_id, effective_since, until, limit), limit)):
                item_key = str(item.get("item_id") or item.get("cursor") or item.get("url") or index)
                c_val = item.get("cursor")
                ledger.save_work_item(run_id, item_key, item, cursor_value=str(c_val) if c_val is not None else None)
            items = ledger.work_items(run_id, ("discovered", "fetched", "failed", "parsed"))
        report["discovered"] = len(items)
        if dry_run:
            report["status"] = "planned"
            completed = True
            return report
        batch = BatchManifest(batch_id=batch_id, source_id=source_id, discovered_count=len(items), data_origin={"production": "live", "demo": "demo", "test": "test"}.get(settings.mode, "live"))
        documents: list[SourceDocument] = []
        facts: list[Fact] = []
        observations: list[Observation] = []
        evidence: list[EvidenceLink] = []
        entities: list[Entity] = []
        series: list[SeriesDefinition] = []
        last_cursor: Optional[str] = None
        failed = False
        for row in items:
            ledger.renew_lease(source_id, run_id, token)
            item = json.loads(row["item_json"])
            if row["status"] == "parsed" and row.get("payload_json"):
                payload = json.loads(row["payload_json"])
                saved_document = SourceDocument.model_validate(payload["document"])
                archive.get(saved_document.raw_uri or "", saved_document.content_sha256)
                documents.append(saved_document)
                facts.extend(Fact.model_validate(value) for value in payload.get("facts", []))
                observations.extend(Observation.model_validate(value) for value in payload.get("observations", []))
                evidence.extend(EvidenceLink.model_validate(value) for value in payload.get("evidence_links", []))
                entities.extend(Entity.model_validate(value) for value in payload.get("entities", []))
                series.extend(SeriesDefinition.model_validate(value) for value in payload.get("series", []))
                report["fetched"] += 1
                report["accepted"] += int(payload.get("accepted", 0)); report["review"] += int(payload.get("review", 0)); report["rejected"] += int(payload.get("rejected", 0))
                last_cursor = row["cursor_value"]
                continue
            url = _safe_url(str(item["url"]))
            request_id = stable_id("request", source_id, row["item_key"], url)
            try:
                if row.get("document_json"):
                    doc = SourceDocument.model_validate(json.loads(row["document_json"]))
                    content = archive.get(doc.raw_uri or "", doc.content_sha256)
                    status = 200
                    report["fetched"] += 1
                else:
                    if settings.mode == "production" and urlparse(url).hostname not in _registered_hosts(policy):
                        raise FetchError("discovered URL is outside the registered source endpoints")
                    fetch_kwargs = {"settings": settings, "max_bytes": int(item.get("max_bytes", 25 * 1024 * 1024))}
                    fetch_kwargs.update({key: item[key] for key in ("request_method", "request_data", "auth_source", "headers") if key in item})
                    if item.get("read_timeout_seconds"):  # slow but verified providers (ECB EXR answers in ~45 s)
                        fetch_kwargs.update(timeout=(10.0, float(item["read_timeout_seconds"])),
                                            total_timeout=3 * float(item["read_timeout_seconds"]))
                    if settings.mode == "production":
                        fetch_kwargs["allowed_hosts"] = _registered_hosts(policy)
                    content, final_url, fetched_type, status = fetch(url, **fetch_kwargs)
                    if settings.mode == "production" and urlparse(final_url).hostname not in _registered_hosts(policy):
                        raise FetchError("redirect target is outside the registered source endpoints")
                    report["fetched"] += 1
                    report["bytes"] += len(content)
                    ledger.record_attempt(run_id, f"{request_id}:fetch", source_id, "fetched", http_status=status, bytes_count=len(content))
                    doc = SourceDocument.from_content(source_id=source_id, canonical_url=str(item.get("canonical_url", url)), publisher=str(item.get("publisher", policy.get("attribution", source_id))), content_bytes=content, batch_id=batch_id, final_url=final_url, data_origin=batch.data_origin, published_at=item.get("published_at"), availability_basis=str(item.get("availability_basis", "first_fetch"))).model_copy(update={"content_type": _content_type(item, fetched_type), "upstream_id": item.get("upstream_id"), "rights_json": json.dumps({"attribution": policy.get("attribution"), "terms_url": policy.get("terms_url")}, sort_keys=True)})
                    previous_document = getattr(canonical_store, "_canonical_documents", {}).get(doc.version_id)
                    if previous_document is not None:
                        doc = doc.model_copy(update={"available_at": previous_document.available_at, "fetched_at": previous_document.fetched_at, "recorded_at": previous_document.recorded_at, "raw_uri": previous_document.raw_uri})
                    doc = archive_document(doc, content, archive=archive)
                    ledger.save_work_item(run_id, row["item_key"], item, status="fetched", cursor_value=row["cursor_value"], document=doc.model_dump(mode="json"))
                from src.utils.api_wrapper import reset_ingestion_budget_context, set_ingestion_budget_context
                budget_token = set_ingestion_budget_context(ledger, auth_run_id, float(getattr(settings, "extraction_budget_usd", 0.0)), input_price_usd_per_1k=getattr(settings, "extraction_input_price_usd_per_1k", None), output_price_usd_per_1k=getattr(settings, "extraction_output_price_usd_per_1k", None), embedding_price_usd_per_1k=getattr(settings, "extraction_embedding_price_usd_per_1k", None), total_budget_usd=getattr(settings, "extraction_total_budget_usd", None))
                try:
                    parser_item = item
                    if source_id in ("EURLEX_CELLAR", "BUNDESBANK_RTD", "EUROSTAT_NAIO", "DESTATIS_GENESIS", "EUROSTAT_STS",
                                     "ECB_SDMX", "BUNDESBANK_SDMX", "GESETZE_IM_INTERNET", "STATISTISCHE_BIBLIOTHEK"):
                        parser_item = dict(item, _store=canonical_store)
                    elif source_id == "OFFICIAL_NEWS":
                        committed_batches = {key for key, value in canonical_store._canonical_batches.items() if value.status == "committed"}
                        previous_facts = [fact for fact in canonical_store._canonical_facts.values()
                            if fact.batch_id in committed_batches and fact.source_id == source_id and fact.data_origin == doc.data_origin]
                        parser_item = dict(item, _previous_facts=previous_facts)
                    parsed_result = parser(parser_item, content, doc)
                finally:
                    reset_ingestion_budget_context(budget_token)
                parsed_facts, parsed_observations, parsed_evidence, parsed_entities, parsed_series, accepted, review, rejected = _parse_result(parsed_result)
                documents.append(doc); facts.extend(parsed_facts); observations.extend(parsed_observations); evidence.extend(parsed_evidence); entities.extend(parsed_entities); series.extend(parsed_series)
                report["accepted"] += accepted; report["review"] += review; report["rejected"] += rejected
                ledger.save_work_item(run_id, row["item_key"], item, status="parsed", cursor_value=row["cursor_value"], document=doc.model_dump(mode="json"), payload={"document": doc.model_dump(mode="json"), "facts": [value.model_dump(mode="json") for value in parsed_facts], "observations": [value.model_dump(mode="json") for value in parsed_observations], "evidence_links": [value.model_dump(mode="json") for value in parsed_evidence], "entities": [value.model_dump(mode="json") for value in parsed_entities], "series": [value.model_dump(mode="json") for value in parsed_series], "accepted": accepted, "review": review, "rejected": rejected})
                ledger.record_attempt(run_id, f"{request_id}:parse", source_id, "parsed", http_status=status, bytes_count=len(content))
                last_cursor = row["cursor_value"]
            except (FetchError, ArchiveError, TypeError, ValueError, OSError, BudgetError) as exc:
                failed = True
                safe = _safe_error(exc)
                report["source_errors"].append({"request_id": request_id, "code": type(exc).__name__, "message": safe})
                ledger.save_work_item(run_id, row["item_key"], item, status="failed", cursor_value=row["cursor_value"])
                ledger.record_attempt(run_id, f"{request_id}:error", source_id, "failed", error_code=type(exc).__name__, error_message=safe)
        distinct_series = {value.series_id for value in series} | {value.series_id for value in observations}
        if max_series is not None and len(distinct_series) > max_series:
            failed = True
            report["source_errors"].append({"code": "PILOT_SERIES_CAP", "message": "statistical series cap exceeded; expansion review required"})
        if failed:
            # Keep archived/parsed work for same-run retry before publishing one immutable batch.
            report["status"] = "failed"
            report["cost_usd"] = ledger.spent_cost(auth_run_id)
            return report
        if not documents:
            report["status"] = "failed" if failed else "empty"
            report["cost_usd"] = ledger.spent_cost(run_id)
            completed = not failed
            return report
        before = len(observations)
        observations, evidence = _reconfirm_unchanged_observations(canonical_store, observations, evidence)
        report["reconfirmed"] = before - len(observations)
        batch.fetched_count = report["fetched"]; batch.accepted_count = report["accepted"]; batch.review_count = report["review"]; batch.rejected_count = report["rejected"]
        manifest_payload = {"manifest_version": "2", "data_origin": batch.data_origin, "batch_metadata": batch.model_dump(mode="json"), "batch_id": batch_id, "source_id": source_id, "cursor_key": policy.get("cursor_key", "default"), "cursor_value": last_cursor, "documents": [d.model_dump(mode="json") for d in documents], "facts": [f.model_dump(mode="json") for f in facts], "observations": [o.model_dump(mode="json") for o in observations], "evidence_links": [e.model_dump(mode="json") for e in evidence], "entities": [e.model_dump(mode="json") for e in entities], "series": [s.model_dump(mode="json") for s in series]}
        manifest_bytes = json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        batch.manifest_uri, batch.archive_sha256 = archive.put(manifest_bytes, content_type="application/json")
        ledger.assert_lease(source_id, run_id, token)
        ledger.record_batch(batch_id, run_id, source_id, "staging", manifest_uri=batch.manifest_uri, archive_sha256=batch.archive_sha256, lease_token=token)
        for doc in documents:
            if not doc.raw_uri or not doc.content_sha256:
                report["status"] = "failed"
                completed = False
                return report
            try:
                raw_content = archive.get(doc.raw_uri, doc.content_sha256)
                if hashlib.sha256(raw_content).hexdigest() != doc.content_sha256:
                    report["status"] = "failed"
                    completed = False
                    return report
            except (ArchiveError, ValueError):
                report["status"] = "failed"
                completed = False
                return report
        canonical_store.stage_batch(batch, documents, facts, observations, evidence_links=evidence, entities=entities, series=series)
        report["staged"] = len(documents) + len(facts) + len(observations)
        ledger.record_batch(batch_id, run_id, source_id, "staged", manifest_uri=batch.manifest_uri, archive_sha256=batch.archive_sha256, lease_token=token)
        ledger.assert_lease(source_id, run_id, token)
        canonical_store.commit_batch(batch_id)
        report["committed"] = report["staged"]
        report["status"] = "committed_with_errors" if failed else "committed"
        ledger.record_batch(batch_id, run_id, source_id, "committed", manifest_uri=batch.manifest_uri, archive_sha256=batch.archive_sha256, lease_token=token)
        if last_cursor is not None and not failed:
            ledger.assert_lease(source_id, run_id, token)
            ledger.advance_cursor(source_id, last_cursor, str(policy.get("cursor_key", "default")), run_id=run_id, token=token)
        report["cost_usd"] = ledger.spent_cost(run_id)
        completed = True
        return report
    except BaseException:
        raise
    finally:
        try:
            ledger.release_lease(source_id, run_id, token, "completed" if completed else "interrupted")
        except JobLeaseError:
            pass


def resume_batch(batch_id: str, *, settings=None, ledger: Optional[IngestionJobLedger] = None,
                 store: Optional[DualLayerStore] = None) -> dict[str, Any]:
    """Recover a staged batch from its verified manifest without refetching."""
    settings = settings or get_settings()
    ledger = ledger or IngestionJobLedger(settings.sqlite_path)
    record = ledger.batch(batch_id)
    if record is None:
        raise ValueError(f"Unknown ingestion batch {batch_id!r}")
    _source_policy(record["source_id"], settings, injected=settings.mode == "test")
    canonical_store = store or DualLayerStore(settings.lancedb_path)
    archive = EvidenceArchive(settings)
    token = ledger.acquire_lease(record["source_id"], record["run_id"])
    completed = False
    try:
        failed_work = ledger.work_items(record["run_id"], ("failed",))
        if failed_work:
            return {"batch_id": batch_id, "run_id": record["run_id"], "source_id": record["source_id"], "status": "failed", "error": "run has failed work items"}
        if not record["manifest_uri"] or not record["archive_sha256"]:
            raise ArchiveError("batch has no durable manifest reference")
        payload = json.loads(archive.get(record["manifest_uri"], record["archive_sha256"]))
        if payload.get("batch_id") != batch_id:
            raise ArchiveError("manifest batch identity mismatch")
        documents = [SourceDocument.model_validate(value) for value in payload.get("documents", [])]
        for document in documents:
            if not document.raw_uri:
                raise ArchiveError("manifest document has no raw archive URI")
            raw_content = archive.get(document.raw_uri, document.content_sha256)
            if hashlib.sha256(raw_content).hexdigest() != document.content_sha256:
                raise ArchiveError("raw document content hash mismatch during resume")
        batch = BatchManifest(batch_id=batch_id, source_id=record["source_id"], manifest_uri=record["manifest_uri"], archive_sha256=record["archive_sha256"], data_origin=documents[0].data_origin if documents else "live")
        facts = [Fact.model_validate(value) for value in payload.get("facts", [])]
        observations = [Observation.model_validate(value) for value in payload.get("observations", [])]
        evidence = [EvidenceLink.model_validate(value) for value in payload.get("evidence_links", [])]
        entities = [Entity.model_validate(value) for value in payload.get("entities", [])]
        series = [SeriesDefinition.model_validate(value) for value in payload.get("series", [])]
        existing = getattr(canonical_store, "_canonical_batches", {}).get(batch_id)
        if existing is None:
            canonical_store.stage_batch(batch, documents, facts, observations, evidence_links=evidence, entities=entities, series=series)
        ledger.assert_lease(record["source_id"], record["run_id"], token)
        canonical_store.commit_batch(batch_id)
        cursor = payload.get("cursor_value")
        if cursor is not None:
            ledger.advance_cursor(record["source_id"], cursor, payload.get("cursor_key", "default"))
        ledger.record_batch(batch_id, record["run_id"], record["source_id"], "committed", manifest_uri=record["manifest_uri"], archive_sha256=record["archive_sha256"], lease_token=token)
        completed = True
        return {"batch_id": batch_id, "run_id": record["run_id"], "source_id": record["source_id"], "status": "committed"}
    finally:
        try:
            ledger.release_lease(record["source_id"], record["run_id"], token, "completed" if completed else "interrupted")
        except JobLeaseError:
            pass


def _eurlex_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    from src.scrapers.eurlex import TARGET_LAWS
    count = 0
    for entry in TARGET_LAWS:
        celex = str(entry.get("celex_id") if isinstance(entry, dict) else entry).strip()
        date_str = entry.get("date") if isinstance(entry, dict) else None
        if since is not None and date_str and date_str < since:
            continue
        if until is not None and date_str and date_str > until:
            continue
        yield {
            "item_id": celex,
            "upstream_id": celex,
            "url": f"https://publications.europa.eu/resource/celex/{celex}",
            "canonical_url": f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}",
            "headers": {"Accept": "application/xhtml+xml", "Accept-Language": "en"},
            "publisher": "Publications Office of the European Union",
            "content_type": "application/xhtml+xml",
            "published_at": date_str,
        }
        count += 1
        if count >= limit:
            break


def _eurlex_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    from src.main import LawIngestionPipeline
    pipeline = LawIngestionPipeline(store=item.get("_store"))
    try:
        facts = pipeline.process_document(document, content)
    except Exception as exc:
        raise FetchError(f"legal extraction failed ({type(exc).__name__}): {_safe_error(exc)}") from exc
    entities = getattr(pipeline, "last_entities", [])
    evidence = getattr(pipeline, "last_evidence_links", [])
    accepted = len([f for f in facts if f.quality_state == "accepted" and not f.is_retracted])
    review = len([f for f in facts if f.quality_state == "needs_review" and not f.is_retracted])
    retractions = len([f for f in facts if f.is_retracted])
    status = "accepted" if (accepted > 0 or retractions > 0) else ("needs_review" if review > 0 else "out_of_scope")
    return {
        "status": status,
        "facts": facts,
        "observations": [],
        "evidence_links": evidence,
        "entities": entities,
        "series": [],
        "accepted_count": accepted,
        "review_count": review,
        "rejected_count": 0,
    }


register_source("EURLEX_CELLAR", _eurlex_discover, _eurlex_parse)


def _news_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    from src.config import load_data_sources_registry
    registry = load_data_sources_registry().get("sources", {}).get(source_id, {})
    endpoints = registry.get("endpoints", {})
    count = 0
    for name, url in endpoints.items():
        if count >= limit:
            break
        yield {
            "item_id": name,
            "upstream_id": name,
            "url": url,
            "canonical_url": url,
            "publisher": registry.get("attribution", "European Commission Press Corner"),
            "content_type": "application/rss+xml",
        }
        count += 1


def _news_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    from src.scrapers.news_scraper import EconomicNewsScraper
    import xml.etree.ElementTree as ET
    scraper = EconomicNewsScraper(offline=True)

    facts: list[Fact] = []
    entities: list[Entity] = []
    evidence: list[EvidenceLink] = []
    seen_articles = set()
    entry_selectors = {}

    is_xml = content.strip().startswith(b"<?xml") or content.strip().startswith(b"<rss") or content.strip().startswith(b"<feed")
    if is_xml:
        try:
            root = ET.fromstring(content)
            xml_items = root.findall(".//item")
            if not xml_items:
                xml_items = [elem for elem in root.iter() if elem.tag.endswith("entry")]
            for x_item in xml_items:
                t_elem = next((e for e in x_item if e.tag.endswith("title")), None)
                title = t_elem.text.strip() if t_elem is not None and t_elem.text else ""
                d_elem = next((e for e in x_item if any(e.tag.endswith(t) for t in ["description", "summary", "content"])), None)
                desc = d_elem.text.strip() if d_elem is not None and d_elem.text else ""
                l_elem = next((e for e in x_item if e.tag.endswith("link")), None)
                link = (l_elem.attrib.get("href") or l_elem.text or "").strip() if l_elem is not None else ""
                g_elem = next((e for e in x_item if e.tag.endswith(("guid", "id"))), None)
                guid = g_elem.text.strip() if g_elem is not None and g_elem.text else ""
                date_elem = next((e for e in x_item if any(e.tag.endswith(t) for t in ["pubDate", "published", "updated", "date"])), None)
                date_str = date_elem.text.strip() if date_elem is not None and date_elem.text else ""
                pub_dt = None
                if date_str:
                    try:
                        pub_dt = parsedate_to_datetime(date_str)
                    except (TypeError, ValueError):
                        try:
                            pub_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    if pub_dt is not None:
                        pub_dt = pub_dt.astimezone(timezone.utc) if pub_dt.tzinfo else None

                item_text = f"{title}\n{desc}" if desc else title
                item_doc = document.model_copy(update={
                    "canonical_url": link or document.canonical_url,
                    "upstream_id": guid or document.upstream_id,
                    "published_at": pub_dt,
                })
                item_facts = scraper.extract_document_events(item_doc, item_text)
                article_id = scraper._article_identity(item_doc.canonical_url, title, pub_dt.isoformat() if pub_dt else "", item_doc.upstream_id or "")
                seen_articles.add(article_id)
                for fact in item_facts:
                    payload = json.loads(fact.payload_json)
                    payload.update({"published_at": pub_dt.isoformat() if pub_dt else None,
                        "evidence_scope": "feed_summary", "article_document_version_id": None,
                        "feed_document_version_id": document.version_id, "feed_canonical_url": document.canonical_url})
                    fact = Fact.model_validate(dict(fact.model_dump(), payload_json=json.dumps(payload, sort_keys=True), fact_version_id=""))
                    entry_selectors[fact.fact_version_id] = {"guid": guid, "article_url": link, "article_id": article_id}
                    facts.append(fact)
        except ET.ParseError as exc:
            raise FetchError("malformed official news feed") from exc

    if not is_xml:
        text = content.decode("utf-8", errors="replace")
        facts = scraper.extract_document_events(document, text)
        seen_articles.add(scraper._article_identity(document.canonical_url, text.splitlines()[0][:300] if text.strip() else "",
            document.published_at.isoformat() if document.published_at else "", document.upstream_id or ""))
        for index, fact in enumerate(facts):
            payload = json.loads(fact.payload_json)
            payload.update({"evidence_scope": "archived_article", "article_document_version_id": document.version_id,
                "feed_document_version_id": None, "published_at": document.published_at.isoformat() if document.published_at else None})
            facts[index] = Fact.model_validate(dict(fact.model_dump(), payload_json=json.dumps(payload, sort_keys=True), fact_version_id=""))

    latest_previous = {}
    for previous in item.get("_previous_facts", []):
        old = latest_previous.get(previous.fact_id)
        if old is None or (previous.available_at or previous.recorded_at) > (old.available_at or old.recorded_at):
            latest_previous[previous.fact_id] = previous
    current_ids = {fact.fact_id for fact in facts}
    for index, fact in enumerate(facts):
        previous = latest_previous.get(fact.fact_id)
        if previous is not None and previous.fact_version_id != fact.fact_version_id:
            facts[index] = fact.model_copy(update={"supersedes_version_id": previous.fact_version_id})
    for previous in latest_previous.values():
        payload = json.loads(previous.payload_json) if previous.payload_json else {}
        same_support = payload.get("feed_canonical_url") == document.canonical_url if is_xml else payload.get("canonical_url") == document.canonical_url
        if same_support and payload.get("article_id") in seen_articles and previous.fact_id not in current_ids and not previous.is_retracted:
            payload.update({"document_version_id": document.version_id, "evidence_text": "",
                "feed_document_version_id": document.version_id if is_xml else None,
                "article_document_version_id": None if is_xml else document.version_id})
            facts.append(Fact.model_validate(dict(previous.model_dump(), fact_version_id="", is_retracted=True,
                payload_json=json.dumps(payload, sort_keys=True), supersedes_version_id=previous.fact_version_id,
                retraction_reason="Removed in revised archived news entry", available_at=document.available_at,
                recorded_at=document.recorded_at, batch_id=document.batch_id)))

    seen_entity_ids = set()
    for f in facts:
        evidence.append(EvidenceLink(
            assertion_version_id=f.fact_version_id,
            assertion_kind="FACT",
            document_version_id=document.version_id,
            selector_type="RSS_ENTRY" if is_xml else "TEXT_SPAN",
            selector_json=json.dumps(entry_selectors.get(f.fact_version_id, {"subject_id": f.subject_id})),
            quoted_text=json.loads(f.payload_json).get("evidence_text", "")[:500] if f.payload_json else "",
            support_type="DIRECT",
            available_at=f.available_at,
            batch_id=document.batch_id,
        ))
        for ent_id, ent_type in [(f.subject_id, "ACTION" if "EVENT" in f.subject_id else "REACTION"), (f.object_id, "COMMODITY" if f.object_id in ("STEEL", "ALUMINIUM") else "SECTOR")]:
            if ent_id and ent_id not in seen_entity_ids:
                seen_entity_ids.add(ent_id)
                entities.append(Entity(
                    entity_id=ent_id,
                    entity_type=ent_type,
                    canonical_label=ent_id,
                    data_origin=document.data_origin,
                    batch_id=document.batch_id,
                    available_at=f.available_at,
                ))
        if f.payload_json:
            try:
                p = json.loads(f.payload_json)
                actor = p.get("action", {}).get("actor")
                if actor and actor not in seen_entity_ids:
                    seen_entity_ids.add(actor)
                    entities.append(Entity(
                        entity_id=actor,
                        entity_type="COMPANY",
                        canonical_label=actor,
                        data_origin=document.data_origin,
                        batch_id=document.batch_id,
                        available_at=f.available_at,
                    ))
            except Exception:
                pass

    accepted = len([f for f in facts if f.quality_state == "accepted" and not f.is_retracted])
    review = len([f for f in facts if f.quality_state == "needs_review" and not f.is_retracted])
    retractions = len([f for f in facts if f.is_retracted])
    status = "accepted" if (accepted > 0 or retractions > 0) else ("needs_review" if review > 0 else "out_of_scope")
    return {
        "status": status,
        "facts": facts,
        "observations": [],
        "evidence_links": evidence,
        "entities": entities,
        "series": [],
        "accepted_count": accepted,
        "review_count": review,
        "rejected_count": 0,
    }


register_source("OFFICIAL_NEWS", _news_discover, _news_parse)


def _stats_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    from src.config import load_data_sources_registry
    catalog = load_data_sources_registry()
    registry = catalog.get("sources", {}).get(source_id, {})
    target = catalog["primary_target"]
    endpoints = registry.get("endpoints", {})
    keys = registry.get("series_keys", {})
    today = datetime.now(timezone.utc).date()
    since = since or (today - timedelta(days=366)).isoformat()
    until = until or today.isoformat()
    if not all(re.fullmatch(r"\d{4}(?:-Q[1-4]|-\d{2}(?:-\d{2})?)?", value) for value in (since, until)) or since > until:
        raise FetchError("invalid bounded statistics acquisition window")
    count = 0
    if not endpoints:
        raise FetchError("source has no verified acquisition endpoint")
    base_url = next(iter(endpoints.values()))
    if source_id == "SMARD_DE" and ("downloadcenter" in base_url.lower() or "chart_data" not in endpoints):
        raise FetchError("SMARD requires a verified official export; download center is not a data API")
    for key_name, key_val in keys.items():
        if count >= limit:
            break
        request_contract = {}
        required_series_keys = []
        if source_id == "EUROSTAT_STS":
            crosscheck = target["eurostat_crosscheck"]
            table = [s for s in registry.get("context_series", {}).values() if s["table_id"] == key_val]
            selections = [s["dimensions"] for s in table if not s.get("panel")]
            if key_val == crosscheck["dataset_id"]:
                selections.insert(0, crosscheck["dimensions"])
            # EU panel series are kept when the response has them; a country that does not report one is not an error.
            panel = [s["dimensions"] for s in table if s.get("panel")]
            if not selections and not panel:
                raise FetchError("Eurostat dataset has no verified exact series selection")
            as_key = lambda dims: f"EUROSTAT_STS|{key_val}|" + "|".join(f"{key}={dims[key]}" for key in sorted(dims))
            required_series_keys = [as_key(dims) for dims in selections]
            request_contract["optional_series_keys"] = [as_key(dims) for dims in panel]
            selections = selections + panel
            query: dict[str, list] = {}
            for dims in selections:  # extra combinations are dropped by the required-series filter
                for key, value in dims.items():
                    if value not in query.setdefault(key, []):
                        query[key].append(value)
            canonical_url = f"{base_url.rstrip('/')}/{key_val}"
            url = f"{canonical_url}?{urlencode({'format': 'JSON', 'lang': 'EN', **query}, doseq=True)}"
            if since:
                url += f"&sinceTimePeriod={since}"
            if until:
                url += f"&untilTimePeriod={until}"
            content_type = "application/json"
        elif source_id == "DESTATIS_GENESIS":
            context = [s for s in registry.get("context_series", {}).values() if s["table_id"] == key_val]
            required_series_keys = [s["provider_series_key"] for s in context]
            # Request only the registered cells: whole tables exceed the GENESIS dialog limit (status 98).
            selected = [{k: v for k, v in s["dimensions"].items() if k != "DINSG"} for s in context]
            if key_val == target["table_id"]:
                required_series_keys.insert(0, target["provider_series_key"])
                selected.insert(0, {k: v for k, v in target["provider_dimension_codes"].items() if k != "DINSG"})
            if not required_series_keys:
                raise FetchError("Destatis table has no verified exact series selection")
            classifying = {}
            for dims in selected:
                for variable, code in dims.items():
                    classifying.setdefault(variable, set()).add(code)
            first_year = int(registry.get("table_first_year", {}).get(key_val, 0))
            request_data = {"name": key_val, "area": "all", "format": "ffcsv", "language": "de",
                            "startyear": str(max(int(since[:4]), first_year)), "endyear": until[:4], "job": "false"}
            for number, variable in enumerate(sorted(classifying), start=1):
                request_data[f"classifyingvariable{number}"] = variable
                request_data[f"classifyingkey{number}"] = ",".join(sorted(classifying[variable]))
            canonical_url = f"{base_url}?name={key_val}"
            url = base_url
            request_contract = {"request_method": "POST", "auth_source": source_id, "request_data": request_data}
            content_type = "application/zip"
        elif source_id == "ECB_SDMX":
            required_series_keys = [key_val]
            if registry.get("read_timeout_seconds"):
                request_contract = {"read_timeout_seconds": registry["read_timeout_seconds"]}
            flow, dimensions = key_val.split(".", 1)
            canonical_url = f"{base_url.rstrip('/')}/{flow}/{dimensions}"
            url = f"{canonical_url}?format=csvdata"
            if since:
                url += f"&startPeriod={since}"
            if until:
                url += f"&endPeriod={until}"
            content_type = "text/csv"
        elif source_id == "BUNDESBANK_SDMX":
            flow, dimensions = key_val.split(".", 1)
            canonical_url = f"{base_url.rstrip('/')}/{flow}/{dimensions}"
            query_params = {"format": "csv"}
            if since:
                query_params["startPeriod"] = since
            if until:
                query_params["endPeriod"] = until
            url = f"{canonical_url}?{urlencode(query_params)}"
            request_contract = {"headers": {"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"}}
            content_type = "text/csv"
        elif source_id == "SMARD_DE":
            required_series_keys = [f"SMARD|{key_val}"]
            year_int = int(since[:4]) if since and since[:4].isdigit() else today.year
            ts = int(datetime(year_int - 1, 12, 31, 23, 0, tzinfo=timezone.utc).timestamp() * 1000)
            canonical_url = f"{base_url.rstrip('/')}/4169_DE_day_{ts}.json"
            url = canonical_url
            content_type = "application/json"
        else:
            canonical_url = f"{base_url}?series={key_val}"
            url = canonical_url
            if since:
                url += f"&since={since}"
            if until:
                url += f"&until={until}"
            content_type = "application/json" if ("EUROSTAT" in source_id or "JSON" in source_id) else "text/csv"

        yield {
            "item_id": f"{source_id}_{key_name}",
            "upstream_id": key_val,
            "series_key": key_val,
            "required_series_keys": required_series_keys,
            "url": url,
            "canonical_url": canonical_url,
            "publisher": registry.get("attribution", source_id),
            "content_type": content_type,
            **request_contract,
        }
        count += 1


def _measure_link(series_id, target, facts, entities, common, entity) -> None:
    from src.utils.taxonomy import commodity_entity, sector_config, sector_entity
    if "commodity" in target:
        commodity = commodity_entity(target["commodity"], **entity)
        sector = sector_entity(sector_config()["commodities"][target["commodity"]]["sector"], **entity)
        entities.update({commodity.entity_id: commodity, sector.entity_id: sector})
        member = Fact(subject_id=commodity.entity_id, predicate="BELONGS_TO", object_id=sector.entity_id,
                      payload_json=json.dumps({"basis": "product classification (CPA 2.1 / GP2019)"}), **common)
        facts.setdefault(member.fact_version_id, (member, series_id))
        measured = commodity.entity_id
    else:
        sector = sector_entity(target["sector"], **entity)
        entities[sector.entity_id] = sector
        measured = sector.entity_id
    link = Fact(subject_id=series_id, predicate="MEASURES", object_id=measured,
                payload_json=json.dumps({"metric": target.get("metric", "level"), "subject_type": "ECONOMIC_INDEX"}), **common)
    facts.setdefault(link.fact_version_id, (link, series_id))


def _series_links(series_ids: list[str], document: SourceDocument, store: Optional[DualLayerStore]):
    """MEASURES (series -> sector/commodity) and BELONGS_TO (commodity -> sector) from config/series_names.json."""
    from pathlib import Path
    catalog = json.loads((Path(__file__).resolve().parents[1] / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    common = {"available_at": document.available_at, "source_id": document.source_id, "claim_kind": "catalog_mapping",
              "batch_id": document.batch_id, "data_origin": document.data_origin}
    entity = {"batch_id": document.batch_id, "data_origin": document.data_origin}
    facts: dict[str, tuple[Fact, str]] = {}
    entities = {}
    for series_id in series_ids:
        targets = (catalog.get(series_id) or {}).get("measures") or []
        for target in (targets if isinstance(targets, list) else [targets]):  # a policy number can act on several nodes
            _measure_link(series_id, target, facts, entities, common, entity)
    kept = _new_facts_only(store, [fact for fact, _ in facts.values()])
    evidence = [EvidenceLink(assertion_version_id=fact.fact_version_id, assertion_kind="FACT", document_version_id=document.version_id,
                             selector_type="SDMX_DIMENSION", selector_json=json.dumps({"series_id": facts[fact.fact_version_id][1]}),
                             support_type="DIRECT", available_at=document.available_at, batch_id=document.batch_id)
                for fact in kept]
    return kept, list(entities.values()), evidence


def _make_stats_parser(source_id: str) -> Parser:
    def _parser(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
        from src.scrapers.statistics import normalize_series_response
        series_key = str(item.get("series_key") or item.get("upstream_id") or "")
        fetched_at = document.available_at or document.fetched_at
        raw_obs = normalize_series_response(source_id, series_key, content, fetched_at)
        if item.get("required_series_keys") or item.get("optional_series_keys"):
            required = set(item.get("required_series_keys") or [])
            raw_obs = [observation for observation in raw_obs
                       if observation.series_id in required | set(item.get("optional_series_keys") or [])]
            if not required <= {observation.series_id for observation in raw_obs}:
                raise FetchError("configured statistical series is missing from provider response; review required")
        source_dimensions = {}
        if source_id in {"ECB_SDMX", "BUNDESBANK_SDMX"}:
            import csv
            import io
            text = content.decode("utf-8-sig", errors="replace")
            delimiter = ";" if ";" in text.split("\n", 1)[0] else ","
            for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
                row_key = row.get("KEY") or row.get("") or ""
                if row_key:
                    source_dimensions[row_key] = {key: value for key, value in row.items()
                        if key and key not in {"KEY", "TIME_PERIOD", "OBS_VALUE"} and not key.startswith("OBS_")
                        and key not in {"LAST_UPDATE", "LAST_UPDATED", "UPDATE_DATE"} and value}
        observations = []
        evidence = []
        for o in raw_obs:
            o_updated = o.model_copy(update={"batch_id": document.batch_id, "data_origin": document.data_origin})
            observations.append(o_updated)
            evidence.append(EvidenceLink(
                assertion_version_id=o_updated.revision_id,
                assertion_kind="OBSERVATION",
                document_version_id=document.version_id,
                selector_type="SDMX_DIMENSION",
                selector_json=json.dumps({"series_id": o.series_id, "period_start": o.period_start}),
                support_type="DIRECT",
                batch_id=document.batch_id,
            ))
        series_defs = {}
        for o in raw_obs:
            if o.series_id not in series_defs:
                freq = "QUARTERLY"
                try:
                    if "T" in o.period_start:
                        dt_s = datetime.fromisoformat(o.period_start.replace("Z", "+00:00"))
                        dt_e = datetime.fromisoformat(o.period_end_exclusive.replace("Z", "+00:00"))
                        diff_h = (dt_e - dt_s).total_seconds() / 3600.0
                        if diff_h <= 1.0:
                            freq = "HOURLY"
                        elif diff_h <= 24.0:
                            freq = "DAILY"
                    else:
                        d_s = datetime.fromisoformat(o.period_start)
                        d_e = datetime.fromisoformat(o.period_end_exclusive)
                        days = (d_e - d_s).days
                        if days <= 1:
                            freq = "DAILY"
                        elif 28 <= days <= 31:
                            freq = "MONTHLY"
                        elif 89 <= days <= 93:
                            freq = "QUARTERLY"
                        elif 365 <= days <= 366:
                            freq = "ANNUAL"
                except Exception:
                    pass

                parts = o.series_id.split("|")
                dim_dict = dict(part.split("=", 1) for part in parts[1:] if "=" in part and not part.endswith("=100"))
                geo_id = None
                base_period = o.unit if "=100" in o.unit else None
                m_kind = "UNKNOWN"
                m_name = "unknown_measurement"

                if source_id in {"ECB_SDMX", "BUNDESBANK_SDMX"}:
                    dim_dict = source_dimensions.get(o.series_id, {})
                    geo_id = dim_dict.get("REF_AREA") or ("DE" if source_id == "BUNDESBANK_SDMX" else None)
                    flow = (series_key or o.series_id).split(".", 1)[0].upper()
                    freq = {"D": "DAILY", "M": "MONTHLY", "Q": "QUARTERLY", "A": "ANNUAL"}.get(dim_dict.get("FREQ"), freq)
                    if flow == "EXR":
                        m_kind, m_name = "EXCHANGE_RATE", "exchange_rate"
                    elif flow in {"MIR", "BBSIS"} or "ZST" in o.series_id or "ZAR" in o.series_id:
                        m_kind, m_name = "INTEREST_RATE", "interest_rate"
                elif source_id == "SMARD_DE" or "power" in (series_key or o.series_id).lower() or "smard" in (series_key or o.series_id).lower():
                    m_kind = "ELECTRICITY_PRICE"
                    m_name = "wholesale_electricity_price"
                    geo_id = "DE"
                    freq = "DAILY" if len(o.period_start) == 10 else "HOURLY"
                elif source_id == "EUROSTAT_STS":
                    geo_id = dim_dict.get("geo")
                    dim_dict["dataset"] = parts[1]
                    freq = {"D": "DAILY", "M": "MONTHLY", "Q": "QUARTERLY", "A": "ANNUAL"}.get(dim_dict.get("freq"), freq)
                    indicator = dim_dict.get("indic_bt") or dim_dict.get("indic", "")
                    from src.config import load_data_sources_registry
                    registered = {sel["provider_series_key"]: sel["measurement_kind"] for sel in
                                  load_data_sources_registry()["sources"]["EUROSTAT_STS"].get("context_series", {}).values()}
                    m_kind = registered.get(o.series_id) or {
                        "PRC_PRR": "PRODUCER_PRICE_INDEX", "COST": "CONSTRUCTION_INPUT_COST_INDEX", "PROD": "PRODUCTION_INDEX",
                        "BS-CPE-BAL": "SURVEY_PRICE_EXPECTATIONS", "BS-FLBA5-PC": "SURVEY_MATERIAL_SHORTAGE"}.get(indicator, "UNKNOWN")
                    m_name = m_kind.lower()
                elif source_id == "DESTATIS_GENESIS":
                    # The registry selection states what each verified Destatis series measures.
                    from src.config import load_data_sources_registry
                    catalog = load_data_sources_registry()
                    kinds = {catalog["primary_target"]["provider_series_key"]: catalog["primary_target"]["measurement_kind"]}
                    kinds.update({sel["provider_series_key"]: sel["measurement_kind"] for sel in
                                  catalog["sources"]["DESTATIS_GENESIS"].get("context_series", {}).values()})
                    m_kind = kinds.get(o.series_id, "UNKNOWN")
                    m_name = m_kind.lower()
                    geo_id = "DE"

                series_defs[o.series_id] = SeriesDefinition(
                    series_id=o.series_id,
                    source_id=source_id,
                    dataset_id=parts[1] if source_id == "EUROSTAT_STS" else (series_key or parts[0]).split(".", 1)[0],
                    provider_series_key=o.series_id,
                    dimensions_json=json.dumps(dim_dict, sort_keys=True),
                    metric_name=m_name,
                    unit=o.unit,
                    currency=dim_dict.get("CURRENCY"),
                    frequency=freq,
                    geography_id=geo_id,
                    base_period=base_period,
                    adjustment=dim_dict.get("s_adj") or dim_dict.get("ADJUSTMENT") or ("NSA" if source_id in {"DESTATIS_GENESIS", "ECB_SDMX", "BUNDESBANK_SDMX"} else None),
                    measurement_kind=m_kind,
                    available_at=document.available_at,
                    batch_id=document.batch_id,
                )
        link_facts, link_entities, link_evidence = _series_links(list(series_defs), document, item.get("_store"))
        evidence.extend(link_evidence)
        accepted = len([o for o in observations if o.quality_state == "accepted"])
        review = len([o for o in observations if o.quality_state == "needs_review"])
        status = "accepted" if accepted > 0 else ("needs_review" if review > 0 else "out_of_scope")
        return {
            "status": status,
            "facts": link_facts,
            "observations": observations,
            "evidence_links": evidence,
            "entities": link_entities,
            "series": list(series_defs.values()),
            "accepted_count": accepted,
            "review_count": review,
            "rejected_count": 0,
        }
    return _parser


for s_id in ["DESTATIS_GENESIS", "EUROSTAT_STS", "ECB_SDMX", "BUNDESBANK_SDMX", "SMARD_DE"]:
    register_source(s_id, _stats_discover, _make_stats_parser(s_id))


def _new_facts_only(store: Optional[DualLayerStore], facts: list[Fact]) -> list[Fact]:
    """Drop facts already committed with the same content; a refetch must not mint new versions."""
    if store is None:
        return facts
    from src.schemas.ingestion import _canonical_json
    committed = {key for key, batch in store._canonical_batches.items() if batch.status == "committed"}
    latest: dict[str, Fact] = {}
    for fact in store._canonical_facts.values():
        if fact.batch_id in committed and (fact.fact_id not in latest or fact.recorded_at > latest[fact.fact_id].recorded_at):
            latest[fact.fact_id] = fact
    kept = []
    for fact in facts:
        prev = latest.get(fact.fact_id)
        if prev is None:
            kept.append(fact)
        elif prev.is_retracted or _canonical_json(prev.payload_json or "{}") != _canonical_json(fact.payload_json or "{}"):
            kept.append(fact.model_copy(update={"supersedes_version_id": prev.fact_version_id}))
    return kept


# =====================================================================
# Eurostat input-output table: what construction buys from each product group, and who buys construction
# =====================================================================
def _jsonstat_cells(data: dict) -> list[tuple[dict[str, str], float]]:
    ids, sizes = data["id"], data["size"]
    codes = {dim: {pos: code for code, pos in data["dimension"][dim]["category"]["index"].items()} for dim in ids}
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    return [({dim: codes[dim][(int(pos) // strides[i]) % sizes[i]] for i, dim in enumerate(ids)}, float(value))
            for pos, value in data.get("value", {}).items() if value is not None]


def _naio_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    # Annual tables are small, so every request covers all published years and ignores the window.
    registry = load_data_sources_registry()["sources"][source_id]
    base = registry["endpoints"]["api_v2"].rstrip("/")
    for name, table in islice(registry["tables"].items(), limit):
        query = urlencode(table["dimensions"], doseq=True)
        yield {"item_id": f"{source_id}_{name}", "upstream_id": table["dataset"], "table": name,
               "url": f"{base}/{table['dataset']}?format=JSON&lang=EN&{query}",
               "canonical_url": f"{base}/{table['dataset']}?{query}",
               "publisher": registry.get("attribution", source_id), "content_type": "application/json"}


def _naio_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    from src.utils.taxonomy import sector_config, sector_entity
    table = load_data_sources_registry()["sources"][document.source_id]["tables"][item["table"]]
    data = json.loads(content)
    labels = {dim: data["dimension"][dim]["category"]["label"] for dim in data["id"]}
    cells = {(c["stk_flow"], c["prd_ava"], c["prd_use"], c["time"]): v for c, v in _jsonstat_cells(data)}
    if not cells:
        raise FetchError("input-output response has no values")
    construction = table["sector"]
    inputs = table["role"] == "inputs"
    other_dim = "prd_ava" if inputs else "prd_use"
    known = sector_config()["sectors"]
    total_code = "P1" if inputs else "TU"  # inputs as a share of output (cost shares), uses as a share of total use
    pairs: list[tuple[Fact, dict]] = []
    entities = {construction: sector_entity(construction, batch_id=document.batch_id, data_origin=document.data_origin)}
    for (flow, ava, use, year), value in sorted(cells.items()):
        other = ava if inputs else use
        code = other.removeprefix("CPA_")
        # Subcontracting (construction buying construction) is kept once, on the inputs side.
        if flow != "TOTAL" or value <= 0 or code not in known or (code == construction and not inputs):
            continue
        key = (ava, use, year)
        total = cells.get(("TOTAL", *((total_code, use) if inputs else (ava, total_code)), year))
        imported = cells.get(("IMP", *key))
        payload = {"episode": int(year), "dataset": item["upstream_id"], "value_mio_eur": value,
                   "share": round(value / total, 5) if total else None, "share_of": total_code,
                   "flow": "input" if inputs else "use", "subject_type": "SECTOR", "object_type": "SECTOR"}
        if imported is not None:
            payload.update(imported_mio_eur=imported, import_share=round(imported / value, 5))
        entities.setdefault(code, sector_entity(code, batch_id=document.batch_id, data_origin=document.data_origin))
        subject, obj = (f"sec_{code}", f"sec_{construction}") if inputs else (f"sec_{construction}", f"sec_{code}")
        fact = Fact(subject_id=subject, predicate="FLOWS_TO", object_id=obj, payload_json=json.dumps(payload, sort_keys=True),
                    valid_from=datetime(int(year), 1, 1, tzinfo=timezone.utc), valid_to=datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc),
                    available_at=document.available_at, source_id=document.source_id, claim_kind="source_claim",
                    batch_id=document.batch_id, data_origin=document.data_origin)
        quote = f"{labels['prd_ava'].get(ava, ava)} -> {labels['prd_use'].get(use, use)}, {year}: {value:g} million EUR"
        pairs.append((fact, {"dataset": item["upstream_id"], "stk_flow": flow, "prd_ava": ava, "prd_use": use, "time": year, "quote": quote}))
    facts = _new_facts_only(item.get("_store"), [fact for fact, _ in pairs])
    selectors = {fact.fact_id: selector for fact, selector in pairs}
    evidence = [EvidenceLink(assertion_version_id=fact.fact_version_id, assertion_kind="FACT", document_version_id=document.version_id,
                             selector_type="TABLE_CELL", selector_json=json.dumps({k: v for k, v in selectors[fact.fact_id].items() if k != "quote"}),
                             quoted_text=selectors[fact.fact_id]["quote"], support_type="DIRECT", available_at=document.available_at,
                             batch_id=document.batch_id) for fact in facts]
    return {"status": "accepted", "facts": facts, "observations": [], "evidence_links": evidence,
            "entities": list(entities.values()), "series": [], "accepted_count": len(facts), "review_count": 0, "rejected_count": 0}


register_source("EUROSTAT_NAIO", _naio_discover, _naio_parse)


# =====================================================================
# German federal laws: configured links, each verified against a quote in the official text
# =====================================================================
def _gii_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    # Only the current consolidated text is published, so the window does not apply.
    registry = load_data_sources_registry()["sources"][source_id]
    base = registry["endpoints"]["law_xml"].rstrip("/")
    for key, law in islice(registry["laws"].items(), limit):
        url = f"{base}/{law['path']}/xml.zip"
        yield {"item_id": f"{source_id}_{key}", "upstream_id": key, "url": url, "canonical_url": url,
               "publisher": registry.get("attribution", source_id), "content_type": "application/zip"}


def _gii_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    from src.scrapers.german_laws import implemented_eu_acts, parse_gii_xml
    from src.utils.taxonomy import commodity_entity, sector_entity
    config = load_data_sources_registry()["sources"][document.source_id]["laws"][item["upstream_id"]]
    try:
        law = parse_gii_xml(content)
    except (ET.ParseError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise FetchError(f"law XML could not be parsed: {_safe_error(exc)}") from exc
    store = item.get("_store")
    known = getattr(store, "_canonical_entities", {})
    origin = {"batch_id": document.batch_id, "data_origin": document.data_origin}
    law_id = f"law_de_{config['path']}"
    entities = {law_id: Entity(entity_id=law_id, entity_type="LAW", entity_subtype="DE_FEDERAL_LAW", canonical_label=config["name"],
                               jurisdiction="DE", metadata_json=json.dumps({"abbreviation": item["upstream_id"]}), **origin),
                "geo_DE": known.get("geo_DE") or Entity(entity_id="geo_DE", entity_type="GEOGRAPHY", canonical_label="DE", jurisdiction="DE", **origin)}
    entities["geo_DE"] = entities["geo_DE"].model_copy(update=origin)
    signed = datetime.fromisoformat(law["signed"]).replace(tzinfo=timezone.utc) if law["signed"] else None
    common = {"available_at": document.available_at, "source_id": document.source_id, "event_at": signed, **origin}
    norms = {n["norm"]: n for n in law["norms"]}
    pairs: list[tuple[Fact, dict]] = []
    for link in config["links"]:
        norm = next((n for label, n in norms.items() if label == link["norm"] or label.startswith((link["norm"] + " ", link["norm"] + "("))), None)
        found = norm is not None and link["quote"] in norm["text"]
        target = (commodity_entity(link["commodity"], **origin) if "commodity" in link else sector_entity(link["sector"], **origin))
        entities[target.entity_id] = target
        payload = {"norm": link["norm"], "status": law["status"], **({"basis": link["basis"]} if "basis" in link else {})}
        fact = Fact(subject_id=law_id, predicate="REGULATES", object_id=target.entity_id, payload_json=json.dumps(payload, sort_keys=True),
                    quality_state="accepted" if found else "needs_review", **common)
        pairs.append((fact, {"selector": {"norm": link["norm"], "doknr": law["doknr"]}, "quote": link["quote"], "found": found}))
    pairs.append((Fact(subject_id=law_id, predicate="APPLIES_IN", object_id="geo_DE", **common),
                  {"selector": {"doknr": law["doknr"]}, "quote": law["title"][:500], "found": True}))
    preamble = " ".join(n["text"] for n in law["norms"][:2])
    for celex in implemented_eu_acts(preamble):
        eu_id = f"law_{celex}"
        entities[eu_id] = (known.get(eu_id) or Entity(entity_id=eu_id, entity_type="LAW", canonical_label=f"EU Law {celex}", **origin)).model_copy(update=origin)
        pairs.append((Fact(subject_id=law_id, predicate="IMPLEMENTS", object_id=eu_id, payload_json=json.dumps({"celex": celex}), **common),
                      {"selector": {"norm": "preamble", "doknr": law["doknr"]}, "quote": preamble[:500], "found": True}))
    facts = _new_facts_only(store, [fact for fact, _ in pairs])
    support = {fact.fact_version_id: meta for fact, meta in pairs}
    evidence = [EvidenceLink(assertion_version_id=f.fact_version_id, assertion_kind="FACT", document_version_id=document.version_id,
                             selector_type="ARTICLE_PARAGRAPH", selector_json=json.dumps(support[f.fact_version_id]["selector"]),
                             quoted_text=support[f.fact_version_id]["quote"],
                             support_type="DIRECT" if support[f.fact_version_id]["found"] else "UNVERIFIED",
                             available_at=document.available_at, batch_id=document.batch_id) for f in facts]
    accepted = sum(f.quality_state == "accepted" for f in facts)
    return {"status": "accepted" if accepted else "needs_review", "facts": facts, "observations": [], "evidence_links": evidence,
            "entities": list(entities.values()), "series": [], "accepted_count": accepted,
            "review_count": len(facts) - accepted, "rejected_count": 0}


register_source("GESETZE_IM_INTERNET", _gii_discover, _gii_parse)


# =====================================================================
# Policy numbers quoted from official law texts (CO2 prices, CBAM phase-in): no LLM, a quote check per number
# =====================================================================
def _law_parameters_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    registry = load_data_sources_registry()["sources"][source_id]
    for key, doc in islice(registry["documents"].items(), limit):
        yield {"item_id": f"{source_id}_{key}", "upstream_id": key, "url": doc["url"], "canonical_url": doc.get("canonical_url", doc["url"]),
               "publisher": registry.get("attribution", source_id), "headers": doc.get("headers", {}),
               "content_type": "application/zip" if doc["format"] == "gii_zip" else "application/xhtml+xml"}


def _law_text(doc: dict, content: bytes, norm: str) -> str:
    """Plain text the quotes are checked against: the named norm of a German law, or the whole EU act."""
    if doc["format"] == "gii_zip":
        from src.scrapers.german_laws import parse_gii_xml
        norms = parse_gii_xml(content)["norms"]
        return " ".join(n["text"] for n in norms if n["norm"] == norm or n["norm"].startswith((norm + " ", norm + "(")))
    import html
    text = html.unescape(re.sub(r"<[^>]+>", " ", content.decode("utf-8", errors="replace")))
    return re.sub(r"\s+", " ", text)


def _law_parameters_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    doc = load_data_sources_registry()["sources"][document.source_id]["documents"][item["upstream_id"]]
    origin = {"batch_id": document.batch_id, "data_origin": document.data_origin}
    observations, evidence, series = [], [], []
    for spec in doc["parameters"]:
        try:
            text = _law_text(doc, content, spec["norm"])
        except (ET.ParseError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise FetchError(f"law text could not be parsed: {_safe_error(exc)}") from exc
        if not text:
            raise FetchError(f"norm {spec['norm']!r} not found in the law text")
        known_from = spec.get("known_from")
        if known_from and spec.get("known_from_quote") not in text:
            known_from = None  # the publication date must itself be in the text; otherwise fall back to the period start
        series.append(SeriesDefinition(
            series_id=spec["series_id"], source_id=document.source_id, dataset_id=item["upstream_id"], provider_series_key=spec["series_id"],
            dimensions_json=json.dumps({"law_id": doc["law_id"], "norm": spec["norm"], "label": spec["label"]}, ensure_ascii=False),
            metric_name=spec["metric"], unit=spec["unit"], frequency=spec["frequency"], geography_id=spec["geo"], adjustment=None,
            measurement_kind=spec["measurement_kind"], available_at=document.available_at, batch_id=document.batch_id))
        for entry in spec["entries"]:
            found = entry["quote"] in text
            year = int(entry["period"])
            obs = Observation(series_id=spec["series_id"], period_start=f"{year}-01-01", period_end_exclusive=f"{year + 1}-01-01",
                              value=float(entry["value"]), original_value_text=entry["quote"], source_unit=spec["unit"], unit=spec["unit"],
                              available_at=document.available_at, quality_state="accepted" if found else "needs_review",
                              conversion_lineage_json=json.dumps({"known_from": known_from or f"{year}-01-01"}), **origin)
            observations.append(obs)
            evidence.append(EvidenceLink(assertion_version_id=obs.revision_id, assertion_kind="OBSERVATION", document_version_id=document.version_id,
                                         selector_type="ARTICLE_PARAGRAPH", selector_json=json.dumps({"norm": spec["norm"], "period": entry["period"]}),
                                         quoted_text=entry["quote"], support_type="DIRECT" if found else "UNVERIFIED",
                                         available_at=document.available_at, batch_id=document.batch_id))
    link_facts, link_entities, link_evidence = _series_links([s.series_id for s in series], document, item.get("_store"))
    accepted = sum(o.quality_state == "accepted" for o in observations)
    return {"status": "accepted" if accepted else "needs_review", "facts": link_facts, "observations": observations,
            "evidence_links": evidence + link_evidence, "entities": link_entities, "series": series,
            "accepted_count": accepted, "review_count": len(observations) - accepted, "rejected_count": 0}


register_source("LAW_PARAMETERS", _law_parameters_discover, _law_parameters_parse)


# =====================================================================
# World Bank commodity prices (Pink Sheet): world prices of oil, gas, coal and metals
# =====================================================================
PINK_SHEET_LINK = re.compile(r"https://thedocs\.worldbank\.org/en/doc/[0-9a-f]{32}-\d{10}/related/CMO-Historical-Data-Monthly\.xlsx")


def _pink_sheet_link(page: str) -> str:
    links = sorted(set(PINK_SHEET_LINK.findall(page)))
    if len(links) != 1:
        raise FetchError(f"expected one monthly Pink Sheet link on the landing page, found {len(links)}")
    return links[0]


def _pink_sheet_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    # The whole history is one workbook whose URL changes monthly; the window does not apply.
    registry = load_data_sources_registry()["sources"][source_id]
    landing = registry["endpoints"]["landing_page"]
    page, _, _, _ = bounded_fetch(landing, allowed_hosts=_registered_hosts(registry), max_bytes=2 * 1024 * 1024)
    url = _pink_sheet_link(page.decode("utf-8", errors="replace"))
    yield {"item_id": f"{source_id}_monthly", "upstream_id": "monthly", "url": url, "canonical_url": url,
           "publisher": registry.get("attribution", source_id),
           "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _xlsx_rows(content: bytes, sheet_name: str) -> Iterable[tuple[int, dict[str, str]]]:
    """Rows of one worksheet as {column letter: text}, read with the standard library (xlsx is zipped XML)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    book = zipfile.ZipFile(io.BytesIO(content))
    strings = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
               for si in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    sheet = next(s for s in ET.fromstring(book.read("xl/workbook.xml")).iter(f"{{{ns['m']}}}sheet") if s.get("name") == sheet_name)
    rel = sheet.get(f"{{{ns['r']}}}id")
    target = next(r.get("Target") for r in ET.fromstring(book.read("xl/_rels/workbook.xml.rels")) if r.get("Id") == rel)
    for row in ET.fromstring(book.read("xl/" + target.lstrip("/").removeprefix("xl/"))).iter(f"{{{ns['m']}}}row"):
        cells = {}
        for cell in row.findall("m:c", ns):
            value = cell.find("m:v", ns)
            if value is not None:
                cells[re.match(r"[A-Z]+", cell.get("r")).group()] = strings[int(value.text)] if cell.get("t") == "s" else value.text
        yield int(row.get("r")), cells


def _pink_sheet_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    registry = load_data_sources_registry()["sources"][document.source_id]
    try:
        rows = list(_xlsx_rows(content, registry["sheet"]))
    except (KeyError, StopIteration, ET.ParseError, zipfile.BadZipFile) as exc:
        raise FetchError(f"Pink Sheet workbook could not be read: {_safe_error(exc)}") from exc
    updated = next((re.search(r"Updated on (\w+ \d{1,2}, \d{4})", c.get("A", "")) for _, c in rows[:6] if "Updated on" in c.get("A", "")), None)
    vintage = datetime.strptime(updated.group(1), "%B %d, %Y").date().isoformat() if updated else None
    header = next(c for _, c in rows if "Crude oil, Brent" in c.values())
    columns = {label.strip(): col for col, label in header.items()}
    missing = [label for label in registry["series"] if label not in columns]
    if missing:
        raise FetchError(f"Pink Sheet columns missing: {missing}")
    origin = {"batch_id": document.batch_id, "data_origin": document.data_origin}
    observations, evidence, series = [], [], []
    for label, spec in registry["series"].items():
        col = columns[label]
        series.append(SeriesDefinition(series_id=spec["series_id"], source_id=document.source_id, dataset_id="CMO-Historical-Data-Monthly",
                                       provider_series_key=label, dimensions_json=json.dumps({"column": label}), metric_name="world_price",
                                       unit=spec["unit"], currency="USD", frequency="MONTHLY", geography_id="WORLD", adjustment="NSA",
                                       measurement_kind="WORLD_COMMODITY_PRICE", available_at=document.available_at, batch_id=document.batch_id))
        for number, cells in rows:
            period = re.fullmatch(r"(\d{4})M(\d{2})", cells.get("A", ""))
            text = cells.get(col, "")
            if not period or not re.fullmatch(r"-?\d+(\.\d+)?(E-?\d+)?", text):
                continue  # "…" and "..." mark months without a price
            year, month = int(period.group(1)), int(period.group(2))
            end = f"{year + month // 12}-{month % 12 + 1:02d}-01"
            obs = Observation(series_id=spec["series_id"], period_start=f"{year}-{month:02d}-01", period_end_exclusive=end,
                              value=round(float(text), 6), original_value_text=text, source_unit=spec["unit"], unit=spec["unit"],
                              vintage_id=vintage, available_at=document.available_at, **origin)
            observations.append(obs)
            evidence.append(EvidenceLink(assertion_version_id=obs.revision_id, assertion_kind="OBSERVATION", document_version_id=document.version_id,
                                         selector_type="TABLE_CELL", selector_json=json.dumps({"sheet": registry["sheet"], "cell": f"{col}{number}"}),
                                         quoted_text=text, support_type="DIRECT", available_at=document.available_at, batch_id=document.batch_id))
    link_facts, link_entities, link_evidence = _series_links([s.series_id for s in series], document, item.get("_store"))
    return {"status": "accepted", "facts": link_facts, "observations": observations, "evidence_links": evidence + link_evidence,
            "entities": link_entities, "series": series, "accepted_count": len(observations), "review_count": 0, "rejected_count": 0}


register_source("WORLD_BANK_PINK_SHEET", _pink_sheet_discover, _pink_sheet_parse)


# =====================================================================
# Published statistical reports (PDF): the construction price index weighting scheme
# =====================================================================
def _library_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    registry = load_data_sources_registry()["sources"][source_id]
    for key, doc in islice(registry["documents"].items(), limit):
        yield {"item_id": f"{source_id}_{key}", "upstream_id": key, "url": doc["url"], "canonical_url": doc["url"],
               "publisher": registry.get("attribution", source_id), "content_type": "application/pdf"}


def _pdf_lines(content: bytes, pages: list[int]) -> list[str]:
    import pymupdf
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        return [line.strip() for number in range(pages[0] - 1, min(pages[1], len(pdf)))
                for line in pdf[number].get_text().splitlines() if line.strip()]


def _weight_after(lines: list[str], label: str) -> Optional[tuple[float, str]]:
    """Per-mille weight printed after a row label (row labels may wrap onto one continuation line)."""
    for index, line in enumerate(lines):
        if line == label:
            for offset in (1, 2):
                value = lines[index + offset] if index + offset < len(lines) else ""
                if re.fullmatch(r"\d{1,3}(?: \d{3})*(?:,\d+)?", value):
                    return float(value.replace(" ", "").replace(",", ".")), " ".join(lines[index:index + offset + 1])
    return None


def _library_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    from src.utils.taxonomy import sector_entity
    config = load_data_sources_registry()["sources"][document.source_id]["documents"][item["upstream_id"]]
    try:
        lines = _pdf_lines(content, config["pages"])
    except Exception as exc:  # malformed PDF
        raise FetchError(f"report PDF could not be read: {_safe_error(exc)}") from exc
    total = _weight_after(lines, config["total"])
    if total is None or total[0] != 1000:
        raise FetchError("weighting table total (1 000 per mille) not found")
    origin = {"batch_id": document.batch_id, "data_origin": document.data_origin}
    parent = config["parent"]
    entities = {parent: sector_entity(parent, **origin)}
    pairs: list[tuple[Fact, str]] = []
    for code, label in config["rows"].items():
        found = _weight_after(lines, label)
        if found is None:
            raise FetchError(f"weight row {label!r} not found in report")
        weight, quote = found
        group = None if code in ("ROHBAUARB", "AUSBAUARB") else ("ROHBAUARB" if code in config["shell_trades"] else "AUSBAUARB")
        node, target = f"F_{code}", f"F_{group}" if group else parent
        entities[node] = sector_entity(node, **origin)
        payload = {"weight_per_mille": weight, "share_of_total": round(weight / 1000, 6), "scheme": "Basis 2021 = 100",
                   "index": "Bauleistungen am Bauwerk, Wohngebäude", "subject_type": "SECTOR", "object_type": "SECTOR"}
        pairs.append((Fact(subject_id=f"sec_{node}", predicate="COMPONENT_OF", object_id=f"sec_{target}",
                           payload_json=json.dumps(payload, sort_keys=True), valid_from=datetime(2021, 1, 1, tzinfo=timezone.utc),
                           available_at=document.available_at, source_id=document.source_id, **origin), quote))
    facts = _new_facts_only(item.get("_store"), [fact for fact, _ in pairs])
    quotes = {fact.fact_version_id: quote for fact, quote in pairs}
    evidence = [EvidenceLink(assertion_version_id=f.fact_version_id, assertion_kind="FACT", document_version_id=document.version_id,
                             selector_type="TABLE_CELL", selector_json=json.dumps({"pages": config["pages"], "row": quotes[f.fact_version_id]}),
                             quoted_text=quotes[f.fact_version_id], support_type="DIRECT", available_at=document.available_at,
                             batch_id=document.batch_id) for f in facts]
    return {"status": "accepted", "facts": facts, "observations": [], "evidence_links": evidence, "entities": list(entities.values()),
            "series": [], "accepted_count": len(facts), "review_count": 0, "rejected_count": 0}


register_source("STATISTISCHE_BIBLIOTHEK", _library_discover, _library_parse)


# =====================================================================
# Bundesbank real-time database: evidenced release dates for the target and predictors
# =====================================================================
_RTD_HEADERS = {"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"}


def _rtd_release_end_of_day(day: str) -> datetime:
    """Release day only has DAY precision; count it as public from the end of that Berlin day."""
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(f"{day}T23:59:59").replace(tzinfo=ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)


def _rtd_dated_series_ids(registry: dict[str, Any], calendar: str) -> tuple[str, ...]:
    """Series whose committed values a release calendar dates: the primary target and/or named context series."""
    names = registry["sources"]["BUNDESBANK_RTD"]["dated_series"][calendar]
    ids = []
    for name in [names] if isinstance(names, str) else names:
        if name == "primary_target":
            ids.append(registry["primary_target"]["provider_series_key"])
            continue
        match = next((entry["context_series"][name]["provider_series_key"] for entry in registry["sources"].values()
                      if name in (entry.get("context_series") or {})), None)
        if match is None:
            raise KeyError(f"Release calendar {calendar} dates unknown series {name}")
        ids.append(match)
    return tuple(ids)


def _rtd_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    """One item per vintage of each release calendar, oldest first; each vintage document is dated on its release day."""
    entry = load_data_sources_registry()["sources"][source_id]
    base_url = entry["endpoints"]["sdmx_rest"].rstrip("/")
    host = urlparse(base_url).hostname
    count = 0
    for calendar, series_key in entry["series_keys"].items():
        flow, key = series_key.split("/", 1)
        indicator = key.split(".")[5]
        # The full vintage matrix header lists every release date; only the per-vintage documents are evidence.
        listing, *_ = bounded_fetch(f"{base_url}/{flow}/{key}?format=csv&lang=en", allowed_hosts={host},
                                    headers=_RTD_HEADERS, max_bytes=20 * 1024 * 1024)
        header = listing.decode("utf-8-sig").splitlines()[0]
        days = sorted(datetime.strptime(cell.strip('"'), "%d.%m.%Y").date().isoformat()
                      for cell in header.split(",")[1:] if cell.strip('"'))
        days = [d for d in days if (not since or d >= since[:10]) and (not until or d <= until[:10])]
        # Pad the key to its 8 series dimensions, then REL_YEAR.REL_MONTH.REL_DAY select one vintage.
        padding = "." * (8 - key.count("."))
        for day in days:
            if count >= limit:
                return
            count += 1
            url = f"{base_url}/{flow}/{key}{padding}{day.replace('-', '.')}?format=csv&lang=en"
            yield {"item_id": f"{source_id}_{calendar}_{day}", "upstream_id": f"{indicator}@{day}",
                   "series_key": indicator, "calendar": calendar,
                   "release_date": day, "url": url, "canonical_url": url, "headers": _RTD_HEADERS,
                   "publisher": entry.get("attribution", source_id), "content_type": "text/csv",
                   "availability_basis": "archived_release", "published_at": _rtd_release_end_of_day(day).isoformat()}


_RTD_STATE_CACHE: dict[tuple, tuple] = {}
_RTD_EMITTED: dict[str, set] = {}  # ponytail: grows by one small set per batch per process; fine for CLI runs


def _rtd_committed_state(store: DualLayerStore, series_ids: tuple[str, ...]) -> tuple[dict[tuple, Observation], dict[tuple, Any]]:
    """Per (series, period): the latest committed revision (value source) and the value already release-dated.

    Cached per store generation: committed state cannot change between the vintages of one batch.
    """
    key = (str(store.storage_dir), store.generation, series_ids)
    if key not in _RTD_STATE_CACHE:
        latest: dict[tuple, Observation] = {}
        dated: dict[tuple, Any] = {}
        for row in store.query_observation_revisions(datetime.now(timezone.utc), "system_as_of"):
            if row.series_id not in series_ids or row.value is None or row.is_retracted:
                continue
            cell = (row.series_id, row.period_start)
            if (row.vintage_id or "").startswith("release_dated_latest|"):
                dated[cell] = row.value
                continue
            previous = latest.get(cell)
            if previous is None or store._available(row) > store._available(previous):
                latest[cell] = row
        _RTD_STATE_CACHE.clear()  # ponytail: one entry per calendar; discovery yields calendars one after another
        _RTD_STATE_CACHE[key] = (latest, dated)
    return _RTD_STATE_CACHE[key]


def _rtd_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    """Date committed values of the calendar's series by the first release vintage that contains each period.

    Values stay the latest committed Destatis revision; revision content is identical across vintages,
    so the earliest staged vintage wins.
    """
    from src.scrapers.statistics import _period_bounds
    periods = {line.split(",", 1)[0] for line in content.decode("utf-8-sig").splitlines()
               if re.fullmatch(r"\d{4}-(Q[1-4]|\d{2}),[-0-9.]+", line.strip())}
    if not periods:
        raise ValueError("Bundesbank real-time vintage contains no parseable periods")
    store = item["_store"]
    registry = load_data_sources_registry()
    calendar = item.get("calendar", "construction_price_release_calendar")
    series_ids = _rtd_dated_series_ids(registry, calendar)
    revision_bound = registry["sources"]["BUNDESBANK_RTD"].get("revision_bounds", {}).get(calendar)
    latest, dated = _rtd_committed_state(store, series_ids)
    emitted = _RTD_EMITTED.setdefault(document.batch_id, set())
    observations, evidence = [], []
    for series_id, period in ((s, p) for s in series_ids for p in sorted(periods)):
        start, end = _period_bounds(period)
        base = latest.get((series_id, start))
        if base is None or dated.get((series_id, start)) == base.value:
            continue  # nothing to date, or an earlier committed vintage already dates this value
        if (series_id, start, base.value) in emitted:
            continue  # an earlier vintage of this batch already dates it (vintages arrive oldest first)
        emitted.add((series_id, start, base.value))
        obs = Observation(
            series_id=series_id, period_start=start, period_end_exclusive=end, value=base.value,
            original_value_text=base.original_value_text, source_unit=base.source_unit, unit=base.unit,
            vintage_id=f"release_dated_latest|{base.unit}", provider_status=base.provider_status,
            conversion_lineage_json=json.dumps({
                "availability_rule": f"first Bundesbank real-time {item.get('series_key', 'PP300')} vintage containing the period; upper bound for periods released before the first vintage",
                "value_source": "latest committed Destatis revision",
                "value_revision_id": base.revision_id,
                "release_calendar": calendar,
                "revision_bound": revision_bound}, sort_keys=True),
            available_at=document.available_at, batch_id=document.batch_id, data_origin=document.data_origin)
        observations.append(obs)
        evidence.append(EvidenceLink(assertion_version_id=obs.revision_id, assertion_kind="OBSERVATION",
                                     document_version_id=document.version_id, selector_type="SDMX_DIMENSION",
                                     selector_json=json.dumps({"period": period, "vintage": item.get("release_date"),
                                                               "dated_series_id": series_id}),
                                     support_type="DIRECT", batch_id=document.batch_id))
    return {"status": "accepted" if observations else "out_of_scope", "facts": [], "observations": observations,
            "evidence_links": evidence, "entities": [], "series": [], "accepted_count": len(observations),
            "review_count": 0, "rejected_count": 0}


register_source("BUNDESBANK_RTD", _rtd_discover, _rtd_parse)


def _gisco_discover(source_id: str, since: Optional[str], until: Optional[str], limit: int) -> Iterable[SourceItem]:
    from src.config import load_data_sources_registry
    registry = load_data_sources_registry().get("sources", {}).get(source_id, {})
    url = registry.get("endpoints", {}).get("nuts_boundary_json", "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_2024_4326.geojson")
    yield {
        "item_id": "GISCO_NUTS_2024",
        "upstream_id": "NUTS_RG_20M_2024_4326",
        "url": url,
        "canonical_url": url,
        "publisher": registry.get("attribution", "Eurostat / GISCO"),
        "content_type": "application/geo+json",
    }


def _gisco_parse(item: SourceItem, content: bytes, document: SourceDocument) -> dict[str, Any]:
    from src.scrapers.geography import normalize_geography_response
    fetched_at = document.published_at or document.available_at or document.fetched_at or datetime.now(timezone.utc)
    raw_facts = normalize_geography_response("GISCO_GEOGRAPHY", content, fetched_at)
    facts = []
    evidence = []
    seen_entity_ids = set()
    entities = []
    for f in raw_facts:
        f_updated = f.model_copy(update={"batch_id": document.batch_id, "data_origin": document.data_origin})
        facts.append(f_updated)
        evidence.append(EvidenceLink(
            assertion_version_id=f_updated.fact_version_id,
            assertion_kind="FACT",
            document_version_id=document.version_id,
            selector_type="TABLE_CELL",
            selector_json=json.dumps({"subject_id": f.subject_id, "predicate": f.predicate}),
            support_type="DIRECT",
            batch_id=document.batch_id,
        ))
        for ent_id in [f.subject_id, f.object_id]:
            if ent_id and ent_id not in seen_entity_ids:
                seen_entity_ids.add(ent_id)
                entities.append(Entity(
                    entity_id=ent_id,
                    entity_type="GEOGRAPHY",
                    canonical_label=ent_id,
                    data_origin=document.data_origin,
                    batch_id=document.batch_id,
                    available_at=f.available_at,
                ))
    accepted = len([f for f in facts if f.quality_state == "accepted"])
    review = len([f for f in facts if f.quality_state == "needs_review"])
    status = "accepted" if accepted > 0 else ("needs_review" if review > 0 else "out_of_scope")
    return {
        "status": status,
        "facts": facts,
        "observations": [],
        "evidence_links": evidence,
        "entities": entities,
        "series": [],
        "accepted_count": accepted,
        "review_count": review,
        "rejected_count": 0,
    }


register_source("GISCO_GEOGRAPHY", _gisco_discover, _gisco_parse)


__all__ = ["ArchiveError", "DiskFloorError", "FetchError", "archive_document", "bounded_fetch", "register_source", "resume_batch", "run_ingestion"]
