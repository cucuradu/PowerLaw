"""The training graph dates every value by evidence or a stated rule, and links only evidenced or tested pairs."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.ops.export_training_graph import build_edges, quarterly_values, same_sector_edges

UTC = timezone.utc


def row(start, end, value, available, dated):
    return {"period_start": start, "period_end_exclusive": end, "value": value, "available_at": available, "release_dated": dated}


def test_values_use_release_dates_when_evidenced_and_the_lag_rule_otherwise():
    fetched = datetime(2026, 10, 20, tzinfo=UTC)
    months = [row(f"2025-0{m}-01", f"2025-0{m + 1}-01", 100 + m, fetched, False) for m in (1, 2, 3)]
    out = quarterly_values(months + [row("2025-04-01", "2025-05-01", 110, fetched, False)], "MONTHLY", 55)
    assert out == {"2025-Q1": (102.0, datetime(2025, 5, 26, tzinfo=UTC))}  # Q2 incomplete; March end + 55 days
    released = datetime(2025, 4, 9, 9, tzinfo=UTC)
    assert quarterly_values([row("2025-01-01", "2025-04-01", 130.0, released, True)], "QUARTERLY", 120)["2025-Q1"][1] == released
    policy = quarterly_values([row("2026-01-01", "2027-01-01", 55.0, fetched, False)], "ANNUAL", None)
    assert set(policy) == {"2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"} and policy["2026-Q1"][1] == datetime(2026, 1, 1, tzinfo=UTC)


def test_edges_come_only_from_evidenced_facts_and_supported_tests():
    fact = lambda s, p, o, payload: SimpleNamespace(subject_id=s, predicate=p, object_id=o, payload_json=json.dumps(payload))
    facts = [fact("sec_C23", "FLOWS_TO", "sec_F", {"share": 0.08, "episode": 2022}),
             fact("sec_C23", "FLOWS_TO", "sec_F", {"share": 0.02, "episode": 2024, "scope": "PL>DE"}),  # FIGARO: excluded
             fact("sec_F_BPNB095", "COMPONENT_OF", "sec_F_ROHBAUARB", {"share_of_total": 0.173})]
    measured = {("DE", "sec_C23", "price"): ["minerals"], ("DE", "sec_F", "output"): ["target"],
                ("DE", "sec_F_BPNB095", "output"): ["concrete"], ("DE", "commodity_cement", "price"): ["cement"]}
    links = {"links": [{"material": "cement", "trade": "F_BPNB095", "hypothesised": True, "verdict": "not supported", "pass_through": 0.1}]}
    edges = {(e["source"], e["target"], e["type"]) for e in build_edges(facts, measured, links)}
    assert edges == {("minerals", "target", "cost_share"), ("concrete", "target", "component")}
    weights = {e["weight"] for e in same_sector_edges({"mineral_prices": ["at", "cz", "pl"]})}
    assert weights == {0.5}


def test_neighbour_signal_is_the_weighted_mean_of_in_neighbours():
    from scripts.ops.graph_signal_test import neighbour_signal
    growth = {"a": {1: 1.0, 2: 3.0}, "b": {1: 4.0}, "t": {1: 0.0, 2: 0.0}}
    edges = [{"source": "a", "target": "t", "type": "cost_share", "weight": "0.75"},
             {"source": "b", "target": "t", "type": "cost_share", "weight": "0.25"},
             {"source": "b", "target": "t", "type": "same_sector", "weight": "1"}]
    signal = neighbour_signal(edges, growth, {"cost_share"})["t"]
    assert signal == {1: 1.75, 2: 3.0}  # quarter 2: only a reports, its weight is renormalised
