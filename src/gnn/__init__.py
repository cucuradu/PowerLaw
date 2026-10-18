"""
Graph Neural Network (GNN) Package for PowerLaw.

Provides:
- GNNGraphExporter: Compiles dual-layer graphs and tabular history into static & temporal tensors.
- TemporalLawReactionGNN: Spatio-temporal neural network architecture (Graph Conv + Temporal GRU).
- TGNNTrainer: Multi-task training, evaluation, and counterfactual policy shock simulation.
"""

from src.gnn.graph_exporter import GNNGraphExporter
from src.gnn.temporal_gnn import TemporalLawReactionGNN, GraphConvLayer, HAS_TORCH
from src.gnn.trainer import TGNNTrainer

__all__ = [
    "GNNGraphExporter",
    "TemporalLawReactionGNN",
    "GraphConvLayer",
    "TGNNTrainer",
    "HAS_TORCH"
]
