# Session: 2026-10-18 — Task 4 review

- Goal: Review the other session's Task 4 implementation and provide a repair prompt, without editing product code.
- Outcome: Existing runner/archive tests passed (8). An isolated review script reproduced 16 defects involving polling/replay, recovery/cursor, manifest hashes, archive settings, source/parser admission, lease races, budget enforcement/state, discovery limits, secret persistence and HTTP cleanup.
- Files changed: Added `docs/superpowers/reviews/2026-10-18-task-4-fix-prompt.md`, this entry, and an ignored local probe at `data/reports/task4_review_probe.py`; updated current-work follow-up.
- Validation: `python -m pytest tests/test_ingestion_runner.py tests/test_object_storage.py -q -p no:cacheprovider --tb=short` — 8 passed. `python -m data.reports.task4_review_probe` — all 16 defect reproductions confirmed; temporary databases, fake HTTP/SDK only.
- Decisions: Level 2 — Task 4 acceptance is incomplete despite its passing focused tests. Product code unchanged in this review.
- Unresolved issues: The repair prompt lists required recovery, writer, archive, budget and validation corrections. Remote S3 behavior was inspected but not live-tested; full suite not rerun for this documentation-only review.
- Next action: Have the implementing session repair Task 4 using the prompt, then rerun focused and shared-contract verification.
