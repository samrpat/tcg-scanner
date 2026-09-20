"""Compose the text of an eBay listing for one card.

The operator's method is to open a comparable *sold* listing and use eBay's "Sell a similar
item", which carries the category and the item specifics across. For trading cards that matters
more than it sounds: the specifics — set, card number, finish, rarity — are what eBay's faceted
search filters on, and a listing built from scratch usually omits half of them and is
correspondingly harder to find. That workflow is kept, not replaced.

Everything here is modelled on three of the operator's own sold listings rather than invented:

    Gengar 70/214 Sm-Unbroken Bonds Regular
    Snorlax SWSH119 SWSH: Sword & Shield Promo Cards Holo
    Unown [G] 57/106 Great Encounters Regular

which is eBay's own catalogue product name: NAME NUMBER SET FINISH. No rarity, no "Pokemon", no
condition — condition is a proper item specific in this category with its own search facet, and
the operator reports measurably more views when it is left out of the title. Matching the
catalogue name is also what lets eBay attach the listing to the existing product page (the ePID),
which is where the price guide and the product reviews live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# eBay's hard limit. Titles are truncated to fit rather than rejected.
TITLE_MAX = 80

# eBay renames the promo sets. Exactly one of these is evidenced by a real sold listing:
#
#     TCGdex "SWSH Black Star Promos" -> eBay "SWSH: Sword & Shield Promo Cards"
#
# rather than invent the rest, `_promo_set_name` below derives them from that one case using the
# series name the catalogue already stores. The derivation reproduces the evidenced mapping
# exactly; for every other promo set it is an inference, which is why `is_promo_set` marks them
# so the UI can ask for a look before listing.
_SET_NAME_OVERRIDES: dict[str, str] = {}

_BLACK_STAR = re.compile(r"^(.+?) Black Star Promos$")

# Series whose set names eBay prefixes. Only "sm" is evidenced from a real sold listing
# ("Sm-Unbroken Bonds"); the title is editable in the UI, which covers any drift elsewhere.
_SERIES_PREFIX = {"sm": "Sm-"}

# TCGdex spells the category without the accent; eBay's aspect values carry it.
_CARD_TYPES = {"pokemon": "Pokémon", "trainer": "Trainer", "energy": "Energy"}

# TCGdex writes the Unown letter bare; eBay brackets it.
_UNOWN = re.compile(r"^Unown ([A-Z!?])$")


@dataclass(frozen=True)
class ListingDraft:
    title: str
    description: str
    condition_label: str | None
    condition_code: str | None
    price: float | None
    template_url: str | None
    sold_search_url: str | None
    specifics: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "title_length": len(self.title),
            "description": self.description,
            "condition_label": self.condition_label,
            "condition_code": self.condition_code,
            "price": self.price,
            "template_url": self.template_url,
            "sold_search_url": self.sold_search_url,
            "specifics": self.specifics,
        }


def ebay_card_name(name: str) -> str:
    """The card's name as eBay's catalogue writes it."""
    match = _UNOWN.match((name or "").strip())
    if match:
        return f"Unown [{match.group(1)}]"
    return (name or "").strip()


def is_promo_set(set_name: str | None) -> bool:
    """Whether this set's eBay name is inferred rather than known.

    Promo sets are the ones eBay renames, and only the Sword & Shield mapping is evidenced. A
    listing whose set name is wrong attaches to no product page, so these are worth a glance.
    """
    return bool(_BLACK_STAR.match((set_name or "").strip()))


def _promo_set_name(code: str, series_name: str | None) -> str:
    """eBay's name for a Black Star Promos set.

    Derived from the one evidenced mapping: the set code, a colon, the series name, then
    "Promo Cards". When the code and the series name are the same word ("XY"), the colon form
    would stutter — "XY: XY Promo Cards" — so the code is dropped.
    """
    series = (series_name or "").strip()
    if not series:
        return f"{code} Promo Cards"
    if series.lower() == code.lower():
        return f"{series} Promo Cards"
    return f"{code}: {series} Promo Cards"


def ebay_set_name(
    set_name: str | None,
    series_id: str | None = None,
    series_name: str | None = None,
) -> str:
    """The set as eBay's catalogue names it, which is not always what TCGdex calls it."""
    name = (set_name or "").strip()
    if not name:
        return ""
    if name in _SET_NAME_OVERRIDES:
        return _SET_NAME_OVERRIDES[name]
    promo = _BLACK_STAR.match(name)
    if promo:
        return _promo_set_name(promo.group(1), series_name)
    prefix = _SERIES_PREFIX.get((series_id or "").lower(), "")
    return f"{prefix}{name}"


