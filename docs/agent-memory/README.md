# Agent Memory Archive

This directory is the project-local memory archive for PowerLaw. It is deliberately concise and Git-friendly.

## Files

- `current-work.md`: active priorities, blockers, and next actions.
- `decisions.md`: durable architectural and workflow decisions.
- `session-log/`: one short record per meaningful agent session.

## Session Record

Create `session-log/YYYY-MM-DD-short-topic.md` with this shape:

```markdown
# Session: YYYY-MM-DD - Topic

## Goal

## Outcome

## Files Changed

- `path/to/file`

## Validation

- `command` - result

## Decisions

- Level 2 or Level 3: decision and evidence.

## Unresolved Issues

- None, or a specific open question.

## Next Action

```

Do not store raw chat transcripts here. Keep those, when needed, in a separate private local archive outside the repository, such as `D:\AgentSessions\PowerLaw\`.