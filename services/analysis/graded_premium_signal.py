"""Live PokemonPriceTracker premium-positioning signal for graded cards.

The live percentile uses exact-ID RAW and slab histories from the same provider.
Old provider-specific forward-return calibrations are intentionally not reused.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from database.operations import DATABASE_NAME


SUPPORTED_MARKETS = (
    "PSA_9",
    "PSA_10",
    "ACE_10",
)


MARKET_LABELS = {
    "PSA_9": "PSA 9",
    "PSA_10": "PSA 10",
    "ACE_10": "ACE 10",
}


# The former provider-specific forward-return calibration constants were removed.
# PokemonPriceTracker Premium Heat is deliberately a live positioning signal
# until enough provider-native history exists to re-calibrate forward returns.


def _heat_bucket(percentile):
    if percentile is None:
        return None

    if pd.isna(percentile):
        return None

    value = float(percentile)

    if value <= 0.20:
        return "COOL"

    if value <= 0.40:
        return "BELOW AVG"

    if value <= 0.60:
        return "NEUTRAL"

    if value <= 0.80:
        return "HOT"

    return "VERY HOT"


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

    value = pd.Timestamp(value)

    if value.tzinfo is not None:
        value = value.tz_convert(
            None
        )

    return value.to_pydatetime()


def fetch_live_price_points() -> pd.DataFrame:
    """Fetch exact-ID PokemonPriceTracker RAW/graded history.

    ``card_id`` is retained as the internal dataframe column name for backwards
    compatibility, but its value is the canonical TCGPlayer ``product_id``.
    """
    import psycopg
    with psycopg.connect(dbname=DATABASE_NAME) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
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
                ORDER BY product_id, market_type, observed_on;
                """
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
    group: pd.DataFrame,
    target: datetime,
    *,
    max_lag_days: int,
):
    eligible = group[
        group["recorded_at"]
        <= target
    ]

    if eligible.empty:
        return None

    row = eligible.iloc[-1]

    lag_days = (
        target.date()
        - row["recorded_at"].date()
    ).days

    if lag_days > max_lag_days:
        return None

    return {
        "date":
            row["recorded_at"],

        "price":
            float(
                row["price"]
            ),

        "source":
            str(
                row["source"]
            ),

        "lag_days":
            int(
                lag_days
            ),
    }


