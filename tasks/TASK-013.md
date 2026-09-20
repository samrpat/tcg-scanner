# TASK-013

## Objective
Processing worker and review routing.

## Requirements
REQ-PRC-101 `process_image_task` runs detection, dewarp and quality off the request path
REQ-PRC-102 Processed image written alongside the preserved original
REQ-PRC-103 Low confidence or failed detection opens a Review of category `image`
REQ-PRC-104 A failure records the reason on the image; it never loses the original
REQ-PRC-105 Reprocessing is idempotent and overwrites only the processed image

## Relevant Docs
docs/IMAGING.md · docs/ARCHITECTURE.md

## Acceptance Criteria
- A good capture ends with four files and no review.
- A capture with no detectable card ends with the original intact and a review open.

## Status
COMPLETE
