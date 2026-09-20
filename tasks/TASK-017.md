# TASK-017

## Objective
Perceptual hashes for the whole catalogue, stored in Postgres.

## Requirements
REQ-REC-001 Compute a perceptual hash per card from TCGdex art
REQ-REC-002 Store hashes in the database, not a mirrored image directory
REQ-REC-003 Hashing is resumable and incremental; re-running skips what is done
REQ-REC-004 A card whose art cannot be fetched is recorded, not silently skipped
REQ-REC-005 Hamming search over the catalogue returns ranked candidates

## Relevant Docs
docs/RECOGNITION.md · docs/DATABASE.md

## Acceptance Criteria
- Hashing 627 cards completes without manual intervention and is re-runnable.
- A card's own art matches itself at distance 0 and ranks first.
- Candidate lookup over the full catalogue stays well under a second.

## Status
NOT STARTED
