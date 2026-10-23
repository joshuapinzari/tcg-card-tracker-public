from datetime import datetime

import pytest

from database.graded_intelligence import (
    _change_over_days,
    _drawdown_from_high,
    _normalise_price_history,
    _premium_change_over_days,
    _monthly_ratio_median,
    _relative_to_history,
)


def rows(values):
    return [
        (
            price,
            "USD",
            "pokemonpricetracker",
            datetime(
                year,
                month,
                day,
            ),
        )
        for (
            year,
            month,
            day,
            price,
        )
        in values
    ]


def test_change_over_90_days():
    history = (
        _normalise_price_history(
            rows(
                [
                    (
                        2026,
                        7,
                        1,
                        100.0,
                    ),
                    (
                        2026,
                        10,
                        1,
                        120.0,
                    ),
                ]
            )
        )
    )

    assert (
        _change_over_days(
            history,
            90,
        )
        == pytest.approx(
            0.20
        )
    )


def test_drawdown_from_high():
    history = (
        _normalise_price_history(
            rows(
                [
                    (
                        2026,
                        1,
                        1,
                        100.0,
                    ),
                    (
                        2026,
                        5,
                        1,
                        200.0,
                    ),
                    (
                        2026,
                        10,
                        1,
                        150.0,
                    ),
                ]
            )
        )
    )

    assert (
        _drawdown_from_high(
            history
        )
        == pytest.approx(
            -0.25
        )
    )


def test_premium_change():
    slab = (
        _normalise_price_history(
            rows(
                [
                    (
                        2026,
                        7,
                        1,
                        200.0,
                    ),
                    (
                        2026,
                        10,
                        1,
                        300.0,
                    ),
                ]
            )
        )
    )

    raw = (
        _normalise_price_history(
            rows(
                [
                    (
                        2026,
                        7,
                        1,
                        100.0,
                    ),
                    (
                        2026,
                        10,
                        1,
                        100.0,
                    ),
                ]
            )
        )
    )

    assert (
        _premium_change_over_days(
            slab,
            raw,
            90,
        )
        == pytest.approx(
            0.50
        )
    )



def test_monthly_ratio_median_and_own_history_position():
    slab = _normalise_price_history(
        rows(
            [
                (2026, 5, 1, 60.0),
                (2026, 5, 15, 66.0),
                (2026, 6, 1, 50.0),
                (2026, 7, 1, 55.0),
            ]
        )
    )
    psa = _normalise_price_history(
        rows(
            [
                (2026, 5, 1, 100.0),
                (2026, 6, 1, 100.0),
                (2026, 7, 1, 100.0),
            ]
        )
    )

    historical_median, months = _monthly_ratio_median(slab, psa)

    # May monthly slab median is 63, then 50 and 55 -> ratios .63, .50, .55.
    assert historical_median == pytest.approx(0.55)
    assert months == 3
    assert _relative_to_history(0.44, historical_median) == pytest.approx(-0.20)
