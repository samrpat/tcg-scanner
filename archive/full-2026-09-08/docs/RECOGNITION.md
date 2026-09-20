# Recognition

Phase 3. A rectified card image in, a specific `card_variants` row out — or an honest list of
candidates for a human.

```
processed-front.jpg
      │
      ├── perceptual hash ──▶ prefilter: nearest N of ~20,000 by Hamming distance
      │                        (hashes live in Postgres; no image mirror needed)
      │
      ├── feature registration against each candidate's official art
      │        └──▶ inlier count = the strongest single signal, AND an exact homography
      │
      ├── OCR of the collector number and set code
      │        └──▶ "051/086 CRI" collapses almost all remaining ambiguity
      │
      └── fusion ──▶ card + variant + confidence
                     └── below threshold: review with ranked candidates
```

## Why registration, not just hashing

Phase 2 ended with a lesson worth carrying: **matching a known image beats hunting for edges.**
Card backs went from a 40% mess of pokéballs and rotations to 10/10 the moment they were
registered against a reference rather than detected. Recognition is the same problem with 20,000
references instead of one.

That has a consequence the roadmap did not anticipate: **recognition improves cropping.** Once we
know which card this is, registering the capture against that card's official art yields a
homography over hundreds of matched features — far better conditioned than four corners fitted
from edges. The crop can then be recomputed to a precision edge detection cannot reach, which is
exactly what the 88 × 63 mm rectification and every downstream millimetre measurement wants.
Phase 2's remaining failure (a trainer card whose artwork panel outscored its own border) is
fixed by this, not by more edge heuristics.

So the pipeline order becomes: rough crop → recognise → **re-crop from the recognised art** →
grade. Recognition is not merely a labelling step; it is the second half of rectification.

## The prefilter

Comparing a capture against 20,000 images directly is not viable on a Pi. A perceptual hash is:
64 bits per card, computed once, compared by Hamming distance. Storing the *hash* rather than the
image means the whole catalogue costs kilobytes rather than the ~2 GB a full art mirror would.

Hashes are computed from TCGdex's `low` art, which is enough for a coarse prefilter. The `high`
art is fetched on demand for the handful of candidates that reach the registration stage.

## OCR as the discriminator

Perceptual hashes confuse cards that look alike — the same Pokémon across sets, reprints,
alternate arts. The collector number does not. `051/086` plus the set code is close to a primary
key, and it sits in a fixed position on a rectified card, which is precisely what rectification
to a known physical size buys us. This is the step most projects skip and the one that collapses
the ambiguity that matters.

## Variants

Recognition identifies a *card*. Inventory points at a *variant* (D-012). Resolving which — holo
versus reverse versus 1st edition — uses the stamp and foil evidence visible in the capture plus
the set's known variant list. Where the capture cannot settle it, the review screen asks rather
than guessing, because that distinction is the difference between a $5 card and a $5,000 one.

## Orientation

Registration recovers rotation as a by-product: the homography says how the reference was rotated
to match the capture. That closes Phase 2's deferred item — upside-down and sideways captures get
corrected once the card is known, without a separate heuristic.
