"""Current intelligence for PSA and ACE graded-card assets."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import median

from database.provider_history import get_product_provider_history


SUPPORTED_MARKETS = {
    "PSA_9",
    "PSA_10",
    "ACE_10",
}

MARKET_LABELS = {
    "PSA_9": "PSA 9",
    "PSA_10": "PSA 10",
    "ACE_10": "ACE 10",
}

SOURCE_PRIORITY = {
    "pokemonpricetracker": 0,
}


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(
            tzinfo=None
        )

    if isinstance(value, date):
        return datetime.combine(
            value,
            time.min,
        )

    parsed = datetime.fromisoformat(
        str(value).replace(
            "Z",
            "+00:00",
        )
    )

    return parsed.replace(
        tzinfo=None
    )


def _normalise_price_history(rows):
    """Return clean price points ordered oldest -> newest."""

    best_by_time = {}

    for (
        price,
        currency,
        source,
        recorded_at,
    ) in rows:
        if price is None:
            continue

        price = float(price)

        if price <= 0:
            continue

        recorded_at = _to_datetime(
            recorded_at
        )

        candidate = (
            recorded_at,
            price,
            str(currency),
            str(source),
        )

        existing = best_by_time.get(
            recorded_at
        )

        if existing is None:
            best_by_time[
                recorded_at
            ] = candidate
            continue

        old_priority = (
            SOURCE_PRIORITY.get(
                existing[3],
                99,
            )
        )

        new_priority = (
            SOURCE_PRIORITY.get(
                candidate[3],
                99,
            )
        )

        if new_priority < old_priority:
            best_by_time[
                recorded_at
            ] = candidate

    return sorted(
        best_by_time.values(),
        key=lambda point: point[0],
    )


def _asof(
    points,
    target: datetime,
    *,
    max_lag_days: int | None = None,
):
    eligible = [
        point
        for point in points
        if point[0] <= target
    ]

    if not eligible:
        return None

    point = eligible[-1]

    if max_lag_days is not None:
        lag = (
            target.date()
            - point[0].date()
        ).days

        if lag > max_lag_days:
            return None

    return point


def _change_over_days(
    points,
    days: int,
):
    if not points:
        return None

    latest = points[-1]

    target = (
        latest[0]
        - timedelta(
            days=days
        )
    )

    previous = _asof(
        points,
        target,
        max_lag_days=62,
    )

    if previous is None:
        return None

    if previous[1] <= 0:
        return None

    return (
        latest[1]
        / previous[1]
        - 1.0
    )


def _drawdown_from_high(
    points,
):
    if not points:
        return None

    current = points[-1][1]

    high = max(
        point[1]
        for point in points
    )

    if high <= 0:
        return None

    return (
        current
        / high
        - 1.0
    )


def _ratio_asof(
    numerator_points,
    denominator_points,
    target: datetime,
):
    numerator = _asof(
        numerator_points,
        target,
        max_lag_days=62,
    )

    denominator = _asof(
        denominator_points,
        target,
        max_lag_days=62,
    )

    if (
        numerator is None
        or denominator is None
        or denominator[1] <= 0
    ):
        return None

    return (
        numerator[1]
        / denominator[1]
    )


def _premium_change_over_days(
    numerator_points,
    denominator_points,
    days: int,
):
    if not numerator_points:
        return None

    latest_date = (
        numerator_points[-1][0]
    )

    current_ratio = _ratio_asof(
        numerator_points,
        denominator_points,
        latest_date,
    )

    historical_ratio = _ratio_asof(
        numerator_points,
        denominator_points,
        latest_date
        - timedelta(
            days=days
        ),
    )

    if (
        current_ratio is None
        or historical_ratio is None
        or historical_ratio <= 0
    ):
        return None

    return (
        current_ratio
        / historical_ratio
        - 1.0
    )


def _monthly_ratio_median(
    numerator_points,
    denominator_points,
):
    """Median same-card monthly price ratio across provider history."""
    numerator_months = {}
    denominator_months = {}

    for point in numerator_points:
        observed_at, price = point[0], point[1]
        numerator_months.setdefault(
            (observed_at.year, observed_at.month),
            [],
        ).append(float(price))

    for point in denominator_points:
        observed_at, price = point[0], point[1]
        denominator_months.setdefault(
            (observed_at.year, observed_at.month),
            [],
        ).append(float(price))

    ratios = []
    for month in sorted(set(numerator_months) & set(denominator_months)):
        numerator = float(median(numerator_months[month]))
        denominator = float(median(denominator_months[month]))
        if numerator > 0 and denominator > 0:
            ratios.append(numerator / denominator)

    if not ratios:
        return None, 0

    return float(median(ratios)), len(ratios)


def _relative_to_history(
    current_ratio,
    historical_median,
):
    if (
        current_ratio is None
        or historical_median is None
        or historical_median <= 0
    ):
        return None

    return (
        float(current_ratio)
        / float(historical_median)
        - 1.0
    )


def _population_latest(
    history,
):
    if not history:
        return None

    return int(
        history[-1][0]
    )


def _confidence(
    *,
    market_type: str,
    history_months: float,
    history_dates: int,
    price_age_days: int,
    sales_90: int,
    days_since_sale: int | None,
):
    reasons = []

    if history_months >= 12:
        reasons.append(
            "substantial price history"
        )
    elif history_months >= 4:
        reasons.append(
            "moderate price history"
        )
    else:
        reasons.append(
            "limited price history"
        )

    if price_age_days <= 14:
        reasons.append(
            "current price observation"
        )
    else:
        reasons.append(
            "older price observation"
        )

    if market_type in {
        "PSA_9",
        "PSA_10",
    }:
        if (
            history_months >= 12
            and history_dates >= 10
            and price_age_days <= 30
        ):
            return (
                "HIGH",
                reasons,
            )

        if (
            history_months >= 4
            and history_dates >= 4
        ):
            return (
                "MEDIUM",
                reasons,
            )

        return (
            "LOW",
            reasons,
        )

    # ACE needs stronger liquidity evidence because its
    # price history is much thinner and sale-driven.
    if (
        history_months >= 9
        and history_dates >= 7
        and sales_90 >= 3
        and days_since_sale is not None
        and days_since_sale <= 90
    ):
        reasons.append(
            "good recent ACE liquidity"
        )

        return (
            "HIGH",
            reasons,
        )

    if (
        history_months >= 4
        and history_dates >= 4
        and (
            sales_90 >= 1
            or (
                days_since_sale
                is not None
                and days_since_sale <= 120
            )
        )
    ):
        reasons.append(
            "some recent ACE liquidity"
        )

        return (
            "MEDIUM",
            reasons,
        )

    reasons.append(
        "thin or stale ACE liquidity"
    )

    return (
        "LOW",
        reasons,
    )


def _provider_histories(product_id: int):
    """Return PokemonPriceTracker histories in the point shape used here."""
    histories = {
        "RAW": [],
        "PSA_9": [],
        "PSA_10": [],
        "ACE_10": [],
    }
    sales = {
        "PSA_9": [],
        "PSA_10": [],
        "ACE_10": [],
    }

    for observed_on, market_type, price, sale_count, volume, series_label in (
        get_product_provider_history(int(product_id))
    ):
        market_type = str(market_type).upper()
        if market_type not in histories or price is None:
            continue
        price = float(price)
        if price <= 0:
            continue
        observed_at = _to_datetime(observed_on)
        histories[market_type].append(
            (observed_at, price, "USD", "pokemonpricetracker")
        )
        if market_type in sales:
            try:
                count = int(sale_count or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                sales[market_type].append((observed_at.date(), price, count))

    for market in histories:
        histories[market] = sorted(histories[market], key=lambda point: point[0])
    for market in sales:
        sales[market] = sorted(sales[market], key=lambda point: point[0])

    return histories, sales


def _weighted_median_price(rows):
    """Weighted median of daily average prices using provider sale counts."""
    expanded = []
    for _, price, count in rows:
        if price is None or count <= 0:
            continue
        expanded.extend([float(price)] * int(count))
    return float(median(expanded)) if expanded else None


def _build_graded_card_intelligence(
    product_id: int,
    market_type: str,
    histories,
    sales_by_market,
):
    """Build one slab-market intelligence object from already-loaded history."""
    slab = histories[market_type]
    if not slab:
        return None

    latest = slab[-1]
    latest_date = latest[0]
    raw = histories["RAW"]
    psa_10 = histories["PSA_10"]

    premium_vs_raw = _ratio_asof(slab, raw, latest_date)
    ratio_vs_psa10 = (
        _ratio_asof(slab, psa_10, latest_date)
        if market_type == "ACE_10"
        else None
    )

    premium_vs_raw_historical_median, premium_ratio_months = (
        _monthly_ratio_median(slab, raw)
    )
    premium_vs_raw_vs_history = _relative_to_history(
        premium_vs_raw,
        premium_vs_raw_historical_median,
    )

    if market_type == "ACE_10":
        ratio_vs_psa10_historical_median, psa_ratio_months = (
            _monthly_ratio_median(slab, psa_10)
        )
        ratio_vs_psa10_vs_history = _relative_to_history(
            ratio_vs_psa10,
            ratio_vs_psa10_historical_median,
        )
    else:
        ratio_vs_psa10_historical_median = None
        ratio_vs_psa10_vs_history = None
        psa_ratio_months = 0

    today = date.today()
    market_sales = sales_by_market.get(market_type, [])

    def sales_within(days: int) -> int:
        return sum(
            count
            for sold_date, _, count in market_sales
            if 0 <= (today - sold_date).days <= days
        )

    sales_30 = sales_within(30)
    sales_90 = sales_within(90)
    sales_180 = sales_within(180)

    last_sale_date = max((row[0] for row in market_sales), default=None)
    days_since_sale = (
        (today - last_sale_date).days if last_sale_date is not None else None
    )
    sale_rows_90 = [
        row for row in market_sales
        if 0 <= (today - row[0]).days <= 90
    ]
    median_sale_90 = _weighted_median_price(sale_rows_90)

    history_dates = len({point[0].date() for point in slab})
    history_days = (
        (slab[-1][0].date() - slab[0][0].date()).days if len(slab) >= 2 else 0
    )
    history_months = history_days / 30.44
    price_age_days = max(0, (today - latest_date.date()).days)

    # The provider history is intentionally six months, so confidence thresholds
    # are calibrated to the data window we actually own rather than the old
    # multi-year legacy-provider assumptions.
    if market_type in {"PSA_9", "PSA_10"}:
        if history_months >= 4.5 and history_dates >= 20 and price_age_days <= 30:
            confidence = "HIGH"
            reasons = ["deep six-month provider history", "current price observation"]
        elif history_months >= 2 and history_dates >= 5:
            confidence = "MEDIUM"
            reasons = ["usable provider history"]
        else:
            confidence = "LOW"
            reasons = ["limited provider history"]
    else:
        if (
            history_months >= 4
            and history_dates >= 5
            and sales_90 >= 5
            and days_since_sale is not None
            and days_since_sale <= 45
        ):
            confidence = "HIGH"
            reasons = ["deep ACE history", "good recent ACE liquidity"]
        elif (
            history_dates >= 2
            and sales_90 >= 1
            and days_since_sale is not None
            and days_since_sale <= 120
        ):
            confidence = "MEDIUM"
            reasons = ["usable ACE history", "some recent ACE liquidity"]
        else:
            confidence = "LOW"
            reasons = ["thin or stale ACE liquidity"]

    return {
        "card_id": int(product_id),
        "product_id": int(product_id),
        "source": "pokemonpricetracker",
        "market_type": market_type,
        "market_label": MARKET_LABELS[market_type],
        "current_price": float(latest[1]),
        "current_date": latest_date,
        "price_age_days": price_age_days,
        "return_30": _change_over_days(slab, 30),
        "return_90": _change_over_days(slab, 90),
        "return_180": _change_over_days(slab, 180),
        "drawdown_from_high": _drawdown_from_high(slab),
        "premium_vs_raw": premium_vs_raw,
        "premium_vs_raw_historical_median": premium_vs_raw_historical_median,
        "premium_vs_raw_vs_history": premium_vs_raw_vs_history,
        "premium_ratio_months": premium_ratio_months,
        "premium_vs_raw_change_90": _premium_change_over_days(slab, raw, 90),
        "premium_vs_raw_change_180": _premium_change_over_days(slab, raw, 180),
        "ratio_vs_psa10": ratio_vs_psa10,
        "ratio_vs_psa10_historical_median": ratio_vs_psa10_historical_median,
        "ratio_vs_psa10_vs_history": ratio_vs_psa10_vs_history,
        "psa_ratio_months": psa_ratio_months,
        "ratio_vs_psa10_change_90": (
            _premium_change_over_days(slab, psa_10, 90)
            if market_type == "ACE_10"
            else None
        ),
        "ratio_vs_psa10_change_180": (
            _premium_change_over_days(slab, psa_10, 180)
            if market_type == "ACE_10"
            else None
        ),
        "sales_30": sales_30,
        "sales_90": sales_90,
        "sales_180": sales_180,
        "last_sale_date": last_sale_date,
        "days_since_sale": days_since_sale,
        "median_sale_90": median_sale_90,
        # Population data is not supplied by PokemonPriceTracker.  Do not blend
        # legacy population estimates into this provider-backed
        # intelligence object.
        "psa_10_population": None,
        "psa_total_population": None,
        "gem_rate": None,
        "history_observations": len(slab),
        "history_dates": history_dates,
        "history_months": history_months,
        "confidence": confidence,
        "confidence_reasons": reasons,
    }


def get_graded_card_intelligence(
    product_id: int,
    market_type: str,
):
    """Return PokemonPriceTracker-backed intelligence for one slab market.

    ``product_id`` is the canonical TCGPlayer product id. All price, premium
    and liquidity metrics here come from the exact-ID PokemonPriceTracker
    backfill. Legacy price/sale tables are intentionally not consulted.
    """
    market_type = str(market_type).upper()
    if market_type not in SUPPORTED_MARKETS:
        raise ValueError(f"Unsupported graded market: {market_type}")

    histories, sales_by_market = _provider_histories(int(product_id))
    return _build_graded_card_intelligence(
        int(product_id),
        market_type,
        histories,
        sales_by_market,
    )


def get_all_graded_card_intelligence(product_id: int) -> dict[str, dict | None]:
    """Return all supported slab intelligence with one provider-history read.

    Card Analysis displays PSA 9, PSA 10 and ACE 10 together. Loading the
    provider history once avoids three identical database reads whenever the
    user switches cards.
    """
    histories, sales_by_market = _provider_histories(int(product_id))
    return {
        market_type: _build_graded_card_intelligence(
            int(product_id),
            market_type,
            histories,
            sales_by_market,
        )
        for market_type in ("PSA_9", "PSA_10", "ACE_10")
    }
