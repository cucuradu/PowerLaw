"""Re-run Bundesbank real-time release dating from vintages already archived in the store (no network).

Use after adding a series to a release calendar when the vintages were fetched before, or while the
Bundesbank API is unavailable.

Usage: python -m scripts.ops.redate_from_archive industrial_producer_price_release_calendar
"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.config import get_settings, load_data_sources_registry
from src.ingestion import _RTD_HEADERS, run_ingestion
from src.storage.dual_layer_store import DualLayerStore


def archived_items(store: DualLayerStore, calendar: str) -> tuple[list, dict]:
    key = load_data_sources_registry()["sources"]["BUNDESBANK_RTD"]["series_keys"][calendar].split("/", 1)[1]
    indicator = key.split(".")[5]
    items, payloads = [], {}
    for doc in store._canonical_documents.values():
        if doc.source_id != "BUNDESBANK_RTD" or f"/{key}." not in doc.canonical_url or not doc.raw_uri:
            continue
        day = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\?", doc.canonical_url)
        if not day:
            continue
        release = "-".join(day.groups())
        payloads[doc.canonical_url] = Path(unquote(urlparse(doc.raw_uri).path.lstrip("/"))).read_bytes()
        items.append({"item_id": f"BUNDESBANK_RTD_{calendar}_{release}_archive", "upstream_id": f"{indicator}@{release}",
                      "series_key": indicator, "calendar": calendar, "release_date": release,
                      "url": doc.canonical_url, "canonical_url": doc.canonical_url, "headers": _RTD_HEADERS,
                      "publisher": doc.publisher, "content_type": "text/csv", "availability_basis": "archived_release",
                      "published_at": doc.published_at.isoformat()})
    return sorted(items, key=lambda i: i["release_date"]), payloads


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("calendar")
    args = parser.parse_args()
    store = DualLayerStore()
    items, payloads = archived_items(store, args.calendar)
    if not items:
        raise SystemExit(f"No archived vintages for {args.calendar}")
    report = run_ingestion("BUNDESBANK_RTD", limit=len(items), settings=get_settings(), store=store,
                           discover=lambda *a: items, fetch=lambda url, **kw: (payloads[url], url, "text/csv", 200))
    print(json.dumps({k: report.get(k) for k in ("status", "fetched", "accepted", "committed", "source_errors")}, default=str))


if __name__ == "__main__":
    main()
