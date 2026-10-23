"""Build the saved opportunity rankings once, outside dashboard startup.

Uses only data already in PostgreSQL. No provider requests or paid credits.
Run: python -m services.pipelines.refresh_opportunity_snapshot
"""
from __future__ import annotations

import argparse
import time

from services.analysis.opportunity_snapshot import (
    CACHE_DIR, input_lock, load_snapshot, revision, save_snapshot,
)


def refresh_snapshot(*, only_if_needed: bool = False) -> dict:
    with input_lock(blocking=False):
        return _refresh_locked(only_if_needed=only_if_needed)


def _refresh_locked(*, only_if_needed: bool = False) -> dict:
    from database.opportunity_scanner import (
        DEFAULT_PRICE_FLOOR_USD, history_summary, scan_raw_opportunities,
    )
    from services.analysis.research_rank import LOCKED_ANALOGS
    from services.analysis.opportunity_model import DEFAULT_CONTEXT_WEIGHT

    current = load_snapshot()
    if only_if_needed and current.status == "ready":
        return current.metadata
    # Prevent two background jobs/manual builds from doing the same heavy work.
    # flock is released by the OS even if a process dies (the app targets macOS).
    import fcntl
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with (CACHE_DIR / "opportunities.build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("An opportunity snapshot build is already running.") from error
        started = time.perf_counter()
        before = revision()
        summary = history_summary()
        if not summary.get("rows") or not summary.get("context_ready"):
            raise RuntimeError("Research inputs are missing/incomplete. Run "
                               "python -m services.pipelines.build_raw_opportunity_research --rebuild-base first.")
        frame = scan_raw_opportunities(price_floor_usd=DEFAULT_PRICE_FLOOR_USD,
                                       max_analogs=LOCKED_ANALOGS)
        if frame.empty:
            raise RuntimeError("Scanner returned no cards. Previous snapshot was not overwritten.")
        built = save_snapshot(frame, source_revision=before, metadata={
            "history_summary": summary, "price_floor_usd": DEFAULT_PRICE_FLOOR_USD,
            "max_analogs": LOCKED_ANALOGS, "context_weight": DEFAULT_CONTEXT_WEIGHT,
            "build_seconds": time.perf_counter() - started,
            "universe": "Frozen primary-printing universe; not every finish",
            "latest_raw_date": str(frame["analysis_date"].max()) if "analysis_date" in frame else None,
        })
        return built.metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--if-needed", action="store_true", help="Keep a fresh matching snapshot.")
    args = parser.parse_args()
    try:
        result = refresh_snapshot(only_if_needed=args.if_needed)
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
    print(f"Saved {result['cards']:,} opportunity rows; built {result['built_at']}.")
    print(f"Build time: {result['build_seconds']:.2f}s. Dashboard startup reads this file instead of rescanning.")


if __name__ == "__main__":
    main()
