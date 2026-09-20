# TASK-011

## Objective
Reject unusable captures before they reach recognition or grading.

## Requirements
REQ-QUA-001 Blur score via variance of Laplacian
REQ-QUA-002 Exposure: clipped highlights and crushed shadows
REQ-QUA-003 Resolution check against the px/mm needed for condition measurement
REQ-QUA-004 Glare estimate — specular blowout on holo surfaces
REQ-QUA-005 Verdict is ok / review / reject with the numbers attached

## Relevant Docs
docs/IMAGING.md · docs/CONDITION.md

## Acceptance Criteria
- A deliberately blurred image scores materially lower than its sharp original.
- An image too small to resolve a 2.5mm² defect is rejected with a reason.

## Status
COMPLETE
