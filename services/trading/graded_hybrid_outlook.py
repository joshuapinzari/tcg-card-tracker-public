"""Hierarchical graded-card outlook.

A graded asset is decomposed into:

    underlying RAW card
    +
    grade-market wrapper

The wrapper measures how a grade market has moved RELATIVE TO raw.

This avoids double-counting the general Pokemon/card market.

Modes:
    AUTO
    DIRECT
    RAW_PLUS_MARKET

AUTO uses exact PokemonPriceTracker slab history whenever the requested
horizon can be formed.  The broad provider grade-market wrapper is a fallback
only when direct slab history is unavailable.

This is descriptive outlook evidence, not yet a validated
forward-return forecast.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import math

import numpy as np
import pandas as pd

from database.operations import DATABASE_NAME
from services.trading.trade_balancer import item_local_value


SUPPORTED_MARKETS = (
    "PSA_9",
    "PSA_10",
    "ACE_10",
)


MARKET_LABELS = {
    "PSA_9":
        "PSA 9",

    "PSA_10":
        "PSA 10",

    "ACE_10":
        "ACE 10",
}


HORIZONS = {
    30: 45,
    90: 62,
    180: 90,
}


DIRECT_WEIGHTS = {
    "HIGH":
        0.75,

    "MEDIUM":
        0.50,

    "LOW":
        0.25,

    "NONE":
        0.0,
}


def _number(value):
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value):
        return None

    return value


def _to_datetime(value):
    value = pd.Timestamp(value)

    if value.tzinfo is not None:
        value = value.tz_convert(None)

    return value.to_pydatetime()


def fetch_hybrid_price_history(product_ids=None):
    """Return PokemonPriceTracker RAW + graded history.

    ``product_ids`` can restrict the query to the cards currently being
    analysed.  This matters in the Streamlit trade builder: after the full
    provider backfill, pulling ~465k rows on every widget rerun made the GUI
    unnecessarily sluggish.

    The dataframe keeps the historical ``card_id`` column name to avoid a large
    internal API break, but the values are TCGPlayer ``product_id`` values.
    """
    import psycopg
    product_ids = tuple(
        sorted({int(value) for value in (product_ids or ()) if value is not None})
    )

    where_product = ""
    params = ()
    if product_ids:
        where_product = " AND product_id = ANY(%s)"
        params = (list(product_ids),)

    with psycopg.connect(dbname=DATABASE_NAME) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    product_id AS card_id,
                    market_type,
                    price::DOUBLE PRECISION AS price,
                    source,
                    observed_on::TIMESTAMP AS recorded_at
                FROM provider_price_history
                WHERE source = 'pokemonpricetracker'
                  AND market_type IN ('RAW', 'PSA_9', 'PSA_10', 'ACE_10')
                  AND price IS NOT NULL
                  AND price > 0
                  {where_product}
                ORDER BY product_id, market_type, observed_on;
                """,
                params,
            )
            rows = cursor.fetchall()

    frame = pd.DataFrame(
        rows,
        columns=["card_id", "market_type", "price", "source", "recorded_at"],
    )
    if frame.empty:
        return frame

    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["recorded_at"] = pd.to_datetime(frame["recorded_at"], errors="coerce")
    frame = frame.dropna(subset=["price", "recorded_at"])
    frame = frame[frame["price"] > 0].copy()
    return (
        frame.sort_values(["card_id", "market_type", "recorded_at"])
        .drop_duplicates(
            subset=["card_id", "market_type", "recorded_at"],
            keep="last",
        )
        .reset_index(drop=True)
    )

def _asof_point(
    group,
    target,
    *,
    max_lag_days,
):
    eligible = group[
        group["recorded_at"]
        <= target
    ]

    if eligible.empty:
        return None

    row = eligible.iloc[-1]

    lag = (
        target.date()
        - row[
            "recorded_at"
        ].date()
    ).days

    if lag > max_lag_days:
        return None

    return {
        "date":
            row["recorded_at"],

        "price":
            float(
                row["price"]
            ),

        "lag_days":
            int(lag),
    }


def _group_map(prices):
    return {
        (
            int(card_id),
            str(market_type),
        ):
            group.sort_values(
                "recorded_at"
            )

        for (
            card_id,
            market_type,
        ), group
        in prices.groupby(
            [
                "card_id",
                "market_type",
            ]
        )
    }


