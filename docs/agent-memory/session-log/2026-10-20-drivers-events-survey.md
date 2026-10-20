# 2026-10-20 Forecast drivers, price events, builder survey

## Goal
User asked whether the neural network understands the drivers and whether news/events should explain price spikes; approved: fix the graph, add an event timeline, add the builder survey and energy prices, keep the GNN only if it wins.

## Outcome
- Graph Explorer: new "Forecast drivers" view (target, used vs tested inputs, VAT, dated events as timing only); stored view with plain names, retired series hidden, news events labelled from their extracted text.
- Home: quarterly price-change chart with event markers; table of the model's biggest past misses with events in the six months before (e.g. 2021-Q2 with the timber/steel surge, 2022-Q1/Q2 with the Ukraine energy shock).
- New inputs: EU construction survey (price expectations, material shortages; Eurostat, 1980+) and energy producer prices (Destatis 61241 GP19-35, dated from archived Bundesbank vintages). Price expectations helped most; material shortages hurt. New main model: see decisions.md.
- Bundesbank API started answering with a bot challenge; dating was redone from archived vintages (`scripts.ops.redate_from_archive`).

## Validation
`pytest`: 581 passed. Live ingestion: Eurostat survey, Destatis backfill, archive re-dating committed. Screens checked in the browser.

## Next action
Neural network stays paused; revisit only with an ablation against the main model.
