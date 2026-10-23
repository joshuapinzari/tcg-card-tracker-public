from __future__ import annotations

from services.analysis.opportunity_snapshot import snapshot_input_writer

import argparse
import sys
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from database.market_analytics import (
    connect as market_connect,
    rebuild_primary_printings,
)
from database.tcgplayer import (
    connect,
    get_database_summary,
    get_pending_products,
    initialise_tcgplayer_schema,
    mark_failed,
    mark_no_history,
    store_price_history,
    upsert_products,
)
from pokemon_eras import TRACKED_ERA_LABELS, era_for_set_name
from sources.tcgcsv.client import (
    TCGCSVClient,
    is_card_product,
    is_target_group,
    product_to_row,
)
from sources.tickermint.client import (
    TickerMintClient,
)


def to_decimal(
    value,
) -> Decimal | None:
    if value is None:
        return None

    try:
        return Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


def history_to_rows(
    product_id: int,
    history: dict,
) -> list[tuple]:
    rows = []

    for printing, observations in (
        history.items()
    ):
        if not isinstance(
            observations,
            list,
        ):
            continue

        printing_name = (
            str(printing)
            if printing
            else "Unknown"
        )

        for observation in observations:
            raw_date = observation.get(
                "snapshot_date"
            )

            if not raw_date:
                continue

            try:
                snapshot_date = (
                    date.fromisoformat(
                        raw_date[:10]
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            low_price = to_decimal(
                observation.get("low")
            )

            if low_price is None:
                low_price = to_decimal(
                    observation.get(
                        "low_price_usd"
                    )
                )

            market_price = to_decimal(
                observation.get("market")
            )

            if market_price is None:
                market_price = to_decimal(
                    observation.get(
                        "market_price_usd"
                    )
                )

            if (
                low_price is None
                and market_price is None
            ):
                continue

            rows.append(
                (
                    product_id,
                    snapshot_date,
                    printing_name,
                    low_price,
                    None,
                    None,
                    market_price,
                    None,
                    "USD",
                    "tcgplayer",
                    "tickermint",
                )
            )

    return rows


def sync_catalog(
    connection,
) -> tuple[int, int]:
    client = TCGCSVClient()

    print()
    print("SYNCING TCGPLAYER CATALOG")
    print("=" * 72)

    groups = client.get_groups()

    selected_groups = [
        group
        for group in groups
        if is_target_group(group)
    ]

    selected_groups.sort(
        key=lambda group: (
            group.get("publishedOn")
            or "",
            group.get("groupId")
            or 0,
        )
    )

    print(
        f"Pokemon groups available: "
        f"{len(groups):,}"
    )

    print(
        f"Target e-Card/EX/DP/Platinum/HGSS/BW/XY/SM/SWSH/SV/ME groups: "
        f"{len(selected_groups):,}"
    )

    total_products = 0

    for index, group in enumerate(
        selected_groups,
        start=1,
    ):
        group_id = int(
            group["groupId"]
        )

        products = client.get_products(
            group_id
        )

        cards = [
            product_to_row(
                group,
                product,
            )
            for product in products
            if is_card_product(product)
        ]

        upsert_products(
            connection,
            cards,
        )

        connection.commit()

        total_products += len(cards)

        abbreviation = (
            group.get("abbreviation")
            or "?"
        )

        print(
            f"[{index:>2}/"
            f"{len(selected_groups)}] "
            f"{abbreviation:<8} "
            f"{group['name']:<42} "
            f"{len(cards):>4} cards"
        )

    print()
    print(
        f"Catalog sync complete: "
        f"{total_products:,} "
        f"card products encountered."
    )

    return (
        len(selected_groups),
        total_products,
    )


def format_seconds(
    seconds: float,
) -> str:
    seconds = max(
        0,
        int(seconds),
    )

    hours, remainder = divmod(
        seconds,
        3600,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    if hours:
        return (
            f"{hours}h "
            f"{minutes}m "
            f"{seconds}s"
        )

    if minutes:
        return (
            f"{minutes}m "
            f"{seconds}s"
        )

    return f"{seconds}s"


def print_summary(
    connection,
) -> None:
    summary = get_database_summary(
        connection
    )

    print()
    print("=" * 72)
    print("TCGPLAYER RESEARCH DATA SUMMARY")
    print("=" * 72)

    print(
        f"Products: "
        f"{summary['products']:,}"
    )

    print(
        f"Products with prices: "
        f"{summary['priced_products']:,}"
    )

    print(
        f"Daily observations: "
        f"{summary['observations']:,}"
    )

    print(
        f"Date range: "
        f"{summary['first_date']} "
        f"-> "
        f"{summary['last_date']}"
    )

    print()
    print("Backfill statuses:")

    for status, count in (
        summary["statuses"].items()
    ):
        print(
            f"  {status:<12} "
            f"{count:>6,}"
        )


@snapshot_input_writer
def run_backfill(
    *,
    limit: int | None,
    refresh: bool,
    delay: float,
    skip_catalog: bool,
    catalog_only: bool,
    era: str | None = None,
) -> None:
    initialise_tcgplayer_schema()

    with connect() as connection:
        if not skip_catalog:
            sync_catalog(
                connection
            )

        if catalog_only:
            print_summary(
                connection
            )
            return

        pending = get_pending_products(
            connection,
            refresh=refresh,
            # Apply the era filter before the user-facing limit so
            # --era Diamond & Pearl --limit 50 means 50 DP cards, not the
            # DP subset of an arbitrary 50-card global slice.
            limit=None if era is not None else limit,
        )

        if era is not None:
            pending = [
                product
                for product in pending
                if era_for_set_name(product.get("set_name")) == era
            ]
            if limit is not None:
                pending = pending[: max(0, int(limit))]

        print()
        print("TICKERMINT HISTORICAL BACKFILL")
        print("=" * 72)

        if era is not None:
            print(f"Era filter: {era}")

        print(
            f"Products to process: "
            f"{len(pending):,}"
        )

        if not pending:
            print(
                "Nothing to do. "
                "Backfill is already complete."
            )

            with market_connect() as market_connection:
                primary_products = rebuild_primary_printings(market_connection)
            print(f"Primary-printing products refreshed: {primary_products:,}")

            print_summary(
                connection
            )
            return

        estimated_seconds = (
            len(pending)
            * delay
        )

        print(
            "Minimum request pacing time: "
            f"{format_seconds(estimated_seconds)}"
        )

        print(
            "Safe to stop with Ctrl-C. "
            "A rerun resumes completed products."
        )

        client = TickerMintClient(
            min_interval=delay
        )

        started = time.monotonic()

        successful = 0
        no_history = 0
        failed = 0
        observations_added = 0

        try:
            for index, product in enumerate(
                pending,
                start=1,
            ):
                product_id = int(
                    product["product_id"]
                )

                name = product["name"]
                set_name = product["set_name"]

                card_number = (
                    product["card_number"]
                    or "?"
                )

                try:
                    history = (
                        client.get_price_history(
                            product_id
                        )
                    )

                    rows = history_to_rows(
                        product_id,
                        history,
                    )

                    if not rows:
                        mark_no_history(
                            connection,
                            product_id,
                        )

                        connection.commit()

                        no_history += 1

                        result = "no history"

                    else:
                        stored = (
                            store_price_history(
                                connection,
                                product_id,
                                rows,
                            )
                        )

                        connection.commit()

                        successful += 1
                        observations_added += (
                            stored
                        )

                        first_date = min(
                            row[1]
                            for row in rows
                        )

                        last_date = max(
                            row[1]
                            for row in rows
                        )

                        result = (
                            f"{stored:,} obs "
                            f"{first_date} -> "
                            f"{last_date}"
                        )

                except Exception as error:
                    connection.rollback()

                    mark_failed(
                        connection,
                        product_id,
                        str(error),
                    )

                    connection.commit()

                    failed += 1

                    result = (
                        f"FAILED: {error}"
                    )

                elapsed = (
                    time.monotonic()
                    - started
                )

                average = (
                    elapsed / index
                )

                remaining = (
                    len(pending)
                    - index
                )

                eta = (
                    average
                    * remaining
                )

                print(
                    f"[{index:>5}/"
                    f"{len(pending):<5}] "
                    f"{name} "
                    f"{card_number} "
                    f"| {set_name} "
                    f"| {result} "
                    f"| ETA "
                    f"{format_seconds(eta)}"
                )

        except KeyboardInterrupt:
            print()
            print()
            print(
                "Backfill stopped safely."
            )

            print(
                "Completed products are "
                "already committed."
            )

            print(
                "Run the same command "
                "again to resume."
            )

        elapsed = (
            time.monotonic()
            - started
        )

        print()
        print("=" * 72)
        print("BACKFILL RUN COMPLETE")
        print("=" * 72)

        print(
            f"Successful: "
            f"{successful:,}"
        )

        print(
            f"No history: "
            f"{no_history:,}"
        )

        print(
            f"Failed: "
            f"{failed:,}"
        )

        print(
            f"Observations processed: "
            f"{observations_added:,}"
        )

        print(
            f"TickerMint HTTP requests: "
            f"{client.request_count:,}"
        )

        print(
            f"Runtime: "
            f"{format_seconds(elapsed)}"
        )

        with market_connect() as market_connection:
            primary_products = rebuild_primary_printings(market_connection)
        print(f"Primary-printing products refreshed: {primary_products:,}")

        print_summary(
            connection
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill daily TCGPlayer "
            "Pokemon price history using "
            "TCGCSV metadata and "
            "TickerMint history."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Maximum number of products "
            "to process this run."
        ),
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Fetch products even if they "
            "were previously completed."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.65,
        help=(
            "Minimum seconds between "
            "TickerMint requests."
        ),
    )

    parser.add_argument(
        "--skip-catalog",
        action="store_true",
        help=(
            "Skip the TCGCSV catalog sync."
        ),
    )

    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help=(
            "Sync product metadata only "
            "without fetching history."
        ),
    )

    parser.add_argument(
        "--era",
        choices=TRACKED_ERA_LABELS,
        help=(
            "Only backfill cards belonging to one tracked English Pokemon era. "
            "Catalog sync still discovers every tracked set."
        ),
    )

    args = parser.parse_args()

    if args.delay < 0.5:
        raise SystemExit(
            "Refusing delay below 0.5s "
            "on the free API."
        )

    run_backfill(
        limit=args.limit,
        refresh=args.refresh,
        delay=args.delay,
        skip_catalog=args.skip_catalog,
        catalog_only=args.catalog_only,
        era=args.era,
    )


if __name__ == "__main__":
    main()