def _history_return(
    groups,
    card_id,
    market_type,
    *,
    horizon,
    as_of,
):
    group = groups.get(
        (
            int(card_id),
            str(market_type),
        )
    )

    if group is None:
        return None

    current = _asof_point(
        group,
        as_of,
        max_lag_days=30,
    )

    prior = _asof_point(
        group,
        (
            as_of
            - timedelta(
                days=horizon
            )
        ),
        max_lag_days=(
            HORIZONS[
                horizon
            ]
        ),
    )

    if (
        current is None
        or prior is None
        or prior["price"] <= 0
    ):
        return None

    return (
        current["price"]
        / prior["price"]
        - 1.0
    )


def build_grade_market_wrappers(
    *,
    prices=None,
    as_of=None,
):
    """Broad grade-market returns relative to raw."""

    if prices is None:
        prices = (
            fetch_hybrid_price_history()
        )

    if prices.empty:
        return {}

    if as_of is None:
        as_of = prices[
            "recorded_at"
        ].max()

    as_of = _to_datetime(as_of)

    groups = _group_map(
        prices
    )

    card_ids = sorted(
        {
            int(card_id)
            for card_id
            in prices[
                "card_id"
            ].unique()
        }
    )

    output = {}

    for market_type in SUPPORTED_MARKETS:
        output[
            market_type
        ] = {}

        for horizon in HORIZONS:
            wrappers = []

            for card_id in card_ids:
                raw_group = groups.get(
                    (
                        card_id,
                        "RAW",
                    )
                )

                slab_group = groups.get(
                    (
                        card_id,
                        market_type,
                    )
                )

                if (
                    raw_group is None
                    or slab_group is None
                ):
                    continue

                current_raw = _asof_point(
                    raw_group,
                    as_of,
                    max_lag_days=30,
                )

                current_slab = _asof_point(
                    slab_group,
                    as_of,
                    max_lag_days=30,
                )

                prior_target = (
                    as_of
                    - timedelta(
                        days=horizon
                    )
                )

                prior_raw = _asof_point(
                    raw_group,
                    prior_target,
                    max_lag_days=(
                        HORIZONS[
                            horizon
                        ]
                    ),
                )

                prior_slab = _asof_point(
                    slab_group,
                    prior_target,
                    max_lag_days=(
                        HORIZONS[
                            horizon
                        ]
                    ),
                )

                if (
                    current_raw is None
                    or current_slab is None
                    or prior_raw is None
                    or prior_slab is None
                ):
                    continue

                if (
                    current_raw["price"] <= 0
                    or current_slab["price"] <= 0
                    or prior_raw["price"] <= 0
                    or prior_slab["price"] <= 0
                ):
                    continue

                premium_now = (
                    current_slab["price"]
                    / current_raw["price"]
                )

                premium_prior = (
                    prior_slab["price"]
                    / prior_raw["price"]
                )

                if premium_prior <= 0:
                    continue

                wrapper = (
                    premium_now
                    / premium_prior
                    - 1.0
                )

                if math.isfinite(
                    wrapper
                ):
                    wrappers.append(
                        wrapper
                    )

            if wrappers:
                series = pd.Series(
                    wrappers,
                    dtype=float,
                )

                output[
                    market_type
                ][
                    horizon
                ] = {
                    "wrapper_return":
                        float(
                            series.median()
                        ),

                    "n_cards":
                        int(
                            len(
                                series
                            )
                        ),

                    "q25":
                        float(
                            series.quantile(
                                0.25
                            )
                        ),

                    "q75":
                        float(
                            series.quantile(
                                0.75
                            )
                        ),

                    "as_of":
                        as_of,
                }

            else:
                output[
                    market_type
                ][
                    horizon
                ] = {
                    "wrapper_return":
                        None,

                    "n_cards":
                        0,

                    "q25":
                        None,

                    "q75":
                        None,

                    "as_of":
                        as_of,
                }

    return output


