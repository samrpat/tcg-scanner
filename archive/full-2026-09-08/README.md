# Pokémon TCG Scanner, Inventory & Marketplace Platform

Self-hosted. Photo in, identified card with a variant, a condition, a price and a listing out.
The database is the source of truth; Collectr, eBay, TCGplayer and TCGdex are replaceable adapters.

**Two modes.**

- **Scanner** (the default) — shoot a pile, get clean photographs, hand the folder to whatever
  is doing the listing. Rectified to a true 88 × 63 mm, squared, uniform margin, four corner
  close-ups per card. See `docs/SCANNER.md`.
- **Full** (`SCANNER_MODE=false`) — the whole path: scan → identify → approve → condition →
  price → an eBay upload file with photographs attached. See `docs/EBAY.md`.

Nothing was removed to add the first; the switch puts every screen back. See `CURRENT.md` for
what exists and what was measured, and `AGENTS.md` for how to work in this repo.

The daily loop is short by design: **scan, set the condition, approve.** Everything else —
cropping, identification, listing images, titles, item specifics, descriptions — happens without
being asked.

## Quick start

```bash
cp .env.example .env       # then set POSTGRES_PASSWORD
make up                    # build and start everything
make migrate               # create the schema
make seed                  # reference data: pricing sources, condition rubric, translations
make sync                  # mirror TCGdex card data (~20k English cards, 218 sets)
make prices                # ingest per-variant pricing
```

Then open <http://localhost:8080>. `make help` lists everything else.

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
| `DECISIONS.md` | Every tradeoff and why |
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
