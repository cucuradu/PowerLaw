"""World commodity prices come from the official Pink Sheet workbook, cell by cell."""
import json
from pathlib import Path

import pytest

from src.ingestion import FetchError, _pink_sheet_link, _pink_sheet_parse
from src.schemas.ingestion import SourceDocument

FIXTURE = Path(__file__).parent / "fixtures" / "ingestion" / "worldbank_pink_sheet_monthly_202610_official.xlsx"
LINK = "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"


def test_the_landing_page_must_name_exactly_one_monthly_workbook():
    assert _pink_sheet_link(f'<a href="{LINK}">Monthly prices</a> <a href="{LINK}">again</a>') == LINK
    with pytest.raises(FetchError):
        _pink_sheet_link("<a href='https://thedocs.worldbank.org/x/CMO-Historical-Data-Annual.xlsx'>annual</a>")


def test_workbook_cells_become_monthly_world_prices_with_cell_evidence():
    raw = FIXTURE.read_bytes()
    doc = SourceDocument.from_content("WORLD_BANK_PINK_SHEET", LINK, "World Bank", raw, "pink", data_origin="test")
    result = _pink_sheet_parse({"upstream_id": "monthly"}, raw, doc)
    by_series = {}
    for o in result["observations"]:
        by_series.setdefault(o.series_id, {})[o.period_start] = o.value
    assert len(by_series) == 7
    assert by_series["WB_PINK|brent"]["1960-01-01"] == 1.6 and by_series["WB_PINK|copper"]["1960-01-01"] == 715
    assert max(by_series["WB_PINK|brent"]) == "2026-09-01"
    assert {o.vintage_id for o in result["observations"]} == {"2026-10-02"}
    cells = {json.loads(e.selector_json)["cell"] for e in result["evidence_links"] if e.assertion_kind == "OBSERVATION"}
    assert "C7" in cells  # Brent, January 1960
    assert {f.object_id for f in result["facts"] if f.predicate == "MEASURES"} >= {"commodity_crude_oil", "commodity_iron_ore"}
