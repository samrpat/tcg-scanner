"""Configuration. Everything comes from the environment; nothing is hardcoded (spec §42)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    postgres_user: str = "tcg"
    postgres_password: str = ""
    postgres_db: str = "tcg"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # API
    log_level: str = "INFO"
    cors_origins: str = ""

    # Storage
    storage_backend: str = "local"
    storage_local_root: str = "/data/images"

    # Worker
    worker_tags: str = "ingest,recognize,condition"
    worker_concurrency: int = 2

    # TCGdex
    tcgdex_base_url: str = "https://api.tcgdex.net/v2"
    tcgdex_language: str = "en"
    tcgdex_request_delay: float = 0.05

    # Pricing
    display_currency: str = "USD"
    pricing_primary_source: str = "tcgdex_tcgplayer"

    # --- Imaging (Phase 2, docs/IMAGING.md) ---
    # Pixels per millimetre in the rectified image.
    #
    # "auto" matches the source capture's own scale, clamped to the range below. That keeps
    # every real pixel the camera resolved without inventing any: upscaling a 7 px/mm photo to
    # 40 adds file size and no information, and downscaling an 80 px/mm photo to 20 throws away
    # detail that surface and edge assessment would have used.
    #
    # "fixed" pins every card to `processed_px_per_mm`, which is convenient if you want
    # identical image dimensions across the whole collection.
    #
    # Nothing downstream needs a constant: the rubric's thresholds are in millimetres and every
    # image carries its own px_per_mm, so measurement divides by the right number per card.
    processed_scale_mode: str = "auto"
    # Decode captures at half resolution when they are large enough to spare it. The dominant
    # per-card cost on a Pi is decoding and sweeping a 12 MP frame to find a card that ends up
    # ~1,300 px across. Measured: 2.5x faster, a quarter of the memory, and detection confidence
    # rises slightly. Set false to keep every original pixel through detection.
    capture_half_decode: bool = True
    # Only halve when the halved image is still at least this wide/tall. A card filling a
    # 1,600 px frame gives ~25 px/mm halved, comfortably above `quality_min_px_per_mm` (8).
    capture_half_decode_min_px: int = 1400

    processed_px_per_mm: float = 40.0
    processed_px_per_mm_min: float = 10.0
    # 80 px/mm is 5040 x 7040 — about 35 megapixels. Past this the file size stops buying
    # anything a grader can act on.
    processed_px_per_mm_max: float = 80.0
    # 97 with 4:4:4 chroma. Subsampling smears exactly the fine colour edges that whitening
    # and print-line defects live on.
    processed_jpeg_quality: int = 97

    # Margin around the card in a listing photograph, in millimetres. A crop cut exactly to the
    # card's edge gives a buyer nowhere to look: edge whitening and corner wear sit right at the
    # boundary, and without background behind them there is no reference for judging where the
    # card ends. A consistent margin on every image makes those defects legible and makes a
    # batch of listings look deliberate rather than machine-cut.
    # A price older than this is not safe to list against.
    #
    # Singles move on news — a rotation, a tournament result, a reprint announcement — and the
    # gap between a three-week-old price and today's can be the whole margin. This is the age at
    # which the system stops quoting a figure as current and asks for a refresh; it is not a
    # claim that prices are stable for a week.
    price_stale_after_days: int = 7

    # Prices are quoted in this currency and no other.
    #
    # Cardmarket figures arrive in EUR and are still stored, but they are never quoted: this
    # collection is sold on eBay US, and a euro figure shown next to a dollar sign — or shown
    # alone and mistaken for dollars — is worse than admitting the card has no usable price.
    # Nothing here converts between currencies, because a made-up exchange rate quietly becomes
    # the number someone lists against.
    price_display_currency: str = "USD"

    # What a card in each condition fetches, as a fraction of its Near Mint market price.
    #
    # Market prices from TCGplayer describe Near Mint stock. Everything below that sells for
    # less, and these are the conventional discounts sellers apply — they are a starting point
    # for a listing, not a measurement, and they are configurable because every category and
    # every seller's audience differs.
    #
    # Biased slightly low on purpose, in line with grading down when unsure: a card that sells
    # is worth more than a card priced optimistically that does not.
    price_condition_multipliers: dict[str, float] = {
        "NM": 1.00,
        "LP": 0.85,
        "MP": 0.65,
        "HP": 0.45,
        "DMG": 0.25,
    }

    # eBay takes roughly this share of the final price in fees.
    ebay_fee_fraction: float = 0.1335

    # eBay charges a fixed amount per order on top of the percentage. At this end of the market
    # it is the dominant term: on a $1 card it is a bigger bite than the percentage fee.
    ebay_fixed_fee: float = 0.30

    # What it costs to put one card in the post, and who pays it.
    #
    # With buyer-paid shipping the seller does not absorb the postage — but eBay still charges
    # its percentage on the shipping the buyer paid, so it is not free either. That fee on
    # postage plus the fixed per-order fee is what a cheap card has to clear.
    ebay_shipping_cost: float = 1.30
    ebay_buyer_pays_shipping: bool = True

    # The least a single card realistically sells for on eBay.
    #
    # TCGplayer market and eBay are not the same market, and they diverge hardest at the cheap
    # end. TCGplayer will quote $0.15 or $0.18 for a common because its sellers move them in
    # hundreds; on eBay nobody lists one card for eighteen cents, so the same card changes hands
    # at around a dollar. Observed on Frogadier 021/086, which TCGdex prices at $0.15 and whose
    # completed eBay listings run $0.99, $1.29, $1.59, $1.99 and $2.70.
    #
    # Judging "is this worth listing" against the TCGplayer figure therefore rejected cards that
    # demonstrably sell. The floor is what makes the estimate an *eBay* estimate rather than a
    # TCGplayer one — it is a floor, not a target, and any real sold price overrides it.
    ebay_floor_price: float = 0.99

    # Below this much profit a single-card listing is not worth writing, photographing and
    # posting, regardless of whether it technically clears zero.
    #
    # This is the number that decides bulk from stock, and it matters far more than any
    # condition multiplier: measured on this collection, 10 of 12 priced cards are worth under
    # $1, where a solo eBay listing loses money outright. Getting this wrong means either
    # spending an evening listing cards for a net loss, or throwing away cards that were worth
    # selling.
    ebay_min_net: float = 1.00
    # A card the feed prices below this in Near Mint goes straight to bulk. Judged on the
    # unadjusted TCGplayer NM figure so the line means one fixed thing.
    bulk_below: float = 0.50
    # ── eBay sold prices ────────────────────────────────────────────────────
    # Apify's eBay scraper, used in `sold` mode. Costs money per result, so nothing fetches
    # automatically: sales are pulled for cards the operator selects and then reused.
    apify_token: str = ""
    # Per card. Sixty completed listings is plenty for a median and keeps a run cheap; the
    # scraper bills per result.
    ebay_sold_max_results: int = 60
    # Below this many comps in the exact grade, the median widens to all conditions and says so.
    ebay_sold_min_comps: int = 3
    # Card prices move. Older comps are ignored rather than deleted.
    ebay_sold_max_age_days: int = 90
    ebay_sold_timeout: float = 120.0

    # ── Where eBay fetches listing photographs from ─────────────────────────
    # Anything S3-compatible: Cloudflare R2 (free tier, no egress fees), Backblaze B2,
    # Amazon S3, MinIO. Preferred over the Cloudflare quick tunnel because a tunnel can lose
    # its hostname while still reporting itself healthy, and eBay shows no error when a
    # picture fetch fails — the draft simply has no photographs.
    s3_endpoint: str = ""        # e.g. https://<account>.r2.cloudflarestorage.com
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "auto"
    # The address the public actually reads from, which is rarely the upload endpoint.
    s3_public_base: str = ""     # e.g. https://pub-xxxx.r2.dev

    # ── Listing defaults eBay's category template requires ──────────────────
    # Marked required by eBay (`*`) and not derivable from a card. The defaults match the
    # operator's own sold listings: no returns, one business day to dispatch.
    ebay_dispatch_days: int = 1
    ebay_returns: str = "ReturnsNotAccepted"

    # ── Scanner mode ────────────────────────────────────────────────────────
    # When on, this is a scanner: cards are cropped, rendered and cut into corners, and never
    # identified or priced. That is the half of the job no listing tool does, and the operator
    # is using one of those for the rest.
    #
    # A setting rather than a rewrite, because the identification and pricing paths still work
    # and the choice of tool is not permanent. Turning this off puts every screen back.
    scanner_mode: bool = True

    listing_margin_mm: float = 5.0

    # Every listing image is rendered at this fixed scale, so all of them come out the same
    # pixel size regardless of how close the card was shot. Without it the output tracked the
    # capture distance — 32 to 41 px/mm across one batch, giving images from 2336 to 2774 px
    # wide, and a card's own front and back differing from each other. In a marketplace gallery
    # that reads as carelessness.
    #
    # 30 is below the lowest capture scale seen (32), so this only ever downsamples: upscaling
    # would invent detail and make a soft capture look worse, not better.
    # Was 30, which was *below* what the captures actually carry: a phone photo of a card
    # filling the frame yields ~36 px/mm, so the listing render — the image a buyer zooms into
    # — was being downscaled from the detail that already existed. 40 is at or just above
    # native for a 12 MP capture, so nothing is thrown away, and the render stays a fixed
    # scale because a batch where every card sits in the same frame reads as deliberate.
    listing_px_per_mm: float = 40.0
    # Above this price a card is worth four extra photographs. Corner close-ups are what a
    # buyer zooms at, and on a cheap card the extra upload time costs more than the doubt they
    # remove. Suggested, never enforced: the button is on every card.
    detail_shots_above: float = 5.0
    # Below this a capture opens an image review instead of proceeding silently.
    detection_min_confidence: float = 0.55
    # Variance of Laplacian below this reads as soft focus.
    # Blur floor, measured at BLUR_REFERENCE_WIDTH (see imaging/quality.py) so it means the
    # same thing at any capture resolution. Provisional: calibrated against 20 real captures
    # spanning 273-1280 (median 719), with TCGdex's own pristine art scoring 3101 for scale.
    # 150 sits well clear of the softest genuinely usable capture, so it will catch a badly
    # out-of-focus or motion-blurred frame without failing ordinary hand-held photographs.
    # It has NOT been calibrated against a truly blurry sample, because none of the twenty is
    # one — tighten it once a real failure exists to measure.
    quality_blur_min: float = 150.0
    # Effective scale below which a 2.5mm² defect is under ~13px and unmeasurable.
    quality_min_px_per_mm: float = 8.0
    quality_max_clipped_fraction: float = 0.08
    quality_max_glare_fraction: float = 0.04
    # A 48MP phone JPEG at maximum quality can exceed 25MB.
    capture_max_bytes: int = 60 * 1024 * 1024

    # Surfaced to the UI so it can tell a phone exactly which URL to open. The camera is
    # unavailable over plain HTTP anywhere but localhost, and guessing the port would be worse
    # than saying nothing.
    web_tls_port: int = 8443

    testing: bool = False

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    @property
    def tags(self) -> list[str]:
        return [t.strip() for t in self.worker_tags.split(",") if t.strip()]

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
