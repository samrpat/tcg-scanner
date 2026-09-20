"""Read the collector number off a rectified card.

The collector number is the only thing on a Pokémon card that identifies it *uniquely and
textually*. Art is reprinted across sets — two Gyarados from Ancient Origins and Generations
scored 191 and 181 ORB inliers against the same capture, which no amount of feature matching
can separate, because the pictures really are the same. "20/98" and "21/83" are not.

This works here and would not work on a raw photo, because Phase 2 rectifies every card to
exactly 88x63 mm. That turns "where is the collector number" from a search problem into
arithmetic: it sits in a known rectangle measured in millimetres, whatever the camera did.

Two eras, two places:
- Sword & Shield and earlier put it bottom-RIGHT ("35/113").
- Scarlet & Violet moved it bottom-LEFT and dropped the set total on many cards ("086/198").
Both regions are read and the better-scoring result wins, so no era detection is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

# Card geometry, millimetres. The regions are generous: they must survive the ~1 mm of slop that
# the outset in recrop leaves, and cropping a digit in half costs far more than including some
# surrounding art.
CARD_W_MM = 63.0
CARD_H_MM = 88.0

# (x0, y0, x1, y1) in mm from the card's top-left.
REGION_BOTTOM_RIGHT = (36.0, 80.8, 60.5, 85.4)
REGION_BOTTOM_LEFT = (3.0, 80.8, 27.0, 85.4)

# Tesseract: single text line, digits and separators only. The whitelist is what makes this
# reliable — without it "1" becomes "l", "0" becomes "O", and "/" becomes "7".
# Two whitelists, tried in that order. Letters are needed for promo prefixes ("XY48") but they
# actively corrupt ordinary numbers: with letters allowed, tesseract read the "102/086" on
# CARD-000011 as "C02/086", losing the leading 1 and turning card 102 into card 2. Digits-only
# cannot make that class of mistake, so it goes first and letters are the fallback.
_DIGITS_ONLY = "0123456789/"
_WITH_LETTERS = "0123456789/ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Page segmentation modes, cheapest-to-most-general. 7 assumes one line and is right for older
# cards; 6 and 11 are needed because the crop usually catches the illustrator credit on the line
# above the number, and 7 mangles two lines into one string.
TESS_MODES = (7, 6, 11)

# Height the text patch is normalised to before thresholding, in pixels.
#
# A fixed *multiplier* was wrong for the same reason the old blur floor was: it makes behaviour
# depend on the capture's resolution. The adaptive threshold's block size is derived from the
# patch height, so at 40 px/mm the block covered a different fraction of a digit than at 20, and
# the same card read "35/113" at one resolution and "4 5/13" at another — which then identified
# a Legendary Treasures Empoleon as a Black & White Zebstrika.
#
# Normalising to a fixed height makes one set of thresholding constants correct everywhere.
# ~2 mm of cap height inside a 4.6 mm band lands around 65 px of text at this size, comfortably
# above the ~30 px tesseract wants.
OCR_TARGET_HEIGHT = 150

_NUMBER = re.compile(r"(?<![0-9])([0-9]{1,3})\s*/\s*([0-9]{1,3})(?![0-9])")
# Promos print a set prefix and no total: "XY48", "SWSH123", "SV001". Worth catching because a
# promo is exactly the case where art matching is weakest — the same art is often a promo *and*
# a set card, and the prefix is the only thing that separates them.
_PROMO = re.compile(r"\b([A-Z]{2,4})\s?([0-9]{1,3})\b")
_BARE = re.compile(r"(?<![0-9])([0-9]{2,3})(?![0-9/])")


@dataclass(frozen=True)
class CollectorNumber:
    """A collector number read off a card.

    `total` is None for Scarlet & Violet cards that print only the number.
    """

    number: int
    total: int | None
    text: str
    region: str
    confidence: float
    prefix: str | None = None

    @property
    def key(self) -> str:
        """Normalised for comparison against a catalogue's `local_id`, which is a string and may
        carry leading zeros ("086") or letters ("TG12")."""
        return f"{self.prefix}{self.number}" if self.prefix else str(self.number)

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "total": self.total,
            "prefix": self.prefix,
            "text": self.text,
            "region": self.region,
            "confidence": round(self.confidence, 3),
        }


def _crop_mm(image: np.ndarray, region: tuple, px_per_mm: float) -> np.ndarray | None:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = (int(round(v * px_per_mm)) for v in region)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 10 or y1 - y0 < 6:
        return None
    return image[y0:y1, x0:x1]


def _binarisations(patch: np.ndarray):
    """Yield candidate binarisations of a text patch, best-guess first.

    One global threshold is not enough. The collector number sits over whatever the artwork
    happens to be doing at the bottom of the card, and on a full art or a card whose picture
    bleeds down to the border, that is a strong gradient across the patch. Measured on
    CARD-000014 (Empoleon 35/113): Otsu drove the right-hand half of the patch solid black and
    erased the number completely, while "Illus. kawayoo" on the lighter left-hand side survived.

    Adaptive thresholding fixes exactly that, because it decides per neighbourhood. Both
    polarities are yielded because the number is dark-on-light on bordered cards and
    light-on-dark on full arts, and Otsu is kept as a fallback for flat, high-contrast patches
    where a global cut is actually the cleaner one.
    """
    grey = patch if patch.ndim == 2 else cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    # Normalise to a fixed height so every downstream constant means the same thing whatever
    # resolution the capture happened to be rectified at.
    scale = OCR_TARGET_HEIGHT / max(1, grey.shape[0])
    grey = cv2.resize(
        grey,
        (max(1, round(grey.shape[1] * scale)), OCR_TARGET_HEIGHT),
        interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
    )
    grey = cv2.bilateralFilter(grey, 7, 60, 60)

    # Block must span more than a stroke but less than the gradient. The text is ~2mm tall and
    # the patch is upscaled, so tie it to patch height rather than hard-coding pixels.
    block = max(15, int(grey.shape[0] * 0.35) | 1)

    for invert in (False, True):
        flag = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        yield cv2.adaptiveThreshold(
            grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, flag, block, 10
        )
    for invert in (False, True):
        flag = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        _, binary = cv2.threshold(grey, 0, 255, flag + cv2.THRESH_OTSU)
        yield binary


def _clean(binary: np.ndarray) -> np.ndarray:
    """Drop specks and pad. Tesseract wants dark text on white with a quiet border."""
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8), iterations=1
    )
    return cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def _read(patch: np.ndarray, mode: int, whitelist: str) -> tuple[str, float]:
    import pytesseract

    config = f"--psm {mode} -c tessedit_char_whitelist={whitelist}"
    data = pytesseract.image_to_data(
        patch, config=config, output_type=pytesseract.Output.DICT
    )
    words, confs = [], []
    for text, conf in zip(data["text"], data["conf"], strict=False):
        text = text.strip()
        if not text:
            continue
        try:
            value = float(conf)
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        words.append(text)
        confs.append(value)
    if not words:
        return "", 0.0
    return " ".join(words), float(np.mean(confs)) / 100.0


def _parse(text: str, region: str, confidence: float) -> CollectorNumber | None:
    match = _NUMBER.search(text)
    if match:
        number, total = int(match.group(1)), int(match.group(2))
        # Sanity: a card cannot be number 250 of a 100-card set. Secret rares exceed the total
        # (e.g. 114/113), so allow a margin rather than requiring number <= total.
        if 1 <= number <= total + 40 and 10 <= total <= 400:
            return CollectorNumber(number, total, text, region, confidence)
        return None
    promo = _PROMO.search(text)
    if promo:
        prefix, number = promo.group(1), int(promo.group(2))
        # "ILLUS" and "CRI" are printed right next to the number and would otherwise read as
        # promo prefixes.
        if prefix not in {"ILLUS", "CRI", "EN", "HP", "NO", "WT", "PO"} and 1 <= number <= 400:
            return CollectorNumber(number, None, text, region, confidence * 0.8, prefix)

    bare = _BARE.search(text)
    if bare:
        number = int(bare.group(1))
        if 1 <= number <= 400:
            # No total to cross-check, so it is worth materially less. Say so in the confidence
            # rather than pretending a bare number is as good as a checked pair.
            return CollectorNumber(number, None, text, region, confidence * 0.6)
    return None


def read_collector_number(
    image: np.ndarray, px_per_mm: float | None = None
) -> CollectorNumber | None:
    """Read the collector number from a rectified card front.

    `image` must be the dewarped card, not a raw photo: the regions are in card millimetres.
    Returns the best-scoring read across both era regions, or None.
    """
    if image is None or image.size == 0:
        return None
    if px_per_mm is None:
        px_per_mm = image.shape[1] / CARD_W_MM

    results: list[CollectorNumber] = []
    for name, region in (
        ("bottom_right", REGION_BOTTOM_RIGHT),
        ("bottom_left", REGION_BOTTOM_LEFT),
    ):
        patch = _crop_mm(image, region, px_per_mm)
        if patch is None:
            continue
        found = False
        for whitelist in (_DIGITS_ONLY, _WITH_LETTERS):
            for binary in _binarisations(patch):
                cleaned = _clean(binary)
                for mode in TESS_MODES:
                    try:
                        text, confidence = _read(cleaned, mode, whitelist)
                    except Exception:
                        # OCR is an enhancement, never a reason to fail a capture.
                        continue
                    parsed = _parse(text, name, confidence)
                    if parsed is not None:
                        results.append(parsed)
                        if parsed.total is not None:
                            # A number/total pair that passed the sanity check is as good as
                            # this gets, so stop burning OCR passes on this region.
                            found = True
                            break
                if found:
                    break
            if found:
                break

    if not results:
        return None
    # A read carrying a set total is strictly better evidence than a bare number, so rank on
    # that first and only then on tesseract's own confidence.
    return max(results, key=lambda r: (r.total is not None, r.prefix is not None, r.confidence))
