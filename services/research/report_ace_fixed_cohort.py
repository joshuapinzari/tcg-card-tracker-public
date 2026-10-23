"""Report longitudinal ACE 10 strength using a fixed card cohort."""

from __future__ import annotations

from database.provider_history import (
    SOURCE_POKEMON_PRICE_TRACKER,
    get_provider_fixed_cohort_analysis,
    initialise_provider_history_schema,
)


def _ratio(value) -> str:
    return "-" if value is None else f"{float(value):.3f}x"


def _pct(value) -> str:
    return "-" if value is None else f"{float(value) * 100:+.1f}%"


def _share(part: int, total: int) -> str:
    if not total:
        return "-"
    return f"{100.0 * part / total:.1f}%"


def main() -> None:
    initialise_provider_history_schema()
    report = get_provider_fixed_cohort_analysis(
        source=SOURCE_POKEMON_PRICE_TRACKER
    )

    months = report["months"]
    monthly = report["monthly"]
    cohort_size = report["cohort_size"]

    print("\n" + "=" * 118)
    print("ACE 10 FIXED-COHORT LONGITUDINAL STUDY")
    print("=" * 118)

    if not months or not monthly:
        print("Not enough complete monthly provider history to build a fixed cohort.")
        return

    print(
        f"Complete months: {months[0]} -> {months[-1]} | "
        f"same cards in every month: {cohort_size:,}"
    )
    print(
        "Boundary months are excluded automatically. A cohort card must have "
        "RAW, PSA 10 and ACE 10 prices in every included month."
    )
    print()
    print(
        f"{'Month':<12} {'PSA/RAW':>9} {'ACE/RAW':>9} {'ACE/PSA':>9} "
        f"{'ACE sales':>10} {'sales/card':>11} {'up':>8} {'down':>8}"
    )
    print("-" * 118)

    for row in monthly:
        (
            month,
            cards,
            psa_raw_median,
            ace_raw_median,
            ace_psa_median,
            _ace_psa_mean,
            ace_sales,
            avg_sales,
            _median_sales,
            cards_up,
            cards_down,
            cards_flat,
        ) = row
        comparable = int(cards_up or 0) + int(cards_down or 0) + int(cards_flat or 0)
        print(
            f"{str(month):<12} {_ratio(psa_raw_median):>9} "
            f"{_ratio(ace_raw_median):>9} {_ratio(ace_psa_median):>9} "
            f"{int(ace_sales or 0):>10,} {float(avg_sales or 0):>11.2f} "
            f"{_share(int(cards_up or 0), comparable):>8} "
            f"{_share(int(cards_down or 0), comparable):>8}"
        )

    start_to_end = report["start_to_end"]
    if start_to_end:
        cards, start_med, end_med, median_change, up, down, flat = start_to_end
        total = int(up or 0) + int(down or 0) + int(flat or 0)
        print("\n" + "-" * 118)
        print("FIXED-COHORT START -> END")
        print(
            f"ACE10/PSA10 median: {_ratio(start_med)} -> {_ratio(end_med)} | "
            f"median per-card change: {_pct(median_change)}"
        )
        print(
            f"Cards improving: {int(up or 0):,}/{int(cards or 0):,} "
            f"({_share(int(up or 0), total)}) | "
            f"declining: {int(down or 0):,}/{int(cards or 0):,} "
            f"({_share(int(down or 0), total)})"
        )

    print("\n" + "-" * 118)
    print("BY ACE LIQUIDITY (average monthly ACE 10 sales across the same months)")
    print(
        f"{'Band':<14} {'cards':>7} {'avg sales/mo':>13} {'start':>9} "
        f"{'end':>9} {'median chg':>12} {'up':>8} {'down':>8}"
    )
    for row in report["liquidity_bands"]:
        band, cards, avg_sales, start_med, end_med, median_change, up, down, flat = row
        total = int(up or 0) + int(down or 0) + int(flat or 0)
        print(
            f"{band:<14} {int(cards):>7,} {float(avg_sales or 0):>13.2f} "
            f"{_ratio(start_med):>9} {_ratio(end_med):>9} {_pct(median_change):>12} "
            f"{_share(int(up or 0), total):>8} {_share(int(down or 0), total):>8}"
        )

    print("=" * 118)
    print(
        "Interpretation: this removes changing-card-mix bias by following the "
        "same cards through all complete months."
    )


if __name__ == "__main__":
    main()
