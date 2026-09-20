# TASK-008

## Objective
Test scaffolding, with the Phase 1 logic actually covered.

## Requirements
REQ-TST-001 Unit tests run with no database or network
REQ-TST-002 Integration tests run against a seeded throwaway database
REQ-TST-003 TCGdex normalisation tested against a recorded fixture, never the live API
REQ-TST-004 Condition points arithmetic and marketplace translation covered
REQ-TST-005 Storage paths and traversal rejection covered
REQ-TST-006 `make test` is the single entry point

## Relevant Docs
docs/CONDITION.md · docs/DATABASE.md

## Acceptance Criteria
- `make test` passes from a clean checkout.
- Tests do not reach the network.
- Rubric boundaries (3/6/12/24) are asserted explicitly, including the not-allowed lists.

## Status
COMPLETE
