# TASK-019

## Objective
OCR the collector number and set code from a rectified card.

## Requirements
REQ-REC-201 Locate the collector-number region by geometry, not by searching the image
REQ-REC-202 Read the number and set code with a bounded character set
REQ-REC-203 Report per-field confidence; never silently return a guess
REQ-REC-204 Handle promos, which carry a bare number with no total
REQ-REC-205 The reading is a discriminator among candidates, not a sole identifier

## Relevant Docs
docs/RECOGNITION.md

## Acceptance Criteria
- A rectified card yields its printed number on a clear capture.
- An unreadable region returns nothing rather than a plausible-looking wrong number.
- Region location holds across card eras, where the number sits in different corners.

## Status
NOT STARTED
