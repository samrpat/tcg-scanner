"""The seller description.

A description is what a buyer measures the arrived card against. Every sentence in it is a
promise, so the tests here are mostly about what it must *not* say: no claim about photographs
that do not exist, no claim about packaging nobody specified, no field invented to fill a gap.
A wrong detail here is a return and a bad review over a card that was actually fine.
"""

import pytest

from app.services.listing_draft import build_description

GENGAR = {
    "hp": 130,
    "types": ["Psychic"],
    "stage": "Stage2",
    "evolveFrom": "Haunter",
    "retreat": 0,
    "attacks": [
        {
            "cost": ["Colorless", "Colorless", "Colorless"],
            "name": "Twilight Poison",
            "damage": 70,
            "effect": "Your opponent's Active Pokémon is now Asleep and Poisoned.",
        }
    ],
    "abilities": [
        {"name": "Shadow Pain", "type": "Ability", "effect": "Put 6 damage counters."}
    ],
    "weaknesses": [{"type": "Darkness", "value": "×2"}],
    "resistances": [{"type": "Fighting", "value": "-20"}],
}

TRAINER = {
    "trainerType": "Item",
    "effect": "Discard all Pokémon Tools and Special Energy from all of your opponent's Pokémon.",
}


def describe(**kw):
    args = dict(
        name="Gengar",
        number="70/214",
        set_name="Sm-Unbroken Bonds",
        variant="Normal",
        condition="LP",
        raw=GENGAR,
        rarity="Rare",
        illustrator="so-taro",
    )
    args.update(kw)
    return build_description(**args)


def test_it_describes_this_card_and_not_a_generic_one():
    text = describe()
    for fact in (
        "Gengar — 70/214",
        "Sm-Unbroken Bonds",
        "Psychic",
        "Stage 2 (evolves from Haunter)",
        "130 HP",
        "Rare",
        "Illustrated by so-taro",
        "Shadow Pain",
        "Twilight Poison",
        "Weakness: Darkness ×2",
        "Resistance: Fighting -20",
        "Retreat: 0",
    ):
        assert fact in text, f"missing: {fact}"


def test_energy_costs_use_ebays_own_notation():
    """eBay's product page writes "[C][C][C] Twilight Poison (70)"."""
    assert "[C][C][C] Twilight Poison (70)" in describe()


def test_it_never_mentions_photographs_when_there_are_none():
    """A listing with no pictures that tells the buyer to check the pictures is a bait listing."""
    text = describe(has_photos=False).lower()
    for word in ("photograph", "picture", "image", "photo"):
        assert word not in text, f"claims {word} with no photos attached"


def test_it_does_mention_photographs_when_there_are_some():
    assert "photographs show the exact card" in describe(has_photos=True)


def test_a_worn_card_asks_the_buyer_to_look_closely_only_when_it_can():
    assert "look closely" in describe(condition="HP", has_photos=True)
    assert "look closely" not in describe(condition="HP", has_photos=False)


def test_no_packaging_claim_unless_one_is_given():
    """The old text promised a rigid mailer, which is untrue of an eBay Standard Envelope."""
    text = describe().lower()
    for word in ("toploader", "mailer", "sleeve", "envelope"):
        assert word not in text, f"claims {word} without being told to"

    assert "Sent in a rigid mailer." in describe(packaging="Sent in a rigid mailer.")


def test_the_condition_sentence_describes_the_bottom_of_the_band():
    """A buyer told to expect the worst of a grade and sent the best is never disappointed."""
    assert "Lightly Played: light wear" in describe(condition="LP")
    assert "Near Mint: may have very minor handling marks" in describe(condition="NM")


def test_unknown_fields_are_omitted_not_guessed():
    text = describe(raw={}, illustrator=None, rarity=None)
    assert "Gengar — 70/214" in text
    for absent in ("HP", "Weakness", "Resistance", "Retreat", "Illustrated by", "None"):
        assert absent not in text, f"invented a value for {absent}"


def test_a_trainer_reads_as_a_trainer():
    """No HP, no attacks, no weakness — its whole text is its effect."""
    text = describe(name="Megaton Blower", number="182/191", raw=TRAINER, rarity="ACE SPEC Rare")
    assert "Item" in text
    assert "Discard all Pokémon Tools" in text
    assert "HP" not in text
    assert "Weakness" not in text


def test_an_attack_with_no_damage_prints_no_number():
    """"(0)" in a listing reads as a data error, which is what it would be."""
    raw = {"attacks": [{"cost": ["Psychic"], "name": "Hypnosis", "effect": "Now Asleep."}]}
    text = describe(raw=raw)
    assert "[P] Hypnosis — Now Asleep." in text
    assert "(0)" not in text


@pytest.mark.parametrize("html", [False, True])
def test_both_renderings_carry_the_same_facts(html):
    text = build_description(
        "Gengar", "70/214", "Sm-Unbroken Bonds", "Normal", "LP",
        html=html, raw=GENGAR, rarity="Rare", illustrator="so-taro",
    )
    assert "Twilight Poison" in text
    # eBay renders the bulk-upload description as HTML, where newlines collapse to nothing.
    assert ("<br>" in text) is html
