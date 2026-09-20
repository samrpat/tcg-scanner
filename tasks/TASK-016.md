# TASK-016

## Objective
Make the crop exact, keep every pixel the camera resolved, and give phones the same capture
experience as the laptop.

## Requirements
REQ-CROP-101 Corners derived from fitted edges, robust to a finger crossing one
REQ-CROP-102 Refinement runs at full resolution, not on the detection working copy
REQ-CROP-103 Rounded card corners must not bias the fit
REQ-CROP-104 A card cropped by the frame is reported, not silently mis-cut
REQ-RES-101 Rectification scale follows the capture rather than a constant
REQ-RES-102 Encoding preserves full chroma
REQ-CAM-201 Phone and laptop share one capture path, guide and pipeline
REQ-CAM-202 HTTPS is available for devices where localhost is not

## Relevant Docs
docs/IMAGING.md · docs/CONDITION.md

## Acceptance Criteria
- Synthetic corner error below 1 px.
- An edge two-thirds obscured still fits to a sub-pixel residual.
- A capture touching the frame edge produces an explicit, actionable message.
- `make cert` + `make up` gives a working camera on a phone over the LAN.

## Status
COMPLETE
