from __future__ import annotations

from datetime import date, timedelta
from math import sqrt

import pandas as pd

from database.market_analytics import (
    connect,
    get_card_screener,
)
from pokemon_eras import era_for_set_name


WINDOWS = (7, 30, 90, 180, 365)

MARKET_STRUCTURE_WINDOWS = (
    30,
    90,
    180,
)

INVESTABLE_PRICE_FLOOR = 5.0
PREMIUM_PRICE_FLOOR = 50.0

def era_for_set(set_name: str) -> str | None:
    return era_for_set_name(set_name)


def search_cards(
    query: str,
    set_name: str | None = None,
    card_number: str | None = None,
    limit: int = 25,
) -> list[dict]:
    conditions = ["p.name ILIKE %s"]
    params: list[object] = [f"%{query}%"]

    if set_name:
        conditions.append("p.set_name = %s")
        params.append(set_name)

    if card_number:
        conditions.append("p.card_number = %s")
        params.append(card_number)

    params.append(limit)

    sql = f"""
        SELECT
            p.product_id,
            p.name,
            p.set_name,
            p.card_number,
            p.rarity,
            p.set_published_on
        FROM tcgplayer_products p
        WHERE {" AND ".join(conditions)}
        ORDER BY
            p.set_published_on,
            p.set_name,
            p.card_number,
            p.name
        LIMIT %s;
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    return [
        {
            "product_id": row[0],
            "name": row[1],
            "set_name": row[2],
            "card_number": row[3],
            "rarity": row[4],
            "set_published_on": row[5],
        }
        for row in rows
    ]


def _get_product(
    connection,
    product_id: int,
) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                product_id,
                name,
                set_name,
                card_number,
                rarity,
                artist,
                set_published_on
            FROM tcgplayer_products
            WHERE product_id = %s;
            """,
            (product_id,),
        )

        row = cursor.fetchone()

    if row is None:
        raise ValueError(
            f"Unknown TCGPlayer product_id: {product_id}"
        )

    return {
        "product_id": row[0],
        "name": row[1],
        "set_name": row[2],
        "card_number": row[3],
        "rarity": row[4],
        "artist": row[5],
        "set_published_on": row[6],
    }


def _choose_printing(
    connection,
    product_id: int,
) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                printing,
                COUNT(*) AS observations
            FROM tcgplayer_daily_prices
            WHERE product_id = %s
              AND market_price IS NOT NULL
            GROUP BY printing
            ORDER BY observations DESC;
            """,
            (product_id,),
        )

        rows = cursor.fetchall()

    if not rows:
        raise ValueError(
            "No market-price history is available "
            f"for product {product_id}."
        )

    maximum = max(
        int(row[1])
        for row in rows
    )

    eligible = [
        (
            str(printing),
            int(count),
        )
        for printing, count in rows
        if int(count) >= maximum * 0.80
    ]

    priority = {
        "Normal": 0,
        "Holofoil": 1,
        "Reverse Holofoil": 2,
    }

    eligible.sort(
        key=lambda item: (
            priority.get(
                item[0],
                99,
            ),
            -item[1],
            item[0],
        )
    )

    return eligible[0][0]


def _load_card_series(
    connection,
    product_id: int,
    printing: str,
    as_of: date | None,
) -> tuple[pd.Series, pd.Series]:
    params: list[object] = [
        product_id,
        printing,
    ]

    as_of_filter = ""

    if as_of is not None:
        as_of_filter = (
            "AND snapshot_date <= %s"
        )
        params.append(as_of)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                snapshot_date,
                market_price
            FROM tcgplayer_daily_prices
            WHERE product_id = %s
              AND printing = %s
              AND market_price IS NOT NULL
              {as_of_filter}
            ORDER BY snapshot_date;
            """,
            params,
        )

        rows = cursor.fetchall()

    if not rows:
        raise ValueError(
            "No usable market-price observations."
        )

    frame = pd.DataFrame(
        rows,
        columns=[
            "date",
            "price",
        ],
    )

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    frame["price"] = pd.to_numeric(
        frame["price"],
        errors="coerce",
    )

    raw = (
        frame
        .dropna()
        .drop_duplicates(
            subset=["date"],
            keep="last",
        )
        .set_index("date")["price"]
        .sort_index()
    )

    daily = raw.reindex(
        pd.date_range(
            raw.index.min(),
            raw.index.max(),
            freq="D",
        )
    )

    # Never carry an old observation indefinitely.
    daily = daily.ffill(limit=7)

    # Seven-day rolling median reduces daily market-price noise.
    smoothed = daily.rolling(
        window=7,
        min_periods=3,
    ).median()

    return raw, smoothed


