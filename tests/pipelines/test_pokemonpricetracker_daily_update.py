from datetime import date

from database.canonical_cards import CanonicalCard
from services.pipelines.update_pokemonpricetracker_daily import (
    apply_credit_budget,
    filter_new_history_rows,
    select_daily_products,
)


def _card(product_id: int, raw: float) -> CanonicalCard:
    return CanonicalCard(
        product_id=product_id,
        name=f"Card {product_id}",
        set_name="Test Set",
        card_number=str(product_id),
        rarity="Rare",
        artist=None,
        image_url=None,
        tcgplayer_url=None,
        set_published_on=None,
        printing="Holofoil",
        market_observations=10,
        latest_raw_date=date(2026, 10, 13),
        latest_raw_usd=raw,
    )


def test_filter_new_history_rows_is_append_only():
    latest = {
        (100, "PSA_10", ""): date(2026, 10, 12),
        (100, "RAW", "Holofoil | Near Mint"): date(2026, 10, 11),
    }
    rows = [
        {
            "product_id": 100,
            "market_type": "PSA_10",
            "series_label": "",
            "observed_on": date(2026, 10, 12),
        },
        {
            "product_id": 100,
            "market_type": "PSA_10",
            "series_label": "",
            "observed_on": date(2026, 10, 14),
        },
        {
            "product_id": 100,
            "market_type": "RAW",
            "series_label": "Holofoil | Near Mint",
            "observed_on": date(2026, 10, 13),
        },
    ]

    new_rows, overlap = filter_new_history_rows(rows, latest)

    assert overlap == 1
    assert sorted(row["observed_on"] for row in new_rows) == [
        date(2026, 10, 13),
        date(2026, 10, 14),
    ]
    assert latest[(100, "PSA_10", "")] == date(2026, 10, 14)


def test_select_daily_products_only_uses_backfill_enrolled_cards():
    today = date(2026, 10, 14)
    universe = [_card(1, 10), _card(2, 50), _card(3, 100)]
    statuses = {1: {"status": "completed"}, 2: {"status": "completed"}}
    snapshots = {2: today}

    selected, enrolled, already_current = select_daily_products(
        universe,
        statuses=statuses,
        latest_refreshes=snapshots,
        product_ids=[],
        force=False,
        today=today,
        limit=None,
    )

    assert enrolled == 2
    assert already_current == 1
    assert [card.product_id for card in selected] == [1]


def test_force_rechecks_products_already_snapshotted_today():
    today = date(2026, 10, 14)
    universe = [_card(1, 10), _card(2, 50)]
    statuses = {1: {}, 2: {}}
    snapshots = {1: today, 2: today}

    selected, _, already_current = select_daily_products(
        universe,
        statuses=statuses,
        latest_refreshes=snapshots,
        product_ids=[],
        force=True,
        today=today,
        limit=None,
    )

    assert already_current == 0
    assert [card.product_id for card in selected] == [2, 1]


def test_credit_budget_is_a_hard_pre_request_cap():
    cards = [_card(1, 30), _card(2, 20), _card(3, 10)]

    selected, deferred = apply_credit_budget(cards, credit_budget=6)

    assert [card.product_id for card in selected] == [1, 2]
    assert deferred == 1
