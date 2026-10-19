"""
Temporal Graph Neural Network (TGNN) Training and Simulation Pipeline for PowerLaw.

Orchestrates:
1. Compiling temporal snapshots from the Dual-Layer Engine (Graph + Tabular Ledger)
2. PyTorch Training Loop with multi-task loss (State Regression + Causal Link Prediction + Risk Classification)
3. Counterfactual Simulation: Injects regulatory shocks into Law/Action nodes and predicts downstream Reaction impulses.
"""

import logging
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
import numpy as np
import pandas as pd

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from src.storage.graph_store import DualLayerGraphStore
from src.gnn.graph_exporter import GNNGraphExporter
from src.gnn.temporal_gnn import TemporalLawReactionGNN, TemporalHeteroGNN
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

        # Training chronology must come from actual history.
        if not history_df.empty and "timestamp" in history_df.columns:
            sorted_dates = sorted(list(history_df["timestamp"].dropna().unique()))
            if len(sorted_dates) >= num_snapshots:
                step = max(1, len(sorted_dates) // num_snapshots)
                selected_dates = [sorted_dates[i * step] for i in range(num_snapshots)]
            else:
                selected_dates = sorted_dates
        else:
            raise ValueError("At least two history dates are required for training")

        if len(selected_dates) < 2:
            raise ValueError("At least two history dates are required for training")

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

    def save_checkpoint(self, filepath: str = "data/checkpoints/tgnn_checkpoint.pt") -> None:
        """Saves trained model state, parameters, and loss history to disk."""
        if self.model is None:
            return
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model_state_dict": self.model.state_dict(),
            "in_features": self.in_features,
            "hidden_dim": self.hidden_dim,
            "num_gcn_layers": self.num_gcn_layers,
            "node_ids": self.node_ids,
            "feature_names": self.feature_names,
            "feature_slices": self.feature_slices,
            "metric_target_idx": self.metric_target_idx,
            "training_history": self.training_history
        }, p)
        logger.info(f"TGNN checkpoint saved to {p}")

    def load_checkpoint(self, filepath: str = "data/checkpoints/tgnn_checkpoint.pt") -> bool:
        """Loads trained model state, parameters, and loss history from disk."""
        p = Path(filepath)
        if not p.exists():
            return False
        try:
            ckpt = torch.load(p, map_location=self.device)
            self.in_features = ckpt["in_features"]
            self.hidden_dim = ckpt.get("hidden_dim", self.hidden_dim)
            self.num_gcn_layers = ckpt.get("num_gcn_layers", self.num_gcn_layers)
            self.node_ids = ckpt["node_ids"]
            self.feature_names = ckpt["feature_names"]
            self.feature_slices = ckpt["feature_slices"]
            self.metric_target_idx = ckpt["metric_target_idx"]
            self.training_history = ckpt.get("training_history", [])

            self.model = TemporalLawReactionGNN(
                in_features=self.in_features,
                hidden_dim=self.hidden_dim,
                num_gcn_layers=self.num_gcn_layers
            ).to(self.device)
            self.model.load_state_dict(ckpt["model_state_dict"])
            self.model.eval()
            logger.info(f"TGNN checkpoint loaded from {p}")
            return True
        except Exception as e:
            logger.warning(f"Could not load TGNN checkpoint: {e}")
            return False


# =========================================================================
# Release-Aware Heterogeneous GNN Trainer & Ablation Runner
# =========================================================================

