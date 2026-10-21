"""Does the EU country panel help? Compare one-model-per-country against one model pooled over all countries.

Target: next quarter's growth of each country's new-home construction price index (Eurostat sts_copi_q, PRC_PRR).
Models, all OLS refitted at every origin on quarters before it (expanding window, origins from --since):
  own_ar      per country: own growth lags 1-4 + season
  own_drivers per country: own_ar + the country's cost drivers (materials, energy, labour, survey) at lag 1
  pooled_ar   all countries: own_ar terms with shared coefficients + a country intercept
  pooled_drivers  pooled_ar + the same drivers + EU-average price growth at lag 1
  partial     per country, own_drivers + EU average, shrunk towards the pooled_drivers coefficients: the pooled fit
              counts as PRIOR_QUARTERS extra quarters of data (a hierarchical model in its simplest form)
Every model is scored on the same country-quarters (those where all inputs exist), so the comparison is like for like.
Release timing is ignored (all inputs at lag 1 quarter): this compares models, it is not a live forecast. Report:
data/<pilot>/reports/panel-backtest-latest.json.

Usage: python -m scripts.ops.panel_backtest [--since 2010]
"""
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats

from scripts.ops.estimate_links import quarterly_growth
from src.config import get_settings, load_data_sources_registry
from src.storage.dual_layer_store import DualLayerStore

DRIVERS = ["new_home_costs", "labour_cost", "wood_prices", "mineral_prices", "metal_prices", "energy_prices",
           "price_expectations", "material_shortage"]
PRIOR_QUARTERS = 40  # ponytail: fixed shrinkage strength; tune on pre-2010 origins if it matters
LEVEL_DRIVERS = {"price_expectations", "material_shortage"}  # survey balances: use the level, not its growth


def panel_series() -> dict:
    """{(slug, geo): series_id} for the EU panel, Germany's survey and cross-check series included."""
    registry = load_data_sources_registry()
    out = {}
    for name, s in registry["sources"]["EUROSTAT_STS"]["context_series"].items():
        geo = s["dimensions"].get("geo")
        if name.startswith("eu_"):
            out[(name[3:].rsplit("_", 1)[0], geo)] = s["provider_series_key"]
    crosscheck = registry["primary_target"]["eurostat_crosscheck"]
    dims = crosscheck["dimensions"]
    out[("new_home_prices", "DE")] = f"EUROSTAT_STS|{crosscheck['dataset_id']}|" + "|".join(f"{k}={dims[k]}" for k in sorted(dims))
    out[("price_expectations", "DE")] = "EUROSTAT_STS|ei_bsbu_m_r2|freq=M|geo=DE|indic=BS-CPE-BAL|s_adj=NSA"
    out[("material_shortage", "DE")] = "EUROSTAT_STS|ei_bsbu_m_r2|freq=M|geo=DE|indic=BS-FLBA5-PC|s_adj=NSA"
    return out


