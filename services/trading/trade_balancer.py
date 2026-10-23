"""Authoritative local-value trade balancing."""

from __future__ import annotations

import math


def _number(value, default=None):
    if value is None:
        return default

    try:
        value = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(value):
        return default

    return value


def item_local_value(item: dict) -> float:
    """Total economic value of a line in the user's local market."""

    qty = _number(
        item.get("quantity"),
        1.0,
    )

    qty = max(
        0.0,
        qty or 0.0,
    )

    local_unit = _number(
        item.get("agreed_unit_value")
    )

    if local_unit is not None:
        return max(
            0.0,
            local_unit * qty,
        )

    # Legacy fallback only.
    market = _number(
        item.get("current_market_usd")
    )

    if market is None:
        return 0.0

    return max(
        0.0,
        market * qty,
    )


def side_local_value(items):
    return sum(
        item_local_value(item)
        for item in items
    )


def weighted_metric(
    items,
    field,
    *,
    require_signal=False,
):
    """Value-weight a field without treating missing values as zero."""

    total = sum(
        item_local_value(item)
        for item in items
    )

    covered = 0.0
    numerator = 0.0

    for item in items:
        weight = item_local_value(item)

        if weight <= 0:
            continue

        if (
            require_signal
            and not bool(
                item.get(
                    "signal_available",
                    False,
                )
            )
        ):
            continue

        value = _number(
            item.get(field)
        )

        if value is None:
            continue

        covered += weight
        numerator += weight * value

    return {
        "value":
            (
                numerator / covered
                if covered > 0
                else None
            ),

        "coverage":
            (
                covered / total
                if total > 0
                else 0.0
            ),

        "covered_value":
            covered,

        "total_value":
            total,
    }


def balance_trade(
    given_items,
    received_items,
    *,
    cash_given=0.0,
    cash_received=0.0,
):
    cash_given = max(
        0.0,
        _number(
            cash_given,
            0.0,
        ),
    )

    cash_received = max(
        0.0,
        _number(
            cash_received,
            0.0,
        ),
    )

    give_cards = side_local_value(
        given_items
    )

    receive_cards = side_local_value(
        received_items
    )

    give_total = (
        give_cards
        + cash_given
    )

    receive_total = (
        receive_cards
        + cash_received
    )

    difference = (
        receive_total
        - give_total
    )

    return {
        "given_cards":
            give_cards,

        "received_cards":
            receive_cards,

        "cash_given":
            cash_given,

        "cash_received":
            cash_received,

        "given_total":
            give_total,

        "received_total":
            receive_total,

        "difference":
            difference,

        "difference_pct":
            (
                difference / give_total
                if give_total > 0
                else None
            ),

        "neutral_receive_addition":
            max(
                0.0,
                -difference,
            ),

        "neutral_give_addition":
            max(
                0.0,
                difference,
            ),
    }
