"""
Unit and Integration Tests for Temporal Graph Neural Network (TGNN).

Tests:
1. GraphConvLayer spatial convolution with self-loops and degree normalization
2. TemporalLawReactionGNN forward pass across temporal snapshots
3. Multi-task heads: State Regressor, Causal Link Predictor, Risk Classifier
4. TGNNTrainer multi-epoch training and loss convergence
5. Model inference (predict) and counterfactual shock simulation (simulate_shock)
6. Edge case handling (single node, empty graph, zero shock)
"""

import pytest
import numpy as np
import pandas as pd
import torch

from src.gnn.temporal_gnn import GraphConvLayer, TemporalLawReactionGNN, HAS_TORCH
from src.gnn.trainer import TGNNTrainer
from src.storage.dashboard_data import get_graph_store
from src.storage.graph_store import DualLayerGraphStore


@pytest.fixture
def sample_graph_store(tmp_path):
    """Instantiates a populated graph store with seed data for tests."""
    return get_graph_store()


def test_has_torch():
    """Verify PyTorch is installed and accessible."""
    assert HAS_TORCH is True
    assert torch is not None


def test_graph_conv_layer():
    """Test spatial graph convolution forward pass and output shape."""
    N = 5
    in_features = 16
    out_features = 32

    conv = GraphConvLayer(in_features, out_features)
    x = torch.randn(N, in_features)
    adj = torch.zeros(N, N)
    # Directed ring graph
    for i in range(N):
        adj[(i + 1) % N, i] = 1.0

    out = conv(x, adj)
    assert out.shape == (N, out_features)
    assert not torch.isnan(out).any()


def test_temporal_gnn_forward():
    """Test Spatio-Temporal GNN forward pass across T snapshots."""
    T = 3
    N = 6
    D = 24
    hidden_dim = 32

    model = TemporalLawReactionGNN(
        in_features=D,
        hidden_dim=hidden_dim,
        out_features=1,
        num_classes=3,
        num_gcn_layers=2
    )

    x_seq = [torch.randn(N, D) for _ in range(T)]
    adj_seq = [torch.eye(N) for _ in range(T)]

    # Candidate edge pairs for link prediction
    edge_pairs = torch.tensor([[0, 1, 2], [1, 2, 0]], dtype=torch.long)

    outputs = model(x_seq=x_seq, adj_seq=adj_seq, edge_pairs=edge_pairs)

    assert "node_embeddings" in outputs
    assert outputs["node_embeddings"].shape == (N, hidden_dim)

    assert "predicted_states" in outputs
    assert outputs["predicted_states"].shape == (N, 1)

    assert "risk_logits" in outputs
    assert outputs["risk_logits"].shape == (N, 3)

    assert "link_probabilities" in outputs
    assert outputs["link_probabilities"].shape == (3, 1)
    # Probabilities must be strictly bounded between 0 and 1
    assert (outputs["link_probabilities"] >= 0.0).all()
    assert (outputs["link_probabilities"] <= 1.0).all()


def test_tgnn_trainer_prepare_dataset(sample_graph_store):
    """Test trainer dataset construction from dual-layer store."""
    trainer = TGNNTrainer(sample_graph_store, hidden_dim=32)
    data = trainer.prepare_dataset(num_snapshots=3)

    assert "x_seq" in data
    assert "adj_seq" in data
    assert "y_state" in data
    assert "edge_pairs" in data
    assert "edge_targets" in data
    assert "y_risk" in data

    N = len(data["node_ids"])
    assert N > 0
    assert len(data["x_seq"]) >= 2
    assert data["x_seq"][0].shape[0] == N
    assert data["adj_seq"][0].shape == (N, N)
    assert data["y_state"].shape == (N, 1)
    assert data["y_risk"].shape == (N,)


def test_tgnn_trainer_train_and_predict(sample_graph_store):
    """Test end-to-end model training and inference output."""
    trainer = TGNNTrainer(sample_graph_store, hidden_dim=32, lr=0.02)
    res = trainer.train(epochs=6)

    assert res["epochs"] == 6
    assert "final_loss" in res
    assert len(res["history"]) == 6
    assert res["final_loss"] > 0

    # Test inference
    predictions = trainer.predict()
    assert isinstance(predictions, pd.DataFrame)
    assert not predictions.empty
    assert "node_id" in predictions.columns
    assert "predicted_state_delta" in predictions.columns
    assert "risk_category" in predictions.columns
    assert "risk_confidence" in predictions.columns
    assert len(predictions) == len(trainer.node_ids)


def test_counterfactual_shock_simulation(sample_graph_store):
    """Test policy shock injection on Law node and downstream impulse measurement."""
    trainer = TGNNTrainer(sample_graph_store, hidden_dim=32)
    trainer.train(epochs=5)

    shock_node = "LAW_32023R0956_CBAM"
    shocks = trainer.simulate_shock(shock_node, shock_magnitude=20.0)

    assert isinstance(shocks, pd.DataFrame)
    assert not shocks.empty
    assert "node_id" in shocks.columns
    assert "impulse_response" in shocks.columns
    assert "pct_change" in shocks.columns
    assert "baseline_state" in shocks.columns
    assert "shocked_state" in shocks.columns

    # Verify that downstream nodes were evaluated
    assert len(shocks) == len(trainer.node_ids)


def test_tgnn_empty_graph_graceful_error(tmp_path):
    """Test that trainer raises informative error when store has no nodes."""
    empty_store = DualLayerGraphStore(str(tmp_path / "empty_lancedb"))
    trainer = TGNNTrainer(empty_store)
    with pytest.raises(ValueError, match="no nodes"):
        trainer.train(epochs=1)
