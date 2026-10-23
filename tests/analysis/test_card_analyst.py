from services.analysis.card_analyst import build_card_assessment, build_graded_market_rows


def _base_intelligence():
    return {
        "card_momentum": {30: 0.10, 90: 0.22},
        "relative_to_set": {30: 0.05, 90: 0.12},
        "relative_to_market": {30: 0.07, 90: 0.15},
        "card_trend_state": "Bullish",
        "distance_ma30": 0.05,
        "distance_ma90": 0.12,
        "drawdown_from_1y_high": -0.08,
        "recovery": {"clean_recovery": False},
        "set_trend_state": "Bullish",
        "era_trend_state": "Bullish",
        "live_overheating": {"signal_percentile": 0.70},
        "market_structure": {90: {"investable_market": {"card_percentile": 0.72}}},
    }


def test_strong_card_scores_green_without_overheat_penalty():
    result = build_card_assessment(
        _base_intelligence(),
        analogue={
            "setup_label": "Heading Up",
            "expected_set_alpha_90d": 0.09,
            "probability_strong_gain_90d": 0.60,
            "probability_crash_90d": 0.05,
            "analogue_quality": "HIGH",
            "analog_count": 200,
            "similarity": 0.70,
        },
        market_regime={"regime": "Neutral"},
        raw_observations=200,
        raw_date="2026-10-14",
    )
    assert result["score"] >= 72
    assert result["verdict"] == "VERY STRONG"
    assert result["confidence"] == "HIGH"
    tail = next(row for row in result["factors"] if row["factor"] == "Validated overheating risk")
    assert tail["rating"] == 0


def test_overheated_strong_regime_card_gets_validated_penalty():
    intel = _base_intelligence()
    intel["live_overheating"] = {"signal_percentile": 0.995}
    intel["market_structure"][90]["investable_market"]["card_percentile"] = 0.995
    result = build_card_assessment(
        intel,
        analogue=None,
        market_regime={"regime": "Strong"},
        raw_observations=200,
        raw_date="2026-10-14",
    )
    tail = next(row for row in result["factors"] if row["factor"] == "Validated overheating risk")
    assert tail["rating"] == -2.0
    assert tail["impact"] < 0


def test_missing_analogue_is_excluded_not_scored_negative():
    result = build_card_assessment(
        _base_intelligence(),
        analogue=None,
        market_regime={"regime": "Neutral"},
        raw_observations=120,
        raw_date="2026-10-14",
    )
    analogue = result["factors"][0]
    assert analogue["assessment"] == "NO DATA"
    assert analogue["impact"] is None
    assert result["coverage"] < 1.0


def test_bearish_card_can_be_weak_or_avoid():
    intel = _base_intelligence()
    intel.update(
        {
            "card_momentum": {30: -0.12, 90: -0.25},
            "relative_to_set": {30: -0.08, 90: -0.15},
            "relative_to_market": {30: -0.10, 90: -0.18},
            "card_trend_state": "Bearish",
            "drawdown_from_1y_high": -0.45,
            "set_trend_state": "Bearish",
            "era_trend_state": "Bearish",
        }
    )
    result = build_card_assessment(
        intel,
        analogue={
            "setup_label": "Suspected Low",
            "expected_set_alpha_90d": -0.05,
            "probability_strong_gain_90d": 0.25,
            "probability_crash_90d": 0.30,
            "analogue_quality": "MEDIUM",
            "analog_count": 120,
            "similarity": 0.50,
        },
        market_regime={"regime": "Neutral"},
        raw_observations=180,
        raw_date="2026-10-14",
    )
    assert result["verdict"] in {"WEAK", "AVOID"}
    assert result["score"] < 45


def test_graded_market_rows_treat_cheap_premium_as_market_relative_value_only():
    rows = build_graded_market_rows(
        {
            "PSA_10": {
                "market_label": "PSA 10",
                "current_price": 500,
                "return_30": 0.05,
                "return_90": 0.10,
                "drawdown_from_high": -0.10,
                "premium_vs_raw": 3.0,
                "premium_vs_raw_vs_history": -0.20,
                "sales_90": 12,
                "confidence": "HIGH",
            }
        }
    )
    assert rows[0]["relative_value_read"] == "CHEAP VS OWN HISTORY"
    assert rows[0]["relative_value_status"] == "VERY POSITIVE"
