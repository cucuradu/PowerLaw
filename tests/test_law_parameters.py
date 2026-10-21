"""Policy numbers are stored only when their quote is in the official law text."""
import copy
import json
from pathlib import Path

from src.config import load_data_sources_registry
from src.ingestion import _law_parameters_discover, _law_parameters_parse
from src.schemas.ingestion import SourceDocument

ROOT = Path(__file__).parents[1]


def parse(key, registry=None):
    item = next(i for i in _law_parameters_discover("LAW_PARAMETERS", None, None, 10) if i["upstream_id"] == key)
    raw = (ROOT / load_data_sources_registry()["sources"]["LAW_PARAMETERS"]["documents"][key]["fixture_path"]).read_bytes()
    doc = SourceDocument.from_content("LAW_PARAMETERS", item["canonical_url"], "official", raw, key, data_origin="test")
    return _law_parameters_parse(item, raw, doc)


def test_co2_price_and_cbam_factor_come_from_the_law_texts():
    behg = parse("behg")
    assert {o.period_start[:4]: o.value for o in behg["observations"]} == {
        "2021": 25, "2022": 30, "2023": 30, "2024": 45, "2025": 55, "2026": 55}
    cbam = parse("eu_2023_959")
    values = {o.period_start[:4]: o.value for o in cbam["observations"]}
    assert values["2026"] == 97.5 and values["2030"] == 51.5 and values["2034"] == 0
    for result in (behg, cbam):
        assert all(o.quality_state == "accepted" for o in result["observations"])
        assert all(e.support_type == "DIRECT" for e in result["evidence_links"])
    # The OJ publication date is printed in the act, so CBAM values are known from then; BEHG values only from 1 January.
    assert {json.loads(o.conversion_lineage_json)["known_from"] for o in cbam["observations"]} == {"2023-05-16"}
    assert json.loads(behg["observations"][3].conversion_lineage_json)["known_from"] == "2024-01-01"
    assert {f.object_id for f in behg["facts"] if f.predicate == "MEASURES"} == {"commodity_diesel", "commodity_natural_gas"}


def test_a_number_whose_quote_is_missing_goes_to_review(monkeypatch):
    registry = copy.deepcopy(load_data_sources_registry())  # the registry is cached; never mutate the shared copy
    spec = registry["sources"]["LAW_PARAMETERS"]["documents"]["behg"]["parameters"][0]
    spec["entries"][0] = {**spec["entries"][0], "value": 99, "quote": "1. Januar 2021 bis zum 31. Dezember 2021: 99 Euro"}
    monkeypatch.setattr("src.ingestion.load_data_sources_registry", lambda: registry)
    observations = parse("behg")["observations"]
    assert [o.quality_state for o in observations if o.value == 99] == ["needs_review"]