def finish_label(variant: str | None) -> str:
    """eBay's "Finish" aspect: Regular, Holo, Reverse Holo."""
    text = (variant or "").strip().lower()
    if not text:
        return ""
    if "reverse" in text:
        return "Reverse Holo"
    if "holo" in text:
        return "Holo"
    if text in {"normal", "regular", "non-holo", "nonholo"}:
        return "Regular"
    return (variant or "").strip()


def printed_number(local_id: str | None, set_total: int | None) -> str:
    """The number as it appears on the card: "70/214", "57/106", "SWSH119".

    Promos carry a bare alphanumeric number with no set total after it, so the slash is only
    added for a purely numeric collector number. The width of the total is taken from the
    collector number itself, because that is what reveals how the set prints it — a three-digit
    "021" belongs to an "/086" set and a two-digit "29" to a "/39" one.
    """
    local = (local_id or "").strip()
    if not local:
        return ""
    if not set_total or not local.isdigit():
        return local
    return f"{local}/{str(set_total).rjust(len(local), '0')}"


def build_title(
    name: str,
    local_id: str | None,
    set_total: int | None,
    set_name: str | None,
    variant: str | None,
    condition: str | None = None,
    rarity: str | None = None,
    series_id: str | None = None,
    series_name: str | None = None,
) -> str:
    """eBay's catalogue product name: NAME NUMBER SET FINISH.

    `condition` and `rarity` are accepted so callers need not change, and are ignored: both are
    item specifics with their own search facets, and spending title characters on them is what
    pushed the set name out of the title in the first place.

    When a title would exceed 80 characters the finish is dropped before the set, since the set
    is the stronger search term and the finish remains in the specifics either way.
    """
    parts = [
        ebay_card_name(name),
        printed_number(local_id, set_total),
        ebay_set_name(set_name, series_id, series_name),
        finish_label(variant),
    ]
    parts = [p for p in parts if p]
    while len(" ".join(parts)) > TITLE_MAX and len(parts) > 2:
        parts.pop()
    return " ".join(parts)[:TITLE_MAX].strip()


def build_features(rarity: str | None, set_name: str | None) -> str:
    """eBay's "Features" aspect.

    Only "Promo" is evidenced — the Snorlax listing carries it and the other two carry nothing —
    so that is the only value emitted. Guessing at "Holo" here would duplicate the Finish aspect
    and put a wrong value in a field buyers filter on.
    """
    if (rarity or "").strip().lower() == "promo" or is_promo_set(set_name):
        return "Promo"
    return ""


def build_specifics(
    name: str,
    local_id: str | None,
    set_total: int | None,
    set_name: str | None,
    variant: str | None,
    rarity: str | None = None,
    hp: int | None = None,
    stage: str | None = None,
    illustrator: str | None = None,
    category: str | None = None,
    series_id: str | None = None,
    series_name: str | None = None,
    condition_label: str | None = None,
) -> dict[str, str]:
    """The item specifics eBay's Single Cards category asks for, in its own order.

    These are the fields that drive the category's search filters, so a listing that fills them
    in is findable in ways one that leaves them blank is not. Anything unknown is omitted rather
    than guessed — a wrong aspect is worse than a missing one.
    """
    stage_text = (stage or "").strip()
    # TCGdex writes "Stage2"; eBay writes "Stage 2".
    stage_text = re.sub(r"^Stage(\d)$", r"Stage \1", stage_text)

    aspects = {
        "Condition": condition_label or "",
        "Rarity": (rarity or "").strip(),
        "Game": "Pokémon TCG",
        "Set": ebay_set_name(set_name, series_id, series_name),
        "Language": "English",
        "Card Name": ebay_card_name(name),
        "HP": str(hp) if hp else "",
        "Manufacturer": "The Pokémon Company",
        "Stage": stage_text,
        "Card Type": _CARD_TYPES.get((category or "").strip().lower(), (category or "").strip()),
        "Card Number": printed_number(local_id, set_total),
        "Finish": finish_label(variant),
        "Illustrator": (illustrator or "").strip(),
        "Features": build_features(rarity, set_name),
        "Country of Origin": "United States",
    }
    return {k: v for k, v in aspects.items() if v}


