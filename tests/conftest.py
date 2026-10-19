"""Isolate the test session from the developer's .env: no real stores, credentials or paid budget.

Several modules call load_dotenv() at import time with override=False, so values set here win.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="powerlaw-tests-"))
atexit.register(shutil.rmtree, _SESSION_DIR, ignore_errors=True)

os.environ.update({
    "POWERLAW_MODE": "test",
    "POWERLAW_DATA_DIR": str(_SESSION_DIR),
    "LANCEDB_PATH": str(_SESSION_DIR / "lancedb"),
    "POWERLAW_SQLITE_PATH": str(_SESSION_DIR / "ingestion.sqlite"),
    "POWERLAW_CACHE_DIR": str(_SESSION_DIR / "cache"),
    "POWERLAW_TEMP_DIR": str(_SESSION_DIR / "temp"),
    "POWERLAW_OBJECT_BACKEND": "local",
    "POWERLAW_EXTRACTION_BUDGET_USD": "0",
    "POWERLAW_EXTRACTION_TOTAL_BUDGET_EUR": "0",
    "GENESIS_API_TOKEN": "",
    "GEMINI_API_KEY": "",
    "GOOGLE_API_KEY": "",
    "GOOGLE_CLOUD_PROJECT": "",
})
