"""Real CPU torch/PyG temporal and chronological trainer regressions."""
from datetime import datetime, timezone

import numpy as np
import pytest
import torch
from torch import nn

from src.gnn.temporal_gnn import HAS_PYG, TemporalHeteroGNN
from src.gnn.trainer import HeteroGNNTrainer


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def snapshot(ids, values, active=None):
    from torch_geometric.data import HeteroData
    data = HeteroData()
    data["series"].node_id = list(ids)
    data["series"].x = torch.tensor(values, dtype=torch.float32).reshape(len(ids), 1)
    data["series"].active_mask = torch.tensor(active if active is not None else [True] * len(ids))
    data["series"].feature_observed_mask = torch.ones_like(data["series"].x)
    return data


def model():
    torch.manual_seed(14)
    result = TemporalHeteroGNN(["series"], [], {"series": 1}, hidden_channels=4, dropout=0)
    result.eval()
    return result


@pytest.mark.skipif(not HAS_PYG, reason="actual PyG required; no attention shim")
def test_temporal_history_follows_stable_ids_across_row_reordering():
    net = model()
    a = snapshot(["A", "B"], [1, 4])
    b = snapshot(["A", "B"], [2, 9])
    reordered = snapshot(["B", "A"], [9, 2])
    expected = net([a, b])
    actual = net([a, reordered])
    assert torch.allclose(actual, expected.flip(0), atol=1e-7)


@pytest.mark.skipif(not HAS_PYG, reason="actual PyG required; no attention shim")
def test_temporal_history_accepts_changing_node_counts():
    net = model()
    result = net([snapshot(["A"], [1]), snapshot(["A", "B"], [2, 3])])
    assert result.shape == (2, 1)
    assert torch.isfinite(result).all()


@pytest.mark.skipif(not HAS_PYG, reason="actual PyG required; no attention shim")
def test_inactive_snapshot_freezes_recurrence_and_inactive_target_rejects():
    net = model()
    first, last = snapshot(["A"], [1]), snapshot(["A"], [3])
    inactive = snapshot(["A"], [999], [False])
    assert torch.allclose(net([first, inactive, last]), net([first, last]), atol=1e-7)
    with pytest.raises(ValueError, match="active"):
        net([first, inactive])


class ScalarPredictor(nn.Module):
    starts = []
    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.tensor(1.0))
        self.calls = 0

    def forward(self, snapshots, target_idx=None):
        if self.training and self.calls == 0:
            self.starts.append(float(self.weight.detach()))
        self.calls += 1
        return self.weight.reshape(1, 1)


def sample(origin, released, y=2.0, value=1.0):
    return {"origin": instant(origin), "first_release_available_at": instant(released),
            "target_series_id": "A", "target_series_present": True,
            "snapshots": [snapshot(["A"], [value])], "target_series_index": np.array([0]),
            "y": np.array([[y]], dtype=np.float32), "y_mask": np.array([[True]])}


@pytest.mark.skipif(not HAS_PYG, reason="snapshot representation requires actual PyG")
def test_ablation_models_start_independently_and_leave_caller_model_unchanged():
    ScalarPredictor.starts = []
    net = ScalarPredictor()
    trainer = HeteroGNNTrainer(net, device="cpu")
    train = [sample("2020-01-01", "2020-05-01"), sample("2020-04-01", "2020-08-01", y=3)]
    test = [sample("2021-01-01", "2021-05-01")]
    results = trainer.run_ablations(train, test, epochs=1)
    assert ScalarPredictor.starts == [1.0] * 4
    assert float(net.weight.detach()) == 1.0
    assert set(results) == {"full_model", "no_news", "no_legal", "no_topology"}


@pytest.mark.skipif(not HAS_PYG, reason="snapshot representation requires actual PyG")
@pytest.mark.parametrize("bad_split", ["reversed_train", "overlap", "future_release", "missing_release"])
def test_trainer_rejects_nonchronological_or_unreleased_training_labels(bad_split):
    trainer = HeteroGNNTrainer(ScalarPredictor(), device="cpu")
    train = [sample("2020-01-01", "2020-05-01"), sample("2020-04-01", "2020-08-01")]
    test = [sample("2021-01-01", "2021-05-01")]
    if bad_split == "reversed_train":
        train.reverse()
    elif bad_split == "overlap":
        test[0]["origin"] = train[0]["origin"]
    elif bad_split == "future_release":
        train[0]["first_release_available_at"] = instant("2022-01-01")
    else:
        train[0]["first_release_available_at"] = None
    with pytest.raises(ValueError, match="chronolog|release|origin|overlap"):
        trainer.run_ablations(train, test, epochs=1)


@pytest.mark.skipif(not HAS_PYG, reason="snapshot representation requires actual PyG")
def test_train_only_target_and_feature_transforms_stay_fixed_on_test_values():
    trainer = HeteroGNNTrainer(ScalarPredictor(), device="cpu")
    train = [sample("2020-01-01", "2020-05-01", y=1, value=10),
             sample("2020-04-01", "2020-08-01", y=3, value=20)]
    trainer.train_epoch(train)
    profile = dict(trainer.feature_profile)
    assert trainer.target_mean == 2
    assert trainer.target_scale == 1
    assert trainer.feature_profile["normalization"]["series"]["mean"] == [15.0]
    trainer.evaluate([sample("2021-01-01", "2021-05-01", y=999, value=100000)])
    assert trainer.feature_profile == profile
    assert trainer.target_mean == 2
    assert trainer.target_scale == 1


@pytest.mark.skipif(not HAS_PYG, reason="snapshot representation requires actual PyG")
def test_evaluation_refuses_an_origin_before_training_labels_were_known():
    trainer = HeteroGNNTrainer(ScalarPredictor(), device="cpu")
    trainer.train_epoch([sample("2020-01-01", "2021-01-01")])
    with pytest.raises(ValueError, match="release|cutoff|origin"):
        trainer.evaluate([sample("2020-06-01", "2021-05-01")])
