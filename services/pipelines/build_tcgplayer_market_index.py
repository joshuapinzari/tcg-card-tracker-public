from __future__ import annotations

import argparse
import time

from database.market_analytics import (
    get_latest_market_summary,
    rebuild_all_market_analytics,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build clean TCGPlayer market indices "
            "from historical daily prices."
        )
    )

    parser.add_argument(
        "--price-floor",
        type=float,
        default=5.0,
        help=(
            "Minimum previous-day market price "
            "for a product to enter the index."
        ),
    )

    parser.add_argument(
        "--min-constituents",
        type=int,
        default=5,
        help=(
            "Minimum cards required to publish "
            "an index value for a day."
        ),
    )

    args = parser.parse_args()

    print()
    print("=" * 72)
    print("BUILDING TCGPLAYER MARKET ANALYTICS")
    print("=" * 72)

    print(
        f"Point-in-time price floor: "
        f"${args.price_floor:,.2f}"
    )

    print(
        f"Minimum constituents: "
        f"{args.min_constituents:,}"
    )

    print()
    print(
        "Raw observations will not be modified."
    )

    print(
        "Selecting primary printings and "
        "building chained daily indices..."
    )

    started = time.monotonic()

    result = rebuild_all_market_analytics(
        price_floor=args.price_floor,
        min_constituents=args.min_constituents,
    )

    elapsed = time.monotonic() - started

    print()
    print("=" * 72)
    print("BUILD COMPLETE")
    print("=" * 72)

    print(
        f"Products with selected "
        f"primary printing: "
        f"{result['primary_products']:,}"
    )

    print(
        f"Derived daily index rows: "
        f"{result['index_rows']:,}"
    )

    print(
        f"Runtime: "
        f"{elapsed:,.1f} seconds"
    )

    print()
    print("LATEST MARKET / ERA VALUES")
    print("=" * 72)

    rows = get_latest_market_summary()

    for (
        scope_type,
        scope_name,
        snapshot_date,
        index_value,
        constituent_count,
        breadth_positive,
    ) in rows:
        change = float(index_value) - 100.0

        breadth = (
            float(breadth_positive)
            * 100.0
        )

        print(
            f"{scope_type.upper():<8} "
            f"{scope_name:<28} "
            f"{snapshot_date}  "
            f"Index {float(index_value):>8.2f}  "
            f"Change {change:>+8.2f}%  "
            f"Cards {constituent_count:>5,}  "
            f"Breadth {breadth:>6.1f}%"
        )

    print()
    print(
        "Market analytics are ready for "
        "the Streamlit GUI."
    )


if __name__ == "__main__":
    main()
