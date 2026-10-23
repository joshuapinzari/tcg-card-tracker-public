from __future__ import annotations

from services.analysis.opportunity_snapshot import snapshot_input_writer

import argparse
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from database.tcgplayer import (
    connect,
    get_daily_price_date_range,
    get_daily_update_groups,
    get_latest_daily_price_dates,
    get_latest_product_price,
    get_product_group_map,
    initialise_tcgplayer_schema,
    store_daily_price_rows,
)
from services.pipelines.backfill_tcgplayer_history import (
    format_seconds,
    sync_catalog,
)
from sources.tickermint.client import (
    TickerMintClient,
)


MEWTWO_GX_78_PRODUCT_ID = 146698


def to_decimal(
    value,
) -> Decimal | None:
    if value in (
        None,
        "",
    ):
        return None

    try:
        result = Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return None

    if result <= 0:
        return None

    return result


def prepare_set_price_rows(
    *,
    group_id: int,
    api_rows: list[dict],
    product_group_map: dict[int, int],
    latest_dates: dict[tuple[int, str], date],
) -> tuple[list[tuple], dict]:
    """
    Convert one TickerMint set CSV response into append-only DB rows.

    Only canonical card products are accepted. Rows at or before the latest
    stored date for the same product/printing are skipped, so provider history
    is never rewritten by the daily updater.
    """
    rows = []
    seen = set()

    stats = {
        "api_rows": len(api_rows),
        "accepted": 0,
        "already_current": 0,
        "unknown_product": 0,
        "wrong_group": 0,
        "invalid": 0,
        "duplicate": 0,
    }

    for item in api_rows:
        try:
            product_id = int(
                item.get("product_id")
            )
        except (
            TypeError,
            ValueError,
        ):
            stats["invalid"] += 1
            continue

        known_group = (
            product_group_map.get(
                product_id
            )
        )

        if known_group is None:
            stats["unknown_product"] += 1
            continue

        if known_group != group_id:
            stats["wrong_group"] += 1
            continue

        raw_date = item.get("date")

        try:
            snapshot_date = (
                date.fromisoformat(
                    str(raw_date)[:10]
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            stats["invalid"] += 1
            continue

        printing = str(
            item.get("printing")
            or ""
        ).strip()

        market_price = to_decimal(
            item.get(
                "market_price_usd"
            )
        )

        if (
            not printing
            or market_price is None
        ):
            stats["invalid"] += 1
            continue

        row_key = (
            product_id,
            snapshot_date,
            printing,
        )

        if row_key in seen:
            stats["duplicate"] += 1
            continue

        seen.add(row_key)

        series_key = (
            product_id,
            printing,
        )

        latest_date = (
            latest_dates.get(
                series_key
            )
        )

        if (
            latest_date is not None
            and snapshot_date
            <= latest_date
        ):
            stats[
                "already_current"
            ] += 1
            continue

        rows.append(
            (
                product_id,
                snapshot_date,
                printing,
                None,
                None,
                None,
                market_price,
                None,
                "USD",
                "tcgplayer",
                "tickermint_set_csv",
            )
        )

        latest_dates[
            series_key
        ] = snapshot_date

        stats["accepted"] += 1

    return rows, stats


def print_mewtwo_check(
    connection,
) -> None:
    row = get_latest_product_price(
        connection,
        MEWTWO_GX_78_PRODUCT_ID,
    )

    print()
    print("MEWTWO GX 78/73 CHECK")
    print("=" * 72)

    if row is None:
        print(
            "Product 146698 has no "
            "stored market price."
        )
        return

    (
        snapshot_date,
        printing,
        market_price,
    ) = row

    print(
        f"Product: "
        f"{MEWTWO_GX_78_PRODUCT_ID}"
    )

    print(
        f"Latest: {snapshot_date} "
        f"| {printing} "
        f"| ${float(market_price):,.2f}"
    )


@snapshot_input_writer
def run_update(
    *,
    delay: float,
    skip_catalog: bool,
    group_id: int | None,
    limit_groups: int | None,
) -> None:
    initialise_tcgplayer_schema()

    with connect() as connection:
        if not skip_catalog:
            sync_catalog(
                connection
            )

        groups = get_daily_update_groups(
            connection
        )

        if group_id is not None:
            groups = [
                group
                for group in groups
                if group["group_id"]
                == group_id
            ]

            if not groups:
                raise SystemExit(
                    "Requested group_id is "
                    "not present in the "
                    "canonical TCGPlayer catalog."
                )

        if limit_groups is not None:
            groups = groups[
                :limit_groups
            ]

        product_group_map = (
            get_product_group_map(
                connection
            )
        )

        latest_dates = (
            get_latest_daily_price_dates(
                connection
            )
        )

        before_first, before_last = (
            get_daily_price_date_range(
                connection
            )
        )

        print()
        print("TICKERMINT DAILY TCGPLAYER UPDATE")
        print("=" * 72)

        print(
            f"Canonical products: "
            f"{len(product_group_map):,}"
        )

        print(
            f"Groups to fetch: "
            f"{len(groups):,}"
        )

        print(
            f"Existing date range: "
            f"{before_first} -> "
            f"{before_last}"
        )

        print(
            "Mode: append-only; stored "
            "historical rows are not "
            "rewritten."
        )

        client = TickerMintClient(
            min_interval=delay
        )

        started = time.monotonic()

        totals = {
            "api_rows": 0,
            "accepted": 0,
            "inserted": 0,
            "already_current": 0,
            "unknown_product": 0,
            "wrong_group": 0,
            "invalid": 0,
            "duplicate": 0,
            "failed_groups": 0,
        }

        for index, group in enumerate(
            groups,
            start=1,
        ):
            current_group_id = int(
                group["group_id"]
            )

            set_name = (
                group["set_name"]
                or "?"
            )

            try:
                api_rows = (
                    client.get_set_prices(
                        current_group_id
                    )
                )

                rows, stats = (
                    prepare_set_price_rows(
                        group_id=(
                            current_group_id
                        ),
                        api_rows=api_rows,
                        product_group_map=(
                            product_group_map
                        ),
                        latest_dates=(
                            latest_dates
                        ),
                    )
                )

                inserted = (
                    store_daily_price_rows(
                        connection,
                        rows,
                    )
                )

                connection.commit()

                for key in (
                    "api_rows",
                    "accepted",
                    "already_current",
                    "unknown_product",
                    "wrong_group",
                    "invalid",
                    "duplicate",
                ):
                    totals[key] += (
                        stats[key]
                    )

                totals["inserted"] += (
                    inserted
                )

                result = (
                    f"API {stats['api_rows']:,} "
                    f"| new {inserted:,} "
                    f"| current "
                    f"{stats['already_current']:,} "
                    f"| outside catalog "
                    f"{stats['unknown_product']:,}"
                )

            except Exception as error:
                connection.rollback()

                totals[
                    "failed_groups"
                ] += 1

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

            eta = (
                average
                * (
                    len(groups)
                    - index
                )
            )

            print(
                f"[{index:>2}/"
                f"{len(groups):<2}] "
                f"{current_group_id:<6} "
                f"{set_name:<42} "
                f"| {result} "
                f"| ETA "
                f"{format_seconds(eta)}"
            )

        after_first, after_last = (
            get_daily_price_date_range(
                connection
            )
        )

        elapsed = (
            time.monotonic()
            - started
        )

        print()
        print("=" * 72)
        print("DAILY UPDATE COMPLETE")
        print("=" * 72)

        print(
            f"TickerMint requests: "
            f"{client.request_count:,}"
        )

        print(
            f"API rows received: "
            f"{totals['api_rows']:,}"
        )

        print(
            f"New observations inserted: "
            f"{totals['inserted']:,}"
        )

        print(
            f"Already current: "
            f"{totals['already_current']:,}"
        )

        print(
            f"Rows outside canonical "
            f"card catalog: "
            f"{totals['unknown_product']:,}"
        )

        print(
            f"Invalid/duplicate rows: "
            f"{totals['invalid'] + totals['duplicate']:,}"
        )

        print(
            f"Failed groups: "
            f"{totals['failed_groups']:,}"
        )

        print(
            f"Date range before: "
            f"{before_first} -> "
            f"{before_last}"
        )

        print(
            f"Date range after:  "
            f"{after_first} -> "
            f"{after_last}"
        )

        print(
            f"Runtime: "
            f"{format_seconds(elapsed)}"
        )

        print_mewtwo_check(
            connection
        )

        totals["requests"] = client.request_count
        totals["before_first"] = before_first
        totals["before_last"] = before_last
        totals["after_first"] = after_first
        totals["after_last"] = after_last
        totals["runtime_seconds"] = elapsed
        return totals


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Append the newest daily "
            "TCGPlayer market prices from "
            "TickerMint set CSV endpoints."
        )
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
            "Skip the TCGCSV catalog sync "
            "before collecting prices."
        ),
    )

    parser.add_argument(
        "--group-id",
        type=int,
        default=None,
        help=(
            "Update one TCGPlayer group "
            "only."
        ),
    )

    parser.add_argument(
        "--limit-groups",
        type=int,
        default=None,
        help=(
            "Update only the first N "
            "canonical groups."
        ),
    )

    args = parser.parse_args()

    if args.delay < 0.5:
        raise SystemExit(
            "Refusing delay below 0.5s "
            "on the free API."
        )

    if (
        args.limit_groups is not None
        and args.limit_groups < 1
    ):
        raise SystemExit(
            "--limit-groups must be "
            "at least 1."
        )

    stats = run_update(
        delay=args.delay,
        skip_catalog=args.skip_catalog,
        group_id=args.group_id,
        limit_groups=(
            args.limit_groups
        ),
    )

    if stats["failed_groups"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
