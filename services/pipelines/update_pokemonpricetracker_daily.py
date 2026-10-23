"""Append new PokemonPriceTracker history and save one current daily snapshot."""

from __future__ import annotations

import argparse
import os
import time
from datetime import date

from database.canonical_cards import CanonicalCard, get_canonical_card_universe
from database.market_analytics import initialise_market_analytics_schema
from database.provider_history import (
    SOURCE_POKEMON_PRICE_TRACKER,
    connect,
    get_provider_daily_refresh_dates,
    get_provider_latest_history_dates,
    get_provider_status_map,
    initialise_provider_history_schema,
    mark_provider_daily_refresh,
    store_provider_card_history,
)
from database.tcgplayer import initialise_tcgplayer_schema
from services.pipelines.backfill_pokemonpricetracker import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_REQUEST_INTERVAL,
    DEFAULT_RETRY_WAIT,
    RequestPacer,
    _format_seconds,
    _parse_ids,
    build_provider_history_rows,
    build_provider_snapshot_rows,
    fetch_with_retry,
)
from sources.pokemonpricetracker.client import PokemonPriceTrackerClient, tcgplayer_id_from_card


DEFAULT_DAYS = 14
DEFAULT_MAX_DATA_POINTS = 14
ESTIMATED_CREDITS_PER_PRODUCT = 3


def _default_credit_budget() -> int:
    raw = os.getenv("POKEMON_PRICE_TRACKER_DAILY_CREDIT_BUDGET", "0").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def filter_new_history_rows(
    history_rows: list[dict],
    latest_dates: dict[tuple[int, str, str], date],
) -> tuple[list[dict], int]:
    """Keep only observations newer than the stored provider series."""
    new_rows: list[dict] = []
    already_stored = 0

    rows = sorted(
        history_rows,
        key=lambda row: (
            int(row["product_id"]),
            str(row["market_type"]),
            str(row.get("series_label") or ""),
            row["observed_on"],
        ),
    )

    for row in rows:
        key = (
            int(row["product_id"]),
            str(row["market_type"]),
            str(row.get("series_label") or ""),
        )
        observed_on = row["observed_on"]
        latest = latest_dates.get(key)

        if latest is not None and observed_on <= latest:
            already_stored += 1
            continue

        new_rows.append(row)
        latest_dates[key] = observed_on

    return new_rows, already_stored


def select_daily_products(
    universe: list[CanonicalCard],
    *,
    statuses: dict[int, dict],
    latest_refreshes: dict[int, date],
    product_ids: list[int],
    force: bool,
    today: date,
    limit: int | None,
) -> tuple[list[CanonicalCard], int, int]:
    """Select cards already enrolled by the historical provider backfill."""
    by_id = {int(card.product_id): card for card in universe}

    if product_ids:
        missing = [product_id for product_id in product_ids if product_id not in by_id]
        if missing:
            raise ValueError(
                "Unknown local TCGPlayer product IDs: "
                + ", ".join(str(value) for value in missing)
            )
        eligible = [by_id[product_id] for product_id in product_ids]
    else:
        enrolled = set(int(value) for value in statuses)
        eligible = [card for card in universe if int(card.product_id) in enrolled]

    eligible.sort(
        key=lambda card: (float(card.latest_raw_usd or 0.0), int(card.product_id)),
        reverse=True,
    )
    enrolled_count = len(eligible)

    if not force:
        eligible = [
            card
            for card in eligible
            if latest_refreshes.get(int(card.product_id)) != today
        ]

    already_current = enrolled_count - len(eligible)

    if limit is not None:
        eligible = eligible[: max(0, int(limit))]

    return eligible, enrolled_count, already_current


def apply_credit_budget(
    cards: list[CanonicalCard],
    *,
    credit_budget: int,
) -> tuple[list[CanonicalCard], int]:
    """Limit work before requests begin so a scheduled run has a hard budget."""
    if credit_budget <= 0:
        return cards, 0

    allowed_products = credit_budget // ESTIMATED_CREDITS_PER_PRODUCT
    limited = cards[:allowed_products]
    deferred = max(0, len(cards) - len(limited))
    return limited, deferred


