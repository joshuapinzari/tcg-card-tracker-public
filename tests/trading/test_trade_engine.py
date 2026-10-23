import pytest

from services.trading.trade_engine import (
    analyse_trade,
    card_trade_profile,
    overheating_alpha_reference,
)


def _item(
    *,
    side="GIVEN",
    price=100.0,
    percentile=0.5,
    momentum_90=0.10,
    relative_market=0.05,
    overheating=False,
    recovery=False,
):
    return {
        "side":
            side,

        "name":
            "Test Card",

        "set_name":
            "Test Set",

        "printing":
            "Holofoil",

        "quantity":
            1,

        "current_market_usd":
            price,

        "ci_momentum_30":
            0.04,

        "ci_momentum_90":
            momentum_90,

        "ci_relative_set_90":
            0.03,

        "ci_relative_market_90":
            relative_market,

        "ci_signal_percentile":
            percentile,

        "ci_top10_overheating":
            overheating,

        "ci_clean_recovery":
            recovery,
    }


def test_overheating_alpha_reference():
    alpha, label = (
        overheating_alpha_reference(
            0.96
        )
    )

    assert alpha == pytest.approx(
        -0.1116
    )

    assert label == "Top 5%"

    alpha, label = (
        overheating_alpha_reference(
            0.75
        )
    )

    assert alpha is None

    assert (
        label
        == "Below top 20% tail"
    )


def test_clean_recovery_profile():
    profile = (
        card_trade_profile(
            _item(
                percentile=0.70,
                recovery=True,
            )
        )
    )

    assert (
        profile["setup"]
        == "Clean recovery"
    )

    assert (
        profile[
            "alpha_reference"
        ]
        is None
    )



def test_value_neutral_trade_can_have_forward_risk_edge():
    """
    Production V2 uses DUAL overheating severity conditioned
    on the market regime.

    In a Strong market:

        GIVEN
            dual top 5%
            historical reference = -15.32%

        RECEIVED
            dual top 10%
            historical reference = -9.49%

    Therefore RECEIVE has the less-negative validated
    forward-risk reference by:

        -9.49% - (-15.32%) = +5.83pp
    """

    given_item = _item(
        side="GIVEN",
        price=100.0,
        percentile=0.95,
        overheating=True,
    )

    given_item[
        "market_signal_percentile"
    ] = 0.95

    given_item[
        "market_regime"
    ] = "Strong"


    received_item = _item(
        side="RECEIVED",
        price=101.0,
        percentile=0.90,
        overheating=True,
    )

    received_item[
        "market_signal_percentile"
    ] = 0.90

    received_item[
        "market_regime"
    ] = "Strong"


    result = analyse_trade(
        [
            given_item
        ],
        [
            received_item
        ],
        value_tolerance=0.03,
    )


    assert (
        result[
            "ready"
        ]
        is True
    )


    assert (
        result[
            "value_neutral"
        ]
        is True
    )


    assert result[
        "given"
    ].alpha_reference == pytest.approx(
        -0.1532
    )


    assert result[
        "received"
    ].alpha_reference == pytest.approx(
        -0.0949
    )


    assert result[
        "alpha_edge"
    ] == pytest.approx(
        -0.0949
        - (
            -0.1532
        )
    )



def test_large_value_gap_does_not_block_signal_analysis():
    result = analyse_trade(
        [
            _item(
                price=100.0
            )
        ],
        [
            _item(
                side="RECEIVED",
                price=90.0,
            )
        ],
        value_tolerance=0.03,
    )

    assert (
        result[
            "value_neutral"
        ]
        is False
    )

    assert (
        result["verdict"]
        == "No validated raw tail-risk edge yet"
    )

    assert result[
        "local_value_edge"
    ] == pytest.approx(
        -0.10
    )



def test_weak_market_does_not_penalise_dual_hot_trade_items():
    """
    Weak-market research does not support an overheating penalty.

    Because the regime and both percentiles ARE known, production
    represents the applied risk adjustment as numeric 0.0.

    This is deliberately different from missing inputs, which
    remain None.
    """

    given_item = _item(
        side="GIVEN",
        price=100.0,
        percentile=0.995,
        overheating=True,
    )

    given_item[
        "market_signal_percentile"
    ] = 0.997

    given_item[
        "market_regime"
    ] = "Weak"


    received_item = _item(
        side="RECEIVED",
        price=100.0,
        percentile=0.80,
        overheating=False,
    )

    received_item[
        "market_signal_percentile"
    ] = 0.80

    received_item[
        "market_regime"
    ] = "Weak"


    result = analyse_trade(
        [
            given_item
        ],
        [
            received_item
        ],
        value_tolerance=0.03,
    )


    # Known signal, deliberately zero production penalty.
    assert result[
        "given"
    ].alpha_reference == pytest.approx(
        0.0
    )


    assert result[
        "received"
    ].alpha_reference == pytest.approx(
        0.0
    )


    assert result[
        "given"
    ].coverage == pytest.approx(
        1.0
    )


    assert result[
        "received"
    ].coverage == pytest.approx(
        1.0
    )


    # Neither side gets a fake advantage.
    assert result[
        "alpha_edge"
    ] == pytest.approx(
        0.0
    )


def test_production_prefers_explicit_set_signal_percentile():
    """
    Regime V2 was researched using set-relative percentile.

    Production must therefore prefer set_signal_percentile
    when it is available, while retaining ci_signal_percentile
    only as a backwards-compatible fallback.
    """

    item = _item(
        side="GIVEN",
        price=100.0,
        percentile=0.20,
        overheating=True,
    )

    # Legacy field says ordinary.
    item[
        "ci_signal_percentile"
    ] = 0.20

    # Explicit V2 set-relative percentile says Top 5%.
    item[
        "set_signal_percentile"
    ] = 0.96

    item[
        "market_signal_percentile"
    ] = 0.97

    item[
        "market_regime"
    ] = "Strong"


    from services.trading.trade_engine import (
        card_trade_profile,
    )


    profile = card_trade_profile(
        item
    )


    assert profile[
        "signal_percentile"
    ] == pytest.approx(
        0.96
    )


    assert profile[
        "alpha_reference"
    ] == pytest.approx(
        -0.1532
    )


def test_missing_regime_is_not_counted_as_zero_penalty_coverage():
    item = _item(
        price=100.0,
        percentile=0.99,
        overheating=True,
    )

    # Set percentile exists, but production V2 also requires the
    # market percentile and regime. Missing inputs must remain UNKNOWN.
    item["set_signal_percentile"] = 0.99

    result = analyse_trade(
        [item],
        [dict(item, side="RECEIVED")],
    )

    assert result["given"].alpha_reference is None
    assert result["received"].alpha_reference is None
    assert result["given"].coverage == pytest.approx(0.0)
    assert result["received"].coverage == pytest.approx(0.0)
    assert result["alpha_edge"] is None
