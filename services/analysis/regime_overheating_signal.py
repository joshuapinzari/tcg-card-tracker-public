"""
Regime-conditioned raw overheating signal.

Research basis
--------------
Signal:
    dual_percentile =
        min(
            set-relative 90D percentile,
            market-wide raw 90D percentile,
        )

Target:
    next-90D card return relative to its set.

Regimes:
    Weak:
        market median 90D <= -2.3%

    Neutral:
        -2.3% < market median 90D < +10.5%

    Strong:
        market median 90D >= +10.5%

Production philosophy
---------------------
This module represents validated historical downside references.

It is NOT a full expected-return model.

Positive historical results do NOT create positive bonuses.

If a regime/severity cell did not show sufficiently robust negative
evidence, the result is None rather than zero.

Why None matters:
    None = no validated penalty reference.

    0.0 would mean we had validated a numeric zero effect, which
    is not what the research established.

Strong-market caveat
--------------------
The Strong validation window contains many weekly formation dates,
but they belong to one contiguous strong-market episode.

Therefore these references are useful risk calibration, but should
not be described as precise universal return forecasts.
"""

from __future__ import annotations

import math


REGIME_WEAK_THRESHOLD = -0.023
REGIME_STRONG_THRESHOLD = 0.105


# Median next-90D set-relative alpha across staggered
# approximately non-overlapping validation schedules.
#
# IMPORTANT:
# - Only robust NEGATIVE cells are included.
# - Weak-market positive cells are deliberately excluded.
# - Neutral top-20/top-10 cells are deliberately excluded.
REGIME_OVERHEATING_REFERENCES = {
    "Weak": (),

    "Neutral": (
        (
            0.99,
            -0.1223,
            "Top 1%",
        ),
        (
            0.975,
            -0.0718,
            "Top 2.5%",
        ),
        (
            0.95,
            -0.0362,
            "Top 5%",
        ),
    ),

    "Strong": (
        (
            0.99,
            -0.1988,
            "Top 1%",
        ),
        (
            0.975,
            -0.1732,
            "Top 2.5%",
        ),
        (
            0.95,
            -0.1532,
            "Top 5%",
        ),
        (
            0.90,
            -0.0949,
            "Top 10%",
        ),
        (
            0.80,
            -0.0449,
            "Top 20%",
        ),
    ),
}


def _number(
    value,
):
    if value is None:
        return None

    try:
        value = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if not math.isfinite(
        value
    ):
        return None

    return value


def normalise_regime(
    market_regime,
):
    if market_regime is None:
        return None

    value = str(
        market_regime
    ).strip().lower()

    mapping = {
        "weak":
            "Weak",

        "neutral":
            "Neutral",

        "strong":
            "Strong",
    }

    return mapping.get(
        value
    )


def classify_market_regime(
    market_return_90,
):
    value = _number(
        market_return_90
    )

    if value is None:
        return None

    if (
        value
        <= REGIME_WEAK_THRESHOLD
    ):
        return "Weak"

    if (
        value
        >= REGIME_STRONG_THRESHOLD
    ):
        return "Strong"

    return "Neutral"


def dual_percentile(
    set_percentile,
    market_percentile,
):
    set_value = _number(
        set_percentile
    )

    market_value = _number(
        market_percentile
    )

    if (
        set_value is None
        or market_value is None
    ):
        return None

    return min(
        set_value,
        market_value,
    )


def regime_overheating_alpha_reference(
    set_percentile,
    market_percentile,
    market_regime,
):
    """
    Return:

        (
            historical_alpha_reference,
            descriptive_label,
        )

    or:

        (None, None)

    when there is no validated negative penalty for the current
    regime/severity combination.
    """

    regime = normalise_regime(
        market_regime
    )

    if regime is None:
        return (
            None,
            None,
        )


    dual = dual_percentile(
        set_percentile,
        market_percentile,
    )

    if dual is None:
        return (
            None,
            None,
        )


    references = (
        REGIME_OVERHEATING_REFERENCES[
            regime
        ]
    )


    for (
        threshold,
        alpha,
        severity,
    ) in references:

        if dual >= threshold:
            return (
                alpha,
                f"{regime} · {severity}",
            )


    return (
        None,
        None,
    )


def regime_overheating_profile(
    set_percentile,
    market_percentile,
    market_regime,
):
    regime = normalise_regime(
        market_regime
    )

    dual = dual_percentile(
        set_percentile,
        market_percentile,
    )

    (
        alpha,
        label,
    ) = (
        regime_overheating_alpha_reference(
            set_percentile,
            market_percentile,
            market_regime,
        )
    )


    if regime == "Weak":
        evidence = (
            "NO VALIDATED PENALTY"
        )

    elif alpha is None:
        evidence = (
            "OUTSIDE VALIDATED PENALTY TAIL"
        )

    else:
        evidence = (
            "VALIDATED NEGATIVE REFERENCE"
        )


    return {
        "market_regime":
            regime,

        "dual_percentile":
            dual,

        "alpha_reference":
            alpha,

        "label":
            label,

        "evidence":
            evidence,
    }