def _direct_confidence(
    prices,
    card_id,
    market_type,
    *,
    as_of,
):
    subset = prices[
        (
            prices["card_id"]
            == int(card_id)
        )
        &
        (
            prices["market_type"]
            == str(market_type)
        )
    ].sort_values(
        "recorded_at"
    )

    if subset.empty:
        return "NONE"

    dates = subset[
        "recorded_at"
    ].drop_duplicates()

    count = len(
        dates
    )

    latest_age = (
        as_of.date()
        - dates.iloc[-1].date()
    ).days

    span = (
        dates.iloc[-1]
        - dates.iloc[0]
    ).days

    if (
        count >= 10
        and latest_age <= 30
        and span >= 180
    ):
        return "HIGH"

    if (
        count >= 5
        and latest_age <= 60
        and span >= 90
    ):
        return "MEDIUM"

    if (
        count >= 2
        and latest_age <= 120
    ):
        return "LOW"

    return "NONE"


def synthetic_return(
    raw_return,
    wrapper_return,
):
    raw_return = _number(
        raw_return
    )

    wrapper_return = _number(
        wrapper_return
    )

    if (
        raw_return is None
        or wrapper_return is None
    ):
        return None

    return (
        (
            1.0
            + raw_return
        )
        *
        (
            1.0
            + wrapper_return
        )
        - 1.0
    )


def _blend_returns(
    direct_return,
    synthetic,
    direct_weight,
):
    direct_return = _number(
        direct_return
    )

    synthetic = _number(
        synthetic
    )

    direct_weight = float(
        direct_weight
    )

    if direct_return is None:
        return synthetic

    if synthetic is None:
        return direct_return

    if (
        direct_return <= -1.0
        or synthetic <= -1.0
    ):
        return (
            direct_weight
            * direct_return
            +
            (
                1.0
                - direct_weight
            )
            * synthetic
        )

    return math.expm1(
        (
            direct_weight
            * math.log1p(
                direct_return
            )
        )
        +
        (
            1.0
            - direct_weight
        )
        * math.log1p(
            synthetic
        )
    )



