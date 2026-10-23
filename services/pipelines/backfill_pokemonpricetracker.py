"""Backfill PokemonPriceTracker RAW / PSA 9 / PSA 10 / ACE 10 history.

The workflow is resumable and idempotent. Provider data is stored in dedicated
TCGPlayer-ID keyed tables and never overwrites data from other providers.
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from database.canonical_cards import CanonicalCard, get_canonical_card_universe
from database.market_analytics import (
    connect as market_connect,
    initialise_market_analytics_schema,
    rebuild_primary_printings,
)
from database.provider_history import (
    SOURCE_POKEMON_PRICE_TRACKER,
    connect,
    get_provider_backfill_summary,
    get_provider_status_map,
    initialise_provider_history_schema,
    mark_provider_failed,
    store_provider_card_history,
)
from database.tcgplayer import initialise_tcgplayer_schema
from pokemon_eras import TRACKED_ERA_LABELS, era_for_set_name
from sources.pokemonpricetracker.client import (
    PokemonPriceTrackerClient,
    PokemonPriceTrackerError,
    graded_price_history_by_tier,
    graded_sales_by_tier,
    raw_price_history,
    tcgplayer_id_from_card,
)


TARGET_GRADED_MARKETS = ("PSA_9", "PSA_10", "ACE_10")
DEFAULT_DAYS = 180
DEFAULT_MAX_DATA_POINTS = 180
DEFAULT_REQUEST_INTERVAL = 1.10
DEFAULT_RETRY_WAIT = 65.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MIN_RAW_USD = 10.0


class RequestPacer:
    def __init__(self, interval: float):
        self.interval = max(0.0, float(interval))
        self.last_request_at: float | None = None

    def wait(self) -> None:
        if self.last_request_at is not None:
            elapsed = time.monotonic() - self.last_request_at
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
        self.last_request_at = time.monotonic()


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_provider_history_rows(
    product_id: int,
    card_payload: dict,
    *,
    preferred_printing: str | None,
) -> list[dict]:
    """Normalize provider raw and graded history into one market-row shape."""
    rows: list[dict] = []

    raw_label, raw_history = raw_price_history(
        card_payload,
        preferred_printing=preferred_printing,
        condition="Near Mint",
    )
    for observation in raw_history:
        observed_on = _date(observation.get("date"))
        price = _number(observation.get("market"))
        if observed_on is None or price is None or price <= 0:
            continue
        rows.append(
            {
                "product_id": int(product_id),
                "market_type": "RAW",
                "observed_on": observed_on,
                "series_label": raw_label or "Near Mint",
                "price": price,
                "sale_count": None,
                "volume": _number(observation.get("volume")),
                "total_value": None,
                "seven_day_average": None,
                "seven_day_volume_avg": None,
                "seven_day_value_avg": None,
            }
        )

    graded_history = graded_price_history_by_tier(card_payload)
    for market_type in TARGET_GRADED_MARKETS:
        history = graded_history.get(market_type) or {}
        if not isinstance(history, dict):
            continue
        for raw_date, observation in history.items():
            if not isinstance(observation, dict):
                continue
            observed_on = _date(raw_date)
            price = _number(observation.get("average"))
            if observed_on is None or price is None or price <= 0:
                continue
            sale_count = observation.get("count")
            try:
                sale_count = int(sale_count) if sale_count is not None else None
            except (TypeError, ValueError):
                sale_count = None
            rows.append(
                {
                    "product_id": int(product_id),
                    "market_type": market_type,
                    "observed_on": observed_on,
                    "series_label": "",
                    "price": price,
                    "sale_count": sale_count,
                    "volume": None,
                    "total_value": _number(observation.get("totalValue")),
                    "seven_day_average": _number(observation.get("sevenDayAverage")),
                    "seven_day_volume_avg": _number(
                        observation.get("sevenDayVolumeAvg")
                    ),
                    "seven_day_value_avg": _number(observation.get("sevenDayValueAvg")),
                }
            )

    return rows


def build_provider_snapshot_rows(card_payload: dict) -> list[dict]:
    """Normalize current graded aggregates for liquidity/current-value research."""
    sales = graded_sales_by_tier(card_payload)
    rows: list[dict] = []

    for market_type in TARGET_GRADED_MARKETS:
        aggregate = sales.get(market_type)
        if not isinstance(aggregate, dict):
            continue
        rows.append(
            {
                "market_type": market_type,
                "sales_count": aggregate.get("count"),
                "median_price": _number(aggregate.get("medianPrice")),
                "average_price": _number(aggregate.get("averagePrice")),
                "min_price": _number(aggregate.get("minPrice")),
                "max_price": _number(aggregate.get("maxPrice")),
                "market_price_7d": _number(aggregate.get("marketPrice7Day")),
                "market_price_median_7d": _number(
                    aggregate.get("marketPriceMedian7Day")
                ),
                "daily_volume_7d": _number(aggregate.get("dailyVolume7Day")),
                "last_sale_date": _date(aggregate.get("lastSaleDate")),
                "smart_market_price": aggregate.get("smartMarketPrice") or {},
            }
        )

    return rows


def fetch_with_retry(
    client: PokemonPriceTrackerClient,
    *,
    product_id: int,
    days: int,
    max_data_points: int,
    pacer: RequestPacer,
    retry_wait: float,
    max_attempts: int,
) -> tuple[dict | None, dict]:
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        pacer.wait()
        try:
            return client.get_card(
                product_id,
                include_ebay=True,
                include_history=True,
                days=days,
                max_data_points=max_data_points,
            )
        except PokemonPriceTrackerError as error:
            last_error = error
            if error.status_code == 429:
                wait = max(float(error.retry_after or 0), float(retry_wait))
                print(f"    Rate limited (429). Waiting {wait:.0f}s before retry...")
                time.sleep(wait)
                continue
            if error.status_code is not None and error.status_code >= 500:
                if attempt < max_attempts:
                    wait = min(30.0, 2.0 * attempt)
                    print(
                        f"    Provider error {error.status_code}. "
                        f"Retrying in {wait:.0f}s ({attempt}/{max_attempts})..."
                    )
                    time.sleep(wait)
                    continue
            raise
        except Exception as error:
            last_error = error
            if attempt >= max_attempts:
                raise
            wait = min(30.0, 2.0 * attempt)
            print(f"    Request failed. Retrying in {wait:.0f}s ({attempt}/{max_attempts})...")
            time.sleep(wait)

    assert last_error is not None
    raise last_error


def _parse_ids(values: list[str] | None) -> list[int]:
    if not values:
        return []
    result: list[int] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                result.append(int(part))
    return result


def select_products(
    universe: list[CanonicalCard],
    *,
    product_ids: list[int],
    min_raw_usd: float,
    all_cards: bool,
    statuses: dict[int, dict],
    requested_days: int,
    refresh: bool,
    limit: int | None,
    era: str | None = None,
) -> tuple[list[CanonicalCard], int]:
    by_id = {int(card.product_id): card for card in universe}

    if product_ids:
        missing = [product_id for product_id in product_ids if product_id not in by_id]
        if missing:
            raise ValueError(
                "Unknown local TCGPlayer product IDs: "
                + ", ".join(str(value) for value in missing)
            )
        eligible = [by_id[product_id] for product_id in product_ids]
    elif all_cards:
        eligible = list(universe)
    else:
        eligible = [
            card
            for card in universe
            if card.latest_raw_usd is not None
            and float(card.latest_raw_usd) >= float(min_raw_usd)
        ]

    if era is not None:
        eligible = [
            card
            for card in eligible
            if era_for_set_name(card.set_name) == era
        ]

    eligible.sort(
        key=lambda card: (float(card.latest_raw_usd or 0.0), int(card.product_id)),
        reverse=True,
    )
    eligible_count = len(eligible)

    if not refresh:
        pending: list[CanonicalCard] = []
        for card in eligible:
            status = statuses.get(int(card.product_id)) or {}
            enough_history = int(status.get("requested_days") or 0) >= int(requested_days)
            terminal = status.get("status") in {"completed", "no_history"}
            if terminal and enough_history:
                continue
            pending.append(card)
        eligible = pending

    if limit is not None:
        eligible = eligible[: max(0, int(limit))]

    return eligible, eligible_count


def _format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _print_database_summary(connection) -> None:
    summary = get_provider_backfill_summary(connection, SOURCE_POKEMON_PRICE_TRACKER)
    print("\n" + "=" * 72)
    print("POKEMONPRICETRACKER DATABASE SUMMARY")
    print("=" * 72)
    print(f"Products stored: {summary['products']:,}")
    print(f"Observations: {summary['observations']:,}")
    print(f"  RAW:    {summary['raw_observations']:,}")
    print(f"  PSA 9:  {summary['psa_9_observations']:,}")
    print(f"  PSA 10: {summary['psa_10_observations']:,}")
    print(f"  ACE 10: {summary['ace_10_observations']:,}")
    print(f"Date range: {summary['first_date']} -> {summary['last_date']}")
    if summary["statuses"]:
        print("Statuses:")
        for status, count in summary["statuses"].items():
            print(f"  {status:<12} {count:>6,}")


def run_backfill(
    *,
    days: int,
    max_data_points: int,
    request_interval: float,
    retry_wait: float,
    max_attempts: int,
    min_raw_usd: float,
    all_cards: bool,
    refresh: bool,
    limit: int | None,
    product_ids: list[int],
    era: str | None = None,
    dry_run: bool = False,
) -> None:
    initialise_tcgplayer_schema()
    initialise_market_analytics_schema()
    initialise_provider_history_schema()

    # New catalogue/history additions must be promoted into the canonical
    # primary-printing layer before selection. Otherwise a newly added era can
    # have raw history in the database but still look unpriced to this backfill.
    with market_connect() as market_connection:
        primary_products = rebuild_primary_printings(market_connection)

    universe = get_canonical_card_universe()

    with connect() as connection:
        statuses = get_provider_status_map(connection, SOURCE_POKEMON_PRICE_TRACKER)
        cards, eligible_count = select_products(
            universe,
            product_ids=product_ids,
            min_raw_usd=min_raw_usd,
            all_cards=all_cards,
            statuses=statuses,
            requested_days=days,
            refresh=refresh,
            limit=limit,
            era=era,
        )

        print("\n" + "=" * 72)
        print("POKEMONPRICETRACKER PRODUCTION BACKFILL")
        print("=" * 72)
        print(f"Canonical products: {len(universe):,}")
        print(f"Primary-printing products: {primary_products:,}")
        if product_ids:
            print(f"Selection: {len(product_ids):,} explicit TCGPlayer IDs")
        elif all_cards:
            print("Selection: all canonical products")
        else:
            print(f"Selection: latest canonical raw >= ${min_raw_usd:,.2f}")
        if era is not None:
            print(f"Era filter: {era}")
        print(f"Eligible before resume filter: {eligible_count:,}")
        print(f"Products to process now: {len(cards):,}")
        print(f"History requested: {days} days / max {max_data_points} points")
        print("Markets: RAW, PSA_9, PSA_10, ACE_10")
        print(f"Request interval: {request_interval:.2f}s")
        print(f"Estimated credits: about {len(cards) * 3:,}")
        print(f"Resume mode: {'refresh all selected' if refresh else 'skip completed'}")
        if dry_run:
            print("Mode: DRY RUN - no PokemonPriceTracker requests or provider-history writes")
        print("=" * 72)

        if dry_run:
            print(
                "Dry run complete. The primary-printing derived table was refreshed "
                "so the eligibility estimate includes newly added raw cards."
            )
            return

        if not cards:
            print("Nothing to do. Selected products are already complete.")
            _print_database_summary(connection)
            return

        client = PokemonPriceTrackerClient()
        pacer = RequestPacer(request_interval)

        started_at = time.monotonic()
        stats = {
            "attempted": 0,
            "completed": 0,
            "no_history": 0,
            "failed": 0,
            "observations": 0,
            "credits": 0,
        }

        try:
            for index, card in enumerate(cards, start=1):
                stats["attempted"] += 1
                number = f" #{card.card_number}" if card.card_number else ""
                print(
                    f"\n[{index}/{len(cards)}] {card.name}{number} | "
                    f"{card.set_name} | TCGPlayer {card.product_id}"
                )

                try:
                    payload, metadata = fetch_with_retry(
                        client,
                        product_id=int(card.product_id),
                        days=days,
                        max_data_points=max_data_points,
                        pacer=pacer,
                        retry_wait=retry_wait,
                        max_attempts=max_attempts,
                    )
                    consumed = metadata.get("apiCallsConsumed") or {}
                    if isinstance(consumed, dict):
                        stats["credits"] += int(consumed.get("total") or 0)
                    elif isinstance(consumed, (int, float)):
                        stats["credits"] += int(consumed)

                    if payload is None:
                        raise RuntimeError("No card returned for exact TCGPlayer ID.")

                    returned_id = tcgplayer_id_from_card(payload)
                    if returned_id not in (None, int(card.product_id)):
                        raise RuntimeError(
                            f"TCGPlayer ID mismatch: requested {card.product_id}, "
                            f"received {returned_id}."
                        )

                    history_rows = build_provider_history_rows(
                        int(card.product_id),
                        payload,
                        preferred_printing=card.printing,
                    )
                    snapshot_rows = build_provider_snapshot_rows(payload)

                    stored = store_provider_card_history(
                        connection,
                        product_id=int(card.product_id),
                        source=SOURCE_POKEMON_PRICE_TRACKER,
                        requested_days=days,
                        history_rows=history_rows,
                        snapshot_rows=snapshot_rows,
                        snapshot_on=date.today(),
                    )
                    connection.commit()

                    stats["observations"] += stored
                    if stored:
                        stats["completed"] += 1
                    else:
                        stats["no_history"] += 1

                    counts = {
                        market: sum(
                            row["market_type"] == market for row in history_rows
                        )
                        for market in ("RAW", "PSA_9", "PSA_10", "ACE_10")
                    }
                    print(
                        "  Stored: "
                        f"RAW={counts['RAW']} | PSA9={counts['PSA_9']} | "
                        f"PSA10={counts['PSA_10']} | ACE10={counts['ACE_10']}"
                    )

                except Exception as error:
                    connection.rollback()
                    mark_provider_failed(
                        connection,
                        product_id=int(card.product_id),
                        source=SOURCE_POKEMON_PRICE_TRACKER,
                        requested_days=days,
                        error=str(error),
                    )
                    connection.commit()
                    stats["failed"] += 1
                    print(f"  FAILED: {error}")

        except KeyboardInterrupt:
            print(
                "\nInterrupted. Completed cards are already committed and "
                "will be skipped next run."
            )

        elapsed = time.monotonic() - started_at
        average = elapsed / stats["attempted"] if stats["attempted"] else 0.0
        print("\n" + "=" * 72)
        print("BACKFILL RUN COMPLETE")
        print("=" * 72)
        print(f"Attempted: {stats['attempted']:,}")
        print(f"Completed with history: {stats['completed']:,}")
        print(f"Completed with no history: {stats['no_history']:,}")
        print(f"Failed: {stats['failed']:,}")
        print(f"Observations stored/upserted: {stats['observations']:,}")
        print(f"Provider credits reported: {stats['credits']:,}")
        print(f"Time: {_format_seconds(elapsed)} | Average: {average:.2f}s/card")
        _print_database_summary(connection)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--max-data-points", type=int, default=DEFAULT_MAX_DATA_POINTS)
    parser.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    parser.add_argument("--retry-wait", type=float, default=DEFAULT_RETRY_WAIT)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument(
        "--min-raw-usd",
        type=float,
        default=DEFAULT_MIN_RAW_USD,
        help=(
            "Default selection floor using the canonical latest raw price. "
            "Set to 0 or use --all-cards for a wider universe."
        ),
    )
    parser.add_argument(
        "--all-cards",
        action="store_true",
        help="Ignore the raw-price floor and process every canonical product.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch selected cards even if this history window already completed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the selected products and estimated credits without provider requests.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--era",
        choices=TRACKED_ERA_LABELS,
        help="Only backfill cards belonging to one tracked English Pokemon era.",
    )
    parser.add_argument(
        "--product-id",
        action="append",
        help="Exact TCGPlayer product ID. Repeat or pass comma-separated IDs.",
    )
    args = parser.parse_args()

    run_backfill(
        days=max(1, int(args.days)),
        max_data_points=max(1, int(args.max_data_points)),
        request_interval=max(0.0, float(args.request_interval)),
        retry_wait=max(1.0, float(args.retry_wait)),
        max_attempts=max(1, int(args.max_attempts)),
        min_raw_usd=max(0.0, float(args.min_raw_usd)),
        all_cards=bool(args.all_cards),
        refresh=bool(args.refresh),
        limit=args.limit,
        product_ids=_parse_ids(args.product_id),
        era=args.era,
        dry_run=bool(args.dry_run),
    )


if __name__ == "__main__":
    main()
