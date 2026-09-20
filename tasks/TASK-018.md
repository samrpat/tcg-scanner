# TASK-018

## Objective
Identify a rectified capture by registering it against candidate art.

## Requirements
REQ-REC-101 Register the capture against each prefiltered candidate's art
REQ-REC-102 Inlier count and ratio drive the match score
REQ-REC-103 The winning homography is retained for re-cropping
REQ-REC-104 Recovered rotation corrects orientation, closing Phase 2's deferred item
REQ-REC-105 Below threshold, produce ranked candidates rather than a guess

## Relevant Docs
docs/RECOGNITION.md · docs/IMAGING.md

## Acceptance Criteria
- A capture of a known card identifies it correctly from the full catalogue.
- A card not in the catalogue produces no confident match.
- The re-crop derived from the winning homography is at least as tight as Phase 2's.

## Status
NOT STARTED
