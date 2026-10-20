"""Extract rules from the configured EU laws with Gemini, one law per run, within the per-run budget.

A long law can need several runs: finished segments are cached, so each retry continues where the last stopped.
Usage: python -m scripts.ops.extract_laws [--celex 32024R3110 ...] [--max-usd 10]
"""
import argparse
import json

from src.ingestion import _eurlex_discover, run_ingestion
from src.storage.dual_layer_store import DualLayerStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--celex", nargs="*", help="only these CELEX IDs (default: all configured laws)")
    parser.add_argument("--max-usd", type=float, default=10.0, help="stop after this much ledger spend in this call")
    parser.add_argument("--max-runs", type=int, default=4, help="runs per law before giving up")
    args = parser.parse_args()
    store = DualLayerStore()
    done = {f.subject_id for f in store._canonical_facts.values() if f.source_id == "EURLEX_CELLAR" and f.predicate == "MANDATES"}
    spent = 0.0
    for item in _eurlex_discover("EURLEX_CELLAR", None, None, 100):
        celex = item["upstream_id"]
        if (args.celex and celex not in args.celex) or f"law_{celex}" in done:
            continue
        for _ in range(args.max_runs):
            if spent >= args.max_usd:
                print(json.dumps({"stopped": "max-usd reached", "spent_usd": round(spent, 4)}))
                return
            report = run_ingestion("EURLEX_CELLAR", limit=1, store=store, discover=lambda *a, i=item: [i])
            spent += float(report.get("cost_usd") or 0)
            errors = [e["message"] for e in report["source_errors"]]
            print(json.dumps({"celex": celex, "status": report["status"], "accepted": report["accepted"],
                              "review": report["review"], "cost_usd": report.get("cost_usd"), "errors": errors}))
            if report["status"] != "failed" or not any("budget" in e.lower() for e in errors):
                break
    print(json.dumps({"spent_usd": round(spent, 4)}))


if __name__ == "__main__":
    main()
