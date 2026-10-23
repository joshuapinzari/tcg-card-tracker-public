"""Print monthly PokemonPriceTracker grading-premium market ratios."""

from __future__ import annotations

from database.provider_history import (
    SOURCE_POKEMON_PRICE_TRACKER,
    get_provider_ratio_timeseries,
    initialise_provider_history_schema,
)


def _fmt(value) -> str:
    return "-" if value is None else f"{float(value):.2f}x"


def main() -> None:
    initialise_provider_history_schema()
    rows = get_provider_ratio_timeseries(source=SOURCE_POKEMON_PRICE_TRACKER)

    print("\n" + "=" * 104)
    print("POKEMONPRICETRACKER MONTHLY GRADE PREMIUMS")
    print("=" * 104)
    print(
        f"{'Month':<12} {'PSA10/RAW med':>14} {'ACE10/RAW med':>14} "
        f"{'ACE10/PSA10 med':>17} {'ACE/RAW cards':>13} "
        f"{'ACE/PSA cards':>13} {'ACE sales':>10}"
    )
    print("-" * 104)

    if not rows:
        print("No provider history is stored yet.")
        return

    for row in rows:
        (
            month,
            psa_raw_median,
            _psa_raw_mean,
            ace_raw_median,
            _ace_raw_mean,
            ace_psa_median,
            _ace_psa_mean,
            _psa_raw_cards,
            ace_raw_cards,
            ace_psa_cards,
            ace_sales,
        ) = row
        print(
            f"{str(month):<12} {_fmt(psa_raw_median):>14} {_fmt(ace_raw_median):>14} "
            f"{_fmt(ace_psa_median):>17} {int(ace_raw_cards or 0):>13,} "
            f"{int(ace_psa_cards or 0):>13,} {int(ace_sales or 0):>10,}"
        )

    print("=" * 104)
    print(
        "Medians are cross-card monthly medians. ACE sales sum the provider's "
        "dated daily counts."
    )


if __name__ == "__main__":
    main()
