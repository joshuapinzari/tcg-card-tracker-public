from datetime import date

from database.canonical_cards import (
    CANONICAL_CARD_UNIVERSE_QUERY,
    rows_to_canonical_cards,
)


def test_canonical_universe_is_tcgplayer_backed_not_legacy_card_backed():
    sql = " ".join(CANONICAL_CARD_UNIVERSE_QUERY.lower().split())

    assert "from tcgplayer_products" in sql
    assert "tcgplayer_daily_prices" in sql
    assert "tcgplayer_primary_printings" in sql

    # The canonical universe must not be constrained by the old
    # legacy provider-backed card catalogue.
    assert " from cards " not in f" {sql} "
    assert " price_observations " not in f" {sql} "
    assert " population_observations " not in f" {sql} "


def test_rows_preserve_card_when_raw_or_graded_overlay_is_missing():
    rows = [
        (
            146698,
            "Mewtwo GX (Secret Shining)",
            "Shining Legends",
            "78/73",
            "Secret Rare",
            None,
            None,
            "https://example.invalid/mewtwo",
            date(2017, 10, 6),
            None,
            None,
            None,
            None,
        )
    ]

    cards = rows_to_canonical_cards(rows)

    assert len(cards) == 1
    assert cards[0].product_id == 146698
    assert cards[0].name == "Mewtwo GX (Secret Shining)"
    assert cards[0].set_name == "Shining Legends"
    assert cards[0].card_number == "78/73"
    assert cards[0].printing is None
    assert cards[0].latest_raw_usd is None


def test_rows_keep_mewtube_raw_history_independent_of_legacy_catalogue():
    rows = [
        (
            146698,
            "Mewtwo GX (Secret Shining)",
            "Shining Legends",
            "78/73",
            "Secret Rare",
            "5ban Graphics",
            None,
            None,
            date(2017, 10, 6),
            "Holofoil",
            976,
            date(2026, 10, 11),
            824.69,
        )
    ]

    [card] = rows_to_canonical_cards(rows)

    assert card.product_id == 146698
    assert card.printing == "Holofoil"
    assert card.market_observations == 976
    assert card.latest_raw_date == date(2026, 10, 11)
    assert card.latest_raw_usd == 824.69