def item_hybrid_outlook(
    item,
    *,
    mode=None,
    prices=None,
    wrappers=None,
):
    """Return a comparable 90D outlook for RAW or graded assets.

    RAW:
        underlying raw trend directly.

    PSA / ACE:
        exact PokemonPriceTracker slab return where available,
        otherwise raw trend + broad provider grade-market wrapper.

    This creates one comparable outlook layer across:
        RAW
        PSA 9
        PSA 10
        ACE 10

    It remains descriptive evidence rather than a validated
    forward-return forecast.
    """

    market_type = str(
        item.get(
            "market_type",
            "RAW",
        )
    ).upper()

    card_id = (
        item.get("product_id")
        if item.get("product_id") is not None
        else item.get("card_id")
    )

    if market_type in SUPPORTED_MARKETS and item.get("printing"):
        # Provider grade histories have no verified finish key. Use only the
        # explicit finish's hydrated RAW evidence + the labelled broad wrapper.
        # DIRECT mode remains unavailable until that mapping is established.
        card_id = None

    # --------------------------------------------------------
    # RAW BASELINE
    # --------------------------------------------------------

    if market_type == "RAW":
        raw_returns = {
            30:
                (
                    _number(
                        item.get(
                            "momentum_30"
                        )
                    )
                    if _number(
                        item.get(
                            "momentum_30"
                        )
                    ) is not None
                    else _number(
                        item.get(
                            "ci_momentum_30"
                        )
                    )
                ),

            90:
                (
                    _number(
                        item.get(
                            "momentum_90"
                        )
                    )
                    if _number(
                        item.get(
                            "momentum_90"
                        )
                    ) is not None
                    else _number(
                        item.get(
                            "ci_momentum_90"
                        )
                    )
                ),

            180:
                _number(
                    item.get(
                        "momentum_180"
                    )
                ),
        }

        raw90 = raw_returns[
            90
        ]

        confidence = (
            "HIGH"
            if raw90 is not None
            else "NONE"
        )

        return {
            "market_type":
                "RAW",

            "market_label":
                "Raw",

            "requested_mode":
                "RAW",

            "effective_source":
                "RAW CARD",

            "direct_confidence":
                confidence,

            "direct_weight":
                1.0,

            "direct_returns":
                raw_returns.copy(),

            "raw_returns":
                raw_returns.copy(),

            "synthetic_returns":
                raw_returns.copy(),

            "final_returns":
                raw_returns.copy(),

            # RAW is the baseline, therefore no
            # grading wrapper is applied.
            "wrapper_90":
                0.0,

            "wrapper_n_90":
                0,

            "confidence":
                confidence,

            "local_value":
                item_local_value(
                    item
                ),

            "as_of":
                None,
        }


    # --------------------------------------------------------
    # GRADED ASSETS
    # --------------------------------------------------------

    if market_type not in SUPPORTED_MARKETS:
        return None

    if prices is None:
        prices = (
            fetch_hybrid_price_history()
        )

    if prices.empty:
        return None

    as_of = _to_datetime(
        prices[
            "recorded_at"
        ].max()
    )

    if wrappers is None:
        wrappers = (
            build_grade_market_wrappers(
                prices=prices,
                as_of=as_of,
            )
        )

    if mode is None:
        mode = item.get(
            "graded_outlook_mode",
            "AUTO",
        )

    mode = str(
        mode
    ).upper()

    groups = _group_map(
        prices
    )

    direct_returns = {}
    raw_returns = {}
    synthetic_returns = {}

    direct_confidence = "NONE"

    if card_id is not None:
        direct_confidence = (
            _direct_confidence(
                prices,
                int(card_id),
                market_type,
                as_of=as_of,
            )
        )

    for horizon in HORIZONS:
        direct_return = None
        raw_return = None

        if card_id is not None:
            direct_return = (
                _history_return(
                    groups,
                    int(card_id),
                    market_type,
                    horizon=horizon,
                    as_of=as_of,
                )
            )

            raw_return = (
                _history_return(
                    groups,
                    int(card_id),
                    "RAW",
                    horizon=horizon,
                    as_of=as_of,
                )
            )

        # If an imported/legacy graded item has no canonical product id,
        # fall back to any raw momentum hydrated directly onto the item.
        if raw_return is None:
            raw_return = _number(
                item.get(
                    f"momentum_{horizon}"
                )
            )

        if (
            raw_return is None
            and horizon in (30, 90)
        ):
            raw_return = _number(
                item.get(
                    f"ci_momentum_{horizon}"
                )
            )

        wrapper_row = (
            wrappers
            .get(
                market_type,
                {}
            )
            .get(
                horizon,
                {}
            )
        )

        wrapper_return = _number(
            wrapper_row.get(
                "wrapper_return"
            )
        )

        direct_returns[
            horizon
        ] = direct_return

        raw_returns[
            horizon
        ] = raw_return

        synthetic_returns[
            horizon
        ] = synthetic_return(
            raw_return,
            wrapper_return,
        )

    direct_weight = (
        DIRECT_WEIGHTS[
            direct_confidence
        ]
    )

    final_returns = {}

    for horizon in HORIZONS:
        direct_return = (
            direct_returns[
                horizon
            ]
        )

        synthetic = (
            synthetic_returns[
                horizon
            ]
        )

        if mode == "DIRECT":
            final = direct_return

        elif (
            mode
            == "RAW_PLUS_MARKET"
        ):
            final = synthetic

        else:
            # AUTO is deliberately direct-first.  The exact card's provider
            # history is stronger evidence than a broad grade-market wrapper.
            # The synthetic RAW + grade-market series is only a fallback when
            # a direct return cannot be formed for this horizon.
            final = (
                direct_return
                if direct_return is not None
                else synthetic
            )

        final_returns[
            horizon
        ] = final

    wrapper90 = (
        wrappers
        .get(
            market_type,
            {}
        )
        .get(
            90,
            {}
        )
    )

    wrapper_n = int(
        wrapper90.get(
            "n_cards",
            0,
        )
        or 0
    )

    raw90 = raw_returns[
        90
    ]
    direct90 = direct_returns[
        90
    ]

    if final_returns[90] is None:
        confidence = "NONE"

    elif mode == "DIRECT":
        confidence = direct_confidence

    elif mode == "AUTO" and direct90 is not None:
        confidence = direct_confidence

    elif market_type == "ACE_10":
        confidence = (
            "MEDIUM"
            if (raw90 is not None and wrapper_n >= 25)
            else "LOW"
        )

    else:
        confidence = (
            "HIGH"
            if (raw90 is not None and wrapper_n >= 100)
            else (
                "MEDIUM"
                if (raw90 is not None and wrapper_n >= 25)
                else "LOW"
            )
        )

    if mode == "AUTO":
        if direct90 is not None:
            effective_source = "DIRECT POKEMONPRICETRACKER"
            effective_direct_weight = 1.0
        else:
            effective_source = "RAW + PROVIDER GRADE FALLBACK"
            effective_direct_weight = 0.0

    elif mode == "DIRECT":
        effective_source = "DIRECT POKEMONPRICETRACKER"
        effective_direct_weight = 1.0 if direct90 is not None else 0.0

    else:
        effective_source = "RAW + PROVIDER GRADE FALLBACK"
        effective_direct_weight = 0.0

    return {
        "market_type":
            market_type,

        "market_label":
            MARKET_LABELS[
                market_type
            ],

        "requested_mode":
            mode,

        "effective_source":
            effective_source,

        "direct_confidence":
            direct_confidence,

        "direct_weight":
            effective_direct_weight,

        "direct_returns":
            direct_returns,

        "raw_returns":
            raw_returns,

        "synthetic_returns":
            synthetic_returns,

        "final_returns":
            final_returns,

        "wrapper_90":
            _number(
                wrapper90.get(
                    "wrapper_return"
                )
            ),

        "wrapper_n_90":
            wrapper_n,

        "confidence":
            confidence,

        "local_value":
            item_local_value(
                item
            ),

        "as_of":
            as_of,
    }



