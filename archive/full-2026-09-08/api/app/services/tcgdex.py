"""TCGdex client and payload normalisation.

Normalisation is a pure function so it can be tested against a recorded fixture rather than
the live API (REQ-TST-003).
"""

import hashlib
import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_RETRYABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)


class TCGdexClient:
    """Thin async client. TCGdex publishes no hard rate limit and asks callers to be
    considerate, so requests are serialised behind a small delay and retried with backoff."""

    def __init__(self, base_url: str | None = None, language: str | None = None) -> None:
        self.base_url = (base_url or settings.tcgdex_base_url).rstrip("/")
        self.language = language or settings.tcgdex_language
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TCGdexClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "tcg-scanner/1.0 (self-hosted)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _get(self, path: str) -> Any:
        assert self._client is not None, "use TCGdexClient as an async context manager"
        url = f"{self.base_url}/{self.language}/{path.lstrip('/')}"
        response = await self._client.get(url)
        response.raise_for_status()
        return response.json()

    async def list_sets(self) -> list[dict]:
        return await self._get("sets")

    async def get_set(self, set_id: str) -> dict:
        return await self._get(f"sets/{set_id}")

    async def get_card(self, card_id: str) -> dict:
        return await self._get(f"cards/{card_id}")


# --- Normalisation ------------------------------------------------------------------------

def content_hash(payload: dict) -> str:
    """Stable hash of an upstream payload, used to skip unchanged rows on re-sync."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_set(payload: dict) -> dict:
    serie = payload.get("serie") or {}
    counts = payload.get("cardCount") or {}
    return {
        "tcgdex_id": payload["id"],
        "name": payload.get("name") or payload["id"],
        "series_id": serie.get("id"),
        "series_name": serie.get("name"),
        "logo_url": payload.get("logo"),
        "symbol_url": payload.get("symbol"),
        "card_count_official": counts.get("official"),
        "card_count_total": counts.get("total"),
        "release_date": payload.get("releaseDate"),
        "content_hash": content_hash(payload),
    }


def normalize_card(payload: dict) -> dict:
    return {
        "tcgdex_id": payload["id"],
        "local_id": str(payload.get("localId") or ""),
        "name": payload.get("name") or "",
        "category": payload.get("category"),
        "rarity": payload.get("rarity"),
        "illustrator": payload.get("illustrator"),
        "hp": payload.get("hp"),
        "types": payload.get("types"),
        "image_url": payload.get("image"),
        "raw": payload,
        "content_hash": content_hash(payload),
    }


def stamp_key(stamp: list | None) -> str:
    """Order-independent key for a stamp list, so variant uniqueness is stable."""
    return ",".join(sorted(str(x) for x in stamp)) if stamp else ""


# Known tokens get a hand-written label. str.title() renders "1st-edition" as "1St Edition",
# which is the kind of detail that makes a listing look automated.
_LABELS: dict[str, str] = {
    "normal": "Normal",
    "holo": "Holo",
    "reverse": "Reverse Holo",
    "unlimited": "Unlimited",
    "shadowless": "Shadowless",
    "1st-edition": "1st Edition",
    "1999-2000-copyright": "1999-2000 Copyright",
    "standard": "Standard",
    "jumbo": "Jumbo",
}


def _pretty(token: str) -> str:
    if token in _LABELS:
        return _LABELS[token]
    # Capitalise the first letter of each hyphen-separated word without touching the rest,
    # so "1st" stays "1st" rather than becoming "1St".
    return " ".join(w[:1].upper() + w[1:] for w in token.split("-") if w)


def _variant_label(type_: str | None, subtype: str | None, stamp: list | None) -> str:
    tokens = ([type_] if type_ else []) + ([subtype] if subtype else [])
    tokens += [str(s) for s in (stamp or [])]
    return " · ".join(_pretty(t) for t in tokens) or "Standard"


def normalize_variants(payload: dict) -> list[dict]:
    """Turn a card payload into one row per collectible variant.

    Prefers `variants_detailed`, which carries the stable `variantId` and distinguishes
    shadowless from unlimited and 1st-edition stamps. Falls back to the older boolean
    `variants` map for cards TCGdex has not detailed yet.
    """
    detailed = payload.get("variants_detailed") or []
    flags = payload.get("variants") or {}

    if detailed:
        # TCGdex sometimes lists the same variant twice within one card, identical down to the
        # variantId. Two identical shapes are one collectible, so collapse them — keeping
        # whichever copy carries pricing, since the duplicate is often the empty one.
        rows: dict[tuple, dict] = {}
        for entry in detailed:
            type_ = entry.get("type")
            subtype = entry.get("subtype")
            stamp = entry.get("stamp")
            row = {
                "tcgdex_variant_id": entry.get("variantId"),
                "type": type_,
                "subtype": subtype,
                "size": entry.get("size"),
                "stamp": stamp,
                "stamp_key": stamp_key(stamp),
                "is_normal": type_ == "normal",
                "is_holo": type_ == "holo",
                "is_reverse": type_ == "reverse",
                "is_first_edition": bool(stamp and "1st-edition" in stamp),
                "is_promo": bool(flags.get("wPromo")),
                "label": _variant_label(type_, subtype, stamp),
                "pricing": entry.get("pricing") or {},
            }
            key = (row["type"], row["subtype"], row["size"], row["stamp_key"])
            if key not in rows or (row["pricing"] and not rows[key]["pricing"]):
                rows[key] = row
        return list(rows.values())

    fallback: list[dict] = []
    first_ed_stamp = ["1st-edition"] if flags.get("firstEdition") else None
    for key, type_ in (("normal", "normal"), ("holo", "holo"), ("reverse", "reverse")):
        if flags.get(key):
            fallback.append(
                {
                    "tcgdex_variant_id": None,
                    "type": type_,
                    "subtype": None,
                    "size": "standard",
                    "stamp": first_ed_stamp,
                    "stamp_key": "1st-edition" if flags.get("firstEdition") else "",
                    "is_normal": type_ == "normal",
                    "is_holo": type_ == "holo",
                    "is_reverse": type_ == "reverse",
                    "is_first_edition": bool(flags.get("firstEdition")),
                    "is_promo": bool(flags.get("wPromo")),
                    "label": _variant_label(type_, None, first_ed_stamp),
                    "pricing": {},
                }
            )
    if not fallback:
        # Every card needs at least one variant for inventory to point at.
        fallback.append(
            {
                "tcgdex_variant_id": None,
                "type": "normal",
                "subtype": None,
                "size": "standard",
                "stamp": None,
                "stamp_key": "",
                "is_normal": True,
                "is_holo": False,
                "is_reverse": False,
                "is_first_edition": False,
                "is_promo": bool(flags.get("wPromo")),
                "label": "Standard",
                "pricing": {},
            }
        )
    return fallback
