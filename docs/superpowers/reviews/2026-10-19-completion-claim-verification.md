# Verification of the software-complete claim

Date: 2026-10-19. Scope: assess the other agent's statement that only internet/API setup, expert review and server deployment remain. Review only: no product fixes, package installs, live calls, account changes or deployment.

**Decision: the claim is too broad. The two latest test-fixture errors are fixed, but the substantive C1-C8 findings in the previous review remain valid.** Passing the existing suite is not proof that those acceptance requirements are implemented correctly. These failures reproduce offline without new credentials.

## What changed and what was verified

Compared SHA-256 hashes against `data/reports/recheck-2026-10-19-pass2/closing/source-hashes-before.json`. Only `tests/test_task3_durability_regressions.py` changed among the recorded code/tests/configuration; no new Python files were added under src/tests/pages/scripts. None of the production files responsible for C1-C8 changed. The fixes to the factory timestamp argument and metric endpoint fixture are present.

Fresh targeted run: **12 passed in 19.79s** (20.27s harness), covering all ten durability tests and the two changed UI page renders. Source hashes remained unchanged during and after the run. The previous two fixture failures are closed; they should not be requested again.

The other agent records **375 passed** in its completion log. That full run was not independently repeated in this turn. My previous broad selected pass was369passed/3skipped/2deselected, plus the separately excluded live smoke. The fresh counterexamples below are sufficient to reject end-to-end completion even if every currently selected pytest case passes.

Fresh offline rechecks completed:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m data.reports.completion-check-2026-10-19.run_closing
.\.venv\Scripts\python.exe -m data.reports.completion-check-2026-10-19.probe_remaining
.\.venv\Scripts\python.exe -m data.reports.completion-check-2026-10-19.observe_forecast
.\.venv\Scripts\python.exe -m data.reports.completion-check-2026-10-19.check_sources_checkpoint
```

Harnesses use isolated test paths, local archive, mocked provider responses and blocked external network. The observational forecast harness records changed outcomes; its exit0 is not a claim that the observed failures passed acceptance. Source-probe hashes were stable. Python3.12.13/torch2.14.1 remain installed; torch-geometric is absent. GPU hardware is not a prerequisite for testing correct dataset eligibility or running small real PyG CPU integration checks; those must not be replaced by shims and labelled real model verification.

## Fresh counterexamples

| Requirement | Observed result | Why account setup cannot resolve it |
| --- | --- | --- |
| Eligible training labels | A staged batch with zero canonical query rows produces a50% label; a retracted release also produces50%. | Dataset construction scans records without correct commit/current-revision selection. |
| Release-aware evaluation | Monthly bounds and `default` vintage produce a quarterly label; lag evaluation uses a target before its release. | Training/evaluation selection logic is wrong. |
| Honest uncertainty |21daily observations in one quarter produce a calibrated interval claiming20samples. | Counting period-start dates is not held-out quarterly calibration. |
| Eligible outlook context | A retracted unexpired rule is ACTIVE despite zero canonical edges. A staged document reappears when another committed document activates fallback. | Outlook bypasses canonical latest-revision/evidence selection. |
| Production data isolation | An accepted live observation can use committed demo evidence. An uncommitted rule chunk under a legitimate law and loose live-labelled news JSON are displayed. | Origin labels and law-level allowlists are insufficient. |
| Source-grounded legal duties | Passport inspection becomes passport creation; CBAM document formatting becomes declaration/certificate duties. Both complete registered fixture runs commit accepted obligations. | Mechanism/scope grounding needs implementation and expert validation. |
| Correct statistical metadata | Eurostat construction definition has geography`unit=I21` and dimension geo`freq=Q`. | The adapter misparses its own named dimension format. |
| Bounded usage and uncertainty | A mocked request settles0.501against0.45budget; two response-loss exceptions settle estimates and authorize two dispatches. | These are local authorization/accounting errors, not billing configuration. |
| Portable bounded restore | Cold test-origin restore works, but retains source-archive raw URIs. With117free/floor100/raw16, restore reports success while writing6508bytes. | Raw reference portability and complete allocation accounting need repair. |

The normal live manifest origin mismatch, provider-specific acquisition contracts, per-article provenance, pilot caps/retry/maturity gates and dataset/GNN architecture gaps remain described in [C1-C8](2026-10-19-tasks-1-11-recheck2.md). Their production implementations are unchanged. They are not reclassified as credentials-only work. The previously repaired evidence/type/early-source, queue/UI, percentage/PDF, same-run lease and test-origin restore controls remain positive evidence.

## Next step

1. **Make C1-C8 executable acceptance regressions and repair the responsible code.** Start with canonical data isolation and label/revision/release selection, then honest forecast/calibration and source semantics. Keep spending and restore controls closed before operational expansion. Use the [existing remaining-repair instructions](2026-10-19-tasks-1-11-recheck2-fix-prompt.md), starting at C1; its two earlier test repairs are now closed.
2. **Run a small local Germany pilot after the software gates pass.** Use bounded real acquisition, verify source identity/units/timestamps/archive links, and get the required held-out legal/news review. Keep real extraction costs disabled until authorization is proved. No dedicated server or new credentials are needed to repair the offline defects above.
3. **Establish forecasting evidence before daily operation.** Build reproducible eligible datasets, compare release-aware baselines, test actual PyG if retained, and publish a versioned forecast artifact with honest calibration or an explicit forecast gap. Collect and refresh data as needed; the quarterly construction target retains its quarterly horizon.
4. **Deploy ongoing operation after recovery and pilot acceptance.** Verify production-origin cold restore with the source archive unavailable, bounded storage, queue/schedule behavior and the required operating record. Server deployment follows that proof; it does not supply missing model correctness.

Keep the project local while these checks are being completed, consistent with the user's preference when storage stays below10GB. No hosting product or paid service was selected by this review.

Evidence: `data/reports/completion-check-2026-10-19/targeted/`, `remaining-results.json`, `forecast/probe_results.json`, `sources/admission-results.json`, `sources/probe-results.json`, `sources/checkpoint-hashes.json`. No live provider, billing/credits, remote privacy, real heterogeneous training, browser/button or installed-schedule check was performed.
