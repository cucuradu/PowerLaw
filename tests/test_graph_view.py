"""Graph page logic: zones bundle until opened, the chosen year and share filter apply, focus and chains lay out."""
from src.storage.graph_view import (edge_details, focus_view, map_view, node_details, schema_details, schema_view,
                                     visible_edges)

ZONES = {z: {"label": z, "color": "#2563EB", "about": z} for z in ("laws", "construction", "economy", "materials", "data")}
TYPES = {t: {"label": t, "color": "#16A34A", "dashes": False, "about": t}
         for t in ("contains", "regulates", "supplies", "buys", "part_of", "chain", "measures")}


def node(nid, zone, kind="sector"):
    return {"id": nid, "zone": zone, "kind": kind, "label": nid, "description": "", "facts": {}, "degree": 1}


VIEW = {"zones": ZONES, "edge_types": TYPES,
        "nodes": [node("law", "laws", "law"), node("rule", "laws", "rule"), node("F", "construction"), node("C23", "economy"),
                  node("C24", "economy"), node("buyer", "economy"), node("gas", "materials", "commodity")],
        "edges": [{"id": "e0", "source": "law", "target": "rule", "type": "contains"},
                  {"id": "e1", "source": "rule", "target": "F", "type": "regulates"},
                  {"id": "e2", "source": "C23", "target": "F", "type": "supplies", "weight": 0.08, "year": 2022},
                  {"id": "e3", "source": "C24", "target": "F", "type": "supplies", "weight": 0.002, "year": 2022},
                  {"id": "e4", "source": "C23", "target": "F", "type": "supplies", "weight": 0.07, "year": 2021},
                  {"id": "e5", "source": "F", "target": "buyer", "type": "buys", "weight": 0.2, "year": 2022},
                  {"id": "e6", "source": "gas", "target": "C23", "type": "chain", "chain": "energy"}]}


def test_year_and_share_filter():
    assert {e["id"] for e in visible_edges(VIEW, 2022, 0.005)} == {"e0", "e1", "e2", "e5", "e6"}


def test_closed_zones_are_bubbles_with_bundled_relations_until_opened():
    nodes, edges = map_view(VIEW, set(), 2022, 0.005)
    assert {n["id"] for n in nodes} == {f"zone:{z}" for z in ZONES}
    bundle = next(e for e in edges if e["id"] == "bundle:zone:economy|zone:construction|supplies")
    assert edge_details(VIEW, bundle["id"], 2022, 0.005)["count"] == 1  # e3 is below the share threshold
    nodes, edges = map_view(VIEW, {"economy"}, 2022, 0.005)
    ids = {n["id"] for n in nodes}
    assert {"C23", "C24", "buyer", "zone:construction"} <= ids and "zone:economy" not in ids
    assert any(e["from"] == "C23" and e["to"] == "zone:construction" for e in edges)


def test_focus_rings_put_inputs_left_and_chains_left_to_right():
    nodes, edges = focus_view(VIEW, "F", 1, 2022, 0.005)
    pos = {n["id"]: (n["x"], n["y"]) for n in nodes}
    assert pos["F"] == (0, 0) and pos["C23"][0] < 0 < pos["buyer"][0]  # supplier left, buyer right
    assert {e["id"] for e in edges} == {"e1", "e2", "e5"}
    nodes, edges = focus_view(VIEW, "F", 1, 2022, 0.005, chain="energy")
    pos = {n["id"]: n["x"] for n in nodes}
    assert pos["gas"] < pos["C23"] and [e["id"] for e in edges] == ["e6"]
    details = node_details(VIEW, "F", 2022, 0.005)
    assert details["relations"][0]["other"] == "buyer"  # strongest first


def test_architecture_counts_kinds_and_relation_types():
    nodes, edges = schema_view(VIEW, 2022, 0.005)
    assert {n["id"] for n in nodes} == {"kind:laws/law", "kind:laws/rule", "kind:construction/sector", "kind:economy/sector",
                                        "kind:materials/commodity"}
    supplies = next(e for e in edges if e["id"] == "schema:kind:economy/sector|supplies|kind:construction/sector")
    assert supplies["label"] == "1"  # e3 below the threshold, e4 another year
    d = schema_details(VIEW, "kind:economy/sector", 2022, 0.005)
    assert d["count"] == 3 and schema_details(VIEW, supplies["id"], 2022, 0.005)["items"][0].startswith("C23 → F")


def test_index_weights_read_as_share_of_group_and_of_index():
    from src.storage.graph_view import weight_text
    view = {"edges": [{"source": "shell", "target": "F", "type": "part_of", "weight": 0.4425},
                      {"source": "steel", "target": "shell", "type": "part_of", "weight": 0.00164}]}
    assert weight_text(view, view["edges"][1]) == "0.4% of the group, 0.16% of the index"
    assert weight_text(view, view["edges"][0]) == "44.2% of the index"
