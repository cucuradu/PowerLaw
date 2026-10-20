# 2026-10-20 Dashboard performance and News Feed fix

## Goal
Review the performance report against the code, implement the supported changes, and fix the News Feed Altair `receptor_signal` failure.

## Outcome
- Production law, news, and outlook readers reuse the existing generation-aware graph store instead of constructing separate canonical stores.
- The main dashboard caches derived read-only data for 60 seconds, keyed by storage generation, and no longer loads unused law/news collections.
- The GNN page defers PyTorch-backed checkpoint and full-export imports until the user requests them.
- PyVis stabilization was reduced from 150 to 50 iterations.
- News normalization preserves a nullable `receptor_signal`, and the Altair tooltip declares it nominal so missing values render safely.

## Files changed
`dashboard.py`, `pages/2_📰_News_Feed.py`, `pages/3_🕸️_Graph_Explorer.py`, `pages/5_🤖_GNN_Modeling_Lab.py`, `src/storage/dashboard_data.py`, `tests/test_dashboard_suite.py`.

## Validation
- Regression test was observed failing with the reported Altair `ValueError`, then passing after the fix.
- `python -m pytest tests/test_dashboard_suite.py tests/test_canonical_acceptance.py -q`: 29 passed, 5 warnings.
- `python -m pytest -q`: 574 passed, 28 subtests passed, 11 warnings.
- Production timing probe: first graph load 15.997 s; cached graph lookup 0.001 s; law rows 0.159 s; news rows 0.325 s after store reuse.

## Decisions
- Reused the existing disk-generation-aware singleton instead of adding a second resource cache that could hide committed store updates.
- Cached only derived dashboard data, with a 60-second TTL and generation key, to preserve point-in-time freshness.

## Unresolved issues
- The production construction-outlook cold query did not finish within the timing capture window. The new cache removes repeated interaction cost but does not reduce that first calculation.
- Initial render of the deferred GNN page is covered; the optional checkpoint-load and full-export button paths were not automated in this session.

## Next action
Profile `get_construction_outlook()` separately if cold dashboard startup remains unacceptable.
