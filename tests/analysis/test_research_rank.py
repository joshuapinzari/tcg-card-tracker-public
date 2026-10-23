from services.analysis.research_rank import (
    LOCKED_ANALOGS,
    LOCKED_WEIGHTS,
    score_locked_research_model,
)


def _base_row(**overrides):
    row = {
        "return_30d": 0.08,
        "return_90d": 0.20,
        "drawdown_1y": -0.12,
        "relative_set_30d": 0.04,
        "relative_market_30d": 0.03,
        "prior_runup": 0.15,
        "correction": -0.05,
        "signal_percentile": 0.70,
        "market_percentile": 0.70,
        "prior_runup_percentile": 0.25,
    }
    row.update(overrides)
    return row


def _good_analogue():
    return {
        "analog_count": 150,
        "analogue_quality": "HIGH",
        "expected_set_alpha_90d": 0.10,
        "probability_strong_gain_90d": 0.60,
        "probability_crash_90d": 0.05,
    }


def test_locked_weights_sum_to_100_and_analogs_are_frozen():
    assert sum(LOCKED_WEIGHTS.values()) == 100.0
    assert LOCKED_ANALOGS == 150


def test_strong_card_scores_above_neutral():
    result = score_locked_research_model(
        _base_row(),
        analogue=_good_analogue(),
        market_regime="Neutral",
    )
    assert result["research_score"] > 60
    assert result["research_coverage"] == 1.0
    assert result["research_ranking_score"] == result["research_score"]


def test_large_prior_runup_is_penalised_relative_to_low_prior_runup():
    low = score_locked_research_model(
        _base_row(prior_runup_percentile=0.10),
        analogue=_good_analogue(),
        market_regime="Neutral",
    )
    high = score_locked_research_model(
        _base_row(prior_runup_percentile=0.95),
        analogue=_good_analogue(),
        market_regime="Neutral",
    )
    assert low["research_score"] > high["research_score"]


def test_missing_analogue_is_excluded_not_scored_as_zero():
    result = score_locked_research_model(
        _base_row(),
        analogue=None,
        market_regime="Neutral",
    )
    analogue = next(
        factor for factor in result["research_factors"]
        if factor["factor"] == "Historical analogues"
    )
    assert analogue["rating"] is None
    assert analogue["effective_weight"] == 0.0
    assert result["research_coverage"] < 1.0


def test_incomplete_positive_score_is_shrunk_toward_neutral_for_ranking():
    result = score_locked_research_model(
        _base_row(),
        analogue=None,
        market_regime="Neutral",
    )
    assert result["research_score"] > 50.0
    assert 50.0 < result["research_ranking_score"] < result["research_score"]
    expected = 50.0 + (result["research_score"] - 50.0) * result["research_coverage"]
    assert abs(result["research_ranking_score"] - expected) < 1e-9


def test_incomplete_negative_score_is_also_shrunk_toward_neutral():
    result = score_locked_research_model(
        _base_row(
            return_30d=-0.10,
            return_90d=-0.25,
            drawdown_1y=-0.45,
            prior_runup_percentile=0.95,
        ),
        analogue=None,
        market_regime="Neutral",
    )
    assert result["research_score"] < 50.0
    assert result["research_score"] < result["research_ranking_score"] < 50.0
