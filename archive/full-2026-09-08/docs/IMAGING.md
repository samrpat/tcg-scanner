# Imaging

Phase 2. Photo in, a card rectified to known physical dimensions out.

```
capture (browser)          worker (queued)
    │                          │
 original-front.jpg  ──▶  detect quad → order corners → warp → orient
 original-back.jpg              │                                │
 written once,                  ▼                                ▼
 never touched            quality checks              processed-{front,back}.jpg
                                │                     1260 × 1760 @ 20 px/mm
                                ▼
                       ok │ review │ reject
```

## The dewarp target, and why it is a physical size

Every card is warped to **88 x 63 mm**, the standard Pokémon/Magic size. That is the invariant.
The *resolution* it is stored at is not fixed — it follows the capture.

This is the hinge the whole product turns on. `docs/CONDITION.md` specifies defects in mm and mm²
— 20 mm of edgewear, a 2.5 mm² defect. Once every card occupies the same known rectangle, a
measured pixel area converts to mm² by division, and grading becomes arithmetic instead of a
model that needs training (D-007). Nothing downstream needs the resolution to be constant,
because every image records its own `px_per_mm` and `app.imaging.geometry` holds the one
conversion both directions.

### Scale follows the capture

`PROCESSED_SCALE_MODE=auto` (the default) rectifies at roughly the scale the camera actually
resolved, clamped to `[PROCESSED_PX_PER_MM_MIN, PROCESSED_PX_PER_MM_MAX]` = 10 to 80 px/mm.

Neither extreme is free. Upscaling a 7 px/mm photo to 40 px/mm quadruples the file and invents
no detail — it just makes a bad capture look expensive. Downscaling an 86 px/mm phone photo to 20
throws away real detail that surface and edge assessment would have used. Matching the source
keeps every real pixel and no fake ones.

| Capture | Source | Stored | Pixels | ~MB |
|---|---|---|---|---|
| Hand-held, card at 16% of a 1080p frame | 7.6 | 10 | 630 x 880 | 0.2 |
| 1080 x 1920, card fills the guide | 18.4 | 18 | 1134 x 1584 | 0.6 |
| 4K webcam, card fills the frame | 33 | 33 | 2079 x 2904 | 2.1 |
| 12MP phone, fills frame | 43 | 43 | 2709 x 3784 | 3.6 |
| 48MP phone, fills frame | 86 | **80** (capped) | 5040 x 7040 | 12.4 |

At the low end a 2.5 mm² defect — the smallest threshold in the rubric — is 250 px²; at 43 px/mm
it is 4,600 px². More is genuinely better for grading, up to the point where the sensor stops
resolving new detail, which is what the cap is for.

Set `PROCESSED_SCALE_MODE=fixed` to pin every card to `PROCESSED_PX_PER_MM` instead, if identical
dimensions across the collection matter more than fidelity.

### Storage

At 2,000 cards with four images each, a 12MP phone filling the frame is roughly **30 GB**; the
same collection shot at 1080p is about **4 GB**. Worth deciding before the scanning session, not
after. Lower `PROCESSED_PX_PER_MM_MAX` to trade fidelity for disk.

### Encoding

JPEG at quality 97 with **4:4:4 chroma**. The default 4:2:0 halves colour resolution, which
smears precisely the fine colour edges that edge whitening and print-line defects consist of.
4:4:4 costs perhaps 15% more bytes and keeps them.

## Geometry only. Never colour.

The processed image is rectified and nothing else. **No white balance, no auto-levels, no contrast
normalisation, no sharpening.**

Condition assessment reads whitening, gloss and scuffing straight off these pixels. Auto-levels
would erase edge whitening — the single most common defect — by pulling the border back to black.
Recognition wants normalised input, but it can normalise in memory at match time; the stored
artefact stays photometrically faithful.

## Card backs are registered, not detected

Every English Pokémon card has the identical back. That turns locating one from a *detection*
problem into a *registration* problem, and registration is strictly better wherever it applies:

| Failure mode of edge detection on backs | Why registration does not have it |
|---|---|
| Locks onto the pokéball or an inner frame | Matches the whole design, not a rectangle |
| Cannot tell 0° from 180°, gets 90° wrong on landscape sub-regions | Recovers orientation from the artwork |
| Four independently fitted corners come out skewed | A homography from hundreds of matches is over-determined |
| Blue border on a black mat has no luminance step | Features are interior texture, not the boundary |

ORB features are matched against a reference back, RANSAC fits a homography, and the reference's
own corners project into the photo. Measured on ten real captures: **10/10 backs registered,
0/10 fronts falsely registered, ~30 ms each** with 217-319 inliers apiece.

Because the same discriminator answers "is this a back?", `process_capture` simply tries
registration first and falls through to edge detection when it declines.

### The reference, and its 4% correction

`assets/pokemon-back.jpg` was cut from one of the user's own captures, so its crop sits slightly
inside the card. `REFERENCE_EXPANSION` corrects that, and the figure was measured rather than
guessed: sweeping the expansion against border evidence across ten independent backs peaks at
**1.04**, nine of ten agreeing within 0.01, and line-fitting the true border on the one back
where the fitter succeeded independently gave 1.029. Replace the asset with an exactly-cropped
scan and the constant becomes 1.0.