def _condition_words(condition: str | None) -> str:
    return {
        "NM": "Near Mint",
        "LP": "Lightly Played",
        "MP": "Moderately Played",
        "HP": "Heavily Played",
        "DMG": "Damaged",
    }.get(condition or "", "")


# What each grade actually means for the card in hand, in plain words. These are the sentences a
# buyer judges the card against when it arrives, so they are written to the bottom of the grade
# rather than the top: a card graded Lightly Played may be nearly flawless, and a buyer who was
# told to expect "minor edge wear" and receives that is not disappointed. The reverse — promising
# the best of the band and shipping the worst of it — is what produces a bad review.
_CONDITION_SENTENCE = {
    "NM": (
        "Near Mint: may have very minor handling marks under close inspection, but no visible"
        " wear at a glance."
    ),
    "LP": (
        "Lightly Played: light wear such as minor edge or corner whitening, small surface"
        " scratches, or slight border wear. No creases or bends."
    ),
    "MP": (
        "Moderately Played: noticeable wear — edge and corner whitening, surface scratching or"
        " scuffing, possibly minor border wear. Still fully playable in a sleeve."
    ),
    "HP": (
        "Heavily Played: significant wear — heavy whitening, scratching, scuffing, and possibly"
        " a bend or crease."
    ),
    "DMG": (
        "Damaged: major flaws such as creasing, bending, water damage, tearing or writing."
    ),
}

# eBay writes energy costs as bracketed initials — "[C][C][C] Twilight Poison (70)" — so the
# description uses the same notation the product page above it does.
_ENERGY_LETTER = {
    "Colorless": "C",
    "Darkness": "D",
    "Dragon": "N",
    "Fairy": "Y",
    "Fighting": "F",
    "Fire": "R",
    "Grass": "G",
    "Lightning": "L",
    "Metal": "M",
    "Psychic": "P",
    "Water": "W",
}


def _energy(types: list | None) -> str:
    if not types:
        return ""
    return "".join(f"[{_ENERGY_LETTER.get(t, t[:1].upper())}]" for t in types)


def _stage_words(stage: str | None, evolve_from: str | None) -> str:
    text = re.sub(r"^Stage(\d)$", r"Stage \1", (stage or "").strip())
    if text and evolve_from:
        return f"{text} (evolves from {evolve_from})"
    return text


