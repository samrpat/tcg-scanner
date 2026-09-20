# TASK-003

## Objective
Full Postgres schema with migrations, covering every entity in spec §22.

## Requirements
REQ-DB-001 18 tables per docs/DATABASE.md
REQ-DB-002 `user_id` on every owned row
REQ-DB-003 `prices` and `condition_assessments` are append-only
REQ-DB-004 Inventory references `card_variants`, never `cards` directly
REQ-DB-005 External IDs are unique columns, never primary keys
REQ-DB-006 Generic `external_mappings` instead of provider-specific columns
REQ-DB-007 Native Postgres enums for condition, image kind, job and review states
REQ-DB-008 Seed pricing sources, condition rubric and marketplace translations

## Relevant Docs
docs/DATABASE.md · docs/CONDITION.md

## Acceptance Criteria
- `make migrate` applies cleanly to an empty database.
- Dropping a `marketplace_accounts` row leaves `inventory_items` untouched.
- Seeded translations cover all five conditions across four marketplaces.

## Status
COMPLETE
