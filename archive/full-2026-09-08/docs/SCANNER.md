# Scanner mode

The app as a card scanner: shoot a pile, get clean photographs, hand the folder to whatever is
doing the listing, start the next pile. No identification, no pricing, no eBay.

This is the half of the job that listing tools — CardUploader, SortSwift, TCG Upload — do not do.
They take "phone photos, any angle" and build a listing around them. What comes out of here is a
card rectified to a true 88 × 63 mm, squared, sitting in a known margin of real background, with
each corner cut out at a resolution where whitening is judgeable.

Turn it off with `SCANNER_MODE=false` in `.env`, and every identification, pricing and eBay
screen comes back exactly as it was. Nothing was removed to build this.

## The loop

1. **Scan** on a phone, or **Capture** on this machine. Front then back — they pair themselves.
2. **Batch** shows every card in the current pile, large. Large on purpose: a thumbnail grid
   hides exactly the flaw this screen exists to catch — a crop that clipped a corner, or a card
   shot back-first.
3. **Download all photos (.zip)**.
4. **Archive & start next.**

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

`?layout=folders` gives a directory per card instead. `?kind=all` adds the untouched originals,
for anything that would rather do its own cropping.

### Turning corner close-ups off

The **corner close-ups** switch in the Batch bar leaves them out of the download — two files per
card instead of six, and roughly a third of the size. Worth having on a card somebody will zoom
into; dead weight on a bulk common.

The switch is remembered. With it off, **remove the ones already made** deletes the corner images
from disk as well; they are derived, so nothing original is lost and they can be cut again at any
time. The front and back are never optional — a listing with no picture of the card is not a
listing.

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
- **A bad crop** — Approve has corner handles; adjusting them reprocesses the card.
- **A card you do not want** — `delete` removes it and its photographs.

## Batches

A batch is a pile worked in one sitting. New scans join the open one; **Archive & start next**
closes it and opens a fresh one in the same action, so the next card never lands nowhere.

**Archiving clears the screen.** That is what it is for: the batch's cards leave the view, and
the next pile starts on an empty one. Nothing is deleted — the cards and photographs are under
**Earlier batches**, where **open** brings them back on screen, **photos** re-downloads the
folder, and **scan into this** reopens the batch for more cards.
