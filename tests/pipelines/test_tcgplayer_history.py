from datetime import date
from decimal import Decimal

from services.pipelines.backfill_tcgplayer_history import (
    history_to_rows,
)
from sources.tcgcsv.client import (
    extended_fields,
    is_card_product,
    is_target_group,
    product_to_row,
)


def test_target_eras():
    assert is_target_group(
        {
            "abbreviation": "SWSH07",
            "name": "SWSH07: Evolving Skies",
        }
    )

    assert is_target_group(
        {
            "abbreviation": "SV04",
            "name": "SV04: Paradox Rift",
        }
    )

    assert is_target_group(
        {
            "abbreviation": "ME01",
            "name": "ME01: Mega Evolution",
        }
    )

    assert is_target_group(
        {
            "abbreviation": "SM10",
            "name": "SM10: Unbroken Bonds",
        }
    )

    assert is_target_group(
        {
            "groupId": 1842,
            "name": "XY - Evolutions",
        }
    )

    assert is_target_group(
        {
            "groupId": 1525,
            "name": "Double Crisis",
        }
    )

    assert is_target_group(
        {
            "groupId": 1728,
            "name": "Generations",
        }
    )


def test_black_white_era_sets_are_included():
    included = [
        "Black and White",
        "Black and White Promos",
        "Emerging Powers",
        "Noble Victories",
        "Next Destinies",
        "Dark Explorers",
        "Dragons Exalted",
        "Dragon Vault",
        "Boundaries Crossed",
        "Plasma Storm",
        "Plasma Freeze",
        "Plasma Blast",
        "Legendary Treasures",
        "Legendary Treasures: Radiant Collection",
    ]

    for name in included:
        assert is_target_group({"name": name})


def test_hgss_era_sets_are_included():
    included = [
        "HeartGold SoulSilver",
        "HeartGold & SoulSilver",
        "Unleashed",
        "Undaunted",
        "Triumphant",
        "Call of Legends",
        "HGSS Promos",
    ]

    for name in included:
        assert is_target_group({"name": name})


def test_diamond_pearl_era_sets_are_included():
    included = [
        "Diamond and Pearl",
        "Diamond & Pearl",
        "Mysterious Treasures",
        "Secret Wonders",
        "Great Encounters",
        "Majestic Dawn",
        "Legends Awakened",
        "Stormfront",
        "Diamond and Pearl Promos",
    ]

    for name in included:
        assert is_target_group({"name": name})


def test_platinum_era_sets_are_included():
    included = [
        "Platinum",
        "Rising Rivals",
        "Supreme Victors",
        "Arceus",
    ]

    for name in included:
        assert is_target_group({"name": name})


def test_ex_series_sets_are_included():
    included = [
        "EX Ruby and Sapphire",
        "EX Sandstorm",
        "EX Dragon",
        "EX Team Magma vs Team Aqua",
        "EX Hidden Legends",
        "EX FireRed & LeafGreen",
        "EX Team Rocket Returns",
        "EX Deoxys",
        "EX Emerald",
        "EX Unseen Forces",
        "EX Delta Species",
        "EX Legend Maker",
        "EX Holon Phantoms",
        "EX Crystal Guardians",
        "EX Dragon Frontiers",
        "EX Power Keepers",
    ]
    for name in included:
        assert is_target_group({"name": name})


def test_legendary_collection_is_included():
    assert is_target_group({"name": "Legendary Collection"})


def test_e_card_sets_are_included():
    for name in ("Expedition", "Aquapolis", "Skyridge"):
        assert is_target_group({"name": name})


def test_nearby_non_expansion_groups_remain_excluded():
    for name in ("EX Battle Stadium", "Neo Destiny"):
        assert not is_target_group({"name": name})


def test_card_product_detection():
    card = {
        "name": "Umbreon VMAX",
        "extendedData": [
            {
                "name": "Number",
                "value": "215/203",
            },
            {
                "name": "Rarity",
                "value": "Secret Rare",
            },
        ],
    }

    sealed = {
        "name": "Evolving Skies Booster Box",
        "extendedData": [],
    }

    code_card = {
        "name": "Evolving Skies Code Card",
        "extendedData": [
            {
                "name": "Number",
                "value": "SWSH07",
            }
        ],
    }

    assert is_card_product(card)
    assert not is_card_product(sealed)
    assert not is_card_product(code_card)


def test_product_mapping():
    group = {
        "groupId": 2848,
        "name": "SWSH07: Evolving Skies",
        "publishedOn": (
            "2021-08-27T00:00:00"
        ),
    }

    product = {
        "productId": 246723,
        "groupId": 2848,
        "name": (
            "Umbreon VMAX "
            "(Alternate Art Secret)"
        ),
        "imageUrl": "example.webp",
        "url": "https://example.com",
        "extendedData": [
            {
                "name": "Number",
                "value": "215/203",
            },
            {
                "name": "Rarity",
                "value": "Secret Rare",
            },
        ],
    }

    row = product_to_row(
        group,
        product,
    )

    assert row["product_id"] == 246723
    assert row["card_number"] == "215/203"
    assert row["rarity"] == "Secret Rare"

    assert row["set_published_on"] == date(
        2021,
        8,
        27,
    )


def test_history_mapping():
    history = {
        "Holofoil": [
            {
                "snapshot_date": "2024-02-08",
                "low": "489.99",
                "market": "540.24",
            },
            {
                "snapshot_date": "2026-10-10",
                "low": "1699.99",
                "market": "2351.42",
            },
        ]
    }

    rows = history_to_rows(
        246723,
        history,
    )

    assert len(rows) == 2

    assert rows[0][0] == 246723
    assert rows[0][1] == date(
        2024,
        2,
        8,
    )

    assert rows[0][2] == "Holofoil"

    assert rows[0][3] == Decimal(
        "489.99"
    )

    assert rows[0][6] == Decimal(
        "540.24"
    )

    assert rows[0][9] == "tcgplayer"
    assert rows[0][10] == "tickermint"


def test_extended_fields():
    product = {
        "extendedData": [
            {
                "name": "Number",
                "value": "215/203",
            },
            {
                "name": "Rarity",
                "value": "Secret Rare",
            },
        ]
    }

    fields = extended_fields(product)

    assert fields["Number"] == "215/203"
    assert fields["Rarity"] == "Secret Rare"


def test_sun_moon_special_sets():
    assert is_target_group(
        {
            "groupId": 2480,
            "name": "Hidden Fates",
        }
    )

    assert is_target_group(
        {
            "groupId": 2054,
            "name": "Shining Legends",
        }
    )


def test_sun_moon_nonstandard_exclusions():
    assert not is_target_group(
        {
            "groupId": 23095,
            "name": (
                "Ash vs Team Rocket Deck Kit "
                "(JP Exclusive)"
            ),
        }
    )

    assert not is_target_group(
        {
            "groupId": 2282,
            "name": "World Championship Decks",
        }
    )
