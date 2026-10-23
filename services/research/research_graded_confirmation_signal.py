from __future__ import annotations

"""Point-in-time research: does graded demand confirm future RAW opportunity alpha?

This script intentionally does *not* change production ranking. It uses only
PokemonPriceTracker observations available on or before each historical formation
 date and asks whether PSA 10 / ACE 10 strength relative to provider RAW improved
subsequent 90D raw-card outcomes. The result decides whether graded confirmation
belongs in the scanner later.
"""

import numpy as np
import pandas as pd

from database.market_analytics import connect
from services.analysis.opportunity_model import SETUP_LABELS, add_setup_flags


SOURCE = "pokemonpricetracker"


def load_panel() -> pd.DataFrame:
    sql = """
        SELECT
            h.formation_date,
            h.product_id,
            h.set_name,
            h.era,
            h.future_return_90d,
            h.future_set_alpha_90d,
            h.future_market_alpha_90d,
            raw_now.price AS provider_raw_now,
            raw_30.price AS provider_raw_30,
            psa_now.price AS psa10_now,
            psa_30.price AS psa10_30,
            ace_now.price AS ace10_now,
            ace_30.price AS ace10_30,
            psa_sales.sales_30d AS psa10_sales_30d,
            ace_sales.sales_30d AS ace10_sales_30d
        FROM raw_opportunity_history_90d h
        LEFT JOIN LATERAL (
            SELECT price::DOUBLE PRECISION AS price
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='RAW'
              AND p.observed_on <= h.formation_date
              AND p.observed_on >= h.formation_date - 14
            ORDER BY p.observed_on DESC LIMIT 1
        ) raw_now ON TRUE
        LEFT JOIN LATERAL (
            SELECT price::DOUBLE PRECISION AS price
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='RAW'
              AND p.observed_on <= h.formation_date - 30
              AND p.observed_on >= h.formation_date - 44
            ORDER BY p.observed_on DESC LIMIT 1
        ) raw_30 ON TRUE
        LEFT JOIN LATERAL (
            SELECT price::DOUBLE PRECISION AS price
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='PSA_10'
              AND p.observed_on <= h.formation_date
              AND p.observed_on >= h.formation_date - 21
            ORDER BY p.observed_on DESC LIMIT 1
        ) psa_now ON TRUE
        LEFT JOIN LATERAL (
            SELECT price::DOUBLE PRECISION AS price
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='PSA_10'
              AND p.observed_on <= h.formation_date - 30
              AND p.observed_on >= h.formation_date - 51
            ORDER BY p.observed_on DESC LIMIT 1
        ) psa_30 ON TRUE
        LEFT JOIN LATERAL (
            SELECT price::DOUBLE PRECISION AS price
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='ACE_10'
              AND p.observed_on <= h.formation_date
              AND p.observed_on >= h.formation_date - 30
            ORDER BY p.observed_on DESC LIMIT 1
        ) ace_now ON TRUE
        LEFT JOIN LATERAL (
            SELECT price::DOUBLE PRECISION AS price
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='ACE_10'
              AND p.observed_on <= h.formation_date - 30
              AND p.observed_on >= h.formation_date - 60
            ORDER BY p.observed_on DESC LIMIT 1
        ) ace_30 ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(p.sale_count), 0)::DOUBLE PRECISION AS sales_30d
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='PSA_10'
              AND p.observed_on > h.formation_date - 30
              AND p.observed_on <= h.formation_date
        ) psa_sales ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(p.sale_count), 0)::DOUBLE PRECISION AS sales_30d
            FROM provider_price_history p
            WHERE p.product_id=h.product_id AND p.source=%s AND p.market_type='ACE_10'
              AND p.observed_on > h.formation_date - 30
              AND p.observed_on <= h.formation_date
        ) ace_sales ON TRUE
        WHERE h.formation_date >= (
            SELECT MIN(observed_on) + 30
            FROM provider_price_history
            WHERE source=%s AND market_type='RAW'
        )
        ORDER BY h.formation_date, h.product_id;
    """
    params = (SOURCE,) * 9
    with connect() as connection:
        frame = pd.read_sql_query(sql, connection, params=params)

    if frame.empty:
        return frame
    frame["formation_date"] = pd.to_datetime(frame["formation_date"])
    numeric = [
        "future_return_90d", "future_set_alpha_90d", "future_market_alpha_90d",
        "provider_raw_now", "provider_raw_30", "psa10_now", "psa10_30",
        "ace10_now", "ace10_30", "psa10_sales_30d", "ace10_sales_30d",
    ]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame["provider_raw_return_30d"] = frame["provider_raw_now"] / frame["provider_raw_30"] - 1
    frame["psa10_return_30d"] = frame["psa10_now"] / frame["psa10_30"] - 1
    frame["ace10_return_30d"] = frame["ace10_now"] / frame["ace10_30"] - 1
    frame["psa10_confirmation"] = frame["psa10_return_30d"] - frame["provider_raw_return_30d"]
    frame["ace10_confirmation"] = frame["ace10_return_30d"] - frame["provider_raw_return_30d"]
    return frame.replace([np.inf, -np.inf], np.nan)


