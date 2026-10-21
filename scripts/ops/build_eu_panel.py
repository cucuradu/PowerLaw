"""Write the EU country panel into config: the same core construction series for every EU member state.

Eurostat publishes each dataset for all countries in one table, so the panel is a list of templates times a list of
countries. This script expands them into EUROSTAT_STS context series (config/data_sources.json) and display names /
graph mappings (config/series_names.json). Existing entries are never renamed or removed; rerun after editing the lists.

Each country's series measure the same sector nodes as Germany's (sec_F, sec_C23, ...); the country is the series'
geography, not a node. A graph model builds (country, sector) nodes from that.

Usage: python -m scripts.ops.build_eu_panel [--fixture-dir tests/fixtures/ingestion]
"""
import hashlib
import json
from pathlib import Path

from src.config import REPO_ROOT

EU27 = {"AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany",
        "DK": "Denmark", "EE": "Estonia", "EL": "Greece", "ES": "Spain", "FI": "Finland", "FR": "France",
        "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LT": "Lithuania", "LU": "Luxembourg",
        "LV": "Latvia", "MT": "Malta", "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
        "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia"}

# slug, dataset, dimensions (geo added per country), measurement kind, frequency, name, measures
TEMPLATES = [
    ("new_home_prices", "sts_copi_q", {"freq": "Q", "indic_bt": "PRC_PRR", "cpa2_1": "CPA_F41001_X_410014", "s_adj": "NSA", "unit": "I21"},
     "PRODUCER_PRICE_INDEX", "QUARTERLY", "construction prices, new homes", {"sector": "F", "metric": "output_price"}),
    ("new_home_costs", "sts_copi_q", {"freq": "Q", "indic_bt": "COST", "cpa2_1": "CPA_F41001_X_410014", "s_adj": "NSA", "unit": "I21"},
     "CONSTRUCTION_INPUT_COST_INDEX", "QUARTERLY", "building costs, new homes", {"sector": "F", "metric": "input_cost"}),
    ("labour_cost", "lc_lci_r2_q", {"freq": "Q", "nace_r2": "F", "lcstruct": "D1_D4_MD5", "s_adj": "NSA", "unit": "I20"},
     "LABOUR_COST_INDEX", "QUARTERLY", "labour cost in construction", {"sector": "D1", "metric": "labour_cost"}),
    ("wood_prices", "sts_inpp_m", {"freq": "M", "indic_bt": "PRC_PRR", "nace_r2": "C16", "s_adj": "NSA", "unit": "I21"},
     "PRODUCER_PRICE_INDEX", "MONTHLY", "producer prices, wood products", {"sector": "C16", "metric": "producer_price"}),
    ("mineral_prices", "sts_inpp_m", {"freq": "M", "indic_bt": "PRC_PRR", "nace_r2": "C23", "s_adj": "NSA", "unit": "I21"},
     "PRODUCER_PRICE_INDEX", "MONTHLY", "producer prices, cement, concrete, bricks, glass", {"sector": "C23", "metric": "producer_price"}),
    ("metal_prices", "sts_inpp_m", {"freq": "M", "indic_bt": "PRC_PRR", "nace_r2": "C24", "s_adj": "NSA", "unit": "I21"},
     "PRODUCER_PRICE_INDEX", "MONTHLY", "producer prices, basic metals", {"sector": "C24", "metric": "producer_price"}),
    ("energy_prices", "sts_inpp_m", {"freq": "M", "indic_bt": "PRC_PRR", "nace_r2": "D35", "s_adj": "NSA", "unit": "I21"},
     "PRODUCER_PRICE_INDEX", "MONTHLY", "producer prices, electricity and gas", {"sector": "D", "metric": "producer_price"}),
    ("price_expectations", "ei_bsbu_m_r2", {"freq": "M", "indic": "BS-CPE-BAL", "s_adj": "NSA"},
     "SURVEY_PRICE_EXPECTATIONS", "MONTHLY", "builders' price expectations (survey)", {"sector": "F", "metric": "price_expectations"}),
    ("material_shortage", "ei_bsbu_m_r2", {"freq": "M", "indic": "BS-FLBA5-PC", "s_adj": "NSA"},
     "SURVEY_MATERIAL_SHORTAGE", "MONTHLY", "builders short of materials (survey)", {"sector": "F", "metric": "material_shortage"}),
]
FIXTURE = "tests/fixtures/ingestion/eurostat_{dataset}_eu27_2025_official.json"


def series_key(dataset: str, dims: dict) -> str:
    return f"EUROSTAT_STS|{dataset}|" + "|".join(f"{k}={dims[k]}" for k in sorted(dims))


def main() -> None:
    sources_path, names_path = REPO_ROOT / "config" / "data_sources.json", REPO_ROOT / "config" / "series_names.json"
    sources, names = (json.loads(p.read_text(encoding="utf-8")) for p in (sources_path, names_path))
    eurostat = sources["sources"]["EUROSTAT_STS"]
    for dataset in sorted({t[1] for t in TEMPLATES} - set(eurostat["series_keys"].values())):
        eurostat["series_keys"][f"eu_panel_{dataset}"] = dataset
    # Keys already registered under another name (Germany's survey series, the target cross-check) stay as they are.
    existing = {s["provider_series_key"] for name, s in eurostat["context_series"].items() if not name.startswith("eu_")}
    existing.add(series_key(sources["primary_target"]["eurostat_crosscheck"]["dataset_id"],
                            sources["primary_target"]["eurostat_crosscheck"]["dimensions"]))
    added = 0
    for slug, dataset, base, kind, freq, label, measures in TEMPLATES:
        fixture = REPO_ROOT / FIXTURE.format(dataset=dataset)
        sha = hashlib.sha256(fixture.read_bytes()).hexdigest() if fixture.exists() else None
        for geo, country in EU27.items():
            dims = {**base, "geo": geo}
            key = series_key(dataset, dims)
            names["series"].setdefault(key, {"name": f"{country}: {label}", "status": "active", "measures": measures})
            if key in existing:
                continue
            eurostat["context_series"][f"eu_{slug}_{geo.lower()}"] = {
                "table_id": dataset, "provider_series_key": key, "dimensions": dims, "panel": True,
                "provider_label": f"{country}: {label}", "measurement_kind": kind, "frequency": freq,
                "fixture_path": FIXTURE.format(dataset=dataset), "fixture_sha256": sha,
                "verified_scope": "EU country panel (scripts/ops/build_eu_panel.py); coverage checked live 2026-10-20, "
                                  "countries without data return no rows."}
            added += 1
    for path, data in ((sources_path, sources), (names_path, names)):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"added_context_series": added}))


if __name__ == "__main__":
    main()
