# CURRENT

**Last verified:** 2026-09-19, against the running stack.

**Two builds off one tree.** `make up` is the scanner — shoot a pile, get clean photographs,
download the folder, archive, next pile — and is what is running. `make full` is the whole path:
scan → identify → approve → condition → price → eBay upload file with photographs attached. Same
database, same photographs, same batches; switching is a restart.

| | |
|---|---|
| Tests | **437 passing** (`make test`) |
| Endpoint smoke checks | **34 scanner / 62 full** (`make smoke`, mode-aware) |
| Lint | `ruff check` clean; TypeScript clean |
| Migrations | **0001 → 0024**, 23 tables |
| Catalogue | 218 sets, 23,544 cards, 35,495 variants |
| API routes | **62 scanner / 100 full** |
| API image | **608 MB scanner / 738 MB full** (tesseract is a build argument) |
| Stack memory, idle | **232 MB** (api 88, worker 64, postgres 62, redis 13, web 5) |

Idle memory was 373 MB on 2026-09-04, but most of that difference is Postgres cache warmth
rather than anything that changed: the attributable part is the API, 101 → 88 MB, from not
mounting the listing routers.

## Running it

    make on      # start everything, VM included
    make off     # stop everything, VM included — nothing left on battery
    make status  # what is running, and in which mode

`make up` / `make full` build first and are for after a code change; `make on` starts what is
already built. On a Pi the VM part is a no-op.

## The workflow, as it actually runs

1. **Scan** — phone or webcam, two sides per card. Capture returns in ~50 ms; processing and
   identification run in the background and need no attention.
2. **Approve** — the only screen where a decision is required. Every card passes through it.
   Anything the pipeline flagged is highlighted there.
3. **Condition** — stated by hand on each card. Not automated, deliberately (D-100).
4. **Price** — the feed price is TCGplayer via TCGdex. For cards worth the spend, select them
   and pull **real eBay sold prices** (D-120).
5. **Inventory** — value the collection, bundle what is not worth listing alone into lots, and
   download the eBay upload file.
6. **eBay** — Seller Hub → Reports → Upload. The file creates **drafts**, with every item
   specific filled and photographs attached.

## Collection state

**18 cards across three archived batches**, all photos-only — cropped, rendered and cut into
corners, never identified.

| Batch | Cards |
|---|---|
| `2026-09-05 · Batch 1` | CARD-000001, 3, 8, 10, 12, 14 |
| `2026-09-06 · Batch 1` | CARD-000015, 17, 18, 19, 20, 21 |
| `2026-09-07 · Batch 1` | CARD-000022, 23, 24, 25, 28, 29 |

`2026-09-09 · Batch 1` is open and empty, ready for the next pile. The 33 earlier development
cards were cleared on 2026-09-04 (D-132) and are in `data/backups/20260904-075318`.

## Phases

| Phase | State |
|---|---|
| 1 — Foundation | COMPLETE — stack, schema, TCGdex sync, storage, health, backup |
| 2 — Capture | COMPLETE — pairing, detection, dewarp, quality gates, review routing |
| 3 — Recognition | COMPLETE — pHash, ORB, OCR, fusion, re-crop, listing images |
| 4 — Condition | COMPLETE — stated by hand; the points rubric remains the defensible route |
| 5 — Pricing | COMPLETE — feed prices, eBay economics, and real sold comps |
| 6 — Collectr | **NOT BUILT, and questionable.** Import is by emailing them a CSV, and their format cannot carry subtype or stamp. Nothing downstream depends on it. Inventory and lots were built instead (D-108, D-109) |
| 7 — Listing presentation | COMPLETE — uniform-margin renders, corner close-ups (D-115) |
| 8 — eBay | **COMPLETE for the bulk path.** Draft upload file with full item specifics and photos (D-114, D-118). The API integration is not built and is not needed for this workflow |
| 9 — TCGplayer | Not started. Second marketplace behind the same seam |
| 11 — Automated condition | Not shipped. Edgewear reached proposal quality only (D-096, D-098) |

## eBay: what the upload file carries

`Action=Draft` — listings land in the Drafts folder to review and publish, never live on upload.

Every `C:` item specific eBay's Single Cards category filters on, taken from the operator's own
sold listings: Game, Set, Card Name, Card Number, Rarity, Finish, Language, Manufacturer, HP,
Stage, Card Type, Illustrator, Features, Country of Origin. Plus category 183454, the eBay
condition code, price, GTC, best-offer, and a description built from that card's own catalogue
entry — typing, stage, attacks in eBay's `[C][C][C]` notation, weakness, resistance, retreat.

**Photographs**: six per card — front, back, and four corner close-ups — served through a
photos-only host that exposes JPEGs and never the API (D-117, D-118).

Three fields are deliberately left empty rather than faked: `P:EPID` (exists only in eBay's
catalogue), the business policy names, and `PicURL` when no photo host is running.

## Verified on real hardware

Run on an M3 MacBook Air under Colima (arm64, 4 CPU / 6 GB). **Nothing has been executed on Pi
hardware yet** — everything below is architecture and footprint, not a live Pi run.

| Check | Result |
|---|---|
| Capture → API response | 48 ms |
| Detect + dewarp + quality | ~1 s per image |
| Recognition | 2–4 s per card, confidence 1.000 on stored captures |
| Concurrent front + back | 12/12 sides processed, zero deadlocks (D-119) |
| Photo host, public fetch | 200 OK through Cloudflare's edge; app returns 404 on every path |
| Corner accuracy | 0.51–0.73 px on synthetic scenes |
| Per card on disk | ~1 MB at 1080p, ~15 MB from a 12 MP phone |
| 2,000 cards | ~4 GB at 1080p, ~30 GB at 12 MP. `PROCESSED_PX_PER_MM_MAX` is the dial |

## Raspberry Pi 5 (8 GB) readiness

The Pi is the target; the Mac is the development machine.

- Images are **linux/arm64** already; every base image is multi-arch.
- Idle memory **373 MB**, leaving ~7 GB headroom.
- `tesseract-ocr` installs from Debian arm64 apt, no compilation.

**Not measured:** CPU. Recognition costs 2–4 s on an M3; a Pi 5 is several times slower, so
expect 10–20 s. That still fits a 36 s/card budget, with less room.

## Open items

- **Variants are the throughput problem.** Roughly a third of cards need a person to choose
  normal / holo / reverse holo (10 of the 33 in the last batch). Distinguishing them is a
  specular response and needs controlled light — a rig problem, not an algorithm problem
  (D-057). At 2,000 cards that is several hundred manual choices.
- **Condition is stated by hand on every card**, deliberately (D-100). Together with variants
  this is essentially all of the human time in the loop.
- **No authentication.** `current_user` returns the single seeded user with no credential. This
  is why the photo host exposes only JPEGs and never the app (D-117). It must be fixed before
  the app is ever exposed.
- **Apify token** not set, so eBay sold prices are unavailable until `APIFY_TOKEN` is in `.env`.
- Curl cannot be measured from a flat photo; still a manual checkbox.
- The outermost 1–2 px of a rectified card may be background, which matters only for edgewear.
