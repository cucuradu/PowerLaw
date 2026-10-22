# Research scripts

One-off tests behind results recorded in `docs/agent-memory/decisions.md`. They read the pilot store and print
results; they are not part of the pipeline and write nothing. Run from the repository root:

    python -m scripts.research.cost_ceiling      # "Cost build-up: prices follow costs"
    python -m scripts.research.global_horizon    # "EU-wide 1-3 year model for the German target"
    python -m scripts.research.hierarchy_h4      # "Index hierarchy: adding up the trades" (1 year)
