# TASK-021

## Objective
Re-crop from the recognised art. Recognition is the second half of rectification.

## Requirements
REQ-REC-401 Recompute the crop from the winning registration homography
REQ-REC-402 Replace the Phase 2 crop only when the new one is measurably better
REQ-REC-403 Corrected orientation is applied and recorded
REQ-REC-404 The improvement is measurable on the existing benchmark

## Relevant Docs
docs/RECOGNITION.md · docs/IMAGING.md

## Acceptance Criteria
- Benchmark accuracy on the stored captures improves, and none regress.
- The trainer card Phase 2 cropped to its artwork panel is corrected.
- The re-crop keeps the card border, which edgewear is measured on.

## Status
NOT STARTED
