"""GENESIS dialog requests must stay small: ask only for the registered series cells."""
from src.ingestion import _stats_discover


def _requests():
    return {item["upstream_id"]: item["request_data"]
            for item in _stats_discover("DESTATIS_GENESIS", "1958-01-01", "2026-10-19", 10)}


def test_target_request_filters_to_the_registered_cell():
    data = _requests()["61261-0002"]
    filters = {data[f"classifyingvariable{i}"]: data[f"classifyingkey{i}"]
               for i in range(1, 6) if data.get(f"classifyingvariable{i}")}
    # The target cell (with VAT) plus the trade indices excluding VAT, from the same release.
    assert filters["BAUAR1"] == "BPNBK" and filters["STEMW1"] == "STEMW1,STEMW2"
    assert {"BAULEISTBW", "BPNB095", "BPNB525"} <= set(filters["BAUAR4"].split(","))
    assert (data["startyear"], data["endyear"]) == ("1958", "2026")


def test_context_requests_filter_and_clamp_each_table():
    requests = _requests()
    ppi = requests["61241-0004"]  # re-enabled 2026-10-20 after the 2026-10-19 provider outage
    assert (ppi["classifyingvariable1"], ppi["classifyingkey1"], ppi["startyear"]) == (
        "GP19M2", "GP19-16,GP19-22,GP19-23,GP19-24,GP19-25,GP19-35", "1976")
    permits = requests["31111-0020"]
    filters = {permits[f"classifyingvariable{i}"]: permits[f"classifyingkey{i}"] for i in range(1, 4)}
    assert filters == {"BAUBR1": "BAUART1", "BAUGB1": "GBD-W", "BAUTK1": "BTK-GEB-NEU"}
    assert permits["startyear"] == "2003"
    cost = requests["61261-0014"]
    assert (cost["classifyingvariable1"], cost["classifyingkey1"], cost["startyear"]) == ("KOSAT1", "KOST011,KOST012", "2000")
