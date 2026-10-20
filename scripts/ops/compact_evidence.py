"""Drop evidence links that only repeat an earlier archived release (see DualLayerStore.redundant_evidence_ids).

Usage: python -m scripts.ops.compact_evidence           # dry run: counts only
       python -m scripts.ops.compact_evidence --apply   # back up the state pin, compact, verify, roll back on mismatch
"""
import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import get_settings
from src.storage.dual_layer_store import DualLayerStore


def fingerprint(store: DualLayerStore) -> dict:
    """What readers see: public rows at quarter ends since 2005, and each revision's availability."""
    cutoffs = [datetime(y, m, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
               for y in range(2005, 2027) for m in (1, 4, 7, 10)] + [datetime.now(timezone.utc)]
    visible = [sorted((r["revision_id"], r["value"]) for r in store.query_observations(cutoff=c, mode="public_as_of").to_pylist())
               for c in cutoffs]
    now = datetime.now(timezone.utc)
    available = {o.revision_id: store._available(o).isoformat()
                 for o in store.query_observation_revisions(now, "public_as_of")}
    return {"visible": visible, "available": available}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = DualLayerStore()
    redundant = store.redundant_evidence_ids()
    print(json.dumps({"store": str(store.storage_dir), "evidence_links": len(store._canonical_evidence),
                      "redundant": len(redundant)}))
    if not args.apply or not redundant:
        return
    before = fingerprint(store)
    backup = Path(get_settings().data_dir) / "backups" / f"state-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    backup.mkdir(parents=True)
    for name in ("canonical_state.json", "generation.json"):
        shutil.copy2(store.storage_dir / name, backup / name)
    removed = store.compact_redundant_evidence()
    after = fingerprint(DualLayerStore())
    if after != before:
        for name in ("canonical_state.json", "generation.json"):
            shutil.copy2(backup / name, store.storage_dir / name)
        raise SystemExit(f"As-of reads changed after compaction; restored the pinned state from {backup}")
    print(json.dumps({"removed": removed, "verified_cutoffs": len(before["visible"]), "backup": str(backup)}))


if __name__ == "__main__":
    main()
