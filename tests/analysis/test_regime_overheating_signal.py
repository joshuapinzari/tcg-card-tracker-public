import pytest

from services.analysis.regime_overheating_signal import (
    classify_market_regime,
    regime_overheating_alpha_reference,
)

from services.trading.trade_engine import (
    overheating_alpha_reference,
)


def test_regime_classification():
    assert (
        classify_market_regime(
            -0.023
        )
        == "Weak"
    )

    assert (
        classify_market_regime(
            0.00
        )
        == "Neutral"
    )

    assert (
        classify_market_regime(
            0.105
        )
        == "Strong"
    )


def test_weak_market_has_no_penalty():
    alpha, label = (
        regime_overheating_alpha_reference(
            0.999,
            0.999,
            "Weak",
        )
    )

    assert alpha is None
    assert label is None


def test_neutral_penalty_begins_at_top_five_percent():
    top_10, _ = (
        regime_overheating_alpha_reference(
            0.92,
            0.92,
            "Neutral",
        )
    )

    top_5, _ = (
        regime_overheating_alpha_reference(
            0.96,
            0.96,
            "Neutral",
        )
    )

    assert top_10 is None

    assert top_5 == pytest.approx(
        -0.0362
    )


def test_strong_market_penalty_curve():
    expected = [
        (
            0.85,
            -0.0449,
        ),
        (
            0.92,
            -0.0949,
        ),
        (
            0.96,
            -0.1532,
        ),
        (
            0.98,
            -0.1732,
        ),
        (
            0.995,
            -0.1988,
        ),
    ]

    observed = []

    for percentile, reference in expected:

        alpha, _ = (
            regime_overheating_alpha_reference(
                percentile,
                percentile,
                "Strong",
            )
        )

        assert alpha == pytest.approx(
            reference
        )

        observed.append(
            alpha
        )


    assert observed == sorted(
        observed,
        reverse=True,
    )


def test_trade_engine_preserves_legacy_one_argument_api():
    result = (
        overheating_alpha_reference(
            0.99
        )
    )

    assert isinstance(
        result,
        tuple,
    )

    assert len(
        result
    ) == 2

    assert result[
        0
    ] is not None


def test_trade_engine_regime_aware_api():
    alpha, label = (
        overheating_alpha_reference(
            0.995,
            0.997,
            "Strong",
        )
    )

    assert alpha == pytest.approx(
        -0.1988
    )

    assert label == (
        "Strong · Top 1%"
    )


def test_trade_engine_missing_regime_is_unknown():
    alpha, label = (
        overheating_alpha_reference(
            0.99,
            0.99,
            None,
        )
    )

    assert alpha is None

    assert (
        "unavailable"
        in label.lower()
    )


def test_trade_engine_distinguishes_known_zero_from_missing_signal():
    """
    Production must preserve the distinction between:

        known no-penalty state -> 0.0
        missing signal inputs   -> None
    """

    weak_alpha, weak_label = (
        overheating_alpha_reference(
            0.999,
            0.999,
            "Weak",
        )
    )


    neutral_top10_alpha, _ = (
        overheating_alpha_reference(
            0.92,
            0.92,
            "Neutral",
        )
    )


    missing_alpha, missing_label = (
        overheating_alpha_reference(
            0.99,
            0.99,
            None,
        )
    )


    assert weak_alpha == pytest.approx(
        0.0
    )

    assert (
        "suppressed"
        in weak_label.lower()
    )


    assert neutral_top10_alpha == pytest.approx(
        0.0
    )


    assert missing_alpha is None

    assert (
        "unavailable"
        in missing_label.lower()
    )
