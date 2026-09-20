# TASK-020

## Objective
Fuse hash, registration and OCR into one identification.

## Requirements
REQ-REC-301 Combine hash distance, registration inliers and OCR into one score
REQ-REC-302 Resolve to a specific card_variants row, never merely a card
REQ-REC-303 Disagreement between signals lowers confidence rather than averaging away
REQ-REC-304 Below threshold, open a review with ranked candidates and the evidence for each
REQ-REC-305 Every identification records which signals contributed

## Relevant Docs
docs/RECOGNITION.md · docs/DATABASE.md

## Acceptance Criteria
- A confident match writes the variant and the evidence behind it.
- Two candidates differing only by variant produce a review, not a coin flip.
- An unrecognisable capture produces a review, never a low-confidence guess written as fact.

## Status
NOT STARTED
