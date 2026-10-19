from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest

from src.ingestion import ArchiveError, archive_document
from src.schemas.ingestion import SourceDocument
from src.utils.minio_client import EvidenceArchive


def settings(tmp_path, **overrides):
    values = {
        "object_backend": "local", "cache_dir": tmp_path, "object_prefix": "evidence",
        "minio_endpoint": None, "minio_access_key": None, "minio_secret_key": None,
        "minio_secure": True, "object_bucket": "powerlaw-evidence",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_local_archive_is_content_addressed_and_verified(tmp_path):
    content = b"official source bytes"
    document = SourceDocument.from_content("TEST", "https://example.com/a", "Example", content, "batch", data_origin="test")
    archive = EvidenceArchive(settings(tmp_path))

    archived = archive_document(document, content, archive=archive)

    assert archived.raw_uri.startswith("file://")
    assert archive.get(archived.raw_uri, document.content_sha256) == content

    raw_path = unquote(urlparse(archived.raw_uri).path)
    if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    Path(raw_path).write_bytes(b"tampered")
    with pytest.raises(OSError):
        archive.get(archived.raw_uri, document.content_sha256)


def test_archive_rejects_document_checksum_mismatch(tmp_path):
    document = SourceDocument.from_content("TEST", "https://example.com/a", "Example", b"right", "batch", data_origin="test")
    with pytest.raises(ArchiveError):
        archive_document(document, b"wrong", archive=EvidenceArchive(settings(tmp_path)))


def test_remote_archive_requires_existing_configured_bucket():
    with pytest.raises(ValueError):
        EvidenceArchive(settings(Path("."), object_backend="s3"))
