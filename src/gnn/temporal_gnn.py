"""
Temporal Graph Neural Network (TGNN) Architecture for PowerLaw.

Implements Spatio-Temporal Graph Neural Networks designed for the Dual-Layer
(Topological Graph + Temporal Tabular Ledger) Engine:
1. Spatial Message Passing: Propagates legal-economic causal influence along directed
   edges (Law -> MANDATES -> Action -> TRIGGERS -> Reaction -> IMPACTS -> Sector).
2. Behind-the-Graph Temporal Recurrence: Sequences node state updates and metric timestamps
   via a Gated Recurrent Unit (GRU) or Temporal Convolution.
3. Multi-Task Prediction Heads:
   - State Regressor: Predicts future continuous metric value (e.g. price change, carbon cost)
   - Causal Link Predictor: Predicts edge formation probabilities between Laws, Actions & Reactions
   - Risk/Impact Classifier: Classifies node volatility and regulatory sensitivity
"""

import math
from typing import Dict, List, Optional, Tuple, Union, Any
import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    HAS_TORCH = True
except ImportError:
    torch = None
    nn = None
    F = None
    HAS_TORCH = False


if HAS_TORCH:
    class GraphConvLayer(nn.Module):
        """
        Directed Graph Convolutional Layer with edge weighting and self-loops.
        Computes H' = ReLU(D_hat^(-1/2) * A_hat * D_hat^(-1/2) * H * W + b)
        """
        def __init__(self, in_features: int, out_features: int, bias: bool = True, dropout: float = 0.1):
            super().__init__()
            self.in_features = in_features
            self.out_features = out_features
            self.weight = nn.Parameter(torch.Tensor(in_features, out_features))
            if bias:
                self.bias = nn.Parameter(torch.Tensor(out_features))
            else:
                self.register_parameter('bias', None)
            self.dropout = nn.Dropout(dropout)
            self.reset_parameters()

        def reset_parameters(self):
            nn.init.kaiming_uniform_(self.weight, a=math.sqrt(5))
            if self.bias is not None:
                fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight)
                bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
                nn.init.uniform_(self.bias, -bound, bound)

        def forward(self, x: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
            """
            Args:
                x: Node features of shape [num_nodes, in_features]
                adj: Dense or normalized adjacency matrix of shape [num_nodes, num_nodes]
            Returns:
                Updated node representations of shape [num_nodes, out_features]
            """
            # Add self-loops if not already present
            num_nodes = x.size(0)
            if adj.shape[0] != num_nodes or adj.shape[1] != num_nodes:
                raise ValueError(f"Adjacency shape {adj.shape} does not match node count {num_nodes}")

            # Ensure self loops on diagonal
            eye = torch.eye(num_nodes, device=adj.device, dtype=adj.dtype)
            a_hat = adj + eye

            # Degree normalization: D^(-1/2) * A * D^(-1/2)
            deg = torch.sum(a_hat, dim=1)
            deg_inv_sqrt = torch.pow(deg.clamp(min=1e-6), -0.5)
            deg_mat = torch.diag(deg_inv_sqrt)
            norm_adj = torch.mm(torch.mm(deg_mat, a_hat), deg_mat)

            # Linear transformation and message passing
            support = torch.mm(x, self.weight)
            out = torch.mm(norm_adj, support)
            if self.bias is not None:
                out = out + self.bias
            return self.dropout(F.relu(out))


    class TemporalLawReactionGNN(nn.Module):
        """
        Spatio-Temporal GNN combining directed Graph Convolutions with
        behind-the-graph temporal sequence modeling (GRU).

        Designed for:
        - Input: T sequential temporal snapshots (X_t, A_t) for t in 0..T-1
        - Node Features X_t: [N, D] combining text embeddings, topology, and historical tabular metrics
        - Adjacency A_t: [N, N] directed legal-economic causal graph at time t
        """
        def __init__(
            self,
            in_features: int,
            hidden_dim: int = 64,
            out_features: int = 1,
            num_classes: int = 3,
            num_gcn_layers: int = 2,
            dropout: float = 0.1
        ):
            super().__init__()
            self.in_features = in_features
            self.hidden_dim = hidden_dim
            self.out_features = out_features
            self.num_classes = num_classes

            # Spatial Graph Convolution stack
            self.gcn_layers = nn.ModuleList()
            self.gcn_layers.append(GraphConvLayer(in_features, hidden_dim, dropout=dropout))
            for _ in range(num_gcn_layers - 1):
                self.gcn_layers.append(GraphConvLayer(hidden_dim, hidden_dim, dropout=dropout))

            # Temporal Sequence Module: GRU cell tracking node states across time horizons
            self.temporal_gru = nn.GRU(
                input_size=hidden_dim,
                hidden_size=hidden_dim,
                num_layers=1,
                batch_first=True
            )

            # Task Head 1: Tabular Node State Regressor (Predict future continuous metric values)
            self.state_regressor = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, out_features)
            )

            # Task Head 2: Causal Link Predictor (Predict edge probability between node pairs)
            self.link_predictor = nn.Bilinear(hidden_dim, hidden_dim, 1)

            # Task Head 3: Impact / Volatility Risk Classifier
            self.risk_classifier = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, num_classes)
            )

        def encode_snapshot(self, x: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
            """
            Passes a single temporal snapshot through spatial graph convolutions.
            Args:
                x: [N, in_features]
                adj: [N, N]
            Returns:
                h: [N, hidden_dim]
            """
            h = x
            for gcn in self.gcn_layers:
                h = gcn(h, adj)
            return h

        def forward(
            self,
            x_seq: Union[torch.Tensor, List[torch.Tensor]],
            adj_seq: Union[torch.Tensor, List[torch.Tensor]],
            edge_pairs: Optional[torch.Tensor] = None
        ) -> Dict[str, torch.Tensor]:
            """
            Forward pass across temporal snapshot sequence.

            Args:
                x_seq: Sequence of T node feature matrices [T, N, in_features] or list of T tensors [N, in_features]
                adj_seq: Sequence of T adjacency matrices [T, N, N] or list of T tensors [N, N]
                edge_pairs: Optional tensor of candidate directed edge indices [2, P] for link prediction

            Returns:
                Dictionary containing:
                - 'node_embeddings': Final spatio-temporal representations [N, hidden_dim]
                - 'predicted_states': Predicted future tabular states [N, out_features]
                - 'risk_logits': Classification logits for node volatility [N, num_classes]
                - 'link_probabilities': Predicted edge probabilities [P, 1] (if edge_pairs provided)
            """
            if isinstance(x_seq, torch.Tensor) and x_seq.dim() == 3:
                T, N, D = x_seq.shape
                x_list = [x_seq[t] for t in range(T)]
            else:
                x_list = list(x_seq)
                T = len(x_list)
                N = x_list[0].shape[0]

            if isinstance(adj_seq, torch.Tensor) and adj_seq.dim() == 3:
                adj_list = [adj_seq[t] for t in range(T)]
            else:
                adj_list = list(adj_seq)

            if len(adj_list) != T:
                raise ValueError(f"Mismatch: {T} feature snapshots vs {len(adj_list)} adjacency snapshots")

            # 1. Spatial GCN across each temporal slice
            spatial_embeddings = []
            for t in range(T):
                h_t = self.encode_snapshot(x_list[t], adj_list[t]) # [N, hidden_dim]
                spatial_embeddings.append(h_t)

            # Stack into [N, T, hidden_dim] for per-node temporal recurrence
            node_trajectories = torch.stack(spatial_embeddings, dim=1) # [N, T, hidden_dim]

            # 2. Behind-the-graph temporal sequence modeling with GRU
            gru_out, _ = self.temporal_gru(node_trajectories) # [N, T, hidden_dim]
            final_embeddings = gru_out[:, -1, :] # [N, hidden_dim] (most recent state)

            # 3. Multi-Task Predictions
            predicted_states = self.state_regressor(final_embeddings)
            risk_logits = self.risk_classifier(final_embeddings)

            results: Dict[str, torch.Tensor] = {
                "node_embeddings": final_embeddings,
                "predicted_states": predicted_states,
                "risk_logits": risk_logits,
                "temporal_hidden_states": gru_out
            }

            if edge_pairs is not None:
                src_idx = edge_pairs[0]
                dst_idx = edge_pairs[1]
                src_emb = final_embeddings[src_idx]
                dst_emb = final_embeddings[dst_idx]
                link_logits = self.link_predictor(src_emb, dst_emb)
                results["link_probabilities"] = torch.sigmoid(link_logits)

            return results


    try:
        import torch_geometric
        from torch_geometric.nn import HeteroConv, TransformerConv
        HAS_PYG = True
    except ImportError:
        torch_geometric = None
        HeteroConv = None
        TransformerConv = None
        HAS_PYG = False


    class TemporalHeteroGNN(nn.Module):
        """
        Release-aware Spatio-Temporal Heterogeneous GNN for German Construction PPI Forecasting.

        Architecture (per technical architecture spec):
        1. Per-type linear input projection to hidden_channels (32).
        2. Two relation-specific HeteroConv layers using TransformerConv(in_channels=32, out_channels=32, heads=1, edge_dim=12, root_weight=False)
           with per-type residual connections, ReLU activation, and dropout 0.1.
        3. Behind-the-graph temporal sequence modeling with 1-layer GRU (hidden_size=32).
        4. Series target regression head Linear(32, 1) reading final hidden state of series targets only.
        """
        def __init__(
            self,
            node_types: List[str],
            edge_types: List[Tuple[str, str, str]],
            in_channels_dict: Dict[str, int],
            hidden_channels: int = 32,
            edge_dim: int = 12,
            target_node_type: str = "series",
            dropout: float = 0.1
        ):
            super().__init__()
            if not HAS_PYG or HeteroConv is None:
                raise ImportError("torch_geometric is required to instantiate TemporalHeteroGNN")

            self.node_types = list(node_types)
            self.edge_types = list(edge_types)
            self.hidden_channels = hidden_channels
            self.edge_dim = edge_dim
            self.target_node_type = target_node_type
            self.dropout = nn.Dropout(dropout)

            # 1. Input projections per node type to hidden_channels
            self.input_projections = nn.ModuleDict()
            for nt in self.node_types:
                in_ch = in_channels_dict.get(nt, 8)
                self.input_projections[nt] = nn.Linear(in_ch, hidden_channels)

            # 2. Two HeteroConv layers with TransformerConv
            conv1_dict = {}
            conv2_dict = {}
            for rel in self.edge_types:
                conv1_dict[rel] = TransformerConv(
                    in_channels=hidden_channels,
                    out_channels=hidden_channels,
                    heads=1,
                    edge_dim=edge_dim,
                    root_weight=False
                )
                conv2_dict[rel] = TransformerConv(
                    in_channels=hidden_channels,
                    out_channels=hidden_channels,
                    heads=1,
                    edge_dim=edge_dim,
                    root_weight=False
                )

            self.conv1 = HeteroConv(conv1_dict, aggr="sum")
            self.conv2 = HeteroConv(conv2_dict, aggr="sum")

            # Per-type residual linears
            self.res1 = nn.ModuleDict({nt: nn.Linear(hidden_channels, hidden_channels) for nt in self.node_types})
            self.res2 = nn.ModuleDict({nt: nn.Linear(hidden_channels, hidden_channels) for nt in self.node_types})

            # 3. GRU for temporal sequence modeling
            self.gru = nn.GRU(
                input_size=hidden_channels,
                hidden_size=hidden_channels,
                num_layers=1,
                batch_first=True
            )

            # 4. Target series regression head
            self.regression_head = nn.Linear(hidden_channels, 1)

        @staticmethod
        def _node_mask(store, device):
            mask = torch.ones(store.x.shape[0], dtype=torch.bool, device=device)
            for name in ("active_mask", "node_mask"):
                candidate = getattr(store, name, None)
                if candidate is not None:
                    if candidate.shape != mask.shape:
                        raise ValueError(f"{name} must have one value per node")
                    mask = mask & candidate.to(device=device, dtype=torch.bool)
            return mask

        def forward_snapshot(self, hdata: Any) -> Dict[str, torch.Tensor]:
            """
            Pass a single HeteroData snapshot through the 2 HeteroConv layers.
            """
            x_dict = {}
            active = {}
            device = next(self.parameters()).device
            for nt in self.node_types:
                if nt in hdata.node_types and hdata[nt].x.shape[0] > 0:
                    active[nt] = self._node_mask(hdata[nt], device)
                    values = hdata[nt].x.to(device)
                    observed = getattr(hdata[nt], "feature_observed_mask", None)
                    if observed is not None:
                        values = values * observed.to(device)
                    values = torch.where(active[nt][:, None], values, torch.zeros_like(values))
                    x_dict[nt] = self.dropout(F.relu(self.input_projections[nt](values)))
                else:
                    x_dict[nt] = torch.zeros((0, self.hidden_channels), device=device)
                    active[nt] = torch.zeros(0, dtype=torch.bool, device=device)

            edge_index_dict = {}
            edge_attr_dict = {}
            for rel in self.edge_types:
                if rel in hdata.edge_types and hdata[rel].edge_index.shape[1] > 0:
                    edges = hdata[rel].edge_index.to(device)
                    keep = active[rel[0]][edges[0]] & active[rel[2]][edges[1]]
                    if keep.any():
                        edge_index_dict[rel] = edges[:, keep]
                        edge_attr_dict[rel] = hdata[rel].edge_attr.to(device)[keep]

            # Layer 1
            h1 = self.conv1(x_dict, edge_index_dict, edge_attr_dict=edge_attr_dict)
            out1 = {}
            for nt in self.node_types:
                res = self.res1[nt](x_dict[nt]) if x_dict[nt].shape[0] > 0 else x_dict[nt]
                msg = h1.get(nt, torch.zeros_like(x_dict[nt]))
                out1[nt] = self.dropout(F.relu(msg + res))

            # Layer 2
            h2 = self.conv2(out1, edge_index_dict, edge_attr_dict=edge_attr_dict)
            out2 = {}
            for nt in self.node_types:
                res = self.res2[nt](out1[nt]) if out1[nt].shape[0] > 0 else out1[nt]
                msg = h2.get(nt, torch.zeros_like(out1[nt]))
                out2[nt] = self.dropout(F.relu(msg + res))

            return out2

        def forward(
            self,
            snapshots: List[Any],
            target_idx: Optional[torch.Tensor] = None
        ) -> torch.Tensor:
            """
            Processes a sequence of T snapshots.
            Extracts trajectory of target_node_type, runs through GRU, and predicts series growth.
            """
            T = len(snapshots)
            if T == 0:
                raise ValueError("Snapshots sequence cannot be empty")

            device = next(self.parameters()).device
            states = {}
            final_ids, final_active = [], None
            for hdata in snapshots:
                encoded_dict = self.forward_snapshot(hdata)
                if self.target_node_type not in hdata.node_types:
                    final_ids, final_active = [], None
                    continue
                target = hdata[self.target_node_type]
                ids = list(getattr(target, "node_id", []))
                if len(ids) != target.x.shape[0] or len(set(ids)) != len(ids):
                    raise ValueError("Every target row requires a unique stable node_id")
                active = self._node_mask(target, device)
                final_ids, final_active = ids, active
                if not ids:
                    continue
                previous = torch.stack([states.get(identity, torch.zeros(self.hidden_channels, device=device)) for identity in ids])
                _, proposed = self.gru(encoded_dict[self.target_node_type].unsqueeze(1), previous.unsqueeze(0))
                updated = torch.where(active[:, None], proposed[0], previous)
                states.update(zip(ids, updated.unbind(0)))
            if not final_ids:
                raise ValueError("Final snapshot has no active target nodes")
            final_h = torch.stack([states[identity] for identity in final_ids])

            if target_idx is not None:
                if isinstance(target_idx, int):
                    target_idx = torch.tensor([target_idx], dtype=torch.long, device=final_h.device)
                elif not isinstance(target_idx, torch.Tensor):
                    target_idx = torch.tensor(target_idx, dtype=torch.long, device=final_h.device)
                else:
                    target_idx = target_idx.to(final_h.device)
                if target_idx.ndim != 1 or ((target_idx < 0) | (target_idx >= len(final_ids))).any():
                    raise ValueError("Target index is outside the final stable node map")
                final_h = final_h[target_idx]
                final_active = final_active[target_idx]
                final_ids = [final_ids[i] for i in target_idx.tolist()]
            if not final_active.all():
                raise ValueError("Predictions require active final target nodes")
            self.output_target_ids = tuple(final_ids)

            out = self.regression_head(final_h) # [M, 1]
            return out


else:
    # Graceful fallback mock class if PyTorch is not yet installed
    class GraphConvLayer:
        def __init__(self, *args, **kwargs):
            raise ImportError("PyTorch is required to use GraphConvLayer. Install torch first.")

    class TemporalLawReactionGNN:
        def __init__(self, *args, **kwargs):
            raise ImportError("PyTorch is required to use TemporalLawReactionGNN. Install torch first.")

    class TemporalHeteroGNN:
        def __init__(self, *args, **kwargs):
            raise ImportError("PyTorch is required to use TemporalHeteroGNN. Install torch first.")
