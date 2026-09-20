# Listing on eBay

The whole point of this system is the last twenty minutes: turning a pile of scanned cards into
listings without typing anything twice. This is how that works and, more usefully, why each part
is the way it is.

## The loop

```
scan  →  approve  →  set condition  →  (price)  →  Inventory  →  upload file  →  Seller Hub
```

Only three of those need a person: approve, condition, and choosing what to sell alone rather
than in a lot.

## The upload file

**Inventory → Download eBay upload file (.csv)**, then eBay → Seller Hub → Reports → Upload.

It is eBay's Seller Hub Reports format (formerly File Exchange). `Action` is **`Draft`**, so
listings land in the Drafts folder to be reviewed and published by hand. This is the default on
purpose: a bulk import that goes live on upload cannot be undone in bulk, and a mistake is then
several hundred live listings. Unticking **send to Drafts** switches to `Add`, which publishes
immediately.

### What it fills in

| Column | Where it comes from |
|---|---|
| `Title` | eBay's own catalogue name: `NAME NUMBER SET FINISH` (D-111) |
| `Category` | 183454 — Collectible Card Game Singles |
| `ConditionID` | 400010 NM · 400011 LP · 400012 MP · 400013 HP/DMG |
| `StartPrice` | The pricing run, or your override |
| `Description` | Built from that card's own catalogue entry |
| `PicURL` | Six photos per card, if a photo host is running |
| `C:*` | Every item specific the category filters on |

The item specifics are Game, Set, Card Name, Card Number, Rarity, Finish, Language, Manufacturer,
HP, Stage, Card Type, Illustrator, Features, Country of Origin — copied from the specifics blocks
of real sold listings rather than guessed. **An aspect eBay does not recognise is dropped
silently on import, and a misspelled one lands as free text no search filter reads**, so guessing
at them is worse than useless.

### What it deliberately leaves empty

- **`P:EPID`** — eBay's catalogue product id. It exists only in eBay's catalogue and cannot be
  derived here. Matching the catalogue title exactly is what attaches the listing instead.
- **Business policy names** — whatever you called them in Seller Hub. Set once in *settings*.
- **`PicURL`** — when no photo host is running. The file is still correct; the descriptions
  simply do not mention photographs.

## Why the title has no condition or rarity in it

Both are item specifics in this category with their own search facets. Repeating them in the
title spends characters that could carry a searchable term and adds nothing a buyer filters on.
Leaving them out is what let the set name back into the title, which is the stronger term.

## Photographs

eBay's servers fetch each picture URL **once, at import**, and copy the image to eBay's own
storage. If that fetch fails the draft is created with **no photographs and no error you will
ever see** — it surfaces days later as a listing nobody watches. So the only thing that matters
about the host is that it answers reliably during the minutes an import runs.

**The app must never be the thing exposed.** It has no authentication of any kind, and a URL
handed to eBay is not a secret.

### Object storage — the durable way, and the recommended one

Put an S3-compatible bucket in `.env` (Cloudflare R2's free tier has no egress fees):

```
S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET=cards
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_PUBLIC_BASE=https://pub-xxxxxxxx.r2.dev
```

Then **Inventory → Publish photos** before downloading the file. Uploads are idempotent, so it is
safe to press again after scanning more cards, and the URLs keep working after this machine is
switched off — which matters because eBay may retry a fetch.

### The tunnel — convenient, and not durable

```bash
make photos-on      # share card JPEGs only, through a temporary address
make photos-check   # confirm eBay can actually fetch them
make photos-off     # when the upload has finished
```

This starts an nginx serving one path shape — `/api/images/CARD-000000/name.jpg` — with GET and
HEAD only, no directory listings, and the image volume mounted read-only. Everything else is a
404. A Cloudflare quick tunnel gives it a public address.

**Do not trust it without checking.** Observed in one session: a quick tunnel served correctly
for hours, then lost its control stream while its container stayed "running" and its metrics
endpoint kept reporting a hostname Cloudflare had already withdrawn from DNS; a freshly created
replacement registered successfully and its hostname never resolved at all. Both failures are
invisible from inside this application, which is why `make photos-on` now recreates the tunnel
every time and then verifies it, and why the app reports **no** photo host rather than a dead
one — a file with no pictures is recoverable, a file full of dead links is not.

### What each card carries

Six photographs: front, back, and four corner close-ups. Every one comfortably clears eBay's
requirements — HTTPS, JPEG, at least 500 px on the longest side (ours are 1587–2940 px), and
well under the 12 MB limit. eBay caps File Exchange at 12 pictures per row.

### Corner close-ups

A buyer deciding on a card worth twenty dollars zooms in, and what they zoom at is the corners.
Corner whitening and edge wear are what separate Near Mint from Lightly Played, and they occupy a
few millimetres of a card eBay serves at a size where a few millimetres is a smudge.

Each corner is **its own JPEG**, because they are uploaded as separate listing photographs. They
are cut from the listing render rather than the tight crop, so each keeps real background beyond
the card edge — a corner cut flush gives a buyer nothing to judge the edge against. They overlap
4% so wear sitting on a seam appears whole in both rather than halved in each.

**Inventory → Add corner close-ups** does the whole export in one pass, skipping cards that
already have them.

## Pricing

The feed price is TCGplayer via TCGdex. It is a different marketplace with different economics
and runs low against eBay on cheap cards — a card it calls $0.18 routinely sells for $0.99 —
and the gap is not a constant, so no multiplier fixes it.

**Real sold prices.** Select cards in Inventory and press **Get real eBay prices**. This reads
completed listings through Apify's eBay scraper, at roughly $1.50 per 1,000 results, so it never
runs automatically. Put your token in `.env`:

```
APIFY_TOKEN=...
```

Comps are matched to the card's condition — eBay's own condition field first, then grades written
into titles, then unknown, which stays unknown rather than defaulting to Near Mint. **Graded
slabs and multi-card lots are excluded outright**: both are wrong by a multiple and look
plausible. With three or more comps in the exact grade the median is used as-is, with no
multiplier and no floor; below that the search widens and says that it did.

**Under $0.50 on TCGplayer NM is bulk**, judged on the unadjusted feed price so the line means
one fixed thing. Real sold comps override it — evidence beats a threshold.

## Lots

A card in a lot is excluded from the upload file, because something sold as part of a bundle must
not also be listed singly. To sell one on its own instead, select it in Inventory and press
**Remove from lot**; it appears in the file.

eBay's fixed per-order fee and the postage are paid once per order, so cards that are each
marginal alone are comfortably worth selling together.

## Descriptions

Built from the card's own catalogue entry — typing, stage and what it evolves from, HP, ability
and attack text in eBay's `[C][C][C]` notation, weakness, resistance, retreat cost, illustrator.
Two cards never get the same description.

Two rules matter more than the content:

- **The photograph sentence is only written when photographs are attached.** A listing that tells
  a buyer to examine pictures it does not have reads as a bait listing.
- **No packaging claim unless you write one.** The original text promised "a penny sleeve and
  toploader inside a rigid mailer", which is untrue of an eBay Standard Envelope shipment — ESE
  has a thickness limit a toploader does not meet.

The condition sentence describes **the bottom of the grade band**. A buyer told to expect minor
edge whitening who receives a nearly flawless card is pleased; the reverse is a bad review.
