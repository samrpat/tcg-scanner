# Architecture

## Principle

The database is the source of truth. Everything external is an adapter behind an interface, and
removing an adapter must never remove inventory.

```
                            YOUR DATABASE
                                 │
     ┌───────────┬───────────┬───┴────────┬────────────┬───────────┐
     ↓           ↓           ↓            ↓            ↓           ↓
  TCGdex      eBay        eBay        Collectr    TCGplayer   Apify
  identity    upload      sold         CSV         export     eBay sold
  + pricing   file        comps       (not built)             comps
```

`photos` and `tunnel` exist because eBay fetches listing pictures from a URL and this machine is
not on the internet. They serve card JPEGs and nothing else — never the API, which has no
authentication — and are off unless `--profile photos` is up. See `docs/EBAY.md`.

## Services

| Service | Image | Role | Runs on |
|---|---|---|---|
| `web` | nginx + static Vite build | UI, proxies `/api` | Pi |
| `api` | python:3.12-slim | FastAPI, HTTP only, never blocks on AI | Pi |
| `worker` | same image as `api` | arq consumer: sync, pricing, later CV | Pi and/or desktop |
| `postgres` | postgres:16-alpine | Everything durable | Pi |
| `redis` | redis:7.4-alpine | Job queue | Pi |
| `tcgdex` | tcgdex/server | Optional local mirror, `--profile tcgdex` | Pi |
| `photos` | nginx:1.27-alpine | Read-only card JPEGs for eBay, `--profile photos` | Pi |
| `tunnel` | cloudflare/cloudflared | Public address for `photos` only, `--profile photos` | Pi |

The worker is the same image as the api with a different command, so models, config and database
sessions are shared rather than duplicated.

## Hardware topologies

Both look identical to the user — one URL, one application.

```
PI ONLY                          PI + DESKTOP
Pi 5 8GB                         Pi 5 8GB                 Windows / RTX 2060
├── web                          ├── web                  └── worker
├── api                          ├── api                      WORKER_TAGS=
├── worker (all tags)            ├── postgres                 recognize,condition
├── postgres                     ├── redis
└── redis                        └── worker (ingest)
```

Workers register capability tags (`ingest`, `recognize`, `condition`) and pull only matching jobs.
Adding the desktop is a matter of pointing a second worker at the Pi's Redis and Postgres; heavy
tags migrate to it automatically. Nothing in the UI changes.

## Interfaces

Four seams keep providers replaceable. Only the first two have Phase 1 implementations.

```
PricingProvider          TCGdexProvider · ManualProvider · (JustTCGProvider)
Storage                  LocalStorage · (S3Storage)
CardRecognitionEngine    Phase 3
ConditionEngine          Phase 4 manual, Phase 11 automated
MarketplaceAdapter       Phase 8 eBay, Phase 9 TCGplayer, Phase 6 Collectr export
```

## The 36-second budget

100 cards/hour means capture must never wait on inference.

```
CAPTURE                    queue                    REVIEW
place → front → back  ──▶  arq/redis  ──▶  keyboard triage, exceptions only
   ~12-18 s/card              async              ~8-15 s/card
```

The API enqueues and returns. Recognition, pricing and condition run behind the queue while the
operator is already on the next card. Review is a deferred batch pass, not an inline confirmation.

## Data flow, Phase 1

```
api.tcgdex.net ──▶ sync job ──▶ sets ──▶ cards ──▶ card_variants
                                                        │
                                   price ingest ────────┴──▶ prices (append-only)
```

Card data is mirrored into Postgres. Nothing in the scan path ever calls an external API.
