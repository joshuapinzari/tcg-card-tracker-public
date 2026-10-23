import pytest

from services.trading.trade_balancer import (
    balance_trade,
    item_local_value,
    weighted_metric,
)


def test_local_fair_value_is_authoritative():
    item = {
        "quantity":
            2,

        "agreed_unit_value":
            80,

        "current_market_usd":
            100,
    }

    assert item_local_value(
        item
    ) == 160


def test_cash_balances_trade():
    result = balance_trade(
        [
            {
                "quantity":
                    1,

                "agreed_unit_value":
                    100,
            }
        ],
        [
            {
                "quantity":
                    1,

                "agreed_unit_value":
                    80,
            }
        ],
        cash_received=20,
    )

    assert result[
        "difference"
    ] == pytest.approx(
        0
    )


def test_missing_is_not_zero():
    items = [
        {
            "quantity":
                1,

            "agreed_unit_value":
                100,

            "momentum_90":
                None,
        },
        {
            "quantity":
                1,

            "agreed_unit_value":
                100,

            "momentum_90":
                0.20,
        },
    ]

    result = weighted_metric(
        items,
        "momentum_90",
    )

    assert result[
        "value"
    ] == pytest.approx(
        0.20
    )

    assert result[
        "coverage"
    ] == pytest.approx(
        0.50
    )


def test_real_zero_is_valid():
    items = [
        {
            "quantity":
                1,

            "agreed_unit_value":
                100,

            "momentum_90":
                0.0,
        },
        {
            "quantity":
                1,

            "agreed_unit_value":
                100,

            "momentum_90":
                0.20,
        },
    ]

    result = weighted_metric(
        items,
        "momentum_90",
    )

    assert result[
        "value"
    ] == pytest.approx(
        0.10
    )
