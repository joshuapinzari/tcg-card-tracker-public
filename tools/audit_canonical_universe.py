"""Read-only smoke check for the TCGPlayer-backed canonical card universe."""

from database.canonical_cards import get_canonical_card_universe


TARGET_PRODUCT_ID = 146698


def main() -> None:
    cards = get_canonical_card_universe()
    print(f"Canonical TCGPlayer products: {len(cards):,}")

    target = next(
        (card for card in cards if card.product_id == TARGET_PRODUCT_ID),
        None,
    )

    print("\n=== MEWTWO GX 78/73 ===")

    if target is None:
        raise SystemExit("Mewtwo GX 78/73 (product 146698) was not found")

    print(f"product_id: {target.product_id}")
    print(f"name: {target.name}")
    print(f"set: {target.set_name}")
    print(f"number: {target.card_number}")
    print(f"printing: {target.printing or 'N/A'}")
    print(f"raw observations: {target.market_observations or 0:,}")
    print(f"latest raw date: {target.latest_raw_date or 'N/A'}")
    print(
        "latest raw USD: "
        + (
            "N/A"
            if target.latest_raw_usd is None
            else f"${target.latest_raw_usd:,.2f}"
        )
    )


if __name__ == "__main__":
    main()
