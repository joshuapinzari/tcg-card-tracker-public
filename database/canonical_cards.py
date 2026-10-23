"""Read-only canonical card universe backed by the TCGPlayer catalogue.

This module deliberately does not depend on the legacy ``cards`` table.
not the source of truth for which Pokemon cards exist in the tracker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


from database.operations import DATABASE_NAME, connect




CANONICAL_RAW_HISTORY_QUERY = """
    SELECT
        d.snapshot_date,
        d.printing,
        d.market_price::DOUBLE PRECISION AS market_price
    FROM tcgplayer_daily_prices AS d
    JOIN tcgplayer_primary_printings AS pp
        ON pp.product_id = d.product_id
       AND pp.printing = d.printing
    WHERE d.product_id = %s
      AND d.market_price IS NOT NULL
      AND d.market_price > 0
    ORDER BY d.snapshot_date;
"""
CANONICAL_CARD_UNIVERSE_QUERY = """
    WITH latest_raw AS (
        SELECT DISTINCT ON (d.product_id)
            d.product_id,
            d.snapshot_date,
            d.printing,
            d.market_price::DOUBLE PRECISION AS market_price
        FROM tcgplayer_daily_prices AS d
        JOIN tcgplayer_primary_printings AS pp
            ON pp.product_id = d.product_id
           AND pp.printing = d.printing
        WHERE d.market_price IS NOT NULL
          AND d.market_price > 0
        ORDER BY
            d.product_id,
            d.snapshot_date DESC
    )
    SELECT
        p.product_id,
        p.name,
        p.set_name,
        p.card_number,
        p.rarity,
        p.artist,
        p.image_url,
        p.tcgplayer_url,
        p.set_published_on,
        pp.printing,
        pp.market_observations,
        latest_raw.snapshot_date,
        latest_raw.market_price
    FROM tcgplayer_products AS p
    LEFT JOIN tcgplayer_primary_printings AS pp
        ON pp.product_id = p.product_id
    LEFT JOIN latest_raw
        ON latest_raw.product_id = p.product_id
    ORDER BY
        p.set_name,
        p.name,
        p.card_number,
        p.product_id;
"""


@dataclass(frozen=True)
class CanonicalCard:
    product_id: int
    name: str
    set_name: str
    card_number: str | None
    rarity: str | None
    artist: str | None
    image_url: str | None
    tcgplayer_url: str | None
    set_published_on: object | None
    printing: str | None
    market_observations: int | None
    latest_raw_date: object | None
    latest_raw_usd: float | None


def _number_or_none(value):
    if value is None:
        return None
    return float(value)


def rows_to_canonical_cards(rows: Iterable[tuple]) -> list[CanonicalCard]:
    """Convert database rows into stable typed canonical-card records."""
    cards: list[CanonicalCard] = []

    for row in rows:
        cards.append(
            CanonicalCard(
                product_id=int(row[0]),
                name=str(row[1]),
                set_name=str(row[2]),
                card_number=(None if row[3] is None else str(row[3])),
                rarity=(None if row[4] is None else str(row[4])),
                artist=(None if row[5] is None else str(row[5])),
                image_url=(None if row[6] is None else str(row[6])),
                tcgplayer_url=(None if row[7] is None else str(row[7])),
                set_published_on=row[8],
                printing=(None if row[9] is None else str(row[9])),
                market_observations=(None if row[10] is None else int(row[10])),
                latest_raw_date=row[11],
                latest_raw_usd=_number_or_none(row[12]),
            )
        )

    return cards


def get_canonical_card_universe() -> list[CanonicalCard]:
    """Return every TCGPlayer product, with raw data when available.

    Products are never removed merely because graded-provider data is
    unavailable. ``printing`` and ``latest_raw_*`` remain ``None`` when the
    TCGPlayer history layer has no usable representative raw series.
    """
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(CANONICAL_CARD_UNIVERSE_QUERY)
            rows = cursor.fetchall()

    return rows_to_canonical_cards(rows)


def get_canonical_card(product_id: int) -> CanonicalCard | None:
    """Return one canonical card by TCGPlayer product id."""
    product_id = int(product_id)
    for card in get_canonical_card_universe():
        if card.product_id == product_id:
            return card
    return None


def get_canonical_raw_history_rows(product_id: int, printing: str | None = None) -> list[tuple]:
    """Return exact-printing history, or the legacy primary when omitted.

    An explicit finish with no history returns no rows, never another finish.
    Missing graded/legacy data cannot remove or alter the RAW series.
    """
    with connect() as connection:
        with connection.cursor() as cursor:
            if printing is None:
                # Backward-compatible research/default path. Interactive finish
                # selectors always supply an explicit printing.
                cursor.execute(CANONICAL_RAW_HISTORY_QUERY, (int(product_id),))
            else:
                from database.card_variants import raw_asset_key
                key = raw_asset_key(product_id, printing)
                cursor.execute("""
                    SELECT snapshot_date, printing, market_price::DOUBLE PRECISION
                    FROM tcgplayer_daily_prices
                    WHERE product_id = %s AND printing = %s
                      AND market_price IS NOT NULL AND market_price > 0
                    ORDER BY snapshot_date;
                """, key)
            return cursor.fetchall()
