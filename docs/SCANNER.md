# Scanner mode

The app as a card scanner: shoot a pile, get clean photographs, hand the folder to whatever is
doing the listing, start the next pile. No identification, no pricing, no eBay.

This is the half of the job that listing tools — CardUploader, SortSwift, TCG Upload — do not do.
They take "phone photos, any angle" and build a listing around them. What comes out of here is a
card rectified to a true 88 × 63 mm, squared, sitting in a known margin of real background, with
each corner cut out at a resolution where whitening is judgeable.

`make up` runs it. `make full` runs the whole app instead — identification, pricing, eBay
titles and upload files — against the same database, the same photographs and the same batches.
Nothing was removed to build the scanner; the two are the same tree with a different half
switched on. A frozen copy of the source as it stood before the split is in `archive/`.

In scanner mode the listing routes are not mounted at all, not merely hidden. This app has no
authentication, so a mounted route is an open route, and the listing half includes deleting
inventory and publishing photographs to the internet.

## The loop

1. **Scan** on a phone, or **Capture** on this machine. Front then back — they pair themselves.
2. **Extras** (optional) walks the pile once more, so the holos get the shot that shows the foil.
3. **Batch** shows every card in the current pile, large. Large on purpose: a thumbnail grid
   hides exactly the flaw this screen exists to catch — a crop that clipped a corner, or a card
   shot back-first.
4. **Download all photos (.zip)**.
5. **Archive & start next.**

## What you get per card

Six photographs, one flat folder, named so the order survives:

```
CARD-000001-1-front.jpg
CARD-000001-2-back.jpg
CARD-000001-3-corner-top-left.jpg
CARD-000001-4-corner-top-right.jpg
CARD-000001-5-corner-bottom-left.jpg
CARD-000001-6-corner-bottom-right.jpg
```

**The numbers are load-bearing.** Uploaders sort photographs by filename and the first becomes
the gallery image; without them a corner crop can land on the search results page.

They are counted as the files are written, not fixed per kind, so a card with no extra shots is
numbered 1–6 with no gap, and a card with one is numbered 1–7 with the extra at 3.

## How many photographs per card

This is the number a bulk uploader has to be told, and the Batch bar shows it before you
download anything:

> **Photos per card** 6 × 11 cards · 7 × 6 cards

CardUploader and its kin are handed a flat folder and a photo count, then chunk the sorted list
into groups of that size. **The count has to be identical for every card in one upload.** A
single card carrying a seventh photograph shifts every card after it by one, and the result is a
listing illustrated with somebody else's card. The bar turns amber when a batch has more than
one count in it, because that is the failure this screen exists to prevent.

**split by photo count** puts each count in its own folder:

```
6-photos-per-card/    11 cards, 66 files
7-photos-per-card/     6 cards, 42 files
```

Folders are named as the number to type into the uploader, because that is the only thing
anyone needs from them. Upload each one separately and every card gets its own photographs.

Cards with no photographs at all are listed explicitly rather than quietly left out — finding
that out from a listing with no picture is the expensive way.

Unlike the corner switch, this one lives in the browser rather than on the batch: it decides how
a download is *arranged*, not what gets *made*.

## Extra shots

Up to three extra photographs per card, framed by hand, showing whatever the standard six do
not: a holo tilted so the foil reads, a crease close up, a signature, the edge of something
thick.

They are **never rectified**. Every other image here is squared and flattened because that is
what makes a card measurable — and it is exactly that treatment which flattens a holo into a
matte card, evens the light across a scuff and squares away a warp. What you framed is the
content; straightening it would undo it.

They sort third in the folder, ahead of the corner close-ups, because on a holo that is the
photograph that sells the card.

They are the only images here that reach a listing without passing through the pipeline, so they
are the only ones that could carry a phone's own metadata — including, on most phones, where the
picture was taken. The orientation tag is applied to the pixels and then **all metadata is
dropped**, before anything is stored.

### The extras pass

**Extras** is the tab for this, and it is the way to do a pile. It walks the batch one card at a
time — the card's own scan on screen so you know which one to pick up — and offers two answers:

| | |
|---|---|
| **Capture extra** | space |
| **Skip** | S, or → |
| back a card | ← |

So a Bluetooth remote works here as it does on the scan screen. Skip is the answer on most
cards, which is why it is the same size as capture rather than a link off to one side.

**only cards without one** is on by default, so a card you have shot leaves the queue and coming
back later resumes where the work actually is. A skip is *not* remembered — cards you passed
over are offered again next time, because there is no reason to make "not this one" permanent.

The batch selector at the top runs the pass over any batch, including archived ones.

### The other two ways

**On the phone, mid-scan**: **✦ extra shot** under the shutter. For the holo you are holding
right now, when you do not want to come back to it. It attaches to the card you are on and does
not touch the front/back rotation — press it mid-card and the shutter still asks for whichever
side is outstanding. It names the card it landed on, because "the card you are on" means the
newest one, and that is the only way to notice a shot that went to the wrong card.

**From the Batch card**: open a card and use **capture an extra shot** for a camera right
there, or **or upload one** for a file taken elsewhere. This is the screen where you notice a
holo wants one — you are already looking at the card large — and sending you to another tab to
act on what you just saw is how a thing stops getting done.

The camera is only running while that panel is open. A camera left open keeps the lamp on and
the radio awake, which on a phone working through a pile is a battery cost for nothing.

Any of the three can be removed individually from the Batch card view; nothing is derived from
them, so deleting one costs exactly the photograph.

They sort third in the folder, ahead of the corner close-ups: on a holo that photograph is the
one that sells the card.

