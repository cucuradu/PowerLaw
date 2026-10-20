"""Trade weights come from the published weighting scheme; material links must survive a common-factor test."""
import json
from pathlib import Path

import numpy as np

from scripts.ops.estimate_links import link_test
from src.ingestion import _library_discover, _library_parse
from src.schemas.ingestion import SourceDocument

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"


def test_weights_are_read_from_the_report_with_their_row_as_evidence():
    item = next(_library_discover("STATISTISCHE_BIBLIOTHEK", None, None, 5))
    raw = (FIXTURES / "itnrw_baupreise_202411_official.pdf").read_bytes()
    doc = SourceDocument.from_content("STATISTISCHE_BIBLIOTHEK", item["canonical_url"], "IT.NRW", raw, "weights", data_origin="test")
    result = _library_parse(item, raw, doc)
    weights = {f.subject_id: (f.object_id, json.loads(f.payload_json)["weight_per_mille"]) for f in result["facts"]}
    assert weights["sec_F_BPNB095"] == ("sec_F_ROHBAUARB", 173.37)  # concrete work
    assert weights["sec_F_ROHBAUARB"][1] + weights["sec_F_AUSBAUARB"][1] == 1000
    assert {e.quoted_text for e in result["evidence_links"]} >= {"Betonarbeiten 173,37"}


def test_link_test_separates_a_real_input_from_the_common_trend():
    rng = np.random.default_rng(0)
    quarters = [(2000 + i // 4, i % 4) for i in range(100)]
    common = {q: rng.normal(0.5, 1) for q in quarters}
    material = {q: common[q] + rng.normal(0, 1) for q in quarters}
    unrelated = {q: common[q] + rng.normal(0, 1) for q in quarters}
    trade = {q: 0.3 * common[q] + 0.4 * material[q] + rng.normal(0, 0.3) for q in quarters}
    since = (2000, 0)
    assert link_test(trade, material, common, since)["p"] < 0.01
    assert link_test(trade, unrelated, common, since)["p"] > 0.05  # co-moves with the trend, but adds nothing
