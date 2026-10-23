"""Finish-aware RAW identities; never fall back to a different printing.

The catalogue's product_id identifies a card, not its finish. TCGPlayer already
stores prices under (product_id, snapshot_date, printing). These reads expose
that identity without changing the frozen primary-printing research universe.
Condition and language are not separate series in this source: these are USD
market references, not guaranteed NM/UK sale prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from database.tcgplayer import connect


@dataclass(frozen=True)
class RawPrinting:
    product_id: int
    printing: str
    observations: int
    first_date: date | None
    latest_date: date | None
    latest_usd: float | None
    is_primary: bool = False


def raw_asset_key(product_id: int, printing: str) -> tuple[int, str]:
    """Return an exact source identity; unspecified finishes are not Normal."""
    if not isinstance(printing, str) or not printing.strip():
        raise ValueError("Choose a RAW finish (Normal, Holofoil, Reverse Holofoil, etc.).")
    # Do not normalise provider labels: e.g. 1st Edition must remain distinct.
    return int(product_id), printing


PRINTINGS_QUERY = """
    SELECT d.printing, COUNT(*), MIN(d.snapshot_date), MAX(d.snapshot_date),
           (array_agg(d.market_price ORDER BY d.snapshot_date DESC))[1]
               ::DOUBLE PRECISION,
           COALESCE(bool_or(pp.printing = d.printing), FALSE)
    FROM tcgplayer_daily_prices AS d
    LEFT JOIN tcgplayer_primary_printings AS pp ON pp.product_id = d.product_id
    WHERE d.product_id = %s AND d.market_price IS NOT NULL AND d.market_price > 0
    GROUP BY d.printing
    ORDER BY CASE d.printing WHEN 'Normal' THEN 0 WHEN 'Holofoil' THEN 1
                            WHEN 'Reverse Holofoil' THEN 2 ELSE 3 END, d.printing;
"""


def get_raw_printings(product_id: int) -> list[RawPrinting]:
    """List only finishes with their own positive price observations."""
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(PRINTINGS_QUERY, (int(product_id),))
            return [RawPrinting(int(product_id), str(r[0]), int(r[1]), r[2], r[3],
                                None if r[4] is None else float(r[4]), bool(r[5]))
                    for r in cursor.fetchall()]


def is_primary_printing(product_id: int, printing: str) -> bool:
    key = raw_asset_key(product_id, printing)
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT printing FROM tcgplayer_primary_printings WHERE product_id = %s;",
                           (key[0],))
            row = cursor.fetchone()
    return bool(row and row[0] == key[1])


def _requested_values(assets: Iterable[tuple[int, str]]) -> tuple[str, list[object]]:
    keys = list(dict.fromkeys(raw_asset_key(p, f) for p, f in assets))
    return ", ".join(["(%s::BIGINT, %s::TEXT)"] * len(keys)), [v for key in keys for v in key]


def get_current_variant_prices(assets: Iterable[tuple[int, str]]) -> dict[tuple[int, str], tuple]:
    values, params = _requested_values(assets)
    if not params:
        return {}
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"""
                WITH wanted(product_id, printing) AS (VALUES {values})
                SELECT w.product_id, w.printing, latest.snapshot_date, latest.market_price
                FROM wanted AS w
                JOIN LATERAL (
                    SELECT d.snapshot_date, d.market_price::DOUBLE PRECISION
                    FROM tcgplayer_daily_prices AS d
                    WHERE d.product_id = w.product_id AND d.printing = w.printing
                      AND d.market_price IS NOT NULL AND d.market_price > 0
                    ORDER BY d.snapshot_date DESC LIMIT 1
                ) AS latest ON TRUE;
            """, params)
            return {(int(p), str(f)): (dt, float(price)) for p, f, dt, price in cursor.fetchall()}


def get_trade_date_variant_prices(
    assets: Iterable[tuple[int, str]], trade_date: date,
) -> dict[tuple[int, str], float]:
    """Use the existing trailing-seven-day median, separately for each finish."""
    values, params = _requested_values(assets)
    if not params:
        return {}
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"""
                WITH wanted(product_id, printing) AS (VALUES {values})
                SELECT w.product_id, w.printing,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.market_price)
                           ::DOUBLE PRECISION
                FROM wanted AS w JOIN tcgplayer_daily_prices AS d
                  ON d.product_id = w.product_id AND d.printing = w.printing
                WHERE d.market_price IS NOT NULL AND d.market_price > 0
                  AND d.snapshot_date BETWEEN %s::DATE - 6 AND %s::DATE
                GROUP BY w.product_id, w.printing;
            """, [*params, trade_date, trade_date])
            return {(int(p), str(f)): float(price) for p, f, price in cursor.fetchall()
                    if price is not None}


def raw_item_key(item: dict) -> tuple[int, str] | None:
    """Unknown historical finishes stay unknown, rather than taking today's primary."""
    if item.get("product_id") is None or str(item.get("market_type") or "RAW") != "RAW":
        return None
    try:
        return raw_asset_key(item["product_id"], item.get("printing"))
    except (TypeError, ValueError):
        return None


def has_unmapped_graded_finish(item: dict) -> bool:
    """A manual slab's explicit RAW finish cannot be matched to product-only slab data.

    Provider-backed grade entries currently have no explicit RAW printing. Their
    existing provider-series workflow stays unchanged. Explicit-finish/manual
    slabs need a verified provider mapping before using direct slab prices.
    """
    return (str(item.get("market_type") or "RAW") in {"PSA_9", "PSA_10", "ACE_10"}
            and bool(item.get("printing")))