def _load_index_series(
    connection,
    scope_type: str,
    scope_name: str,
    as_of: date,
) -> tuple[pd.Series, float | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                snapshot_date,
                index_value,
                breadth_positive
            FROM tcgplayer_market_index_daily
            WHERE scope_type = %s
              AND scope_name = %s
              AND snapshot_date <= %s
            ORDER BY snapshot_date;
            """,
            (
                scope_type,
                scope_name,
                as_of,
            ),
        )

        rows = cursor.fetchall()

    if not rows:
        return pd.Series(dtype=float), None

    frame = pd.DataFrame(
        rows,
        columns=[
            "date",
            "value",
            "breadth",
        ],
    )

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    frame["value"] = pd.to_numeric(
        frame["value"],
        errors="coerce",
    )

    series = (
        frame
        .dropna(
            subset=["value"]
        )
        .drop_duplicates(
            subset=["date"],
            keep="last",
        )
        .set_index("date")["value"]
        .sort_index()
    )

    breadth = pd.to_numeric(
        frame["breadth"],
        errors="coerce",
    ).dropna()

    latest_breadth = (
        float(breadth.iloc[-1])
        if not breadth.empty
        else None
    )

    return series, latest_breadth


def _value_on_or_before(
    series: pd.Series,
    when: date | pd.Timestamp,
    max_age_days: int = 7,
) -> float | None:
    if series.empty:
        return None

    target = pd.Timestamp(when)

    eligible = (
        series.loc[:target]
        .dropna()
    )

    if eligible.empty:
        return None

    observation_date = (
        eligible.index[-1]
    )

    age = (
        target - observation_date
    ).days

    if age > max_age_days:
        return None

    return float(
        eligible.iloc[-1]
    )


def _period_return(
    series: pd.Series,
    as_of: date,
    days: int,
) -> float | None:
    current = _value_on_or_before(
        series,
        as_of,
    )

    start = _value_on_or_before(
        series,
        as_of - timedelta(days=days),
    )

    if (
        current is None
        or start is None
        or start <= 0
    ):
        return None

    return current / start - 1.0


def _momentum_block(
    series: pd.Series,
    as_of: date,
) -> dict:
    return {
        days: _period_return(
            series,
            as_of,
            days,
        )
        for days in WINDOWS
    }


def _context_state(
    momentum: dict[int, float | None],
) -> str:
    """
    Simple transparent trend classification.

    Breadth is deliberately not used here yet because
    the stored breadth metric is one-day positive breadth,
    while these labels describe medium-term trend.

    We will add horizon-matched breadth separately.
    """
    return_30d = momentum.get(30)
    return_90d = momentum.get(90)

    if (
        return_30d is None
        or return_90d is None
    ):
        return "Unknown"

    if (
        return_30d > 0
        and return_90d > 0
    ):
        return "Bullish"

    if (
        return_30d < 0
        and return_90d < 0
    ):
        return "Bearish"

    return "Mixed"


def _relative(
    card_return: float | None,
    benchmark_return: float | None,
) -> float | None:
    if (
        card_return is None
        or benchmark_return is None
    ):
        return None

    return (
        card_return
        - benchmark_return
    )



def _screener_frame(
    days: int,
    set_name: str | None,
    min_start_price: float,
) -> pd.DataFrame:
    """
    Return point-in-time card performance.

    The minimum-price rule applies to the start of
    the measurement period, avoiding hindsight.
    """
    rows = get_card_screener(
        days=days,
        set_name=set_name,
        rarities=None,
        min_start_price=min_start_price,
        min_current_price=0.0,
        min_absolute_move=0.0,
    )

    columns = [
        "product_id",
        "name",
        "set_name",
        "card_number",
        "rarity",
        "start_date",
        "start_price",
        "current_date",
        "current_price",
        "dollar_change",
        "period_return",
    ]

    frame = pd.DataFrame(
        rows,
        columns=columns,
    )

    if frame.empty:
        return frame

    for column in (
        "start_price",
        "current_price",
        "dollar_change",
        "period_return",
    ):
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    return frame


def _cohort_summary(
    frame: pd.DataFrame,
    product_id: int,
) -> dict:
    """
    Describe one comparison universe.

    Breadth means the percentage of cards with a
    positive return over the full selected horizon.
    """
    if frame.empty:
        return {
            "count": 0,
            "breadth_positive": None,
            "median_return": None,
            "mean_return": None,
            "card_percentile": None,
        }

    valid = (
        frame
        .dropna(
            subset=["period_return"]
        )
        .copy()
    )

    if valid.empty:
        return {
            "count": 0,
            "breadth_positive": None,
            "median_return": None,
            "mean_return": None,
            "card_percentile": None,
        }

    breadth = float(
        (
            valid["period_return"]
            > 0
        ).mean()
    )

    median_return = float(
        valid[
            "period_return"
        ].median()
    )

    mean_return = float(
        valid[
            "period_return"
        ].mean()
    )

    card_rows = valid[
        valid["product_id"]
        == product_id
    ]

    percentile = None

    if not card_rows.empty:
        card_return = float(
            card_rows.iloc[0][
                "period_return"
            ]
        )

        percentile = float(
            (
                valid["period_return"]
                <= card_return
            ).mean()
        )

    return {
        "count": int(
            len(valid)
        ),
        "breadth_positive": breadth,
        "median_return": median_return,
        "mean_return": mean_return,
        "card_percentile": percentile,
    }


def _market_structure(
    product_id: int,
    set_name: str,
    era: str | None,
) -> dict:
    """
    Compare the card against several market layers.

    Primary set signal:
        cards worth at least $5 at formation.

    Secondary diagnostics:
        whole set
        $50+ premium/chase cohort
    """
    output = {}

    for days in MARKET_STRUCTURE_WINDOWS:
        whole_set = _screener_frame(
            days=days,
            set_name=set_name,
            min_start_price=0.0,
        )

        investable_set = whole_set[
            whole_set["start_price"]
            >= INVESTABLE_PRICE_FLOOR
        ].copy()

        premium_set = whole_set[
            whole_set["start_price"]
            >= PREMIUM_PRICE_FLOOR
        ].copy()

        investable_market = (
            _screener_frame(
                days=days,
                set_name=None,
                min_start_price=(
                    INVESTABLE_PRICE_FLOOR
                ),
            )
        )

        if (
            era is not None
            and not investable_market.empty
        ):
            era_mask = (
                investable_market[
                    "set_name"
                ]
                .fillna("")
                .map(era_for_set)
                == era
            )

            investable_era = (
                investable_market[
                    era_mask
                ].copy()
            )

        else:
            investable_era = (
                investable_market.iloc[
                    0:0
                ].copy()
            )

        # Live version of our 90D relative-momentum signal.
        live_signal = None

        if days == 90 and not investable_market.empty:
            signal_frame = investable_market.copy()

            signal_frame["set_momentum"] = (
                signal_frame.groupby("set_name")[
                    "period_return"
                ].transform("median")
            )

            signal_frame["relative_momentum"] = (
                signal_frame["period_return"]
                - signal_frame["set_momentum"]
            )

            signal_frame["signal_percentile"] = (
                signal_frame["relative_momentum"]
                .rank(method="average", pct=True)
            )

            card_signal = signal_frame[
                signal_frame["product_id"] == product_id
            ]

            if not card_signal.empty:
                row = card_signal.iloc[0]

                live_signal = {
                    "relative_momentum_90d":
                        float(row["relative_momentum"]),
                    "signal_percentile":
                        float(row["signal_percentile"]),
                    "top_10_overheating":
                        bool(row["signal_percentile"] >= 0.90),
                }

        output[days] = {
            "live_signal": live_signal,
            "whole_set": (
                _cohort_summary(
                    whole_set,
                    product_id,
                )
            ),
            "investable_set": (
                _cohort_summary(
                    investable_set,
                    product_id,
                )
            ),
            "premium_set": (
                _cohort_summary(
                    premium_set,
                    product_id,
                )
            ),
            "investable_era": (
                _cohort_summary(
                    investable_era,
                    product_id,
                )
            ),
            "investable_market": (
                _cohort_summary(
                    investable_market,
                    product_id,
                )
            ),
        }

    return output


def get_card_intelligence(
    product_id: int,
    as_of: date | None = None,
    *,
    include_market_structure: bool = True,
    printing: str | None = None,
) -> dict:
    explicit_printing = printing
    if explicit_printing is not None:
        from database.card_variants import raw_asset_key
        raw_asset_key(product_id, explicit_printing)
        # Cross-sectional percentiles are trained/calculated on primary
        # printings. Do not attach them to a manually selected alternate.
        if include_market_structure:
            from database.card_variants import is_primary_printing
            include_market_structure = is_primary_printing(product_id, explicit_printing)
    with connect() as connection:
        product = _get_product(
            connection,
            product_id,
        )

        printing = explicit_printing if explicit_printing is not None else _choose_printing(
            connection,
            product_id,
        )

        raw, smooth = (
            _load_card_series(
                connection,
                product_id,
                printing,
                as_of,
            )
        )

        latest_date = (
            raw.index.max().date()
        )

        analysis_date = (
            min(
                as_of,
                latest_date,
            )
            if as_of is not None
            else latest_date
        )

        spot = _value_on_or_before(
            raw,
            analysis_date,
        )

        trend_price = (
            _value_on_or_before(
                smooth,
                analysis_date,
            )
        )

        ma30_series = smooth.rolling(
            30,
            min_periods=15,
        ).mean()

        ma90_series = smooth.rolling(
            90,
            min_periods=45,
        ).mean()

        ma30 = _value_on_or_before(
            ma30_series,
            analysis_date,
        )

        ma90 = _value_on_or_before(
            ma90_series,
            analysis_date,
        )

        ma30_old = _value_on_or_before(
            ma30_series,
            analysis_date
            - timedelta(days=30),
        )

        ma90_old = _value_on_or_before(
            ma90_series,
            analysis_date
            - timedelta(days=30),
        )

        ma30_slope = (
            ma30 / ma30_old - 1.0
            if (
                ma30 is not None
                and ma30_old not in (
                    None,
                    0,
                )
            )
            else None
        )

        ma90_slope = (
            ma90 / ma90_old - 1.0
            if (
                ma90 is not None
                and ma90_old not in (
                    None,
                    0,
                )
            )
            else None
        )

        distance_ma30 = (
            trend_price / ma30 - 1.0
            if (
                trend_price is not None
                and ma30 not in (
                    None,
                    0,
                )
            )
            else None
        )

        distance_ma90 = (
            trend_price / ma90 - 1.0
            if (
                trend_price is not None
                and ma90 not in (
                    None,
                    0,
                )
            )
            else None
        )

        trailing_year = (
            smooth.loc[
                pd.Timestamp(
                    analysis_date
                    - timedelta(days=365)
                ):
                pd.Timestamp(
                    analysis_date
                )
            ]
            .dropna()
        )

        year_high = (
            float(
                trailing_year.max()
            )
            if not trailing_year.empty
            else None
        )

        drawdown_1y = (
            trend_price / year_high - 1.0
            if (
                trend_price is not None
                and year_high not in (
                    None,
                    0,
                )
            )
            else None
        )

        trailing_90 = (
            smooth.loc[
                pd.Timestamp(
                    analysis_date
                    - timedelta(days=90)
                ):
                pd.Timestamp(
                    analysis_date
                )
            ]
            .dropna()
        )

        returns_90 = (
            trailing_90
            .pct_change(
                fill_method=None
            )
            .dropna()
        )

        monthly_volatility = (
            float(
                returns_90.std()
            )
            * sqrt(30)
            if len(
                returns_90
            ) >= 10
            else None
        )

        card_momentum = (
            _momentum_block(
                smooth,
                analysis_date,
            )
        )

        era = era_for_set(
            product["set_name"]
        )

        set_series, set_breadth = (
            _load_index_series(
                connection,
                "set",
                product["set_name"],
                analysis_date,
            )
        )

        era_series, era_breadth = (
            _load_index_series(
                connection,
                "era",
                era,
                analysis_date,
            )
            if era is not None
            else (
                pd.Series(dtype=float),
                None,
            )
        )

        market_series, market_breadth = (
            _load_index_series(
                connection,
                "market",
                "Modern English Pokemon",
                analysis_date,
            )
        )

    set_momentum = (
        _momentum_block(
            set_series,
            analysis_date,
        )
    )

    era_momentum = (
        _momentum_block(
            era_series,
            analysis_date,
        )
    )

    market_momentum = (
        _momentum_block(
            market_series,
            analysis_date,
        )
    )

    # ``_market_structure`` executes several broad card-screener queries across
    # 30/90/180 day windows.  They are useful for research / trade diagnostics,
    # but Card Analysis already has the same live cross-sectional information
    # from the opportunity universe.  Allow that view to skip the expensive
    # duplicate work while preserving the historical default for every existing
    # caller.
    market_structure = (
        _market_structure(
            product_id=product_id,
            set_name=product[
                "set_name"
            ],
            era=era,
        )
        if include_market_structure
        else {}
    )

    price_180 = _value_on_or_before(
        smooth,
        analysis_date - timedelta(days=180),
    )
    price_60 = _value_on_or_before(
        smooth,
        analysis_date - timedelta(days=60),
    )
    price_30 = _value_on_or_before(
        smooth,
        analysis_date - timedelta(days=30),
    )

    prior_runup = (
        price_60 / price_180 - 1
        if price_60 and price_180
        else None
    )

    correction = (
        price_30 / price_60 - 1
        if price_30 and price_60
        else None
    )

    recovery_30 = (
        trend_price / price_30 - 1
        if trend_price and price_30
        else None
    )

    set_recovery_30 = (
        market_structure[30][
            "investable_set"
        ]["median_return"]
        if include_market_structure
        else set_momentum.get(30)
    )

    relative_recovery = (
        recovery_30 - set_recovery_30
        if (
            recovery_30 is not None
            and set_recovery_30 is not None
        )
        else None
    )

    live_signal = (
        market_structure[90].get(
            "live_signal"
        )
        if include_market_structure
        else None
    )

    recovery_setup = bool(
        prior_runup is not None
        and correction is not None
        and prior_runup >= 0.20
        and correction <= -0.10
    )

    relative_to_market_30 = (
        recovery_30 - market_momentum[30]
        if (
            recovery_30 is not None
            and market_momentum[30] is not None
        )
        else None
    )

    not_overheated = bool(
        live_signal is not None
        and not live_signal[
            "top_10_overheating"
        ]
    )

    clean_recovery = bool(
        recovery_setup
        and relative_recovery is not None
        and relative_recovery > 0
        and relative_to_market_30 is not None
        and relative_to_market_30 > 0
        and not_overheated
    )

    if (
        trend_price is not None
        and ma30 is not None
        and ma90 is not None
        and ma30_slope is not None
        and trend_price > ma30 > ma90
        and ma30_slope > 0
    ):
        card_state = "Bullish"

    elif (
        trend_price is not None
        and ma30 is not None
        and ma90 is not None
        and ma30_slope is not None
        and trend_price < ma30 < ma90
        and ma30_slope < 0
    ):
        card_state = "Bearish"

    else:
        card_state = "Mixed"

    return {
        **product,
        "era": era,
        "printing": printing,
        "analysis_date": analysis_date,
        "spot_price_usd": spot,
        "trend_price_usd": trend_price,
        "ma30_usd": ma30,
        "ma90_usd": ma90,
        "distance_ma30": distance_ma30,
        "distance_ma90": distance_ma90,
        "ma30_slope_30d": ma30_slope,
        "ma90_slope_30d": ma90_slope,
        "drawdown_from_1y_high": drawdown_1y,
        "monthly_volatility_90d": monthly_volatility,
        "card_momentum": card_momentum,
        "set_momentum": set_momentum,
        "era_momentum": era_momentum,
        "market_momentum": market_momentum,
        "relative_to_set": {
            days: _relative(
                card_momentum[days],
                set_momentum[days],
            )
            for days in WINDOWS
        },
        "relative_to_era": {
            days: _relative(
                card_momentum[days],
                era_momentum[days],
            )
            for days in WINDOWS
        },
        "relative_to_market": {
            days: _relative(
                card_momentum[days],
                market_momentum[days],
            )
            for days in WINDOWS
        },
        "set_breadth": set_breadth,
        "era_breadth": era_breadth,
        "market_breadth": market_breadth,
        "market_structure": market_structure,
        "live_overheating": live_signal,
        "recovery": {
            "prior_runup": prior_runup,
            "correction": correction,
            "recovery_30": recovery_30,
            "relative_recovery": relative_recovery,
            "relative_to_market_30":
                relative_to_market_30,
            "setup": recovery_setup,
            "clean_recovery":
                clean_recovery,
        },
        "card_trend_state": card_state,
        "set_trend_state": _context_state(
            set_momentum,
        ),
        "era_trend_state": _context_state(
            era_momentum,
        ),
        "market_trend_state": _context_state(
            market_momentum,
        ),
    }
