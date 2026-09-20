# AGENTS.md

Read this file, then `CURRENT.md`. Read nothing else until you know which task you are on.

## Reading order

```
AGENTS.md  →  CURRENT.md  →  tasks/TASK-0NN.md  →  the docs that task names  →  source
```

Do not read the full docs set to answer a narrow question. Do not restate content that already
lives in a `.md` file. When updating documentation, edit only the section that changed.

## Rules

- Phases are approved one at a time. Never start the next phase without explicit approval.
- Finish a task through: implement → test → benchmark → document → update `CURRENT.md` → report.
- Every architectural choice with a tradeoff goes in `DECISIONS.md`, one short factual entry.
  That file is gitignored and stays local — see the note in `README.md`.
- No secrets in the repo. Config comes from the environment. See `.env.example`.
- The database is the source of truth. TCGdex, Collectr, eBay and TCGplayer are adapters and
  must never be able to hold inventory hostage.

## Layout

```
api/        FastAPI service + arq worker (same image, different command)
web/        Vite + React static frontend, served by nginx
docs/       ARCHITECTURE, DATABASE, CONDITION
tasks/      TASK-001 … one file per unit of work
scripts/    hardware detection, health checks
data/       runtime images and backups (gitignored)
```

## Commands

```
make on        start everything, VM too    make migrate   apply migrations
make off       stop everything, VM too     make sync      sync TCGdex card data
make up        build + start the scanner   make prices    ingest prices
make full      build + start the whole app make health    check service health
make status    what is running, which mode make shell     shell into the api container
make logs      tail everything             make hw        detect hardware
make test      run the test suite          make backup    dump database and images
```

The stack runs in one of two modes and `make up` / `make full` record which in `.mode`, so every
other target follows the stack that is actually running. `make MODE=full <target>` overrides it
for one command. `docker-compose.yml` is the scanner; `docker-compose.full.yml` layers the
listing half back on. `archive/` holds a frozen pre-split copy of the tree — read it, never run
it; there is one database and it belongs to the live tree.

## Tests without Docker

The unit suite needs neither a database nor the network, so it runs in a bare virtualenv:

```
cd api && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
POSTGRES_PASSWORD=x .venv/bin/python -m pytest -q
```