def analyse_hybrid_side(
    items,
    *,
    prices=None,
    wrappers=None,
):
    """Build one comparable outlook across RAW and graded assets."""

    if prices is None:
        prices = (
            fetch_hybrid_price_history()
        )

    if wrappers is None:
        wrappers = (
            build_grade_market_wrappers(
                prices=prices
            )
            if not prices.empty
            else {}
        )

    profiles = []

    total_asset_value = 0.0
    covered_value = 0.0
    numerator = 0.0

    supported = {
        "RAW",
        "PSA_9",
        "PSA_10",
        "ACE_10",
    }

    for item in items:
        market_type = str(
            item.get(
                "market_type",
                "RAW",
            )
        ).upper()

        if market_type not in supported:
            continue

        value = item_local_value(
            item
        )

        total_asset_value += value

        outlook = item_hybrid_outlook(
            item,
            prices=prices,
            wrappers=wrappers,
        )

        profiles.append(
            {
                "item":
                    item,

                "outlook":
                    outlook,
            }
        )

        if outlook is None:
            continue

        final90 = outlook[
            "final_returns"
        ][90]

        if final90 is None:
            continue

        covered_value += value

        numerator += (
            value
            * final90
        )

    return {
        "profiles":
            profiles,

        "weighted_outlook_90":
            (
                numerator
                / covered_value
                if covered_value > 0
                else None
            ),

        "coverage":
            (
                covered_value
                / total_asset_value
                if total_asset_value > 0
                else 0.0
            ),

        "covered_value":
            covered_value,

        "total_asset_value":
            total_asset_value,

        # Backward-compatible name for any existing UI.
        "total_graded_value":
            total_asset_value,
    }


def analyse_hybrid_trade(
    given_items,
    received_items,
    *,
    prices=None,
    wrappers=None,
):
    if prices is None:
        prices = (
            fetch_hybrid_price_history()
        )

    if wrappers is None:
        wrappers = (
            build_grade_market_wrappers(
                prices=prices
            )
        )

    given = analyse_hybrid_side(
        given_items,
        prices=prices,
        wrappers=wrappers,
    )

    received = analyse_hybrid_side(
        received_items,
        prices=prices,
        wrappers=wrappers,
    )

    edge = None

    if (
        given[
            "weighted_outlook_90"
        ] is not None
        and received[
            "weighted_outlook_90"
        ] is not None
    ):
        edge = (
            received[
                "weighted_outlook_90"
            ]
            - given[
                "weighted_outlook_90"
            ]
        )

    return {
        "given":
            given,

        "received":
            received,

        "edge_90":
            edge,

        "common_coverage":
            min(
                given[
                    "coverage"
                ],
                received[
                    "coverage"
                ],
            ),

        "wrappers":
            wrappers,
    }