def quarterly_level(levels: dict) -> dict:
    quarters = defaultdict(list)
    for period, value in levels.items():
        quarters[(int(period[:4]), (int(period[5:7]) - 1) // 3)].append(value)
    return {q: float(np.mean(v)) for q, v in quarters.items() if len(v) in (1, 3)}


def lag(q, k):
    return q[0] + (q[1] - k) // 4, (q[1] - k) % 4


def build_rows(data: dict) -> list[dict]:
    """One row per (country, quarter) with the target and every candidate input."""
    eu_avg = defaultdict(list)
    for (slug, geo), series in data.items():
        if slug == "new_home_prices":
            for q, v in series.items():
                eu_avg[q].append(v)
    eu_avg = {q: float(np.mean(v)) for q, v in eu_avg.items() if len(v) >= 10}
    rows = []
    for (slug, geo), y in data.items():
        if slug != "new_home_prices":
            continue
        for q in sorted(y):
            ar = [y.get(lag(q, k)) for k in range(1, 5)]
            drivers = [data.get((d, geo), {}).get(lag(q, 1)) for d in DRIVERS]
            rows.append({"geo": geo, "q": q, "y": y[q], "ar": ar, "drivers": drivers, "eu": eu_avg.get(lag(q, 1))})
    return rows


def design(rows, geos, pooled: bool, drivers: bool):
    cols = []
    for r in rows:
        x = [1.0] + r["ar"] + [float(r["q"][1] == s) for s in (1, 2, 3)]
        if pooled:
            x += [float(r["geo"] == g) for g in geos[1:]]
        if drivers:
            x += r["drivers"] + [r["eu"]]
        cols.append(x)
    return np.array(cols, dtype=float)


def shrunk_fit(X: np.ndarray, y: np.ndarray, prior: np.ndarray, quarters: float) -> np.ndarray:
    """OLS pulled towards `prior` as if the prior had been fitted on `quarters` extra quarters (scale-free per column)."""
    penalty = np.diag(quarters * np.maximum(np.diag(X.T @ X) / len(y), 1e-6))
    return np.linalg.solve(X.T @ X + penalty, X.T @ y + penalty @ prior)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=int, default=2010)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    ids = panel_series()
    raw = defaultdict(dict)
    wanted = {sid: key for key, sid in ids.items()}
    for row in DualLayerStore().query_observations(cutoff=now, mode="public_as_of").to_pylist():
        if row["series_id"] in wanted and row["value"] is not None:
            raw[wanted[row["series_id"]]][row["period_start"][:7]] = row["value"]
    data = {key: (quarterly_level(levels) if key[0] in LEVEL_DRIVERS else quarterly_growth(levels)) for key, levels in raw.items()}
    rows = [r for r in build_rows(data) if None not in r["ar"] and None not in r["drivers"] and r["eu"] is not None
            and abs(r["y"]) < 50]  # ponytail: drops a handful of rebasing/hyperinflation outliers, list them if it matters
    geos = sorted({r["geo"] for r in rows})
    origins = sorted({r["q"] for r in rows if r["q"][0] >= args.since})
    models = {"own_ar": (False, False), "own_drivers": (False, True), "pooled_ar": (True, False), "pooled_drivers": (True, True),
              "partial": (None, True)}
    n_dummies = len(geos) - 1
    errors = {m: [] for m in models}
    keys = []
    for origin in origins:
        train = [r for r in rows if r["q"] < origin]
        test = [r for r in rows if r["q"] == origin]
        preds = {}
        for name, (pooled, drivers) in models.items():
            if pooled is None:  # partial pooling around the pooled fit computed just before
                out = []
                for r in test:
                    own = [t for t in train if t["geo"] == r["geo"]]
                    g = geos.index(r["geo"])
                    prior = np.concatenate([[pooled_beta[0] + (pooled_beta[8 + g - 1] if g else 0)], pooled_beta[1:8],
                                            pooled_beta[8 + n_dummies:]])
                    if not own:
                        x = design([r], geos, False, True)[0]
                        out.append(float(x @ prior))
                        continue
                    beta = shrunk_fit(design(own, geos, False, True), np.array([t["y"] for t in own]), prior, PRIOR_QUARTERS)
                    out.append(float(design([r], geos, False, True)[0] @ beta))
                preds[name] = np.array(out)
                continue
            if pooled:
                X, y = design(train, geos, True, drivers), np.array([r["y"] for r in train])
                beta = np.linalg.lstsq(X, y, rcond=None)[0]
                if drivers:
                    pooled_beta = beta
                preds[name] = design(test, geos, True, drivers) @ beta
            else:
                out = []
                for r in test:
                    own = [t for t in train if t["geo"] == r["geo"]]
                    width = 8 + (len(DRIVERS) + 1 if drivers else 0)
                    if len(own) < width + 8:  # too short to fit: fall back to the country's mean growth
                        out.append(float(np.mean([t["y"] for t in own])) if own else 0.0)
                        continue
                    beta = np.linalg.lstsq(design(own, geos, False, drivers), np.array([t["y"] for t in own]), rcond=None)[0]
                    out.append(float((design([r], geos, False, drivers) @ beta)[0]))
                preds[name] = np.array(out)
        for name in models:
            errors[name].extend(np.abs(preds[name] - np.array([r["y"] for r in test])).tolist())
        keys.extend((r["geo"], f"{r['q'][0]}-Q{r['q'][1] + 1}") for r in test)
    err = {m: np.array(v) for m, v in errors.items()}
    by_geo = defaultdict(lambda: defaultdict(list))
    for i, (geo, _) in enumerate(keys):
        for m in models:
            by_geo[geo][m].append(err[m][i])

    def dm(a, b):  # paired test on absolute errors, quarters clustered by averaging across countries first
        quarters = defaultdict(list)
        for i, (_, q) in enumerate(keys):
            quarters[q].append(err[a][i] - err[b][i])
        d = np.array([np.mean(v) for v in quarters.values()])
        return float(stats.ttest_1samp(d, 0).pvalue)

    report = {
        "generated_at": now.isoformat(), "since": args.since, "countries": geos, "country_quarters": len(keys),
        "mae": {m: round(float(v.mean()), 3) for m, v in err.items()},
        "p_value_vs_own_ar": {m: round(dm(m, "own_ar"), 4) for m in models if m != "own_ar"},
        "mae_by_country": {g: {m: round(float(np.mean(v)), 3) for m, v in ms.items()} for g, ms in sorted(by_geo.items())},
        "countries_where_partial_beats_own_ar": int(sum(np.mean(ms["partial"]) < np.mean(ms["own_ar"]) for ms in by_geo.values())),
        "method": __doc__.split("\n\n")[1].replace("\n", " "),
    }
    out = Path(get_settings().data_dir) / "reports" / "panel-backtest-latest.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("country_quarters", "mae", "p_value_vs_own_ar", "countries_where_partial_beats_own_ar")}
                     | {"DE": report["mae_by_country"].get("DE"), "output": str(out)}))


if __name__ == "__main__":
    main()