def build_description(
    name: str,
    number: str,
    set_name: str | None,
    variant: str | None,
    condition: str | None,
    html: bool = False,
    raw: dict | None = None,
    rarity: str | None = None,
    illustrator: str | None = None,
    has_photos: bool = True,
    packaging: str | None = None,
) -> str:
    """A description built from this specific card's own data.

    Everything here comes from the catalogue entry for the exact card being sold — its typing,
    stage, HP, ability and attack text, weakness, resistance, retreat cost and illustrator — so
    two cards never get the same description and nothing in it is generic filler.

    **Nothing is asserted that is not known.** A missing field is omitted rather than guessed at,
    because an item description is what a buyer measures the arrived card against, and a wrong
    detail there is a return and a bad review over a card that was actually fine.

    Two claims in particular are conditional rather than boilerplate, because both were false in
    the previous version of this function:

    - **The photographs.** The sentence pointing a buyer at the images is only written when
      images are actually attached. A bulk-uploaded listing with no pictures that tells the
      buyer to examine the pictures reads as a bait listing.
    - **The packaging.** The old text promised "a penny sleeve and toploader inside a rigid
      mailer", which is not true of an eBay Standard Envelope shipment — ESE has a thickness
      limit a toploader does not meet. Promising rigid packaging and shipping an envelope is
      exactly the kind of small, avoidable inaccuracy that costs a review, so the sentence is
      now supplied by the operator and defaults to a claim that holds either way.
    """
    raw = raw or {}
    lines: list[str] = []

    # ── What it is ────────────────────────────────────────────────────────────
    head = f"{name} — {number}" if number else name
    lines.append(head)
    if set_name:
        lines.append(set_name)

    traits = [
        " / ".join(raw.get("types") or []),
        _stage_words(raw.get("stage"), raw.get("evolveFrom")),
        f"{raw['hp']} HP" if raw.get("hp") else "",
        raw.get("trainerType") or "",
        raw.get("energyType") or "",
    ]
    traits = [t for t in traits if t]
    if traits:
        lines.append(" · ".join(traits))

    printing = [rarity or "", finish_label(variant), "English"]
    lines.append(" · ".join(p for p in printing if p))
    if illustrator:
        lines.append(f"Illustrated by {illustrator}")

    # ── What the card does ────────────────────────────────────────────────────
    card_text: list[str] = []
    for ability in raw.get("abilities") or []:
        kind = ability.get("type") or "Ability"
        label = f"{kind} — {ability.get('name', '')}".strip(" —")
        effect = (ability.get("effect") or "").strip()
        card_text.append(f"{label}: {effect}" if effect else label)

    for attack in raw.get("attacks") or []:
        cost = _energy(attack.get("cost"))
        damage = attack.get("damage")
        # A damage of 0 or None means the attack does no listed damage; say nothing rather
        # than print "(0)", which reads as a data error.
        dmg = f" ({damage})" if damage else ""
        label = f"{cost} {attack.get('name', '')}{dmg}".strip()
        effect = (attack.get("effect") or "").strip()
        card_text.append(f"{label} — {effect}" if effect else label)

    # A Trainer's whole text is its effect.
    if raw.get("effect") and not card_text:
        card_text.append(raw["effect"].strip())

    if card_text:
        lines.append("")
        lines.extend(card_text)

    stats = []
    for weak in raw.get("weaknesses") or []:
        stats.append(f"Weakness: {weak.get('type', '')} {weak.get('value', '')}".strip())
    for res in raw.get("resistances") or []:
        stats.append(f"Resistance: {res.get('type', '')} {res.get('value', '')}".strip())
    if raw.get("retreat") is not None:
        stats.append(f"Retreat: {raw['retreat']}")
    if stats:
        lines.append(" · ".join(stats))

    # ── What state it is in ───────────────────────────────────────────────────
    if condition:
        lines.append("")
        lines.append(f"Condition — {_condition_words(condition)}")
        sentence = _CONDITION_SENTENCE.get(condition)
        if sentence:
            lines.append(sentence)

    if has_photos:
        closer = (
            "The photographs show the exact card you will receive, front and back, under even"
            " lighting — please check them for centering, edges and surface."
        )
        # On a worn card the photographs are the whole basis of the sale, so say so plainly
        # rather than leaving a buyer to infer how bad "heavily played" is.
        if condition in {"HP", "DMG"}:
            closer += " Please look closely before buying."
        lines.append(closer)

    if packaging:
        lines.append(packaging.strip())

    text = "\n".join(lines).strip()
    if not html:
        return text
    # A bulk-upload description is rendered as HTML, where newlines collapse to nothing. An
    # empty entry stays empty so that joining puts two breaks there and it reads as one blank
    # line — substituting a <br> of its own would give three, and a gappy description reads as
    # broken rather than spaced.
    return "<br>".join(lines).strip()


def sell_one_like_this(item_id: str | None) -> str | None:
    """eBay's own "Sell a similar item" entry point.

    This is the URL behind the "Sell a similar item" link on a completed listing, and going
    through it is what carries the category and item specifics across. The mode is
    `SellSimilarItem` — taken from the operator's own sold listings, since eBay also has a
    `RelistItem` mode on the same endpoint and the two are not interchangeable: relisting
    resurrects the sold listing, which is not what is wanted for a different physical card.
    """
    if not item_id:
        return None
    return f"https://www.ebay.com/sl/list?itemId={item_id}&mode=SellSimilarItem"


def extract_item_id(pasted: str) -> str | None:
    """Pull an eBay item id out of whatever the operator pasted.

    They will paste a full listing URL far more often than a bare id, so accept both rather than
    asking someone to find the number inside a hundred-character URL.
    """
    text = (pasted or "").strip()
    if text.isdigit() and 9 <= len(text) <= 15:
        return text
    for pattern in (r"/itm/(\d{9,15})", r"itemId=(\d{9,15})", r"item=(\d{9,15})", r"(\d{9,15})"):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None
