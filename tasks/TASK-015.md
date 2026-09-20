# TASK-015

## Objective
Make the pipeline work on real photographs, and give a failed detection a way out.

## Requirements
REQ-DET-101 Detection must not depend on a single edge strategy
REQ-DET-102 Corners refined to sub-pixel accuracy before the warp
REQ-DET-103 Interior rectangles (artwork windows) must not win over the card outline
REQ-DET-104 A failed detection is correctable by hand, producing an equivalent processed image
REQ-CAM-101 The preview shows exactly the frame that will be captured
REQ-CAM-102 Capture at the sensor's maximum resolution; display it and the resulting px/mm
REQ-CAM-103 A phone must be able to capture at full resolution over plain HTTP
REQ-UI-101 A failed capture must never display as "processing"

## Relevant Docs
docs/IMAGING.md · docs/CONDITION.md

## Acceptance Criteria
- A card held against a cluttered background is detected where Canny alone found nothing.
- Corner error on synthetic scenes stays under 3 px.
- Manual corners produce a processed image indistinguishable in schema from an automatic one.
- Route declaration order is asserted by a test.

## Status
COMPLETE