def _print_quintiles(frame: pd.DataFrame, signal: str, title: str) -> None:
    work = frame[[signal, "future_set_alpha_90d", "future_market_alpha_90d", "future_return_90d"]].dropna()
    print()
    print(title)
    print("-" * 104)
    if len(work) < 100 or work[signal].nunique() < 5:
        print(f"Insufficient data: N={len(work):,}")
        return
    ranks = work[signal].rank(method="first")
    work["quintile"] = pd.qcut(ranks, 5, labels=False, duplicates="drop") + 1
    summary = work.groupby("quintile").agg(
        n=(signal, "size"),
        signal=(signal, "median"),
        return90=("future_return_90d", "mean"),
        set_alpha=("future_set_alpha_90d", "mean"),
        market_alpha=("future_market_alpha_90d", "mean"),
        hit=("future_set_alpha_90d", lambda x: (x > 0).mean()),
    )
    for q, row in summary.iterrows():
        print(
            f"Q{int(q)} N={int(row['n']):>5,} signal={row['signal']*100:+7.1f}%  "
            f"next90={row['return90']*100:+7.1f}%  set alpha={row['set_alpha']*100:+7.1f}%  "
            f"market alpha={row['market_alpha']*100:+7.1f}%  set-hit={row['hit']*100:5.1f}%"
        )
    if 1 in summary.index and 5 in summary.index:
        spread = summary.loc[5, "set_alpha"] - summary.loc[1, "set_alpha"]
        print(f"Q5 - Q1 future set-alpha spread: {spread*100:+.2f} pp")
    rank_ic = work[signal].rank().corr(work["future_set_alpha_90d"].rank())
    print(f"Rank IC vs future set alpha: {rank_ic:+.4f}")


def main() -> None:
    frame = load_panel()
    if frame.empty:
        raise SystemExit("No overlapping provider/raw opportunity history found.")

    print()
    print("=" * 104)
    print("GRADED-DEMAND CONFIRMATION RESEARCH — POINT-IN-TIME")
    print("=" * 104)
    print(
        f"Rows in overlap panel: {len(frame):,} · dates {frame['formation_date'].min().date()} -> "
        f"{frame['formation_date'].max().date()}"
    )
    print("All provider inputs are as-of the formation date; future 90D outcomes are labels only.")

    _print_quintiles(
        frame,
        "psa10_confirmation",
        "PSA 10 30D strength minus provider RAW 30D strength",
    )
    _print_quintiles(
        frame,
        "psa10_return_30d",
        "PSA 10 absolute 30D strength",
    )
    _print_quintiles(
        frame,
        "ace10_confirmation",
        "ACE 10 30D strength minus provider RAW 30D strength (sparser)",
    )

    print()
    print("Interpretation rule: only add a graded-confirmation feature to production if stronger")
    print("confirmation shows a reasonably monotonic improvement in forward alpha and survives")
    print("the smaller provider-history sample. A pretty single quintile is not enough.")
    print("=" * 104)


if __name__ == "__main__":
    main()
