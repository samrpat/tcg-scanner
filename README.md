# Pokémon TCG Scanner, Inventory & Marketplace Platform

Self-hosted. Photo in, identified card with a variant, a condition, a price and a listing out.
The database is the source of truth; Collectr, eBay, TCGplayer and TCGdex are replaceable adapters.

**Two modes, one tree, one database.**

- **Scanner** (`make up`, the default) — shoot a pile, get clean photographs, hand the folder to
  whatever is doing the listing. Rectified to a true 88 × 63 mm, squared, uniform margin, four
  corner close-ups per card. See `docs/SCANNER.md`.
- **Full** (`make full`) — the whole path: scan → identify → approve → condition → price → an
  eBay upload file with photographs attached. See `docs/EBAY.md`.

They share the database, the photographs and the batches; switching is a restart, not a
migration. The scanner build does not merely hide the listing screens — it does not mount their
routes, and it is built without tesseract, which the scanner never calls. A frozen copy of the
source as it stood before the split is in `archive/`, for reading and for diffing.

See `CURRENT.md` for what exists and what was measured, and `AGENTS.md` for how to work in this
repo.

> **`D-NNN` references.** Comments and docs throughout cite decisions by number — "see D-119".
> Those point at a design log kept outside this repository: a running account of why each
> tradeoff went the way it did. The reasoning that matters to reading the code is in the code,
> next to it; the numbers are provenance, not a missing chapter.

The daily loop is short by design: **scan, set the condition, approve.** Everything else —
cropping, identification, listing images, titles, item specifics, descriptions — happens without
being asked.

## Quick start

```bash
cp .env.example .env       # then set POSTGRES_PASSWORD
make up                    # build and start the scanner
make migrate               # create the schema
```

Then open <http://localhost:8080>. It will ask you to **choose a password** — until you do, the
instance is unclaimed and serves nothing but that screen. That is everything the scanner needs.

Deploying it somewhere it will be left running: `docs/DEPLOY.md`.
Publishing it as a Docker image for other people: `docs/DISTRIBUTION.md`.

For full mode, `make full` instead, and then the reference data it identifies and prices
against:

```bash
make seed                  # pricing sources, condition rubric, translations
make sync                  # mirror TCGdex card data (~20k English cards, 218 sets)
make prices                # ingest per-variant pricing
```

## Day to day

```bash
make on      # start everything, VM included
make off     # stop everything, VM included
make status  # what is running, and in which mode
```

On a Mac, Docker is a Linux VM, and a VM left running is a laptop that never really idles.
`make off` stops the VM too, so nothing is left burning battery between scanning sessions. On a
Pi there is no VM and these are just the containers.

`make up` / `make full` build before starting and are what to use after changing code; `make on`
starts what is already built. `make help` lists everything else.

## Listing on eBay

Scan and approve what you want to sell, set conditions, then in **Inventory**:

**Photographs.** eBay fetches each picture URL once at import; if that fails you get drafts with
no photos and no error. The durable way is an S3-compatible bucket — Cloudflare R2's free tier
works — configured in `.env`, then **Inventory → Publish photos**. See `docs/EBAY.md`.

For a one-off, a temporary tunnel also works:

```bash
make photos-on             # share the card photos so eBay can fetch them
make photos-check          # confirm eBay can actually reach them
```

Download the **eBay upload file** and take it to Seller Hub → Reports → Upload. It creates
**drafts**, not live listings, with every item specific filled in and six photographs per card
(front, back, four corner close-ups).

```bash
make photos-off            # when the upload has finished
```

`make photos-on` shares **only the card JPEGs**, read-only. The app itself is never exposed,
which matters because it has no password on it.

### Real eBay prices

The feed price is TCGplayer via TCGdex, which runs low against eBay on cheap cards. To price a
card off actual completed sales instead, put an [Apify](https://console.apify.com/account/integrations)
token in `.env` as `APIFY_TOKEN`, then select cards in Inventory and press **Get real eBay
prices**. It costs roughly $1.50 per 1,000 results, so it never runs on its own.

### Capturing from a phone

Browsers only allow camera access from a secure page, so a phone on the network needs HTTPS —
`localhost` is exempt, which is why the laptop works without it:

```bash
make cert          # self-signed, covers localhost and this machine's LAN addresses
make up
```

Open `https://<lan-ip>:8443` on the phone and accept the warning once. The phone then behaves
identically to the laptop: same preview, same alignment guide, same shutter, same pipeline.

Measured throughput: **~7 cards/second** (524 cards in 75s on an M3 Air), so a full English
catalogue of roughly 20,000 cards takes **45-60 minutes**. It is network-bound rather than
CPU-bound, so a Pi 5 should be comparable. The sync is resumable and incremental — interrupting
it is safe, and re-running skips everything already stored (103 cards re-checked in 2.1s).

## Adding the desktop as an AI worker

Later phases push recognition and condition work off the Pi. On the desktop, point a worker at the
Pi's Postgres and Redis and give it the heavy tags:

```bash
docker compose build api                                  # worker shares the api image
WORKER_TAGS=recognize,condition docker compose up -d worker
```

The Pi keeps `ingest`. One application, one URL, either way.

## Documentation

| File | Purpose |
|---|---|
| `AGENTS.md` | How to work in this repo. Read first. |
| `CURRENT.md` | Live state. Read second. |
| `docs/ARCHITECTURE.md` | Services, topologies, interfaces |
| `docs/DATABASE.md` | Schema and its rules |
| `docs/CONDITION.md` | The TCGplayer grading rubric |
| `docs/COLLECTR.md` | Collectr's CSV format, confirmed against a real export |
| `docs/SCANNER.md` | Scanner mode: the loop, the files, the image quality |
| `docs/EBAY.md` | The listing file, its columns, and the photo host |
| `docs/IMAGING.md` | Detection, dewarp, quality gates, and getting a capture worth grading |
| `docs/RECOGNITION.md` | How a photograph becomes a card id |
| `THIRD_PARTY_LICENSES.md` | Dependencies and their implications |
| `CHANGELOG.md` | What changed, in order |
| `tasks/` | One file per unit of work |

Pokémon and the Pokémon TCG are trademarks of Nintendo, Creatures Inc. and GAME FREAK inc.
This project is unaffiliated with and unendorsed by any of them.
