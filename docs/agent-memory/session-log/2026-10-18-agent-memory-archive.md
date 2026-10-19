# Session: 2026-10-18 - Agent Memory Archive

## Goal

Create a durable local archive structure for PowerLaw agent sessions and define a quality scale for recorded memory.

## Outcome

Added a Git-tracked Markdown archive under `docs/agent-memory/` and documented the workflow in `AGENTS.md`.

## Files Changed

- `AGENTS.md`
- `docs/agent-memory/README.md`
- `docs/agent-memory/current-work.md`
- `docs/agent-memory/decisions.md`
- `docs/agent-memory/session-log/README.md`

## Validation

- Checked all archive files exist.
- Ran `git diff --check -- AGENTS.md docs/agent-memory` successfully.

## Decisions

- Level 3 - Durable: Keep concise project memory in the repository, while keeping raw transcripts in a separate private local archive. This makes durable knowledge reviewable and versioned without committing private conversation content.
- Level 2 - Verified: Require goal, outcome, files changed, validation, decisions, unresolved issues, and next action in each meaningful session record.

## Unresolved Issues

- Raw transcript export from VS Code is not automated by this repository workflow.

## Next Action

At the end of each meaningful session, add a dated summary using the template in `docs/agent-memory/README.md`.
