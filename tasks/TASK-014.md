# TASK-014

## Objective
Capture UI: press-to-capture, keyboard driven, built for the 36-second budget.

## Requirements
REQ-UI-001 Live camera preview via getUserMedia, with file upload as fallback
REQ-UI-002 Press-to-capture; auto-capture deferred to a later phase
REQ-UI-003 Always shows which side is expected next
REQ-UI-004 Keyboard first: space captures, no mouse needed in the loop
REQ-UI-005 Never blocks on processing — the next card can be shot immediately
REQ-UI-006 Recent captures strip with per-item processing state

## Relevant Docs
docs/IMAGING.md

## Acceptance Criteria
- A full front/back cycle needs no pointer input.
- The shutter stays responsive while images are still processing.

## Status
COMPLETE
