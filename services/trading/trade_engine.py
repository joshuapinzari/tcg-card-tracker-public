"""Forward-looking trade-builder logic.

Local Fair Value is the authoritative economic exposure. A value gap is
reported as part of the trade-off; it does not block signal analysis.

The engine then compares forward-looking card characteristics. Only
historically validated signals are converted into forward-return references.
Provisional signals remain descriptive until validated.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.analysis.regime_overheating_signal import (
    regime_overheating_alpha_reference,
)



# Existing overheating research:
#
# Relative-momentum bucket -> median next-90D set-relative alpha.
#
# These are historical references, NOT forecasts for an individual card.
OVERHEATING_ALPHA_BUCKETS = (
    (0.99, -0.1907, "Top 1%"),
    (0.975, -0.1677, "Top 2.5%"),
    (0.95, -0.1116, "Top 5%"),
    (0.90, -0.0662, "Top 10%"),
    (0.80, -0.0335, "Top 20%"),
)


@dataclass(frozen=True)
class BasketForwardRisk:
    market_value: float
    covered_value: float
    coverage: float
    alpha_reference: float | None
    overheating_value_share: float | None
    clean_recovery_value_share: float | None


def _number(value):
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None




def item_market_value(
    item: dict,
) -> float:
    """Authoritative economic weight = Local Fair Value."""
    from services.trading.trade_balancer import (
        item_local_value,
    )

    return item_local_value(
        item
    )


def _legacy_overheating_alpha_reference(
    percentile: float | None,
) -> tuple[float | None, str]:
    """Return the validated historical overheating-tail reference."""

    percentile = _number(percentile)

    if percentile is None:
        return None, "No signal"

    for (
        threshold,
        alpha,
        label,
    ) in OVERHEATING_ALPHA_BUCKETS:
        if percentile >= threshold:
            return alpha, label

    return (
        None,
        "Below top 20% tail",
    )


# ============================================================
# REGIME-CONDITIONED OVERHEATING V2
# ============================================================

_MISSING_MARKET_PERCENTILE = object()
_MISSING_MARKET_REGIME = object()


def overheating_alpha_reference(
    percentile,
    market_percentile=_MISSING_MARKET_PERCENTILE,
    market_regime=_MISSING_MARKET_REGIME,
):
    """
    Return a historical raw overheating risk reference.

    Backward-compatible research API
    --------------------------------
    Existing code may still call:

        overheating_alpha_reference(percentile)

    That deliberately preserves the original unconditional
    lookup so old tests / notebooks remain reproducible.

    Production V2
    -------------
    Trade analysis calls:

        overheating_alpha_reference(
            set_percentile,
            market_percentile,
            market_regime,
        )

    This uses the validated regime-conditioned DUAL-hot signal.

    Production semantics:

        None
            missing / unknown signal inputs

        0.0
            signal inputs are known, but production applies
            no overheating penalty in this regime/severity

        negative number
            validated regime-conditioned downside reference

    Missing data therefore never silently becomes numeric zero.
    """

    legacy_call = (
        market_percentile
        is _MISSING_MARKET_PERCENTILE
        and market_regime
        is _MISSING_MARKET_REGIME
    )


    if legacy_call:
        return (
            _legacy_overheating_alpha_reference(
                percentile
            )
        )


    if (
        market_percentile
        is _MISSING_MARKET_PERCENTILE
        or market_regime
        is _MISSING_MARKET_REGIME
        or market_percentile is None
        or market_regime is None
    ):
        return (
            None,
            "Regime signal unavailable",
        )


    alpha, label = (
        regime_overheating_alpha_reference(
            percentile,
            market_percentile,
            market_regime,
        )
    )


    if alpha is not None:
        return (
            alpha,
            label,
        )


    regime = str(
        market_regime
    ).strip().title()


    if regime == "Weak":
        # Known regime + known percentiles.
        #
        # Research does not support a negative overheating
        # penalty here, so production deliberately applies
        # a ZERO adjustment.
        #
        # This is different from missing/unknown data, which
        # remains None above.
        return (
            0.0,
            "Weak · overheating penalty suppressed",
        )


    if regime == "Neutral":
        return (
            0.0,
            "Neutral · no penalty below top-5% tail",
        )


    if regime == "Strong":
        return (
            0.0,
            "Strong · no penalty below top-20% tail",
        )


    return (
        None,
        "No validated regime reference",
    )



def card_trade_profile(item: dict) -> dict:
    percentile = _number(item.get('set_signal_percentile'))
    if percentile is None:
        percentile = _number(item.get('ci_signal_percentile'))
    alpha_reference, alpha_bucket = overheating_alpha_reference(percentile, item.get('market_signal_percentile'), item.get('market_regime'))
    clean_recovery = bool(item.get('ci_clean_recovery', False))
    overheated = bool(item.get('ci_top10_overheating', False))
    relative_market = _number(item.get('ci_relative_market_90'))
    momentum_90 = _number(item.get('ci_momentum_90'))
    if overheated:
        setup = 'Overheating risk'
    elif clean_recovery:
        setup = 'Clean recovery'
    elif relative_market is not None and relative_market > 0 and (momentum_90 is not None) and (momentum_90 > 0):
        setup = 'Constructive strength'
    elif relative_market is not None and relative_market < 0:
        setup = 'Lagging market'
    else:
        setup = 'Mixed / neutral'
    return {'side': item.get('side'), 'name': item.get('name'), 'set_name': item.get('set_name'), 'printing': item.get('printing'), 'quantity': int(item.get('quantity', 1) or 1), 'market_value_usd': item_market_value(item), 'momentum_30': _number(item.get('ci_momentum_30')), 'momentum_90': momentum_90, 'relative_set_90': _number(item.get('ci_relative_set_90')), 'relative_market_90': relative_market, 'signal_percentile': percentile, 'alpha_reference': alpha_reference, 'alpha_bucket': alpha_bucket, 'overheated': overheated, 'clean_recovery': clean_recovery, 'setup': setup}




def analyse_basket(
    items: list[dict],
) -> BasketForwardRisk | None:

    profiles = [
        card_trade_profile(
            item
        )
        for item in items
    ]

    market_value = sum(
        profile[
            "market_value_usd"
        ]
        for profile
        in profiles
    )

    if market_value <= 0:
        return None

    covered = [
        profile
        for profile in profiles
        if profile[
            "alpha_reference"
        ] is not None
    ]

    covered_value = sum(
        profile[
            "market_value_usd"
        ]
        for profile
        in covered
    )

    # Known regime/severity states with no validated downside receive
    # an explicit 0.0 penalty. Missing regime/percentile inputs remain
    # uncovered (None) and are excluded from this weighted reference.
    # A zero penalty does NOT mean expected return = 0%.
    alpha_reference = (
        sum(
            profile[
                "market_value_usd"
            ]
            * (
                profile[
                    "alpha_reference"
                ]
            )
            for profile
            in covered
        )
        / covered_value
        if covered_value > 0
        else None
    )

    overheating_share = (
        sum(
            profile[
                "market_value_usd"
            ]
            for profile
            in covered
            if profile[
                "overheated"
            ]
        )
        / covered_value
        if covered_value > 0
        else None
    )

    recovery_share = (
        sum(
            profile[
                "market_value_usd"
            ]
            for profile
            in covered
            if profile[
                "clean_recovery"
            ]
        )
        / covered_value
        if covered_value > 0
        else None
    )

    return BasketForwardRisk(
        market_value=
            market_value,

        covered_value=
            covered_value,

        coverage=(
            covered_value
            / market_value
        ),

        alpha_reference=
            alpha_reference,

        overheating_value_share=
            overheating_share,

        clean_recovery_value_share=
            recovery_share,
    )


def analyse_trade(
    given_items: list[dict],
    received_items: list[dict],
    *,
    value_tolerance: float = 0.03,
) -> dict:

    given = analyse_basket(
        given_items
    )

    received = analyse_basket(
        received_items
    )

    if (
        given is None
        or received is None
    ):
        return {
            "ready": False,
            "reason": (
                "Both sides need positive Local Fair Value."
            ),
        }

    market_value_difference = (
        received.market_value
        - given.market_value
    )

    market_value_edge = (
        market_value_difference
        / given.market_value
        if given.market_value > 0
        else None
    )

    value_neutral = bool(
        market_value_edge
        is not None
        and abs(
            market_value_edge
        )
        <= value_tolerance
    )

    alpha_edge = None

    if (
        given.alpha_reference
        is not None
        and received.alpha_reference
        is not None
    ):
        alpha_edge = (
            received.alpha_reference
            - given.alpha_reference
        )

    common_coverage = min(
        given.coverage,
        received.coverage,
    )

    if alpha_edge is None:
        verdict = (
            "No validated raw tail-risk "
            "edge yet"
        )

    elif alpha_edge >= 0.03:
        verdict = (
            "Receive has lower validated "
            "overheating risk"
        )

    elif alpha_edge <= -0.03:
        verdict = (
            "Give has lower validated "
            "overheating risk"
        )

    else:
        verdict = (
            "Validated forward risk "
            "is similar"
        )

    return {
        "ready":
            True,

        "given":
            given,

        "received":
            received,

        "market_value_difference":
            market_value_difference,

        "market_value_edge":
            market_value_edge,

        # Preferred semantic names. The market_value_* keys are
        # retained for backwards compatibility, but both are based
        # on authoritative Local Fair Value.
        "local_value_difference":
            market_value_difference,

        "local_value_edge":
            market_value_edge,

        "value_tolerance":
            value_tolerance,

        "value_neutral":
            value_neutral,

        "alpha_edge":
            alpha_edge,

        "common_signal_coverage":
            common_coverage,

        "verdict":
            verdict,
    }


def trade_card_rows(
    given_items: list[dict],
    received_items: list[dict],
) -> list[dict]:

    rows = []

    for side, items in (
        (
            "GIVE",
            given_items,
        ),
        (
            "RECEIVE",
            received_items,
        ),
    ):
        for item in items:

            profile = (
                card_trade_profile(
                    item
                )
            )

            rows.append(
                {
                    "Side":
                        side,

                    "Card":
                        profile[
                            "name"
                        ],

                    "Set":
                        profile[
                            "set_name"
                        ],

                    "Market Value USD":
                        profile[
                            "market_value_usd"
                        ],

                    "30D":
                        profile[
                            "momentum_30"
                        ],

                    "90D":
                        profile[
                            "momentum_90"
                        ],

                    "90D vs Set":
                        profile[
                            "relative_set_90"
                        ],

                    "90D vs Market":
                        profile[
                            "relative_market_90"
                        ],

                    "Signal Percentile":
                        profile[
                            "signal_percentile"
                        ],

                    "Historical 90D Alpha Ref":
                        profile[
                            "alpha_reference"
                        ],

                    "Tail Bucket":
                        profile[
                            "alpha_bucket"
                        ],

                    "Setup":
                        profile[
                            "setup"
                        ],
                }
            )

    return rows
