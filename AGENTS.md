# PowerLaw Agent Guide

## Instruction Contract

- This file is the canonical project instruction set. `CLAUDE.md` is a compatibility bridge for Claude-based agents and must not duplicate or contradict it.
- Follow platform, user, and repository instructions in that order. When instructions conflict or a requested change is destructive, stop and ask for clarification.
- Before editing, inspect the relevant file and nearby test. Preserve unrelated working-tree changes; never reset, checkout, or overwrite user work.
- State assumptions when evidence is incomplete. Do not claim a test, command, source, or behavior was checked unless it was actually checked in this session.
- Treat credentials, tokens, private data, and raw chat transcripts as sensitive. Never place them in source, committed memory, logs, or session summaries.

## Project Shape

PowerLaw is a Python and Streamlit platform for evidence-backed EU law, construction economics, supply-chain events, temporal data, and experimental GNN forecasting.

- `dashboard.py` and `pages/` are the read-only Streamlit UI.
- `src/scrapers/` contains source adapters and parsers.
- `src/schemas/` defines canonical typed data contracts.
- `src/storage/` owns persistence, committed batches, graph projections, and as-of queries.
- `src/gnn/` and `src/models/` own dataset export and model training.
- `src/quant/` contains quantitative and macro models.
- `scripts/ops/` contains maintenance and ingestion operations; `scripts/debug/` contains diagnostics.
- `tests/` contains the executable contract for the dashboard, ingestion, storage, schemas, graph exports, and models.

Keep dependencies flowing from adapters to ingestion/storage, then to as-of projections, datasets/models, and UI. Streamlit pages must not ingest, seed, train, or mutate production data. Models must not import scrapers.

## Setup And Checks

From the repository root:

```powershell
python -m pip install -r requirements.txt
pytest
streamlit run dashboard.py
```

Use `python -m pytest <test-path> -q` for a focused check. There is no repository lint or type-check configuration; do not invent one as part of an unrelated change. Keep credentials in a private `.env`; use the README's ADC instructions for Vertex AI and never commit secrets.

## Data And Storage Rules

- Respect `POWERLAW_MODE`, `POWERLAW_DATA_DIR`, `LANCEDB_PATH`, and `POWERLAW_SQLITE_PATH`. Demo, test, and production origins must remain explicitly separated.
- Treat `data/production/lancedb` and its SQLite ingestion ledger as the planned production store. Do not silently mix it with legacy/demo `data/lancedb` or `storage/lancedb`.
- Canonical facts and observations are append-only and versioned. Graph, dashboard, search, and vector tables are rebuildable projections.
- Production reads must use committed batches and point-in-time/as-of availability and validity filters. Preserve evidence, revisions, vintages, and supersession links.
- Unknown values stay null with an explicit reason. Never substitute zero, today, 1900, 1970, or synthetic production data.
- Use stable canonical natural keys or SHA-256 identities, never Python `hash()` for persisted IDs.
- Only one ingestion writer is supported. Preserve the storage lock, SQLite ledger, staged-batch validation, and recovery behavior.
- Every tracked source must be free and rights-checked, with no paid or paywalled fallback. Keep source-specific rights and attribution metadata.
- The primary target is Germany's residential construction producer-price index. Do not silently substitute material PPI, input-cost, house-price, CPI, or equity-price data.

## Change Practice

Before editing, find the owning abstraction and a nearby test. Add or update the narrow regression test first for changes to persistence, temporal eligibility, ingestion identity, source parsing, or demo isolation. Run the smallest affected test, then the full suite when shared contracts change.

Prefer existing Pydantic, Arrow/PyArrow, LanceDB, SQLite, MinIO, and API-wrapper patterns. Do not add Neo4j, another vector database, a workflow platform, or a second source of truth for the pilot without an explicit design decision. Never use `eval()` on imported metadata; JSON is the canonical format and a bounded `ast.literal_eval()` migration is the maximum legacy exception.

## Agent Memory Archive

Project memory is stored in `docs/agent-memory/`. Treat it as the durable, Git-tracked record for this repository; VS Code chat history remains a recovery aid, not the source of truth.

- Read `docs/agent-memory/current-work.md` before starting substantial work.
- Record stable architectural choices in `docs/agent-memory/decisions.md`.
- After a meaningful session, add one file under `docs/agent-memory/session-log/` named `YYYY-MM-DD-short-topic.md`.
- Keep session entries concise and factual. Do not copy full transcripts, secrets, tokens, private user data, or generated noise.
- Update `current-work.md` only when active priorities, blockers, or next actions change.
- A session entry must include: goal, outcome, files changed, validation run, decisions, unresolved issues, and next action.

### Memory Quality Scale

Use the highest level supported by evidence; never fill gaps with guesses.

| Level | Meaning | Required evidence |
| --- | --- | --- |
| 0 - Omit | Not useful or not verified | Do not record it |
| 1 - Note | A useful observation | State the source or affected file |
| 2 - Verified | Confirmed project fact or completed change | Include the command, test, or code path that confirms it |
| 3 - Durable | A reusable rule or decision | Include rationale, scope, and the consequence for future work |

At session close, write the summary at Level 2 by default. Use Level 3 only for decisions that will remain true across tasks. Mark uncertain items as `Open question` rather than presenting them as facts.

### Session Close Checklist

Before ending a meaningful session:

1. Record the outcome and the exact files changed.
2. Record the narrow validation command and its result, or explain why validation was unavailable.
3. Separate verified facts, durable decisions, and open questions.
4. Record the next action only when there is a real follow-up.

Agents that do not support automatic session export must still write this concise Markdown record manually. `AGENTS.md` cannot force an external agent or host integration to load it; integrations must be configured to include this file.

### Cross-Agent Skill Availability

The VS Code user setting `chat.pluginLocations` registers the installed Superpowers, Caveman, and Ponytail collections for Copilot. It does not install or configure those plugins for Claude Code, Gemini, Codex CLI, or other hosts. Those hosts may read this repository's instruction bridges, but each host must discover or install its own skill catalog.

## Documentation Map

Use the [README](README.md) for setup, entry points, and the repository map. Read the [technical architecture](docs/superpowers/specs/2026-10-18-technical-architecture.md) before changing schemas, storage, graph projections, or GNN tensors. Read the [data ingestion design](docs/superpowers/specs/2026-10-18-data-ingestion-design.md) before changing source acquisition, evidence, timestamps, retries, budgets, or publication. The [implementation plan](docs/superpowers/plans/2026-10-18-data-ingestion.md) records task order and proof obligations. The [free-source and readiness spec](docs/superpowers/specs/2026-10-18-free-sources-and-ingestion-readiness.md) governs source eligibility and operational gates.