def run_daily_update(
    *,
    days: int,
    max_data_points: int,
    request_interval: float,
    retry_wait: float,
    max_attempts: int,
    credit_budget: int,
    limit: int | None,
    product_ids: list[int],
    force: bool,
    dry_run: bool,
) -> dict:
    initialise_tcgplayer_schema()
    initialise_market_analytics_schema()
    initialise_provider_history_schema()

    universe = get_canonical_card_universe()
    today = date.today()

    with connect() as connection:
        statuses = get_provider_status_map(connection, SOURCE_POKEMON_PRICE_TRACKER)
        latest_refreshes = get_provider_daily_refresh_dates(
            connection,
            SOURCE_POKEMON_PRICE_TRACKER,
        )
        cards, enrolled_count, already_current = select_daily_products(
            universe,
            statuses=statuses,
            latest_refreshes=latest_refreshes,
            product_ids=product_ids,
            force=force,
            today=today,
            limit=limit,
        )
        cards, budget_deferred = apply_credit_budget(
            cards,
            credit_budget=credit_budget,
        )

        print()
        print("POKEMONPRICETRACKER DAILY UPDATE")
        print("=" * 72)
        print(f"Canonical products: {len(universe):,}")
        print(f"Backfill-enrolled products: {enrolled_count:,}")
        print(f"Already refreshed today: {already_current:,}")
        print(f"Products to request: {len(cards):,}")
        print(f"History overlap window: {days} days / max {max_data_points} points")
        print("Markets: RAW, PSA_9, PSA_10, ACE_10")
        print(f"Estimated credits: about {len(cards) * ESTIMATED_CREDITS_PER_PRODUCT:,}")
        if credit_budget > 0:
            print(f"Credit budget: {credit_budget:,}")
            print(f"Deferred by budget: {budget_deferred:,}")
        print("Mode: append new history only + one current daily market snapshot")
        if force:
            print("Force: refresh even when today's provider refresh already succeeded")
        if dry_run:
            print("DRY RUN: no provider requests and no market-data writes")
            return {
                "requested_products": 0,
                "planned_products": len(cards),
                "new_history": 0,
                "already_stored": 0,
                "snapshots": 0,
                "failed": 0,
                "credits": 0,
                "budget_deferred": budget_deferred,
            }

        if not cards:
            print("Nothing to do. All selected products are current or outside the budget.")
            return {
                "requested_products": 0,
                "planned_products": 0,
                "new_history": 0,
                "already_stored": 0,
                "snapshots": 0,
                "failed": 0,
                "credits": 0,
                "budget_deferred": budget_deferred,
            }

        latest_dates = get_provider_latest_history_dates(
            connection,
            SOURCE_POKEMON_PRICE_TRACKER,
            product_ids=[int(card.product_id) for card in cards],
        )

        client = PokemonPriceTrackerClient()
        pacer = RequestPacer(request_interval)
        started = time.monotonic()
        stats = {
            "requested_products": 0,
            "planned_products": len(cards),
            "new_history": 0,
            "already_stored": 0,
            "snapshots": 0,
            "failed": 0,
            "credits": 0,
            "budget_deferred": budget_deferred,
        }

        for index, card in enumerate(cards, start=1):
            stats["requested_products"] += 1
            number = f" #{card.card_number}" if card.card_number else ""
            label = f"{card.name}{number} | {card.set_name} | TCGPlayer {card.product_id}"

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
                new_rows, already_stored = filter_new_history_rows(
                    history_rows,
                    latest_dates,
                )
                snapshot_rows = build_provider_snapshot_rows(payload)

                stored = store_provider_card_history(
                    connection,
                    product_id=int(card.product_id),
                    source=SOURCE_POKEMON_PRICE_TRACKER,
                    requested_days=days,
                    history_rows=new_rows,
                    snapshot_rows=snapshot_rows,
                    snapshot_on=today,
                    update_backfill_status=False,
                )
                mark_provider_daily_refresh(
                    connection,
                    product_id=int(card.product_id),
                    source=SOURCE_POKEMON_PRICE_TRACKER,
                    success=True,
                    refresh_on=today,
                )
                connection.commit()

                stats["new_history"] += stored
                stats["already_stored"] += already_stored
                if snapshot_rows:
                    stats["snapshots"] += 1

                print(
                    f"[{index:>3}/{len(cards):<3}] {label} | "
                    f"new {stored:,} | overlap {already_stored:,} | "
                    f"snapshot {'yes' if snapshot_rows else 'no'}"
                )

            except Exception as error:
                connection.rollback()
                mark_provider_daily_refresh(
                    connection,
                    product_id=int(card.product_id),
                    source=SOURCE_POKEMON_PRICE_TRACKER,
                    success=False,
                    refresh_on=today,
                    error=str(error),
                )
                connection.commit()
                stats["failed"] += 1
                print(f"[{index:>3}/{len(cards):<3}] {label} | FAILED: {error}")

        elapsed = time.monotonic() - started
        print()
        print("=" * 72)
        print("GRADED DAILY UPDATE COMPLETE")
        print("=" * 72)
        print(f"Products requested: {stats['requested_products']:,}")
        print(f"New history observations: {stats['new_history']:,}")
        print(f"Overlapping history skipped: {stats['already_stored']:,}")
        print(f"Current snapshots stored: {stats['snapshots']:,}")
        print(f"Failed products: {stats['failed']:,}")
        print(f"Provider credits reported: {stats['credits']:,}")
        print(f"Deferred by credit budget: {stats['budget_deferred']:,}")
        print(f"Runtime: {_format_seconds(elapsed)}")
        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--max-data-points", type=int, default=DEFAULT_MAX_DATA_POINTS)
    parser.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    parser.add_argument("--retry-wait", type=float, default=DEFAULT_RETRY_WAIT)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument(
        "--credit-budget",
        type=int,
        default=_default_credit_budget(),
        help=(
            "Maximum estimated provider credits for this run. "
            "0 means unlimited. Each product is budgeted at about 3 credits."
        ),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--product-id",
        action="append",
        help="Exact TCGPlayer product ID. Repeat or pass comma-separated IDs.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = run_daily_update(
        days=max(1, int(args.days)),
        max_data_points=max(1, int(args.max_data_points)),
        request_interval=max(0.0, float(args.request_interval)),
        retry_wait=max(1.0, float(args.retry_wait)),
        max_attempts=max(1, int(args.max_attempts)),
        credit_budget=max(0, int(args.credit_budget)),
        limit=args.limit,
        product_ids=_parse_ids(args.product_id),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )

    if stats["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
