"""GENESIS dialog requests must stay small: ask only for the registered series cells."""
from src.ingestion import _stats_discover


def _requests():
    return {item["upstream_id"]: item["request_data"]
            for item in _stats_discover("DESTATIS_GENESIS", "1958-01-01", "2026-10-19", 10)}


def test_target_request_filters_to_the_registered_cell():
    data = _requests()["61261-0002"]
    filters = {data[f"classifyingvariable{i}"]: data[f"classifyingkey{i}"]
               for i in range(1, 6) if data.get(f"classifyingvariable{i}")}
    assert filters == {"STEMW1": "STEMW1", "BAUAR1": "BPNBK", "BAUAR4": "BAULEISTBW"}
    assert (data["startyear"], data["endyear"]) == ("1958", "2026")


def test_material_request_lists_all_selected_products_and_clamps_to_table_start():
    data = _requests()["61241-0004"]
    assert data["classifyingvariable1"] == "GP19M2"
    assert data["classifyingkey1"] == "GP19-16,GP19-22,GP19-23,GP19-24,GP19-25"
    assert data["startyear"] == "1976"
