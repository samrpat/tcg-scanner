# TASK-006

## Objective
Ingest per-variant pricing from TCGdex into an append-only price history.

## Requirements
REQ-PRC-001 `PricingProvider` interface; TCGdex and manual implementations
REQ-PRC-002 Every price carries source, currency, amount, type and timestamp
REQ-PRC-003 Both TCGplayer (USD) and Cardmarket (EUR) captured; USD is display default
REQ-PRC-004 Append-only; a new observation never overwrites an old one
REQ-PRC-005 Missing prices are absent rows, never zeroes
REQ-PRC-006 Scheduled refresh, safe to run repeatedly

## Relevant Docs
docs/ARCHITECTURE.md · docs/DATABASE.md

## Acceptance Criteria
- Ingesting a set populates prices for variants that have them and skips those that do not.
- Running twice yields two timestamped observations, not one overwritten row.
- Latest-price lookup returns the newest row per (variant, source, type).

## Status
COMPLETE