def build_live_premium_heat_universe(
    *,
    as_of=None,
) -> pd.DataFrame:
    """Build live cross-sectional Premium Heat.

    Research definition:

    1. Calculate current slab/raw premium.
    2. Calculate slab/raw premium approximately 90 days ago.
    3. Calculate 90D premium change.
    4. Cross-sectionally rank current premium.
    5. Cross-sectionally rank 90D premium change.
    6. Average those two ranks.
    7. Re-rank the composite.
    8. Convert final percentile into quintile buckets.
    """

    prices = fetch_live_price_points()

    if prices.empty:
        return pd.DataFrame()

    if as_of is None:
        as_of = prices[
            "recorded_at"
        ].max()

    as_of = _to_datetime(
        as_of
    )

    prior_target = (
        as_of
        - timedelta(
            days=90
        )
    )

    grouped = {
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

    card_ids = sorted(
        {
            int(value)
            for value
            in prices[
                "card_id"
            ].unique()
        }
    )

    rows = []

    for market_type in SUPPORTED_MARKETS:
        for card_id in card_ids:
            raw_group = grouped.get(
                (
                    card_id,
                    "RAW",
                )
            )

            slab_group = grouped.get(
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

            prior_raw = _asof_point(
                raw_group,
                prior_target,
                max_lag_days=62,
            )

            prior_slab = _asof_point(
                slab_group,
                prior_target,
                max_lag_days=62,
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

            premium_change_90 = (
                premium_now
                / premium_prior
                - 1.0
            )

            rows.append(
                {
                    "card_id":
                        card_id,

                    "market_type":
                        market_type,

                    "market_label":
                        MARKET_LABELS[
                            market_type
                        ],

                    "as_of":
                        as_of,

                    "slab_price":
                        current_slab[
                            "price"
                        ],

                    "raw_price":
                        current_raw[
                            "price"
                        ],

                    "premium_vs_raw":
                        premium_now,

                    "premium_90d_ago":
                        premium_prior,

                    "premium_change_90":
                        premium_change_90,

                    "slab_price_date":
                        current_slab[
                            "date"
                        ],

                    "raw_price_date":
                        current_raw[
                            "date"
                        ],
                }
            )

    frame = pd.DataFrame(
        rows
    )

    if frame.empty:
        return frame

    frame[
        "premium_level_rank"
    ] = (
        frame.groupby(
            "market_type"
        )[
            "premium_vs_raw"
        ]
        .rank(
            pct=True,
            method="average",
        )
    )

    frame[
        "premium_change_rank"
    ] = (
        frame.groupby(
            "market_type"
        )[
            "premium_change_90"
        ]
        .rank(
            pct=True,
            method="average",
        )
    )

    frame[
        "premium_heat_raw"
    ] = (
        frame[
            [
                "premium_level_rank",
                "premium_change_rank",
            ]
        ]
        .mean(
            axis=1
        )
    )

    # Research re-ranked the combined signal.
    # Do the same live.
    frame[
        "premium_heat_percentile"
    ] = (
        frame.groupby(
            "market_type"
        )[
            "premium_heat_raw"
        ]
        .rank(
            pct=True,
            method="average",
        )
    )

    frame[
        "premium_heat_bucket"
    ] = (
        frame[
            "premium_heat_percentile"
        ]
        .apply(
            _heat_bucket
        )
    )

    # The previous numeric bucket-return references were calibrated on the
    # retired provider history.  Do not silently carry those coefficients
    # across to a different provider.  The live percentile itself is valid; a
    # forward-return calibration can be rebuilt later on this exact dataset.
    frame["historical_relative_reference"] = None
    frame["signal_confidence"] = frame["market_type"].map(
        {"PSA_9": "MEDIUM", "PSA_10": "MEDIUM", "ACE_10": "MEDIUM"}
    )
    frame["evidence_status"] = "POKEMONPRICETRACKER LIVE"

    frame[
        "ace_very_hot_warning"
    ] = (
        (
            frame[
                "market_type"
            ] == "ACE_10"
        )
        &
        (
            frame[
                "premium_heat_bucket"
            ] == "VERY HOT"
        )
    )

    return frame.sort_values(
        [
            "market_type",
            "premium_heat_percentile",
        ]
    ).reset_index(
        drop=True
    )


def get_live_premium_heat(
    card_id: int,
    market_type: str,
    *,
    universe=None,
):
    market_type = str(
        market_type
    ).upper()

    if market_type not in SUPPORTED_MARKETS:
        return None

    if universe is None:
        universe = (
            build_live_premium_heat_universe()
        )

    if universe.empty:
        return None

    match = universe[
        (
            universe[
                "card_id"
            ] == int(
                card_id
            )
        )
        &
        (
            universe[
                "market_type"
            ] == market_type
        )
    ]

    if match.empty:
        return None

    row = match.iloc[0]

    reference = row[
        "historical_relative_reference"
    ]

    return {
        "card_id":
            int(
                row[
                    "card_id"
                ]
            ),

        "market_type":
            str(
                row[
                    "market_type"
                ]
            ),

        "market_label":
            str(
                row[
                    "market_label"
                ]
            ),

        "as_of":
            row[
                "as_of"
            ],

        "premium_vs_raw":
            float(
                row[
                    "premium_vs_raw"
                ]
            ),

        "premium_change_90":
            float(
                row[
                    "premium_change_90"
                ]
            ),

        "premium_level_rank":
            float(
                row[
                    "premium_level_rank"
                ]
            ),

        "premium_change_rank":
            float(
                row[
                    "premium_change_rank"
                ]
            ),

        "premium_heat_percentile":
            float(
                row[
                    "premium_heat_percentile"
                ]
            ),

        "premium_heat_bucket":
            str(
                row[
                    "premium_heat_bucket"
                ]
            ),

        "historical_relative_reference":
            (
                None
                if pd.isna(
                    reference
                )
                else float(
                    reference
                )
            ),

        "signal_confidence":
            str(
                row[
                    "signal_confidence"
                ]
            ),

        "evidence_status":
            str(
                row[
                    "evidence_status"
                ]
            ),

        "ace_very_hot_warning":
            bool(
                row[
                    "ace_very_hot_warning"
                ]
            ),

        "ace_very_hot_research_reference": None,
    }
