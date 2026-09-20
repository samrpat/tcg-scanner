# TASK-007

## Objective
Health checks and hardware detection so deployment problems are visible without Docker knowledge.

## Requirements
REQ-OPS-001 `/health` liveness, `/health/ready` readiness, `/health/detail` per-dependency
REQ-OPS-002 Readiness checks database, redis and storage writability
REQ-OPS-003 Hardware detection reports model, arch, RAM, cores, disk, GPU
REQ-OPS-004 Detection recommends a worker topology (Pi-only vs Pi + desktop)
REQ-OPS-005 Makefile wraps every routine operation; no raw docker commands needed

## Relevant Docs
docs/ARCHITECTURE.md

## Acceptance Criteria
- `make health` prints a readable per-dependency status.
- `make hw` identifies a Pi 5 and recommends offloading heavy tags if RAM ≤ 8GB.
- Stopping postgres flips readiness to unhealthy while liveness stays up.

## Status
COMPLETE
