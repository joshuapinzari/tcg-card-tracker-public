from __future__ import annotations

import pandas as pd

from services.analysis.opportunity_model import (
    DEFAULT_CONTEXT_WEIGHT,
    add_setup_flags,
    calibrate_one,
    classify_setups,
)


def _base(**overrides):
    row = {
        "return_30d": 0.08,
        "return_90d": 0.18,
        "return_180d": 0.25,
        "acceleration_30_vs_90": 0.02,
        "relative_set_30d": 0.04,
        "relative_set_90d": 0.06,
        "relative_era_30d": 0.03,
        "relative_era_90d": 0.05,
        "relative_market_30d": 0.05,
        "relative_market_90d": 0.08,
        "drawdown_1y": -0.10,
        "prior_runup": 0.25,
        "correction": -0.12,
        "recovery_30d": 0.08,
        "set_return_30d": 0.04,
        "set_return_90d": 0.12,
        "era_return_30d": 0.05,
        "era_return_90d": 0.13,
        "market_return_30d": 0.02,
        "market_return_90d": 0.08,
        "set_breadth": 0.60,
        "era_breadth": 0.62,
        "market_breadth": 0.58,
    }
    row.update(overrides)
    return row


def _outcomes(**overrides):
    row = {
        "future_return_90d": 0.20,
        "future_set_alpha_90d": 0.10,
        "future_era_alpha_90d": 0.08,
        "future_market_alpha_90d": 0.12,
    }
    row.update(overrides)
    return row


def test_heading_up_and_strong_era_are_detected():
    setups = classify_setups(_base())
    assert "HEADING_UP" in setups
    assert "ERA_STRENGTH" in setups


def test_recovery_requires_prior_runup_correction_and_reacceleration():
    setups = classify_setups(_base(drawdown_1y=-0.20))
    assert "RECOVERY" in setups

    broken = classify_setups(_base(prior_runup=0.05, drawdown_1y=-0.20))
    assert "RECOVERY" not in broken


def test_suspected_low_requires_deep_drawdown_and_improving_relative_action():
    row = _base(
        return_30d=0.01,
        return_90d=-0.18,
        acceleration_30_vs_90=0.07,
        drawdown_1y=-0.35,
        relative_set_30d=0.04,
        relative_market_30d=0.03,
    )
    assert "SUSPECTED_LOW" in classify_setups(row)


def test_analogue_calibration_uses_forward_outcomes_and_context():
    rows = []
    for i in range(100):
        row = _base()
        row.update(
            _outcomes(
                future_return_90d=0.20 if i < 70 else -0.05,
                future_set_alpha_90d=0.10 if i < 70 else -0.03,
                future_era_alpha_90d=0.08 if i < 70 else -0.02,
                future_market_alpha_90d=0.12 if i < 70 else -0.04,
            )
        )
        rows.append(row)
    history = add_setup_flags(pd.DataFrame(rows))
    current = pd.Series(_base())
    result = calibrate_one(
        current,
        history,
        "HEADING_UP",
        max_analogs=100,
        context_weight=DEFAULT_CONTEXT_WEIGHT,
    )
    assert result is not None
    assert result["analog_count"] == 100
    assert result["probability_strong_gain_90d"] > 0.65
    assert result["expected_set_alpha_90d"] > 0
    assert result["expected_era_alpha_90d"] > 0
    assert result["expected_market_alpha_90d"] > 0
    assert 0 < result["context_similarity"] <= 1


def test_context_matching_prefers_similar_market_regime():
    rows = []
    # Same card-state features, but two different market regimes with opposite outcomes.
    for _ in range(70):
        supportive = _base(
            set_return_30d=0.08,
            set_return_90d=0.18,
            era_return_30d=0.07,
            era_return_90d=0.17,
            market_return_30d=0.05,
            market_return_90d=0.12,
            set_breadth=0.70,
            era_breadth=0.68,
            market_breadth=0.64,
        )
        supportive.update(_outcomes(future_set_alpha_90d=0.14))
        rows.append(supportive)
    for _ in range(70):
        weak = _base(
            set_return_30d=-0.08,
            set_return_90d=-0.15,
            era_return_30d=-0.07,
            era_return_90d=-0.14,
            market_return_30d=-0.06,
            market_return_90d=-0.10,
            set_breadth=0.30,
            era_breadth=0.32,
            market_breadth=0.35,
        )
        weak.update(_outcomes(future_set_alpha_90d=-0.10))
        rows.append(weak)

    history = add_setup_flags(pd.DataFrame(rows))
    current = pd.Series(
        _base(
            set_return_30d=0.07,
            set_return_90d=0.17,
            era_return_30d=0.06,
            era_return_90d=0.16,
            market_return_30d=0.04,
            market_return_90d=0.11,
            set_breadth=0.68,
            era_breadth=0.66,
            market_breadth=0.62,
        )
    )
    no_context = calibrate_one(current, history, "HEADING_UP", max_analogs=80, context_weight=0.0)
    with_context = calibrate_one(current, history, "HEADING_UP", max_analogs=80, context_weight=0.50)
    assert no_context is not None and with_context is not None
    assert with_context["expected_set_alpha_90d"] > no_context["expected_set_alpha_90d"]
