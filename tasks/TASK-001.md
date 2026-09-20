# TASK-001

## Objective
Repository scaffold and the documentation set the agent workflow depends on.

## Requirements
REQ-DOC-001 AGENTS.md defines reading order and is short
REQ-DOC-002 CURRENT.md reflects live state and stays short
REQ-DOC-003 DECISIONS.md records every tradeoff, factually
REQ-DOC-004 THIRD_PARTY_LICENSES.md covers every major dependency incl. commercial implications
REQ-DOC-005 docs/CONDITION.md carries the full TCGplayer rubric as the build-time reference

## Relevant Docs
docs/ARCHITECTURE.md · docs/DATABASE.md · docs/CONDITION.md · docs/COLLECTR.md

## Acceptance Criteria
- A new agent can orient from AGENTS.md → CURRENT.md without reading source.
- Every AGPL dependency is flagged with its implications.
- The condition rubric is complete enough to implement Phase 4 without refetching the PDF.

## Status
COMPLETE
