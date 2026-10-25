"""The EU panel keeps every registered country series a response carries and tolerates countries that report none."""
import json
from pathlib import Path

from src.config import load_data_sources_registry
from src.ingestion import _make_stats_parser, _stats_discover
from src.schemas.ingestion import SourceDocument

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"


def test_panel_fixtures_parse_to_registered_country_series():
    context = load_data_sources_registry()["sources"]["EUROSTAT_STS"]["context_series"].values()
    kinds = {s["provider_series_key"]: (s["measurement_kind"], s["dimensions"]["geo"]) for s in context}
    names = json.loads((Path(__file__).parents[1] / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    for item in _stats_discover("EUROSTAT_STS", "2025-01", "2025-12", 10):
        raw = (FIXTURES / f"eurostat_{item['upstream_id']}_eu27_2025_official.json").read_bytes()
        doc = SourceDocument.from_content("EUROSTAT_STS", item["canonical_url"], "Eurostat", raw, "panel", data_origin="test")
        series = _make_stats_parser("EUROSTAT_STS")(item, raw, doc)["series"]
        assert {s.series_id for s in series} <= set(item["required_series_keys"]) | set(item["optional_series_keys"])
        assert len({s.geography_id for s in series}) >= 20, item["upstream_id"]
        for s in series:
            assert (s.measurement_kind, s.geography_id) == kinds.get(s.series_id, (s.measurement_kind, "DE"))
            assert names[s.series_id]["measures"]
    # France publishes no residential cost index: registered, absent from the response, and not an error.
    copi = next(i for i in _stats_discover("EUROSTAT_STS", "2025-01", "2025-12", 10) if i["upstream_id"] == "sts_copi_q")
    assert any("geo=FR|indic_bt=COST" in k for k in copi["optional_series_keys"])


def test_partial_pooling_moves_from_own_fit_to_the_pooled_prior():
    import numpy as np
    from scripts.ops.panel_backtest import shrunk_fit
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(40), rng.normal(size=40)])
    y = X @ np.array([1.0, 2.0]) + rng.normal(0, 0.1, 40)
    prior = np.array([0.0, 0.0])
    assert np.allclose(shrunk_fit(X, y, prior, 0), np.linalg.lstsq(X, y, rcond=None)[0])
    assert np.allclose(shrunk_fit(X, y, prior, 1e6), prior, atol=1e-3)
    assert 0 < shrunk_fit(X, y, prior, 40)[1] < shrunk_fit(X, y, prior, 0)[1]


def test_every_chain_node_exists_or_names_its_data_source():
    root = Path(__file__).parents[1] / "config"
    chains = json.loads((root / "chains.json").read_text(encoding="utf-8"))
    sectors = json.loads((root / "sectors.json").read_text(encoding="utf-8"))
    known = {f"sector:{c}" for c in sectors["sectors"]} | {f"commodity:{c}" for c in sectors["commodities"]} | {
        f"new:{n}" for n, spec in chains["new_nodes"].items() if spec["source"]} | {
        f"law:{law[4:]}" for law in json.loads((root / "law_profiles.json").read_text(encoding="utf-8"))["laws"]}
    for chain in chains["chains"]:
        for link in chain["links"]:
            assert {link["from"], link["to"]} <= known, (chain["id"], link)
            assert link["mechanism"] and link["status"] in {"in_store", "tested_supported", "tested_not_supported", "needs_data", "law_text"}
            assert link["status"] != "law_text" or link.get("quote")  # a law link quotes the provision


def test_fact_copies_stay_safe_without_deep_copy():
    from src.schemas.ingestion import Fact
    mutable = [k for k, v in Fact.model_fields.items() if any(t in str(v.annotation) for t in ("list", "List", "dict", "Dict", "set"))]
    assert mutable == []  # query_facts returns shallow copies; a mutable field would leak store state
