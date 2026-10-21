# 2026-10-21: policy numbers, stable rule IDs, world prices, pooling

- `LAW_PARAMETERS`: BEHG CO2 price 2021-2026 and CBAM factor 2024-2034 stored with verified quotes (no LLM).
- Rule identity: stable IDs and full-rule retraction; first migration attempt restored from backup, then applied by the user (365 rules, counts unchanged); re-key done-check fixed.
- `WORLD_BANK_PINK_SHEET`: 7 world prices 1960+, ECB monthly EUR/USD 1999+ (120 s timeout). Chain links tested in euros: 6 of 8 supported.
- Pooled seasonal AR for the German target: no gain (next release 0.507 vs 0.497); published model unchanged.
