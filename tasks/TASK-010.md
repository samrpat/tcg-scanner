# TASK-010

## Objective
Card detection and perspective dewarp to a known physical rectangle.

## Requirements
REQ-DEW-001 Detect the card quad in a photo against an arbitrary background
REQ-DEW-002 Order corners deterministically (TL, TR, BR, BL)
REQ-DEW-003 Warp to exactly 88 x 63 mm at a configured px/mm, default 20
REQ-DEW-004 Correct landscape captures to portrait
REQ-DEW-005 Report a detection confidence, never a silent guess
REQ-DEW-006 Geometry only — no colour or brightness alteration (see docs/IMAGING.md)
REQ-DEW-007 Fall back to minAreaRect when no clean quad is found, at lower confidence

## Relevant Docs
docs/IMAGING.md · docs/CONDITION.md

## Acceptance Criteria
- A synthetic card put through a known homography is recovered to within 2 px per corner.
- Output is exactly 1260 x 1760 at the default 20 px/mm.
- A photo with no card present returns no detection rather than a wrong one.

## Status
COMPLETE
