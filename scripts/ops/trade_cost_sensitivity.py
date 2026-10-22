"""How strongly does each trade's price follow material and labour costs? (per-trade what-ifs)

For every trade price index (61261-0002, excl. VAT) with an official weight: annual log change regressed on the annual
log changes of the material and labour building-cost components over the same year (quarterly overlapping windows
from 2001), each trade pulled towards the fit pooled over all trades (prior worth 40 windows, scripts.ops.panel_backtest
.shrunk_fit). The slopes are the what-ifs: "materials +10% over a year -> this trade +b_m x 10%".
Check (conditional, costs known): sensitivities fitted on windows ending before 2013 predict 2013+ annual trade changes
from the actual cost changes; compared with the single 60/40 rule of src.quant.cost_model and the pooled fit.
Writes data/<pilot>/reports/trade-cost-sensitivity-latest.json.

Usage: python -m scripts.ops.trade_cost_sensitivity
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from scripts.ops.cost_scenarios import LABOUR, MATERIAL
from scripts.ops.hierarchy_test import trade_weights
from scripts.ops.panel_backtest import shrunk_fit
from scripts.ops.project_series import quarterly_levels
from src.config import REPO_ROOT, get_settings
from src.quant.cost_model import WEIGHT
from src.quant.forecasting import _quarter_index
from src.storage.dual_layer_store import DualLayerStore

SPLIT = 2013 * 4
PRIOR_WINDOWS = 40


def logs(levels: list) -> dict:
    return {_quarter_index(r["period_start"]): 100 * math.log(r["value"]) for r in levels if r.get("value")}


def windows(trade: dict, m: dict, lab: dict, until: int | None = None) -> tuple:
    """Rows [1, material yoy, labour yoy] and trade yoy for windows ending at q (q < until)."""
    x, y, ends = [], [], []
    for q in sorted(trade):
        if all(k in d for d in (trade, m, lab) for k in (q, q - 4)) and (until is None or q < until):
            x.append([1.0, m[q] - m[q - 4], lab[q] - lab[q - 4]])
            y.append(trade[q] - trade[q - 4])
            ends.append(q)
    return np.array(x), np.array(y), ends


def fit_all(trades: dict, m: dict, lab: dict, until: int | None = None) -> tuple:
    data = {sid: windows(t, m, lab, until) for sid, t in trades.items()}
    data = {sid: d for sid, d in data.items() if len(d[1]) >= 12}
    pooled = np.linalg.lstsq(np.vstack([d[0] for d in data.values()]), np.concatenate([d[1] for d in data.values()]), rcond=None)[0]
    return pooled, {sid: shrunk_fit(d[0], d[1], pooled, PRIOR_WINDOWS) for sid, d in data.items()}


def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    revisions = store.query_observation_revisions(now, "system_as_of")
    weights = trade_weights(store, now, names)
    trades = {sid: logs(quarterly_levels(revisions, sid, None, store)) for sid in weights}
    m, lab = logs(quarterly_levels(revisions, MATERIAL, None, store)), logs(quarterly_levels(revisions, LABOUR, None, store))
    # Conditional check: fit before 2013, predict 2013+ windows from the actual cost changes.
    pooled_dev, own_dev = fit_all(trades, m, lab, until=SPLIT)
    errors = {"trade_specific": [], "pooled": [], "rule_60_40": []}
    for sid, coef in own_dev.items():
        x, y, ends = windows(trades[sid], m, lab)
        test = [i for i, q in enumerate(ends) if q >= SPLIT]
        rule = np.array([0.0, WEIGHT, 1 - WEIGHT])
        for name, c in (("trade_specific", coef), ("pooled", pooled_dev), ("rule_60_40", rule)):
            errors[name].append(float(np.mean(np.abs(y[test] - x[test] @ c))))
    check = {name: float(np.mean(v)) for name, v in errors.items()}
    check["trades_better_than_pooled"] = int(sum(a < b for a, b in zip(errors["trade_specific"], errors["pooled"])))
    check["trades"] = len(errors["pooled"])
    pooled, own = fit_all(trades, m, lab)
    rows = sorted(({"series_id": sid, "name": names[sid]["name"].replace("Construction prices excl. VAT: ", ""),
                    "weight": weights[sid], "material": float(c[1]), "labour": float(c[2])} for sid, c in own.items()),
                  key=lambda r: -r["material"])
    out = Path(get_settings().data_dir) / "reports" / "trade-cost-sensitivity-latest.json"
    out.write_text(json.dumps({"generated_at": now.isoformat(), "method": __doc__.split("\n\n")[1].replace("\n", " "),
                               "pooled": {"material": float(pooled[1]), "labour": float(pooled[2])},
                               "conditional_check_2013": check, "trades": rows}, indent=1), encoding="utf-8")
    print(json.dumps({"pooled": [round(float(v), 3) for v in pooled], "check": check}, indent=1))
    for r in rows[:5] + rows[-5:]:
        print(f"{r['name'][:45]:45} material {r['material']:.2f} labour {r['labour']:.2f}")


if __name__ == "__main__":
    main()
