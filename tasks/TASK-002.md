# TASK-002

## Objective
Dockerised stack that comes up with one command on both a Pi 5 and an x86 desktop.

## Requirements
REQ-INF-001 `docker compose up -d` starts web, api, worker, postgres, redis
REQ-INF-002 Self-hosted tcgdex available behind `--profile tcgdex`
REQ-INF-003 All config via environment; `.env.example` documents every variable
REQ-INF-004 No secrets in the repo; postgres and redis not published to the host by default
REQ-INF-005 arm64 and amd64 both build from the same Dockerfiles
REQ-INF-006 Worker capability tags via `WORKER_TAGS` so the desktop can take heavy jobs

## Relevant Docs
docs/ARCHITECTURE.md

## Acceptance Criteria
- Cold `make up` reaches all-healthy with no manual steps.
- `docker compose ps` shows healthchecks passing for postgres, redis, api, web.
- Only the web port is bound to the host.

## Status
COMPLETE
