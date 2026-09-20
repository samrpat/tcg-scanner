"""Test fixtures.

Unit tests here reach neither the network nor a database (REQ-TST-001, REQ-TST-003).
TCGdex payloads are recorded fixtures captured from the live API once.
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def base_charizard() -> dict:
    """Base Set Charizard 4/102 — four variants including shadowless 1st edition."""
    return load_fixture("card_base1-4.json")


@pytest.fixture
def modern_card() -> dict:
    """Surging Sparks Exeggcute — a modern card with normal and reverse printings."""
    return load_fixture("card_sv08-001.json")


@pytest.fixture
def duplicate_variant_card() -> dict:
    """Base Set 77 — TCGdex lists the same shadowless 1st-edition variant twice."""
    return load_fixture("card_duplicate_variants.json")


@pytest.fixture
def tmp_storage(tmp_path):
    from app.storage.local import LocalStorage

    return LocalStorage(tmp_path / "images")
