# TASK-005

## Objective
Storage abstraction with the per-item directory layout from spec §20.

## Requirements
REQ-STO-001 Layout `CARD-NNNNNN/{original,processed}-{front,back}.jpg`
REQ-STO-002 Originals are never modified or deleted by processing
REQ-STO-003 Local filesystem backend now, S3/MinIO behind the same interface
REQ-STO-004 sha256 and dimensions recorded for every stored image
REQ-STO-005 SKU allocation is atomic and gapless per user

## Relevant Docs
docs/ARCHITECTURE.md · docs/DATABASE.md

## Acceptance Criteria
- Storing front and back yields exactly the specified paths.
- Path traversal in a SKU or filename is rejected.
- Swapping the backend requires no change to calling code.

## Status
COMPLETE
