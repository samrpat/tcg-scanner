# TASK-012

## Objective
Capture endpoint with automatic front/back pairing.

## Requirements
REQ-CAP-001 Posting a front creates an inventory item and allocates a SKU
REQ-CAP-002 Posting a back attaches to the item awaiting one — no manual linking
REQ-CAP-003 Originals are written once and never modified
REQ-CAP-004 The request returns as soon as the original is stored; processing is queued
REQ-CAP-005 Batch upload pairs by filename hint, falling back to arrival order
REQ-CAP-006 Re-posting the same side replaces only with explicit intent

## Relevant Docs
docs/IMAGING.md · docs/ARCHITECTURE.md

## Acceptance Criteria
- front, back, front, back produces two items with two images each.
- An interrupted pair leaves a visible half-finished item, not an orphan image.
- Capture returns in well under a second regardless of processing time.

## Status
COMPLETE
