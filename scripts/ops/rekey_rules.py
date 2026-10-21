"""Give stored EU-law rules stable IDs (law + article + paragraph + mechanism) without calling Gemini.

For each law, the rules already extracted (the full rule is the payload of its law -> MANDATES -> rule fact) are run
through the normal ingestion again with the archived law text, so grounding checks are identical. Facts under the old
order-based IDs (rule_<celex>_<article>_<n>) are retracted by the usual revision logic, with the rule's own sector,
commodity and geography links. Back up data/<pilot>/lancedb first. Idempotent: laws already keyed are skipped.

Usage: python -m scripts.ops.rekey_rules [--celex 32024R3110 ...] [--dry-run]
"""
import argparse
import json
from datetime import datetime, timezone

import src.main as law_pipeline
from src.ingestion import _eurlex_discover, run_ingestion
from src.schemas.rule_schema import GNNRule
from src.storage.dual_layer_store import DualLayerStore
from src.utils.minio_client import EvidenceArchive


def stored_rules(store: DualLayerStore, celex: str) -> list[GNNRule]:
    """Latest live version of each law -> rule MANDATES fact, as rules."""
    committed = {k for k, b in store._canonical_batches.items() if b.status == "committed"}
    latest = {}
    for fact in store._canonical_facts.values():
        if fact.batch_id in committed and fact.predicate == "MANDATES" and fact.subject_id == f"law_{celex}":
            if fact.fact_id not in latest or store._available(fact) > store._available(latest[fact.fact_id]):
                latest[fact.fact_id] = fact
    live = sorted((f for f in latest.values() if not f.is_retracted), key=lambda f: f.object_id)
    return [GNNRule.model_validate_json(f.payload_json) for f in live], [f.object_id for f in live]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--celex", nargs="*")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    def no_paid_calls(*a, **k):
        raise RuntimeError("rekey_rules must not call Gemini")
    law_pipeline.get_completion_with_json = law_pipeline.get_embedding = no_paid_calls

    store = DualLayerStore()
    archive = EvidenceArchive()
    for item in _eurlex_discover("EURLEX_CELLAR", None, None, 100):
        celex = item["upstream_id"]
        if args.celex and celex not in args.celex:
            continue
        rules, old_ids = stored_rules(store, celex)
        if not rules:
            continue
        new_ids = law_pipeline.rule_ids(celex, rules)
        if sorted(new_ids) == old_ids:
            print(json.dumps({"celex": celex, "skipped": "already stable"}))
            continue
        docs = [d for d in store._canonical_documents.values() if d.source_id == "EURLEX_CELLAR" and d.canonical_url == item["canonical_url"]]
        doc = max(docs, key=lambda d: store._available(d))
        raw = archive.get(doc.raw_uri, doc.content_sha256)
        if args.dry_run:
            print(json.dumps({"celex": celex, "rules": len(rules), "new_ids": len(set(new_ids))}))
            continue
        law_pipeline.LawIngestionPipeline.extract_rules_from_text = lambda self, *a, r=rules, **k: list(r)
        report = run_ingestion("EURLEX_CELLAR", limit=1, store=store, discover=lambda *a, i=item: [i],
                               fetch=lambda url, raw=raw, d=doc, **kw: (raw, d.final_url or url, d.content_type, 200))
        print(json.dumps({"celex": celex, "status": report["status"], "accepted": report["accepted"], "review": report["review"],
                          "errors": [e["message"] for e in report["source_errors"]], "at": datetime.now(timezone.utc).isoformat()}))


if __name__ == "__main__":
    main()
