"""First-release labels must be committed, comparable and backed by a release."""
import copy
import numpy as np
import pytest

from tests.acceptance_fixtures import stage_evidenced, utc
from src.schemas.ingestion import Observation, SeriesDefinition
from src.storage.dual_layer_store import DualLayerStore
from src.models.gnn_dataset import build_forecast_dataset
from src.quant.forecasting import evaluate_baselines

SID = "series:destatis:61261-0002"


def fixture(tmp_path, *, commit=True, vintage="release:2024-08", end="2024-07-01",
            basis="provider_vintage", denominator_release="2024-08-01T00:00:00Z"):
    store = DualLayerStore(str(tmp_path))
    definition = SeriesDefinition(series_id=SID, source_id="DESTATIS_GENESIS", dataset_id="61261-0002",
        provider_series_key="DE.Q.RESIDENTIAL", metric_name="construction_producer_price_index",
        unit="INDEX", frequency="Q", geography_id="DE", base_period="2021=100", batch_id="definition",
        available_at=utc(2020), recorded_at=utc(2020))
    stage_evidenced(store, "definition", series=[definition])
    obs = [Observation(series_id=SID, period_start=start, period_end_exclusive=finish, value=value,
        unit="INDEX", source_unit="2021=100", vintage_id=vintage, available_at=utc(release),
        recorded_at=utc(release), batch_id="release", data_origin="test")
        for start, finish, value, release in [
            ("2024-01-01", "2024-04-01", 100, denominator_release),
            ("2024-04-01", end, 110, "2024-08-01T00:00:00Z")]]
    stage_evidenced(store, "release", observations=obs, commit=commit, availability_basis=basis)
    return store, obs


def build(store, **kw):
    return build_forecast_dataset(SID, [utc("2024-03-31T23:59:00Z")], store=store, window_size=1, **kw)


@pytest.mark.parametrize("kwargs", [{"commit": False}, {"vintage": None},
    {"end": "2024-05-01"}, {"basis": "first_fetch"}])
def test_unverified_or_incomplete_label_is_masked(tmp_path, kwargs):
    store, _ = fixture(tmp_path, **kwargs)
    assert not build(store)["samples"][0]["y_mask"][0, 0]


def test_retracted_release_does_not_train(tmp_path):
    store, obs = fixture(tmp_path)
    tomb = obs[1].model_copy(update={"revision_id": "tomb", "is_retracted": True,
        "supersedes_revision_id": obs[1].revision_id, "batch_id": "withdrawn",
        "available_at": utc("2024-09-01T00:00:00Z"), "recorded_at": utc("2024-09-01T00:00:00Z")})
    stage_evidenced(store, "withdrawn", observations=[tomb], availability_basis="provider_vintage")
    assert not build(store)["samples"][0]["y_mask"][0, 0]


def test_release_time_includes_denominator_and_missing_target_index(tmp_path):
    store, _ = fixture(tmp_path, denominator_release="2024-09-01T00:00:00Z")
    sample = build(store)["samples"][0]
    assert sample["first_release_available_at"] == utc("2024-09-01T00:00:00Z")
    assert sample["y"][0, 0] == pytest.approx(10)
    assert sample["target_series_index"].tolist() == [-1]
    assert sample["target_series_present"] is False


def test_manifest_changes_when_committed_release_changes(tmp_path):
    store, obs = fixture(tmp_path)
    before = build(store)
    revised = obs[1].model_copy(update={"revision_id": "revision2", "value": 112, "batch_id": "revised",
        "vintage_id": "release:2024-10", "available_at": utc("2024-10-01T00:00:00Z")})
    stage_evidenced(store, "revised", observations=[revised], availability_basis="provider_vintage")
    after = build(store)
    assert before["dataset_id"] != after["dataset_id"]
    assert before["dataset_id"] == build_forecast_dataset(SID, [utc("2024-03-31T23:59:00Z")],
        store=DualLayerStore(store.storage_dir), window_size=1,
        label_cutoff=before["manifest"]["label_cutoff"])["dataset_id"] or before["manifest"]["committed_generation"] < after["manifest"]["committed_generation"]


def test_future_label_cannot_supply_a_lag_predictor():
    samples = []
    for year, quarter, value in [(2023, 1, 10), (2023, 2, 20), (2023, 3, 30), (2023, 4, 40), (2024, 1, 50)]:
        month = quarter * 3
        samples.append({"origin": utc(f"{year}-{month:02d}-28T00:00:00Z"), "target_series_id": SID,
            "target_period_start": f"{year}-{quarter*3-2:02d}-01", "y": np.array([[value]]),
            "y_mask": np.array([[True]]), "first_release_available_at": utc(2025)})
    result = evaluate_baselines({"samples": list(reversed(samples))})
    assert result["baselines"]["autoregressive_lag1"]["mae"] == 30
    assert result["baselines"]["seasonal_lag4"]["mae"] == 30
