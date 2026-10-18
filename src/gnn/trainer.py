"""
Temporal Graph Neural Network (TGNN) Training and Simulation Pipeline for PowerLaw.

Orchestrates:
1. Compiling temporal snapshots from the Dual-Layer Engine (Graph + Tabular Ledger)
2. PyTorch Training Loop with multi-task loss (State Regression + Causal Link Prediction + Risk Classification)
3. Counterfactual Simulation: Injects regulatory shocks into Law/Action nodes and predicts downstream Reaction impulses.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any, Union
import numpy as np
import pandas as pd

import torch
import torch.nn as nn
import torch.optim as optim

from src.storage.graph_store import DualLayerGraphStore
from src.gnn.graph_exporter import GNNGraphExporter
from src.gnn.temporal_gnn import TemporalLawReactionGNN
from src.models.dual_layer_model import NodeType

logger = logging.getLogger(__name__)


class TGNNTrainer:
    """
    Manages dataset construction, training, evaluation, and counterfactual simulation
    for the PowerLaw Temporal Graph Neural Network.
    """
    def __init__(
        self,
        store: DualLayerGraphStore,
        hidden_dim: int = 64,
        num_gcn_layers: int = 2,
        lr: float = 0.01,
        weight_decay: float = 1e-4,
        device: Optional[str] = None
    ):
        self.store = store
        self.exporter = GNNGraphExporter(store)
        self.hidden_dim = hidden_dim
        self.num_gcn_layers = num_gcn_layers
        self.lr = lr
        self.weight_decay = weight_decay
        
        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)
            
        self.model: Optional[TemporalLawReactionGNN] = None
        self.optimizer: Optional[optim.Optimizer] = None
        self.node_ids: List[str] = []
        self.feature_names: List[str] = []
        self.feature_slices: Dict[str, Tuple[int, int]] = {}
        self.metric_target_idx: int = 0
        self.in_features: int = 0
        self.training_history: List[Dict[str, float]] = []

    def prepare_dataset(
        self,
        num_snapshots: int = 4
    ) -> Dict[str, Any]:
        """
        Extracts chronological temporal snapshots from the dual-layer store.
        Returns tensor batches ready for TemporalLawReactionGNN.
        """
        history_df = self.store.get_history_df()
        nodes_df = self.store.get_nodes_df()
        edges_df = self.store.get_edges_df()

        if nodes_df.empty:
            raise ValueError("DualLayerGraphStore has no nodes to train on.")

        nodes = self.store.get_nodes()
        self.node_ids = [n.id for n in nodes]
        N = len(self.node_ids)
        node_to_idx = {nid: i for i, nid in enumerate(self.node_ids)}

        # Build dates from tabular history or synthetic chronological split
        if not history_df.empty and "timestamp" in history_df.columns:
            sorted_dates = sorted(list(history_df["timestamp"].dropna().unique()))
            if len(sorted_dates) >= num_snapshots:
                step = max(1, len(sorted_dates) // num_snapshots)
                selected_dates = [sorted_dates[i * step] for i in range(num_snapshots)]
            else:
                selected_dates = sorted_dates
        else:
            selected_dates = ["2023-01-01", "2023-06-30", "2024-01-01", "2024-06-30"]

        if len(selected_dates) < 2:
            selected_dates = ["2023-01-01", "2023-06-30", "2024-01-01", "2024-06-30"]

        x_snapshots = []
        adj_snapshots = []

        # Determine feature names, dimension, and feature slices using exporter
        base_x, self.feature_names, self.feature_slices = self.exporter.compile_node_features(nodes=nodes)
        self.in_features = len(self.feature_names)

        # Pin tabular state target index (latest_metric_value)
        tab_slice = self.feature_slices.get("tabular_metrics", (0, 1))
        self.metric_target_idx = tab_slice[0]

        # Build feature matrix and adjacency matrix for each snapshot
        for dt in selected_dates:
            dt_str = str(dt)[:10]
            # 1. Feature matrix at date dt
            X_t, _, _ = self.exporter.compile_node_features(as_of_date=dt_str, nodes=nodes)
            x_snapshots.append(torch.tensor(X_t, dtype=torch.float32, device=self.device))

            # 2. Adjacency matrix at date dt
            # In message passing: target node v receives causal influence from source node u.
            # Thus A[v, u] represents directed forward influence (Law -> Action -> Reaction).
            # A[u, v] carries a fractional feedback/audit signal (0.3).
            A_t = np.zeros((N, N), dtype=np.float32)
            if not edges_df.empty:
                for _, edge in edges_df.iterrows():
                    src = str(edge.get("source_id", ""))
                    tgt = str(edge.get("target_id", ""))
                    w = float(edge.get("weight", 1.0))
                    if src in node_to_idx and tgt in node_to_idx:
                        u = node_to_idx[src]
                        v = node_to_idx[tgt]
                        A_t[v, u] += w        # Forward causal flow: v receives from u
                        A_t[u, v] += 0.3 * w  # Feedback / audit signal
            adj_snapshots.append(torch.tensor(A_t, dtype=torch.float32, device=self.device))

        # Target 1: State Regression Target Y_state (tabular numerical state at horizon)
        latest_feat = x_snapshots[-1].cpu().numpy()
        y_state_np = latest_feat[:, self.metric_target_idx : self.metric_target_idx + 1] # [N, 1]
        Y_state = torch.tensor(y_state_np, dtype=torch.float32, device=self.device)

        # Target 2: Candidate edge pairs for causal link prediction
        active_edges = []
        for i in range(N):
            for j in range(N):
                if i != j and adj_snapshots[-1][i, j] > 0:
                    active_edges.append((i, j, 1.0))
                elif i != j and np.random.rand() < 0.05: # sample negative pairs
                    active_edges.append((i, j, 0.0))

        if not active_edges:
            active_edges = [(0, min(1, N - 1), 1.0)]

        edge_src = [e[0] for e in active_edges]
        edge_dst = [e[1] for e in active_edges]
        edge_labels = [e[2] for e in active_edges]

        edge_pairs = torch.tensor([edge_src, edge_dst], dtype=torch.long, device=self.device)
        edge_targets = torch.tensor(edge_labels, dtype=torch.float32, device=self.device).unsqueeze(1)

        # Target 3: Node Risk/Volatility Class (0=Low, 1=Medium, 2=High based on metric volatility)
        abs_vals = np.abs(y_state_np.flatten())
        q33 = np.percentile(abs_vals, 33) if len(abs_vals) > 0 else 0.0
        q66 = np.percentile(abs_vals, 66) if len(abs_vals) > 0 else 1.0
        risk_classes = np.zeros(N, dtype=np.int64)
        risk_classes[abs_vals > q33] = 1
        risk_classes[abs_vals > q66] = 2
        Y_risk = torch.tensor(risk_classes, dtype=torch.long, device=self.device)

        return {
            "x_seq": x_snapshots,
            "adj_seq": adj_snapshots,
            "y_state": Y_state,
            "edge_pairs": edge_pairs,
            "edge_targets": edge_targets,
            "y_risk": Y_risk,
            "node_ids": self.node_ids,
            "feature_names": self.feature_names
        }

    def initialize_model(self, in_features: int) -> TemporalLawReactionGNN:
        """Instantiates the model and optimizer."""
        self.model = TemporalLawReactionGNN(
            in_features=in_features,
            hidden_dim=self.hidden_dim,
            out_features=1,
            num_classes=3,
            num_gcn_layers=self.num_gcn_layers
        ).to(self.device)
        self.optimizer = optim.AdamW(
            self.model.parameters(),
            lr=self.lr,
            weight_decay=self.weight_decay
        )
        return self.model

    def train(
        self,
        epochs: int = 40,
        alpha_state: float = 1.0,
        beta_link: float = 0.5,
        gamma_risk: float = 0.2,
        callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Executes multi-task training loop across the temporal snapshot sequence.
        """
        data = self.prepare_dataset()
        if self.model is None or self.in_features != len(data["feature_names"]):
            self.initialize_model(len(data["feature_names"]))

        mse_loss_fn = nn.MSELoss()
        bce_loss_fn = nn.BCELoss()
        ce_loss_fn = nn.CrossEntropyLoss()

        self.model.train()
        self.training_history = []

        for epoch in range(1, epochs + 1):
            self.optimizer.zero_grad()
            
            outputs = self.model(
                x_seq=data["x_seq"],
                adj_seq=data["adj_seq"],
                edge_pairs=data["edge_pairs"]
            )

            # Compute multi-task losses
            l_state = mse_loss_fn(outputs["predicted_states"], data["y_state"])
            l_link = bce_loss_fn(outputs["link_probabilities"], data["edge_targets"])
            l_risk = ce_loss_fn(outputs["risk_logits"], data["y_risk"])

            total_loss = (alpha_state * l_state) + (beta_link * l_link) + (gamma_risk * l_risk)

            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=2.0)
            self.optimizer.step()

            record = {
                "epoch": epoch,
                "total_loss": float(total_loss.item()),
                "state_loss": float(l_state.item()),
                "link_loss": float(l_link.item()),
                "risk_loss": float(l_risk.item())
            }
            self.training_history.append(record)

            if callback is not None and callable(callback):
                callback(epoch, epochs, record)

        self.model.eval()
        return {
            "epochs": epochs,
            "final_loss": self.training_history[-1]["total_loss"],
            "history": self.training_history,
            "num_nodes": len(self.node_ids),
            "num_features": len(data["feature_names"])
        }

    def predict(self) -> pd.DataFrame:
        """
        Runs model inference on the latest snapshot and formats predictions
        as an interpretable DataFrame.
        """
        if self.model is None:
            raise ValueError("Model has not been trained yet. Call train() first.")

        data = self.prepare_dataset()
        self.model.eval()

        with torch.no_grad():
            outputs = self.model(
                x_seq=data["x_seq"],
                adj_seq=data["adj_seq"]
            )

        pred_states = outputs["predicted_states"].cpu().numpy().flatten()
        risk_probs = torch.softmax(outputs["risk_logits"], dim=1).cpu().numpy()
        pred_risk_classes = np.argmax(risk_probs, axis=1)

        risk_labels = {0: "Low", 1: "Moderate", 2: "High / Volatile"}
        nodes_df = self.store.get_nodes_df()
        type_map = {}
        label_map = {}
        if not nodes_df.empty:
            for _, r in nodes_df.iterrows():
                node_type_val = r.get("node_type") or r.get("type") or "UNKNOWN"
                type_map[str(r.get("id"))] = str(node_type_val)
                label_map[str(r.get("id"))] = str(r.get("label", ""))

        rows = []
        for i, nid in enumerate(self.node_ids):
            rows.append({
                "node_id": nid,
                "label": label_map.get(nid, nid),
                "type": type_map.get(nid, "UNKNOWN"),
                "predicted_state_delta": round(float(pred_states[i]), 4),
                "risk_category": risk_labels.get(pred_risk_classes[i], "Low"),
                "risk_confidence": round(float(risk_probs[i, pred_risk_classes[i]]), 3)
            })

        return pd.DataFrame(rows)

    def simulate_shock(
        self,
        shocked_node_id: str,
        shock_magnitude: float = 10.0
    ) -> pd.DataFrame:
        """
        Performs counterfactual policy shock simulation:
        1. Takes current snapshot sequence
        2. Injects shock_magnitude into the target Law/Action node's tabular state
        3. Propagates through graph convolutions and temporal GRU
        4. Compares baseline vs. shocked downstream node states.
        """
        if self.model is None:
            self.train(epochs=20)

        data = self.prepare_dataset()
        if shocked_node_id not in self.node_ids:
            raise ValueError(f"Node '{shocked_node_id}' not found in graph nodes.")

        target_idx = self.node_ids.index(shocked_node_id)
        target_feat_idx = self.metric_target_idx

        # Baseline prediction
        self.model.eval()
        with torch.no_grad():
            baseline_out = self.model(x_seq=data["x_seq"], adj_seq=data["adj_seq"])
            base_states = baseline_out["predicted_states"].cpu().numpy().flatten()

            # Create shocked sequence: inject shock into tabular metric of target node
            shocked_x_seq = [t.clone() for t in data["x_seq"]]
            shocked_x_seq[-1][target_idx, target_feat_idx] += shock_magnitude

            shocked_out = self.model(x_seq=shocked_x_seq, adj_seq=data["adj_seq"])
            shocked_states = shocked_out["predicted_states"].cpu().numpy().flatten()

        impulse_deltas = shocked_states - base_states
        nodes_df = self.store.get_nodes_df()
        type_map = {str(r.get("id")): str(r.get("node_type") or r.get("type") or "UNKNOWN") for _, r in nodes_df.iterrows()} if not nodes_df.empty else {}
        label_map = {str(r.get("id")): str(r.get("label", "")) for _, r in nodes_df.iterrows()} if not nodes_df.empty else {}

        results = []
        for i, nid in enumerate(self.node_ids):
            delta = float(impulse_deltas[i])
            base_val = float(base_states[i])
            shocked_val = float(shocked_states[i])
            pct = (delta / (abs(base_val) + 1e-4)) * 100.0
            results.append({
                "node_id": nid,
                "label": label_map.get(nid, nid),
                "type": type_map.get(nid, "UNKNOWN"),
                "baseline_state": round(base_val, 4),
                "shocked_state": round(shocked_val, 4),
                "impulse_response": round(delta, 4),
                "pct_change": round(pct, 2)
            })

        res_df = pd.DataFrame(results)
        return res_df.sort_values(by="impulse_response", key=abs, ascending=False)