class HeteroGNNTrainer:
    """
    Experimental trainer for the release-aware TemporalHeteroGNN.
    Trains on quarterly forecast dataset with future-only first-release labels,
    chronological train/validation splits, masked MSE loss on train-normalized growth,
    and supports news, legal, and topology ablations.
    """
    def __init__(
        self,
        model: Optional[TemporalHeteroGNN] = None,
        lr: float = 0.005,
        weight_decay: float = 1e-4,
        device: Optional[str] = None
    ):
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = model
        self.lr = lr
        self.weight_decay = weight_decay
        self.optimizer = None
        self._initial_model = copy.deepcopy(model)
        self.feature_profile = None
        self.target_mean = 0.0
        self.target_scale = 1.0
        self.fit_cutoff = None
        self._train_origins = None
        self.ablation_trainers = {}
        if self.model is not None:
            self.model.to(self.device)
            self.optimizer = optim.Adam(self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay)

    @staticmethod
    def _instant(value, name):
        if value is None:
            raise ValueError(f"Missing {name} timestamp")
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError(f"Invalid {name} timestamp")
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def _validate_samples(self, samples):
        origins, releases = [], []
        for sample in samples:
            origin = self._instant(sample.get("origin"), "origin")
            origins.append(origin)
            y, mask = np.asarray(sample["y"]), np.asarray(sample["y_mask"], dtype=bool)
            if y.shape != mask.shape or y.ndim != 2 or y.shape[1] != 1:
                raise ValueError("Labels and masks must have shape [targets, 1]")
            if mask.any():
                if not np.isfinite(y[mask]).all():
                    raise ValueError("Observed labels must be finite")
                release = self._instant(sample.get("first_release_available_at"), "label release")
                if release <= origin:
                    raise ValueError("Forecast label release must follow its origin")
                releases.append(release)
            if not sample["snapshots"]:
                raise ValueError("Forecast origin requires historical snapshots")
            for snapshot in sample["snapshots"]:
                cutoff = getattr(snapshot, "cutoff", None)
                if cutoff is not None and self._instant(cutoff, "snapshot cutoff") > origin:
                    raise ValueError("Snapshot cutoff is after its forecast origin")
        if not origins or any(current <= previous for previous, current in zip(origins, origins[1:])):
            raise ValueError("Samples must have strictly chronological origins")
        return origins, releases

    def _fit_transforms(self, samples, fit_cutoff=None):
        from src.gnn.graph_exporter import fit_heterodata_transforms
        origins, releases = self._validate_samples(samples)
        if not releases:
            raise ValueError("Training requires verified released labels")
        cutoff = self._instant(fit_cutoff, "fit cutoff") if fit_cutoff is not None else max(releases)
        if max(releases) > cutoff or max(origins) >= cutoff:
            raise ValueError("Training label release or origin is after the fit cutoff")
        if self._train_origins is not None and origins != self._train_origins:
            raise ValueError("Training origin split is frozen; use a fresh trainer for a new split")
        if self.feature_profile is None:
            values = np.concatenate([np.asarray(s["y"])[np.asarray(s["y_mask"], dtype=bool)] for s in samples])
            self.target_mean = float(values.mean())
            self.target_scale = float(values.std()) or 1.0
            self.feature_profile = fit_heterodata_transforms([snapshot for sample in samples for snapshot in sample["snapshots"]])
            self._train_origins = origins
            self.fit_cutoff = cutoff

    def _sample_inputs(self, sample, ablation):
        from src.gnn.graph_exporter import apply_heterodata_transforms
        if ablation not in {None, "no_news", "no_event", "no_legal", "no_rule", "no_topology"}:
            raise ValueError("Unsupported ablation")
        snapshots = []
        for snapshot in sample["snapshots"]:
            current = apply_heterodata_transforms(snapshot, self.feature_profile)
            if ablation in {"no_news", "no_event", "no_legal", "no_rule"}:
                removed = {"event", "reaction"} if ablation in {"no_news", "no_event"} else {"law", "rule"}
                for kind in removed & set(current.node_types):
                    current[kind].x.zero_()
                    for name in ("active_mask", "node_mask"):
                        mask = getattr(current[kind], name, None)
                        if mask is not None:
                            mask.zero_()
                for relation in current.edge_types:
                    if relation[0] in removed or relation[2] in removed:
                        current[relation].edge_index = current[relation].edge_index[:, :0]
                        current[relation].edge_attr = current[relation].edge_attr[:0]
            elif ablation == "no_topology":
                for relation in current.edge_types:
                    current[relation].edge_index = current[relation].edge_index[:, :0]
                    current[relation].edge_attr = current[relation].edge_attr[:0]
            snapshots.append(current)
        mask = np.asarray(sample["y_mask"], dtype=bool).reshape(-1)
        indices = np.asarray(sample["target_series_index"])
        if indices.ndim != 1 or len(indices) != len(mask) or (indices[mask] < 0).any():
            raise ValueError("Observed target requires a valid final stable series index")
        if sample.get("target_series_present") is False and mask.any():
            raise ValueError("Observed target is absent from its forecast snapshot")
        return snapshots, torch.as_tensor(indices[mask], dtype=torch.long, device=self.device)

    def train_epoch(self, samples: List[Dict[str, Any]], ablation: Optional[str] = None,
                    *, fit_cutoff=None) -> float:
        """
        Train one epoch over the samples with valid masked labels.
        Supports ablations:
        - 'no_news' / 'no_event': zeroes event/reaction inputs
        - 'no_legal' / 'no_rule': zeroes law/rule inputs
        - 'no_topology': removes cross-type edge messages (residual only)
        """
        if self.model is None or self.optimizer is None:
            raise ValueError("Model and optimizer must be initialized")
        self._fit_transforms(samples, fit_cutoff)
        self.model.train()
        total_loss = 0.0
        valid_count = 0

        for sample in samples:
            mask = np.asarray(sample["y_mask"], dtype=bool)
            if not mask.any():
                continue
            raw_y = np.asarray(sample["y"])[mask].reshape(-1, 1)
            y_true = torch.tensor((raw_y - self.target_mean) / self.target_scale, dtype=torch.float32, device=self.device)
            snapshots, target_idx = self._sample_inputs(sample, ablation)
            self.optimizer.zero_grad()
            y_pred = self.model(snapshots, target_idx=target_idx)
            loss = F.mse_loss(y_pred, y_true)
            loss.backward()
            self.optimizer.step()

            total_loss += loss.item()
            valid_count += 1

        return total_loss / max(1, valid_count)

    def evaluate(self, samples: List[Dict[str, Any]], ablation: Optional[str] = None) -> Dict[str, Any]:
        """
        Evaluates model on samples with valid masked labels.
        Returns MAE, RMSE, directional accuracy.
        """
        if self.model is None:
            raise ValueError("Model must be initialized")
        origins, _ = self._validate_samples(samples)
        if self.feature_profile is None or self.fit_cutoff is None:
            raise ValueError("Fit training transforms before evaluation")
        if min(origins) <= max(self._train_origins) or min(origins) < self.fit_cutoff:
            raise ValueError("Evaluation origin overlaps training origins or precedes the label-release fit cutoff")
        self.model.eval()
        preds = []
        actuals = []

        with torch.no_grad():
            for sample in samples:
                mask = np.asarray(sample["y_mask"], dtype=bool)
                if not mask.any():
                    continue
                snapshots, target_idx = self._sample_inputs(sample, ablation)
                y_pred = self.model(snapshots, target_idx=target_idx).cpu().numpy().reshape(-1)
                preds.extend((y_pred * self.target_scale + self.target_mean).tolist())
                actuals.extend(np.asarray(sample["y"])[mask].tolist())

        if not actuals:
            return {"sample_count": 0, "mae": None, "rmse": None, "directional_accuracy": None}

        actuals_np = np.array(actuals)
        preds_np = np.array(preds)
        err = actuals_np - preds_np
        mae = float(np.mean(np.abs(err)))
        rmse = float(np.sqrt(np.mean(err ** 2)))
        dir_acc = float(np.mean(np.sign(actuals_np) == np.sign(preds_np)))

        return {
            "sample_count": len(actuals),
            "mae": round(mae, 4),
            "rmse": round(rmse, 4),
            "directional_accuracy": round(dir_acc, 4),
            "predictions": preds,
            "actuals": actuals
        }

    def run_ablations(self, train_samples: List[Dict[str, Any]], test_samples: List[Dict[str, Any]], epochs: int = 5) -> Dict[str, Any]:
        """
        Runs training and evaluation across full model and ablations:
        - full_model
        - no_news
        - no_legal
        - no_topology
        """
        results = {}
        train_origins, train_releases = self._validate_samples(train_samples)
        test_origins, _ = self._validate_samples(test_samples)
        if max(train_origins) >= min(test_origins):
            raise ValueError("Chronological train/test origins overlap")
        cutoff = min(test_origins)
        if not train_releases or max(train_releases) > cutoff:
            raise ValueError("Training label release is after the first test origin")
        if self._initial_model is None or epochs < 1:
            raise ValueError("A fresh model and positive epoch count are required")
        self.ablation_trainers = {}
        for abl in [None, "no_news", "no_legal", "no_topology"]:
            abl_name = abl or "full_model"
            independent = HeteroGNNTrainer(copy.deepcopy(self._initial_model), lr=self.lr,
                                          weight_decay=self.weight_decay, device=str(self.device))
            devices = [self.device.index or 0] if self.device.type == "cuda" else []
            with torch.random.fork_rng(devices=devices):
                torch.manual_seed(0)
                for _ in range(epochs):
                    independent.train_epoch(train_samples, ablation=abl, fit_cutoff=cutoff)
                eval_metrics = independent.evaluate(test_samples, ablation=abl)
            eval_metrics.update({"fit_cutoff": cutoff.isoformat(), "target_mean": independent.target_mean,
                                 "target_scale": independent.target_scale, "feature_profile": independent.feature_profile,
                                 "seed": 0})
            self.ablation_trainers[abl_name] = independent
            results[abl_name] = eval_metrics
        return results
