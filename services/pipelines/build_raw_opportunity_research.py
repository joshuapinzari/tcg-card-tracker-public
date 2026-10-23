from __future__ import annotations

import argparse
import time

from database.market_analytics import (
    rebuild_all_market_analytics,
)
from database.opportunity_scanner import (
    history_summary,
    rebuild_raw_opportunity_history,
)
from database.raw_research import (
    connect,
    initialise_raw_research_schema,
    rebuild_raw_relative_momentum_90d,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the historical raw-card opportunity panel used by the scanner."
    )
    parser.add_argument(
        "--rebuild-base",
        action="store_true",
        help="Rebuild the underlying point-in-time 90D momentum panel first.",
    )
    return parser.parse_args()


def _base_rows() -> int:
    initialise_raw_research_schema()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM raw_relative_momentum_90d;")
            return int(cursor.fetchone()[0])


def main() -> None:
    args = parse_args()
    started = time.time()

    base_rows = _base_rows()
    if args.rebuild_base or base_rows == 0:
        reason = "requested" if args.rebuild_base else "base panel is empty"
        print(f"Refreshing set / era / market indices ({reason})...", flush=True)
        market_result = rebuild_all_market_analytics()
        print(
            f"Market analytics ready: {market_result['primary_products']:,} primary products · "
            f"{market_result['index_rows']:,} index rows",
            flush=True,
        )
        print(f"Rebuilding base point-in-time 90D research panel ({reason})...", flush=True)
        rebuild_raw_relative_momentum_90d()

    print("Building raw opportunity history...", flush=True)
    rows = rebuild_raw_opportunity_history()
    summary = history_summary()

    print()
    print("=" * 78)
    print("RAW OPPORTUNITY RESEARCH READY")
    print("=" * 78)
    print(f"Historical states: {rows:,}")
    print(f"Products represented: {summary['products']:,}")
    print(f"Formation range: {summary['first_date']} -> {summary['last_date']}")
    print(f"Time: {time.time() - started:.1f}s")
    print()
    print("Building the saved dashboard scan from this research...")
    from services.pipelines.refresh_opportunity_snapshot import refresh_snapshot
    result = refresh_snapshot()
    print(f"Saved {result['cards']:,} rankings. Opening the dashboard will not rescan them.")


if __name__ == "__main__":
    main()
