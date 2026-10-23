"""Run the incremental market-data pipeline used by the scheduled daily job."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
from datetime import date

from database.market_analytics import (
    connect as market_connect,
    rebuild_all_market_analytics,
    rebuild_primary_printings,
)
from database.opportunity_scanner import history_summary, rebuild_raw_opportunity_history
from database.provider_history import (
    SOURCE_POKEMON_PRICE_TRACKER,
    connect as provider_connect,
    get_provider_daily_refresh_dates,
    get_provider_status_map,
)
from database.raw_research import rebuild_raw_relative_momentum_90d
from database.tcgplayer import connect as tcg_connect, get_daily_update_groups
from database.update_pipeline import (
    finish_pipeline_run,
    initialise_update_pipeline_schema,
    mark_component_finished,
    mark_component_skipped,
    mark_component_started,
    start_pipeline_run,
)
from services.pipelines.backfill_pokemonpricetracker import DEFAULT_MIN_RAW_USD
from services.pipelines.update_pokemonpricetracker_daily import (
    ESTIMATED_CREDITS_PER_PRODUCT,
    _default_credit_budget,
    run_daily_update as run_graded_daily_update,
    select_daily_products,
)
from services.pipelines.update_tcgplayer_daily_prices import run_update as run_raw_daily_update
from database.canonical_cards import get_canonical_card_universe
from services.pipelines.refresh_opportunity_snapshot import refresh_snapshot


LOGGER = logging.getLogger("tcg_daily_update")
LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "daily_update.log"


def configure_logging() -> None:
    if LOGGER.handlers:
        return

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    LOGGER.addHandler(stream)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def _run_research_refresh() -> dict:
    market_result = rebuild_all_market_analytics()
    rebuild_raw_relative_momentum_90d()
    history_rows = rebuild_raw_opportunity_history()
    summary = history_summary()
    return {
        "primary_products": int(market_result["primary_products"]),
        "index_rows": int(market_result["index_rows"]),
        "history_rows": int(history_rows),
        "products": int(summary["products"]),
        "first_date": summary["first_date"],
        "last_date": summary["last_date"],
    }


def _dry_run_plan(
    *,
    force: bool,
    graded_credit_budget: int,
    graded_limit: int | None,
) -> dict:
    with tcg_connect() as connection:
        raw_groups = get_daily_update_groups(connection)

    universe = get_canonical_card_universe()
    today = date.today()
    with provider_connect() as connection:
        statuses = get_provider_status_map(connection, SOURCE_POKEMON_PRICE_TRACKER)
        refreshes = get_provider_daily_refresh_dates(
            connection,
            SOURCE_POKEMON_PRICE_TRACKER,
        )

    cards, enrolled_count, already_current = select_daily_products(
        universe,
        statuses=statuses,
        latest_refreshes=refreshes,
        product_ids=[],
        force=force,
        today=today,
        limit=graded_limit,
    )
    enrolled_ids = {int(product_id) for product_id in statuses}
    unenrolled_candidates = sum(
        1
        for card in universe
        if card.latest_raw_usd is not None
        and float(card.latest_raw_usd) >= float(DEFAULT_MIN_RAW_USD)
        and int(card.product_id) not in enrolled_ids
    )

    if graded_credit_budget > 0:
        allowed = graded_credit_budget // ESTIMATED_CREDITS_PER_PRODUCT
        planned_graded = min(len(cards), allowed)
    else:
        planned_graded = len(cards)

    return {
        "raw_groups": len(raw_groups),
        "graded_enrolled": enrolled_count,
        "graded_already_current": already_current,
        "graded_unenrolled_candidates": unenrolled_candidates,
        "graded_planned": planned_graded,
        "graded_estimated_credits": planned_graded * ESTIMATED_CREDITS_PER_PRODUCT,
    }


def run_pipeline(
    *,
    dry_run: bool,
    force: bool,
    skip_catalog: bool,
    skip_graded: bool,
    skip_research: bool,
    raw_delay: float,
    raw_limit_groups: int | None,
    graded_days: int,
    graded_max_data_points: int,
    graded_request_interval: float,
    graded_retry_wait: float,
    graded_max_attempts: int,
    graded_credit_budget: int,
    graded_limit: int | None,
) -> dict:
    configure_logging()

    if dry_run:
        plan = _dry_run_plan(
            force=force,
            graded_credit_budget=graded_credit_budget,
            graded_limit=graded_limit,
        )
        print()
        print("TCG CARD TRACKER DAILY UPDATE - DRY RUN")
        print("=" * 72)
        print(f"RAW TickerMint groups: {plan['raw_groups']:,}")
        print(f"Graded backfill-enrolled products: {plan['graded_enrolled']:,}")
        print(f"Graded already current today: {plan['graded_already_current']:,}")
        print(
            f"Priced >= ${DEFAULT_MIN_RAW_USD:.0f} but not graded-enrolled: "
            f"{plan['graded_unenrolled_candidates']:,}"
        )
        print(f"Graded products planned: {plan['graded_planned']:,}")
        print(f"Estimated graded credits: {plan['graded_estimated_credits']:,}")
        if graded_credit_budget > 0:
            print(f"Configured graded credit budget: {graded_credit_budget:,}")
        if force:
            print("Research: would rebuild even if RAW has no new observations (--force)")
        else:
            print("Research: would rebuild only if RAW inserts new observations")
        print("No provider requests or market-data writes were made.")
        return {"status": "dry_run", "plan": plan, "failures": []}

    initialise_update_pipeline_schema()
    run_id = start_pipeline_run(
        pipeline="daily_update",
        dry_run=False,
        force_run=force,
        details="Incremental RAW + graded + conditional research refresh",
    )

    failures: list[str] = []
    raw_stats: dict | None = None
    graded_stats: dict | None = None
    research_stats: dict | None = None

    LOGGER.info("Daily update run %s started", run_id)

    mark_component_started("raw", details="TickerMint append-only daily prices")
    try:
        raw_stats = run_raw_daily_update(
            delay=raw_delay,
            skip_catalog=skip_catalog,
            group_id=None,
            limit_groups=raw_limit_groups,
        )
        raw_success = int(raw_stats.get("failed_groups", 0)) == 0
        primary_products = None
        if raw_success:
            # Promote newly collected catalogue/history rows before the graded
            # updater asks for the canonical universe. This keeps new eras and
            # newly released cards from being one pipeline run behind.
            with market_connect() as market_connection:
                primary_products = rebuild_primary_printings(market_connection)
        raw_details = (
            f"inserted={int(raw_stats.get('inserted', 0))}; "
            f"requests={int(raw_stats.get('requests', 0))}; "
            f"failed_groups={int(raw_stats.get('failed_groups', 0))}; "
            f"primary_products={primary_products if primary_products is not None else 'not rebuilt'}"
        )
        mark_component_finished(
            "raw",
            success=raw_success,
            details=raw_details,
            error=None if raw_success else raw_details,
        )
        if not raw_success:
            failures.append("raw")
        LOGGER.info("RAW step finished: %s", raw_details)
    except Exception as error:
        message = str(error)
        mark_component_finished("raw", success=False, error=message)
        failures.append("raw")
        LOGGER.exception("RAW step failed")

    if skip_graded:
        mark_component_skipped("graded", details="Skipped by --skip-graded")
        LOGGER.info("Graded step skipped")
    else:
        mark_component_started(
            "graded",
            details="PokemonPriceTracker incremental history + daily snapshot",
        )
        try:
            graded_stats = run_graded_daily_update(
                days=graded_days,
                max_data_points=graded_max_data_points,
                request_interval=graded_request_interval,
                retry_wait=graded_retry_wait,
                max_attempts=graded_max_attempts,
                credit_budget=graded_credit_budget,
                limit=graded_limit,
                product_ids=[],
                force=force,
                dry_run=False,
            )
            graded_success = int(graded_stats.get("failed", 0)) == 0
            graded_details = (
                f"requested={int(graded_stats.get('requested_products', 0))}; "
                f"new_history={int(graded_stats.get('new_history', 0))}; "
                f"snapshots={int(graded_stats.get('snapshots', 0))}; "
                f"credits={int(graded_stats.get('credits', 0))}; "
                f"failed={int(graded_stats.get('failed', 0))}"
            )
            mark_component_finished(
                "graded",
                success=graded_success,
                details=graded_details,
                error=None if graded_success else graded_details,
            )
            if not graded_success:
                failures.append("graded")
            LOGGER.info("Graded step finished: %s", graded_details)
        except Exception as error:
            message = str(error)
            mark_component_finished("graded", success=False, error=message)
            failures.append("graded")
            LOGGER.exception("Graded step failed")

    raw_changed = bool(raw_stats and int(raw_stats.get("inserted", 0)) > 0)
    raw_complete = bool(raw_stats and int(raw_stats.get("failed_groups", 0)) == 0)

    if skip_research:
        mark_component_skipped("research", details="Skipped by --skip-research")
        LOGGER.info("Research step skipped by flag")
    elif not raw_complete:
        reason = "Skipped because RAW refresh did not complete cleanly"
        mark_component_skipped("research", details=reason)
        LOGGER.warning(reason)
    elif not raw_changed and not force:
        reason = "Skipped because RAW inserted no new observations"
        mark_component_skipped("research", details=reason)
        LOGGER.info(reason)
    else:
        mark_component_started(
            "research",
            details="Market indices + raw momentum + opportunity history",
        )
        try:
            research_stats = _run_research_refresh()
            research_details = (
                f"index_rows={research_stats['index_rows']}; "
                f"history_rows={research_stats['history_rows']}; "
                f"products={research_stats['products']}"
            )
            mark_component_finished(
                "research",
                success=True,
                details=research_details,
            )
            LOGGER.info("Research step finished: %s", research_details)
        except Exception as error:
            message = str(error)
            mark_component_finished("research", success=False, error=message)
            failures.append("research")
            LOGGER.exception("Research step failed")

    snapshot_stats = None
    if raw_complete and "raw" not in failures and not skip_research and "research" not in failures:
        mark_component_started("opportunity_snapshot", details="Saved full-universe rankings for dashboard startup")
        try:
            snapshot_stats = refresh_snapshot(only_if_needed=True)
            mark_component_finished("opportunity_snapshot", success=True,
                                    details=f"rows={snapshot_stats['cards']}; built={snapshot_stats['built_at']}")
            LOGGER.info("Saved opportunity rankings ready: %s cards", snapshot_stats['cards'])
        except Exception as error:
            failures.append("opportunity_snapshot")
            mark_component_finished("opportunity_snapshot", success=False, error=str(error))
            LOGGER.exception("Snapshot build failed; older saved results were kept")
    else:
        mark_component_skipped("opportunity_snapshot", details="Research skipped, incomplete, or RAW failed")

    final_status = "success" if not failures else "failed"
    details = (
        f"raw_inserted={int((raw_stats or {}).get('inserted', 0))}; "
        f"graded_new={int((graded_stats or {}).get('new_history', 0))}; "
        f"failures={','.join(failures) if failures else 'none'}"
    )
    finish_pipeline_run(
        run_id,
        status=final_status,
        details=details,
        error=None if not failures else ", ".join(failures),
    )
    LOGGER.info("Daily update run %s finished: %s", run_id, final_status)

    print()
    print("=" * 72)
    print("DAILY PIPELINE COMPLETE")
    print("=" * 72)
    print(f"Status: {final_status.upper()}")
    print(f"RAW new observations: {int((raw_stats or {}).get('inserted', 0)):,}")
    print(f"Graded new history: {int((graded_stats or {}).get('new_history', 0)):,}")
    if graded_stats is not None:
        print(f"Graded credits reported: {int(graded_stats.get('credits', 0)):,}")
    print(f"Research rebuilt: {'yes' if research_stats is not None else 'no'}")
    print(f"Saved opportunity scan: {'ready' if snapshot_stats is not None else 'not refreshed'}")
    print(f"Log: {LOG_PATH}")
    if failures:
        print("Failed components: " + ", ".join(failures))

    return {
        "status": final_status,
        "run_id": run_id,
        "raw": raw_stats,
        "graded": graded_stats,
        "research": research_stats,
        "opportunity_snapshot": snapshot_stats,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-catalog", action="store_true")
    parser.add_argument("--skip-graded", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--raw-delay", type=float, default=0.65)
    parser.add_argument("--raw-limit-groups", type=int)
    parser.add_argument("--graded-days", type=int, default=14)
    parser.add_argument("--graded-max-data-points", type=int, default=14)
    parser.add_argument("--graded-request-interval", type=float, default=1.10)
    parser.add_argument("--graded-retry-wait", type=float, default=65.0)
    parser.add_argument("--graded-max-attempts", type=int, default=4)
    parser.add_argument(
        "--graded-credit-budget",
        type=int,
        default=_default_credit_budget(),
        help="0 means unlimited; otherwise this caps estimated daily provider credits.",
    )
    parser.add_argument("--graded-limit", type=int)
    args = parser.parse_args()

    if args.raw_delay < 0.5:
        raise SystemExit("Refusing --raw-delay below 0.5s on the free API.")

    result = run_pipeline(
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        skip_catalog=bool(args.skip_catalog),
        skip_graded=bool(args.skip_graded),
        skip_research=bool(args.skip_research),
        raw_delay=float(args.raw_delay),
        raw_limit_groups=args.raw_limit_groups,
        graded_days=max(1, int(args.graded_days)),
        graded_max_data_points=max(1, int(args.graded_max_data_points)),
        graded_request_interval=max(0.0, float(args.graded_request_interval)),
        graded_retry_wait=max(1.0, float(args.graded_retry_wait)),
        graded_max_attempts=max(1, int(args.graded_max_attempts)),
        graded_credit_budget=max(0, int(args.graded_credit_budget)),
        graded_limit=args.graded_limit,
    )

    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
