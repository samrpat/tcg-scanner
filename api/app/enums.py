"""Native Postgres enums. Values are what land in the database — keep them stable."""

from enum import StrEnum


class Condition(StrEnum):
    """Canonical condition scale. Translated per marketplace at listing time (D-004)."""

    NM = "NM"
    LP = "LP"
    MP = "MP"
    HP = "HP"
    DMG = "DMG"


class Severity(StrEnum):
    """TCGplayer imperfection severity. Point values live in app.conditioning.rubric."""

    SLIGHT = "slight"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


class ImageKind(StrEnum):
    ORIGINAL_FRONT = "original_front"
    ORIGINAL_BACK = "original_back"
    PROCESSED_FRONT = "processed_front"
    PROCESSED_BACK = "processed_back"
    # Presentation copies for marketplace listings. Deliberately separate from the processed
    # images: those are exactly 88 x 63 mm because every condition measurement divides by that
    # scale, while a listing photograph needs a margin around the card so a buyer can see where
    # the card ends and judge its edges.
    LISTING_FRONT = "listing_front"
    LISTING_BACK = "listing_back"
    # Corner close-ups: each quarter of the front, enlarged. On a card worth enough that a
    # buyer will zoom, the four corners are what they zoom at — corner whitening and edge wear
    # decide the grade, and a whole-card photograph resolves neither.
    DETAIL_FRONT_TL = "detail_front_tl"
    DETAIL_FRONT_TR = "detail_front_tr"
    DETAIL_FRONT_BL = "detail_front_bl"
    DETAIL_FRONT_BR = "detail_front_br"
    # Up to three extra photographs, taken by hand, showing whatever the standard six do not.
    # A holo tilted so the foil reads, a crease close up, a signature, the edge of a thick card.
    #
    # They are never rectified, and that is the point of having them: every other image here is
    # squared and flattened because that is what makes a card measurable, and it is exactly that
    # treatment which hides a holo, flattens a dent and evens out the light on a scuff. What the
    # operator framed by hand is the content.
    #
    # Three slots rather than an open-ended list. Past three a buyer is scrolling, not looking.
    EXTRA_1 = "extra_1"
    EXTRA_2 = "extra_2"
    EXTRA_3 = "extra_3"


class CaptureSource(StrEnum):
    """Where an image came from. `picamera` exists now so a future fixed rig's captures are
    distinguishable from handheld ones in the data."""

    UPLOAD = "upload"
    WEBCAM = "webcam"
    PICAMERA = "picamera"
    IMPORT = "import"


class QualityVerdict(StrEnum):
    OK = "ok"
    REVIEW = "review"
    REJECT = "reject"


class AssessmentSource(StrEnum):
    AI = "ai"
    HUMAN = "human"
    IMPORT = "import"


class InventoryStatus(StrEnum):
    CAPTURED = "captured"      # images in, nothing identified yet
    IDENTIFIED = "identified"  # card and variant resolved
    GRADED = "graded"          # condition assessed
    PRICED = "priced"
    READY = "ready"            # cleared review, listable
    LISTED = "listed"
    SOLD = "sold"
    ARCHIVED = "archived"


class ListingKind(StrEnum):
    INDIVIDUAL = "individual"
    LOT = "lot"


class ListingStatus(StrEnum):
    DRAFT = "draft"
    PREVIEW = "preview"
    PUBLISHED = "published"
    ENDED = "ended"
    FAILED = "failed"


class Marketplace(StrEnum):
    EBAY = "ebay"
    TCGPLAYER = "tcgplayer"
    CARDMARKET = "cardmarket"
    COLLECTR = "collectr"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewCategory(StrEnum):
    IDENTIFICATION = "identification"
    VARIANT = "variant"
    CONDITION = "condition"
    IMAGE = "image"
    PRICING = "pricing"
    COLLECTR = "collectr"
    MARKETPLACE = "marketplace"


class ReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class PriceType(StrEnum):
    MARKET = "market"
    LOW = "low"
    MID = "mid"
    HIGH = "high"
    DIRECT_LOW = "direct_low"
    TREND = "trend"
    AVG1 = "avg1"
    AVG7 = "avg7"
    AVG30 = "avg30"
