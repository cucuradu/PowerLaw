"""
Graph Neural Network (GNN) Dataset & Preparation Package for PowerLaw.
Provides data compilation, temporal snapshot slicing, and export pipelines
for static and temporal GNN models (PyG, T-GCN, EvolveGCN).
"""

from src.gnn.graph_exporter import GNNGraphExporter

__all__ = ["GNNGraphExporter"]