They are the only images here that reach a listing without passing through the pipeline, so they
are the only ones that could carry a phone's own metadata — including, on most phones, where the
picture was taken. The orientation tag is applied to the pixels and then **all metadata is
dropped**, before anything is stored.

`?layout=folders` gives a directory per card instead. `?kind=all` adds the untouched originals,
for anything that would rather do its own cropping.

### The corner close-ups switch

**corner close-ups** in the Batch bar belongs to the batch, not to the browser, and it decides
what gets **made** — not just what gets downloaded. With it on, the four crops are cut as each
card finishes processing, so they are simply there by the time you look. It shows even on an
empty batch, so the next pile can be set up before the first card.

| | |
|---|---|
| On | six files per card; corners cut automatically as you scan |
| Off | two files per card, roughly a third of the size; nothing new is cut |

**Turning it off never deletes anything.** It stops new ones being cut and leaves them out of the
download. Removing the files is a separate, deliberate click — *delete the ones on disk* — because
a switch that quietly destroys work on its way past is one nobody can use with confidence. They
are derived from the listing front, so the cost of deleting them is the seconds to cut them again.

*cut any missing* fills gaps without re-cutting what is already there: a card moved in from a
batch that had the switch off, or one whose crop failed. Safe to press repeatedly.

The switch is inherited by the next batch when you archive — one pile following another should
not silently change what it produces.

The front and back are never optional. A listing with no picture of the card is not a listing.

## Quality

| | |
|---|---|
| Listing renders | 2920 × 3920 px, ~44 px/mm, JPEG q97 |
| Corner close-ups | 1576 × 2116 px each |
| Typical size | 2–3 MB per listing image, under 1 MB per corner |

The render scale was 30 px/mm until it was measured against what the captures actually carry: a
phone photo of a card filling the frame yields about 36 px/mm, so the listing images — the ones a
buyer zooms into — were being **downscaled from detail that already existed**. At 40 px/mm
nothing is thrown away.

JPEG quality went 94 → 97 for the same reason. The difference shows up precisely where it
matters: the fine speckle of edge whitening, which 94 smooths into the border.

All of it stays well inside eBay's limits (12 MB, minimum 500 px on the longest side).

## Fixing a mis-shot

- **Front and back the wrong way round** — `swap sides` on the card. Both photographs are kept;
  only their roles change, and everything derived from them is rebuilt. Corner cuts come from
  the front specifically, so relabelling rather than rebuilding would leave a card whose "front
  corners" were cut from its back.
- **A bad crop** — open the card, then **edit crop & corners**. Editing is a mode rather than
  handles on every card, so the screen stays something a pile can go past quickly. Two separate
  things live there: *where the card's edges are*, which decides the whole crop, and *how close
  the corner shots sit*, which decides only those four images.
- **A card you do not want** — `delete` removes it and its photographs.

## Batches

A batch is a pile worked in one sitting. New scans join the open one; **Archive & start next**
closes it and opens a fresh one in the same action, so the next card never lands nowhere.

**Archiving clears the screen.** That is what it is for: the batch's cards leave the view, and
the next pile starts on an empty one. Nothing is deleted — the cards and photographs are under
**Earlier batches**, where **open** brings them back on screen, **photos** re-downloads the
folder, and **scan into this** reopens the batch for more cards.

### Renaming a batch

Click a batch's name — in the bar or in **Earlier batches** — and type. Enter saves, Escape
abandons, and an empty name puts the generated one back.

The generated name is the date and the batch number within it, which is right while the pile is
on the bench and often wrong afterwards. "Binder A · holos" is what the batch actually is, and
an archive list of eleven identical-looking dates is something to search rather than read.

Only the label changes. Nothing keys off a batch's name — the id is the identity.

### Sections

A batch is an evening's scanning; it is usually not one pile. Sort the cards before you scan
them — reverse holos, normals, unknowns, then each of those by condition — and keep that shape
with **+ section**.

New scans land in the current section, so the loop is: name the pile, scan it, name the next
one. Click a heading to rename it, fold it away with the arrow, and **remove section** takes
the heading while leaving every card where it is.

**You can do all of this without leaving the Scan screen.** The pill at the top left says
where cards are going; tap it to name the next pile or switch to an earlier one. It takes
effect on the very next shutter press — which is the point, because the moment you need it is
mid-pile with a phone in one hand.

**a folder per section** puts each one in its own folder in the download, and composes with
the photo-count split:

```
01 Reverse holo - NM/7-photos-per-card/CARD-000061-1-front.jpg
02 Reverse holo - LP/7-photos-per-card/CARD-000063-1-front.jpg
00 unsorted/…
```

Numbered so they sort in the order you scanned them. Upload each folder as its own batch and
every listing gets the right condition and the right photographs.

### Moving cards between batches

Groupings get made wrong: a pile shot across a break lands in two, a card belonging to
yesterday's lot turns up today, a batch gets archived one card early. Tick **move** on the cards
and pick a destination — a section of this batch, another batch, or *a new batch*, which is created closed so that
making somewhere to put cards does not quietly redirect the scanner into it mid-pile.

Only the grouping changes. No photograph is touched, nothing is reprocessed, and moving them
back is the same two clicks — which is why it asks for no confirmation.

## Starting and stopping

    make on     # start everything, VM included
    make off    # stop everything, VM included
    make status # what is running, and in which mode

On a Mac, Docker is a Linux VM, and a VM left running is a laptop that never really idles.
`make off` stops the containers **and** the VM, so a machine on battery between scanning
sessions has nothing of this running on it at all. On a Pi there is no VM and these are just the
containers.

`make up` and `make full` build first and are what to use after changing code; `make on` only
starts what is already built, which is the difference between a few seconds and a few minutes.
