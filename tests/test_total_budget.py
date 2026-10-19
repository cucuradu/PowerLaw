"""Durable project-wide paid-call authorization across runs and restarts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from src.storage.ingestion_jobs import BudgetError, IngestionJobLedger


def test_total_limit_survives_new_runs_and_reopening(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(1.0)
    ledger.reserve_cost("first", "one", .6, 10, price_known=True)
    ledger.settle_cost("first", "one", .6)

    reopened = IngestionJobLedger(ledger.path)
    reopened.configure_spending_limit(1.0)
    with pytest.raises(BudgetError, match="total"):
        reopened.reserve_cost("second", "two", .5, 10, price_known=True)
    reopened.reserve_cost("second", "fits", .4, 10, price_known=True)
    summary = reopened.spending_summary()
    assert summary["limit_usd"] == 1.0
    assert summary["committed_usd"] == pytest.approx(.6)
    assert summary["reserved_usd"] == pytest.approx(.4)
    assert summary["remaining_usd"] == pytest.approx(0)


def test_per_run_limit_still_applies_with_larger_total_limit(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(10)
    with pytest.raises(BudgetError, match="extraction budget"):
        ledger.reserve_cost("small", "one", .6, .5, price_known=True)
    assert ledger.spending_summary()["reserved_usd"] == 0


def test_lost_response_blocks_a_new_run_after_reopening(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(10)
    ledger.reserve_cost("lost-response", "one", .1, 1, price_known=True)
    reopened = IngestionJobLedger(ledger.path)
    with pytest.raises(BudgetError, match="unresolved"):
        reopened.reserve_cost("new-run", "two", .1, 1, price_known=True)
    assert reopened.spending_summary()["unresolved_reservations"] == 1


def test_overrun_blocks_other_runs_and_remains_counted(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(10)
    ledger.reserve_cost("overrun", "one", .1, 1, price_known=True)
    with pytest.raises(BudgetError, match="authorized"):
        ledger.settle_cost("overrun", "one", .2)
    reopened = IngestionJobLedger(ledger.path)
    with pytest.raises(BudgetError, match="unresolved"):
        reopened.reserve_cost("new-run", "two", .1, 1, price_known=True)
    summary = reopened.spending_summary()
    assert summary["committed_usd"] == pytest.approx(.2)
    assert summary["remaining_usd"] == pytest.approx(9.8)
    assert summary["overrun_reservations"] == 1


def test_failed_request_with_known_usage_still_consumes_total_budget(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(.5)
    ledger.reserve_cost("failed", "one", .4, 1, price_known=True)
    ledger.settle_cost("failed", "one", .4)
    ledger.record_attempt("failed", "one", "law", "failed", actual_cost_usd=.4)
    ledger.fail_cost("failed", "one")
    with pytest.raises(BudgetError, match="total"):
        ledger.reserve_cost("new-run", "two", .2, 1, price_known=True)
    assert ledger.spending_summary()["committed_usd"] == pytest.approx(.4)


def test_total_limit_includes_usage_before_configuration(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.reserve_cost("existing", "one", .6, 1, price_known=True)
    ledger.settle_cost("existing", "one", .6)
    ledger.configure_spending_limit(.5)
    with pytest.raises(BudgetError, match="total"):
        ledger.reserve_cost("new-run", "two", .01, 1, price_known=True)
    assert ledger.spending_summary()["remaining_usd"] == 0


def test_total_limit_cannot_be_reconfigured_to_reset_authorization(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(1)
    for changed in (.5, 2):
        with pytest.raises(BudgetError, match="immutable"):
            ledger.configure_spending_limit(changed)
    assert IngestionJobLedger(ledger.path).spending_summary()["limit_usd"] == 1


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf")])
def test_total_limit_rejects_invalid_amounts(tmp_path, invalid):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    with pytest.raises(BudgetError):
        ledger.configure_spending_limit(invalid)


def test_zero_total_limit_blocks_paid_calls(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.configure_spending_limit(0)
    with pytest.raises(BudgetError, match="total"):
        ledger.reserve_cost("one", "request", .01, 1, price_known=True)


def test_concurrent_new_runs_cannot_both_reserve_total_budget(tmp_path):
    path = tmp_path / "jobs.sqlite"
    ledger = IngestionJobLedger(path)
    ledger.configure_spending_limit(1)
    peers = [IngestionJobLedger(path), IngestionJobLedger(path)]
    barrier = Barrier(2)

    def reserve(index):
        barrier.wait(timeout=5)
        try:
            peers[index].reserve_cost(f"run-{index}", "one", .6, 1, price_known=True)
            return True
        except BudgetError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]
    assert ledger.spending_summary()["reserved_usd"] == pytest.approx(.6)


def test_unconfigured_ledger_preserves_separate_run_authorization(tmp_path):
    ledger = IngestionJobLedger(tmp_path / "jobs.sqlite")
    ledger.reserve_cost("one", "request", .6, 1, price_known=True)
    ledger.reserve_cost("two", "request", .6, 1, price_known=True)
    assert ledger.spending_summary()["limit_usd"] is None
    assert ledger.spending_summary()["remaining_usd"] is None