ORB rather than SIFT: no patent questions, fast enough per capture on a Pi, and a card back is
high-contrast line art, which corner features like. Feature count is capped at 1200 — brute-force
Hamming matching is quadratic, and 3000 took the test suite from 5s to 83s for no extra accuracy.

## Detection

**No single edge strategy works everywhere.** Canny with Otsu thresholds is excellent on a card
lying on a plain mat and finds *nothing at all* when the card is held up against a face and a
textured wall — the scene's dominant contrast is elsewhere, so the card's own outline does not
survive thresholding. This was not a theory; it is what the first real photographs did.

So all four strategies run, every candidate goes into one pool, and the best-scoring quad wins:

| Strategy | Earns its place on |
|---|---|
| Canny + Otsu | Card on a plain, evenly lit background |
| Adaptive threshold | Uneven lighting, busy backgrounds |
| Morphological gradient | Low-contrast boundaries |
| Saturation gradient | Printed card against skin or a wall |

```
downscale to ~1000px  →  four edge masks  →  contours (RETR_LIST)
    →  approxPolyDP over the contour AND its convex hull, fine to coarse epsilons
    →  filter: convex, plausible area, fills its own quad
    →  suppress quads contained in a larger plausible quad
    →  best by score  →  sub-pixel corner refinement  →  warpPerspective
```

Four details that each came from a real failure:

- **`RETR_LIST`, not `RETR_EXTERNAL`.** A held card sits inside the outline of the hand and body.
  Restricting to outermost contours loses it entirely.
- **The convex hull is approximated too.** A real card's raw contour is ragged — rounded corners,
  fingers crossing the edge — and rarely simplifies to exactly four vertices at any epsilon. Its
  hull does, reliably, because a card is convex and the noise is all inward.
- **Epsilons run fine to coarse and every 4-vertex result is kept.** A coarse-only sweep meant a
  clean card back collapsed to two vertices at every epsilon and was never seen.
- **Interior quads are suppressed.** A card's artwork window is a clean rectangle whose aspect can
  fit a *sideways* card better than the card's own perspective-skewed outline does. Without
  suppression the detector rectifies the picture on the card instead of the card.

### Getting the crop exact

The corners a contour gives you are wrong in three ways at once. Thresholding biases the boundary
outward. A real card has **rounded corners**, so the contour's extreme point is not where the
card's straight edges would meet. And anything crossing the edge — a finger, a shadow, a sleeve
seam — drags the trace with it. At 40 px/mm a six-pixel bias is 0.15 mm of card that is not
there, sitting exactly where edgewear is measured.

Fitting fixes all three. Each edge is sampled at 48 points; at each one the sub-pixel position of
the intensity step is found by looking along the edge normal and fitting a parabola to the
gradient peak. A line is then fitted through those points **by RANSAC**, and adjacent lines are
intersected to give the corners.

Three details matter:

- **RANSAC, not sigma-clipping.** A finger does not scatter samples randomly — it puts a
  *consistent run* of them on a different straight line, and iterative clipping fits halfway
  between the two. On a real fingered edge that was a 3.5 px residual; RANSAC gives 0.3 px.
- **The ends of each edge are skipped.** Cards have rounded corners, so the last 12% of an edge
  curves away. Fitting only the straight part and intersecting recovers the corner of the card's
  *virtual* sharp rectangle — which is what rectification needs and what no corner detector can
  see, because the card does not physically have one.
- **Refinement runs at full resolution.** Detection uses a ~1000px copy because a card outline is
  a large-scale feature, but that caps precision at one working pixel — four full-resolution
  pixels on a 12MP capture.

Measured corner error on synthetic scenes: **11 px** raw, **1.0–1.6 px** with `cornerSubPix`,
**0.51–0.73 px** with full-resolution line fitting. `cornerSubPix` remains the fallback when four
clean edges cannot be fitted.

Manually placed corners get the same treatment: a hand placement is good to perhaps ten pixels,
and the fitter snaps it to the true edge — but only if it moves less than 14 px, so it sharpens
the operator's intent rather than overriding it.

### What was tried and rejected

Following the card's **traced outline** with a Coons patch, to correct lens bow. Measured against
both a real photograph and synthetic barrel distortion it produced a *worse* result than the
plain homography (similarity 0.73 against 0.91) — the traced outline is not reliably the card's
outer edge, so the warp followed a finger and rippled the card. The code and its guards are kept
behind `ENABLE_BOUNDARY_WARP` (off) because the approach is sound and the failure is diagnosable,
but it does not ship until it demonstrably beats the homography.

Two shape tests, because either alone is fooled. **Fill** (contour ÷ quad area) catches a hand
approximated to four points, but a finger crossing the card edge also drags it down on a perfectly
good card. **Convexity** (contour ÷ hull area) stays high for the occluded card and low for a hand
with splayed fingers. A candidate passes on fill, or on convexity plus a relaxed fill.

