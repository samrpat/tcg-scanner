# TASK-004

## Objective
Mirror TCGdex card data into Postgres so the scan path never calls an external API.

## Requirements
REQ-SYNC-001 Sync sets, then cards, then variants from `variants_detailed`
REQ-SYNC-002 Capture the stable `tcgdex_variant_id` per variant
REQ-SYNC-003 Incremental: unchanged cards are skipped on re-run
REQ-SYNC-004 Resumable: interruption does not corrupt or require a restart from zero
REQ-SYNC-005 Retry with backoff; TCGdex publishes no hard rate limit, so be considerate
REQ-SYNC-006 Full TCGdex payload preserved in `cards.raw`

## Relevant Docs
docs/ARCHITECTURE.md · docs/DATABASE.md

## Acceptance Criteria
- Full sync produces 218 English sets and their cards.
- Base Set Charizard resolves to four variants including the shadowless 1st-edition entry.
- Second run completes materially faster and creates no duplicate rows.

## Status
COMPLETE
