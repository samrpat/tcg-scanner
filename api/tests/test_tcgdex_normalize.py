"""TCGdex payload normalisation, against recorded fixtures — never the live API."""

from app.services.tcgdex import (
    content_hash,
    normalize_card,
    normalize_set,
    normalize_variants,
)


def test_card_normalisation_keeps_identity_fields(base_charizard):
    card = normalize_card(base_charizard)
    assert card["tcgdex_id"] == "base1-4"
    assert card["name"] == "Charizard"
    assert card["local_id"] == "4"
    assert card["rarity"] == "Rare"
    assert card["image_url"].startswith("https://")


def test_full_payload_is_preserved(base_charizard):
    card = normalize_card(base_charizard)
    assert card["raw"] == base_charizard
    assert card["raw"]["attacks"], "keeping raw is what saves us re-syncing for an unmodelled field"


def test_content_hash_is_stable_and_order_independent():
    a = {"id": "x", "name": "Pikachu", "hp": 60}
    b = {"hp": 60, "name": "Pikachu", "id": "x"}
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash({**a, "hp": 70})


def test_charizard_has_four_distinguishable_variants(base_charizard):
    """The whole point of variants_detailed: shadowless vs unlimited vs 1st edition."""
    variants = normalize_variants(base_charizard)
    assert len(variants) == 4

    labels = {v["label"] for v in variants}
    assert any("Shadowless" in label for label in labels)
    assert any("1St Edition" in label or "1st" in label.lower() for label in labels)

    first_editions = [v for v in variants if v["is_first_edition"]]
    assert len(first_editions) == 1
    assert first_editions[0]["subtype"] == "shadowless"


def test_variant_ids_are_captured(base_charizard):
    variants = normalize_variants(base_charizard)
    ids = [v["tcgdex_variant_id"] for v in variants]
    assert all(ids), "every detailed variant carries a stable upstream id"
    assert len(set(ids)) == len(ids)


def test_shadowless_first_edition_is_distinct_from_plain_shadowless(base_charizard):
    """Regression: Base Set Charizard has two ("holo", "shadowless", "standard") variants that
    differ only by the 1st-edition stamp. Without stamp_key in the uniqueness key they collapse
    into one row and the most valuable distinction in vintage Pokémon is silently lost."""
    variants = normalize_variants(base_charizard)
    shadowless = [v for v in variants if v["subtype"] == "shadowless"]
    assert len(shadowless) == 2
    assert {v["stamp_key"] for v in shadowless} == {"", "1st-edition"}


def test_variant_shapes_are_unique(base_charizard):
    """The (type, subtype, size, stamp_key) uniqueness constraint must hold for real data."""
    variants = normalize_variants(base_charizard)
    shapes = [(v["type"], v["subtype"], v["size"], v["stamp_key"]) for v in variants]
    assert len(set(shapes)) == len(shapes)


def test_pricing_is_carried_through_for_the_priced_variant(base_charizard):
    priced = [v for v in normalize_variants(base_charizard) if v["pricing"]]
    assert priced, "the unlimited holo carries Cardmarket and TCGplayer prices"
    pricing = priced[0]["pricing"]
    assert "tcgplayer" in pricing or "cardmarket" in pricing


def test_modern_card_normalises(modern_card):
    card = normalize_card(modern_card)
    variants = normalize_variants(modern_card)
    assert card["tcgdex_id"] == "sv08-001"
    assert variants
    assert all(v["size"] for v in variants)


def test_fallback_to_the_boolean_variant_map():
    """Cards TCGdex has not detailed yet still produce usable variant rows."""
    payload = {
        "id": "test-1",
        "name": "Test",
        "variants": {"normal": True, "reverse": True, "holo": False, "firstEdition": False},
    }
    variants = normalize_variants(payload)
    assert {v["type"] for v in variants} == {"normal", "reverse"}
    assert all(v["tcgdex_variant_id"] is None for v in variants)


def test_stamp_key_is_order_independent():
    from app.services.tcgdex import stamp_key

    assert stamp_key(["1st-edition", "staff"]) == stamp_key(["staff", "1st-edition"])
    assert stamp_key(None) == ""


def test_a_card_with_no_variant_data_still_gets_one_row():
    """Inventory must always have a variant to point at."""
    variants = normalize_variants({"id": "test-2", "name": "Test"})
    assert len(variants) == 1
    assert variants[0]["is_normal"]
    assert variants[0]["label"] == "Standard"


def test_set_normalisation(base_charizard):
    payload = {
        "id": "base1",
        "name": "Base Set",
        "cardCount": {"official": 102, "total": 102},
        "releaseDate": "1999-01-09",
        "serie": {"id": "base", "name": "Base"},
    }
    result = normalize_set(payload)
    assert result["tcgdex_id"] == "base1"
    assert result["card_count_official"] == 102
    assert result["series_name"] == "Base"
    assert result["content_hash"]


def test_variant_ids_are_a_class_not_a_row_identity(base_charizard, modern_card):
    """Regression: TCGdex `variantId` names a variant CLASS shared across many cards.

    Every holo rare in Base Set carries the same four variantIds. Treating the column as
    globally unique fails to insert, and a price lookup keyed on it alone would attach one
    card's prices to another. Row identity is (card, type, subtype, size, stamp_key).
    """
    charizard_ids = {v["tcgdex_variant_id"] for v in normalize_variants(base_charizard)}
    modern_ids = {v["tcgdex_variant_id"] for v in normalize_variants(modern_card)}

    # Different variant classes, so these two happen not to overlap...
    assert charizard_ids and modern_ids

    # ...but the id must never be relied on as unique. The shape key is what identifies a row.
    shapes = [
        (v["type"], v["subtype"], v["size"], v["stamp_key"])
        for v in normalize_variants(base_charizard)
    ]
    assert len(set(shapes)) == len(shapes)


def test_duplicate_variants_within_one_card_are_collapsed(duplicate_variant_card):
    """Regression: TCGdex lists four entries for Base Set 77, two of them identical down to
    the variantId. Two identical shapes are one collectible; without collapsing them the
    insert violates uq_variant_shape and the card is dropped from the sync."""
    entries = duplicate_variant_card["variants_detailed"]
    assert len(entries) == 4, "fixture should still contain the duplicate"

    variants = normalize_variants(duplicate_variant_card)
    assert len(variants) == 3

    shapes = [(v["type"], v["subtype"], v["size"], v["stamp_key"]) for v in variants]
    assert len(set(shapes)) == len(shapes)


def test_deduplication_keeps_the_priced_copy():
    """When duplicates differ only in whether they carry pricing, keep the priced one."""
    payload = {
        "id": "test-dupe",
        "name": "Test",
        "variants_detailed": [
            {"type": "holo", "subtype": "unlimited", "size": "standard", "pricing": {}},
            {
                "type": "holo",
                "subtype": "unlimited",
                "size": "standard",
                "pricing": {"tcgplayer": {"unit": "USD", "holofoil": {"marketPrice": 12.0}}},
            },
        ],
    }
    variants = normalize_variants(payload)
    assert len(variants) == 1
    assert variants[0]["pricing"], "the copy carrying prices must win"


def test_variant_labels_are_presentable(base_charizard):
    """These strings end up in eBay titles, so "1St Edition" is not acceptable output."""
    labels = {v["label"] for v in normalize_variants(base_charizard)}
    assert "Holo · Shadowless · 1st Edition" in labels
    assert "Holo · Unlimited" in labels
    assert "Holo · 1999-2000 Copyright" in labels
    assert not any("1St" in label for label in labels)