Below `DETECTION_MIN_CONFIDENCE` the item opens a Review of category `image` rather than returning
a wrong answer with a confident face.

## Benchmarking against real captures

Synthetic scenes catch regressions; they cannot tell you that a Pokémon card back contains a
pokéball whose bounding quad is card-shaped, or that a play mat's aspect matches a sideways
card. Both of those were found by looking at actual photographs, so every stored original is a
benchmark case:

```bash
make benchmark
```

It runs detection over every original and reports, per image, the method, confidence, area
fraction, aspect, frame margin and verdict — then scores each against plausibility bounds
learned from real data. Not every capture contains a card, so label the exceptions and a
non-detection there counts as the pass it is:

```bash
make mark ARGS="CARD-000005 CARD-000008 --no-card"
```

Everything in the current detector's scoring came from this loop rather than from reasoning:

| Failure seen | Fix |
|---|---|
| Card lost to the mat it lay on | Suppression by score, not by size |
| Pokéball detected instead of the card back | Size scored as a band, not monotonically |
| Artwork window detected instead of the card | Landscape orientation penalised |
| Mat detected on a frame-filling capture | Frame-hugging quads penalised |
| A bad crop passing as "ok" | Detections under 18% of frame flagged |

## When detection fails anyway

It will. A card at 16% of a cluttered frame is genuinely hard, and a card that cannot be rectified
is a card that cannot be sold. So a failed capture is never a dead end: **Set corners** in the
capture strip opens the original with four draggable handles, and rectifying from them stores a
`manual` detection at confidence 1.0 with exactly the same fields an automatic one carries,
written by the same function. A human placed those corners; there is nothing uncertain about them.

The handles are **hollow rings with a crosshair**, not filled dots, because a solid dot covers
precisely the pixel you are trying to line up with. Selecting a corner opens a **magnifier** on
the opposite side of the frame showing the original pixels around it at 3-16x with its
coordinate, and the arrow keys nudge by one pixel (`shift` for ten). Placement is verifiable
rather than guessed — at 40 px/mm, being three pixels out is 0.075 mm, but at the frame's display
scale three pixels is invisible.

## Getting a capture worth grading

The quality gate is not being fussy. These are the numbers:

- **Fill the frame with the card — but leave a margin.** At 1080 x 1920 a card filling 84% of the
  height gives ~18 px/mm; the same card at 16% of the frame gives **7.6 px/mm**, below the
  measurable limit. But if the card *touches* the frame edge it has been cropped by it, and there
  is then no visible border on that side to fit, so the crop cannot be exact however good the
  detector is. The quality report flags this explicitly.
- **Plain, contrasting background.** A dark mat under the card takes detection from "sometimes" to
  "always". A hand, a face and a textured wall is the hard case.
- **Steady and in focus.** Blur score is measured and reported; a soft photo invents scuffing.
- **Diffuse light, off-axis.** Cross-polarisation if you can, for holos.

The capture screen shows the live resolution and the px/mm you would get *if the card filled the
guide*, so this is checkable before shooting a thousand cards rather than after.

## Cameras: one path, every device

A phone and a laptop run the **identical** capture path — same live preview, same alignment
guide, same shutter, same queue, same detection and rectification. Nothing branches on device
type, and `images.source` is a label for later analysis, not a switch.

The preview shows the **whole frame at the stream's true aspect ratio**. An earlier version used
a fixed 4:3 box with `object-fit: cover`, which cropped a 9:16 phone stream — so the guide the
operator lined the card up against was not where the card landed in the file. A guide that lies
is worse than no guide.

Capture happens at the sensor's full resolution: `getCapabilities()` is queried and the maximum
applied where the browser supports it, and the frame is grabbed at `videoWidth × videoHeight`,
never at the size the preview happens to be laid out at.

### Why a phone needs HTTPS

Browsers expose `getUserMedia` only in a **secure context**: HTTPS, or `localhost`. A laptop at
`http://localhost:8080` therefore gets the camera and a phone at `http://192.168.x.x:8080` never
will — there is no permission, flag or prompt that changes it.

So the server can serve HTTPS:

```bash
make cert     # self-signed, covering localhost and every LAN address this machine has
make up
```

Then open `https://<lan-ip>:8443` on the phone and accept the certificate warning once. The
certificate is valid for 825 days, the longest Safari accepts.

TLS is entirely optional: without a certificate nginx serves plain HTTP exactly as before, and
the entrypoint says so in the logs rather than failing to start. When the page is not a secure
context the capture screen detects it precisely and prints the exact HTTPS URL to open, instead
of reporting a camera failure it cannot explain.

The native camera picker (`capture="environment"`) remains as a **fallback only**, offered when
the in-page camera is unavailable. It shoots at full resolution but through the OS camera app,
which means no alignment guide and no live resolution readout — the operator is framing blind,
and that is exactly what produced the 7.6 px/mm captures.

## Throughput

Capture returns as soon as the original is on disk; detection and dewarp run on the queue. The
shutter never waits for the pipeline, which is what keeps the loop inside its 36 s/card budget
(D-005).
