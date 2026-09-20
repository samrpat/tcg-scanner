"""Canonical condition → marketplace code.

The database table `condition_translations` is authoritative at runtime and editable in
settings; this module holds the seed values and the same mapping for tests and offline use.
"""

from dataclasses import dataclass

from app.enums import Condition, Marketplace


@dataclass(frozen=True)
class Translation:
    external_code: str
    external_label: str
    requires_photo: bool = False
    note: str | None = None


# eBay: categories 183050, 183454 and 261328 require item condition 4000 (Ungraded) plus a
# conditionDescriptors entry with Card Condition ID 40001. Only four buckets exist against our
# five, so HP and DMG both land on Poor — the conservative reading (D-004).
EBAY_CONDITION_ID_UNGRADED = "4000"
EBAY_CARD_CONDITION_DESCRIPTOR_ID = "40001"

DEFAULT_TRANSLATIONS: dict[Marketplace, dict[Condition, Translation]] = {
    Marketplace.EBAY: {
        Condition.NM: Translation("400010", "Near mint or better"),
        Condition.LP: Translation("400011", "Excellent"),
        Condition.MP: Translation("400012", "Very good"),
        Condition.HP: Translation("400013", "Poor"),
        Condition.DMG: Translation(
            "400013", "Poor", requires_photo=True, note="Damaged cards must show photos."
        ),
    },
    Marketplace.TCGPLAYER: {
        Condition.NM: Translation("NM", "Near Mint"),
        Condition.LP: Translation("LP", "Lightly Played"),
        Condition.MP: Translation("MP", "Moderately Played"),
        Condition.HP: Translation("HP", "Heavily Played"),
        Condition.DMG: Translation("DMG", "Damaged"),
    },
    # Cardmarket runs a seven-tier scale and its "Mint" is stricter than the US "Near Mint",
    # so the mapping is shifted rather than one-to-one.
    Marketplace.CARDMARKET: {
        Condition.NM: Translation("NM", "Near Mint"),
        Condition.LP: Translation("EX", "Excellent"),
        Condition.MP: Translation("GD", "Good"),
        Condition.HP: Translation("PL", "Played"),
        Condition.DMG: Translation("PO", "Poor"),
    },
    # Confirmed against a real Collectr CSV export: the "Card Condition" column carries the
    # full words, not the two-letter codes. See docs/COLLECTR.md.
    Marketplace.COLLECTR: {
        Condition.NM: Translation("Near Mint", "Near Mint"),
        Condition.LP: Translation("Lightly Played", "Lightly Played"),
        Condition.MP: Translation("Moderately Played", "Moderately Played"),
        Condition.HP: Translation("Heavily Played", "Heavily Played"),
        Condition.DMG: Translation("Damaged", "Damaged"),
    },
}


def translate(condition: Condition, marketplace: Marketplace) -> Translation:
    try:
        return DEFAULT_TRANSLATIONS[marketplace][condition]
    except KeyError as exc:  # pragma: no cover - guards a programming error
        raise KeyError(f"no translation for {condition} on {marketplace}") from exc


def seed_rows() -> list[dict]:
    return [
        {
            "marketplace": marketplace,
            "condition": condition,
            "external_code": t.external_code,
            "external_label": t.external_label,
            "requires_photo": t.requires_photo,
            "note": t.note,
        }
        for marketplace, mapping in DEFAULT_TRANSLATIONS.items()
        for condition, t in mapping.items()
    ]
