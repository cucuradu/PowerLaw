"""Real archived fixtures for source-to-forecast acceptance regressions."""
from datetime import datetime, timezone
from pathlib import Path

from src.schemas.ingestion import BatchManifest, EvidenceLink, SourceDocument


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else datetime(value, 1, 1, tzinfo=timezone.utc)


def stage_evidenced(store, batch_id, *, facts=(), observations=(), entities=(), series=(),
                    origin="test", available=None, commit=True, source_id="TEST", availability_basis="first_fetch"):
    """Archive evidence and stage supplied records without modifying their contracts."""
    records = [(f.fact_version_id, "FACT", f) for f in facts]
    records += [(o.revision_id, "OBSERVATION", o) for o in observations]
    text = "\n".join(f"Fixture evidence for {key}" for key, _, _ in records) or f"Fixture {batch_id}"
    raw = text.encode()
    available = available or min((r.available_at for _, _, r in records if r.available_at), default=utc(2020))
    doc = SourceDocument.from_content(source_id, f"https://example.test/{batch_id}", "Acceptance fixture",
                                      raw, batch_id, data_origin=origin, available_at=available,
                                      availability_basis=availability_basis)
    path = Path(store.storage_dir) / "raw" / doc.content_sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    doc = doc.model_copy(update={"raw_uri": path.as_uri(), "recorded_at": available, "published_at": available})
    evidence = [EvidenceLink(assertion_version_id=key, assertion_kind=kind, document_version_id=doc.version_id,
                             quoted_text=text, batch_id=batch_id, available_at=record.available_at or available,
                             recorded_at=record.recorded_at)
                for key, kind, record in records]
    batch = BatchManifest(batch_id=batch_id, source_id=source_id, data_origin=origin)
    store.stage_batch(batch, [doc], list(facts), list(observations), evidence_links=evidence,
                      entities=list(entities), series=list(series))
    if commit:
        store.commit_batch(batch_id)
    return doc
