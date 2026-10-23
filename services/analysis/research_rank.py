"""Frozen Card Analyst challenger score used for live ranking.

This module is intentionally small and deterministic.  The structure was selected
with embargoed, expanding-window research on 2026-10-16 and is now frozen as the
Challenger v1 specification while prospective 90-day outcomes accumulate.

It is a ranking score, not a calibrated probability or price forecast.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from services.analysis.regime_overheating_signal import regime_overheating_profile


LOCKED_MODEL_VERSION = "challenger_v1_2026-10-16"
LOCKED_ANALOGS = 150
LOCKED_PRIMARY_PRICE_FLOOR_USD = 20.0
LOCKED_WEIGHTS = {
    "Historical analogues": 32.0,
    "Momentum profile": 28.0,
    "Validated overheating risk": 28.0,
    "Prior run-up": 9.0,
    "Recovery / drawdown": 3.0,
}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _clip_rating(value: float | None) -> float | None:
    if value is None:
        return None
    return max(-2.0, min(2.0, float(value)))


def _analogue_rating(analogue: dict | None) -> tuple[float | None, float, str]:
    if not analogue or not int(analogue.get("analog_count") or 0):
        return None, 0.0, "No calibrated historical setup"

    alpha = _number(analogue.get("expected_set_alpha_90d"))
    strong_gain = _number(analogue.get("probability_strong_gain_90d"))
    crash = _number(analogue.get("probability_crash_90d"))
    quality = str(analogue.get("analogue_quality") or "LOW").upper()

    rating = 0.0
    if alpha is not None:
        if alpha >= 0.08:
            rating += 1.5
        elif alpha >= 0.03:
            rating += 1.0
        elif alpha <= -0.08:
            rating -= 1.5
        elif alpha <= -0.03:
            rating -= 1.0
    if strong_gain is not None and strong_gain >= 0.55:
        rating += 0.5
    if crash is not None:
        if crash >= 0.25:
            rating -= 1.0
        elif crash >= 0.15:
            rating -= 0.5

    quality_multiplier = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.50}.get(quality, 0.50)
    return _clip_rating(rating), quality_multiplier, quality


def _momentum_rating(return_30d: Any, return_90d: Any) -> float | None:
    r30 = _number(return_30d)
    r90 = _number(return_90d)
    if r30 is None or r90 is None:
        return None
    if r90 >= 0.08 and r30 >= 0.02:
        return 2.0
    if r90 > 0 and r30 >= 0:
        return 1.0
    if r90 > 0 and r30 < 0:
        return 0.0
    if r90 <= 0 and r30 > 0:
        return 0.5
    if r90 <= -0.15 and r30 <= -0.05:
        return -2.0
    return -1.0


def _recovery_rating(row: dict) -> float | None:
    drawdown = _number(row.get("drawdown_1y"))
    r30 = _number(row.get("return_30d"))
    rel_set30 = _number(row.get("relative_set_30d"))
    rel_market30 = _number(row.get("relative_market_30d"))
    prior_runup = _number(row.get("prior_runup"))
    correction = _number(row.get("correction"))
    signal_percentile = _number(row.get("signal_percentile"))

    clean_recovery = bool(
        prior_runup is not None
        and correction is not None
        and rel_set30 is not None
        and rel_market30 is not None
        and prior_runup >= 0.20
        and correction <= -0.10
        and rel_set30 > 0
        and rel_market30 > 0
        and (signal_percentile is None or signal_percentile < 0.90)
    )
    if clean_recovery:
        return 2.0
    if drawdown is None:
        return None
    if drawdown <= -0.20 and r30 is not None and rel_set30 is not None:
        if r30 > 0 and rel_set30 > 0:
            return 1.0
        if r30 < -0.05:
            return -2.0
        if r30 < 0:
            return -1.0
    if drawdown <= -0.40 and r30 is not None and r30 < 0:
        return -2.0
    return 0.0


def _overheating_rating(row: dict, market_regime: str | None) -> tuple[float | None, dict]:
    set_percentile = _number(row.get("signal_percentile"))
    market_percentile = _number(row.get("market_percentile"))
    profile = regime_overheating_profile(set_percentile, market_percentile, market_regime)
    alpha = _number(profile.get("alpha_reference"))
    if set_percentile is None or market_percentile is None or market_regime is None:
        return None, profile
    if alpha is None:
        return 0.0, profile
    if alpha <= -0.15:
        return -2.0, profile
    if alpha <= -0.08:
        return -1.5, profile
    return -1.0, profile


def _prior_runup_rating(percentile: Any) -> float | None:
    """V3 learned a negative direction for prior run-up.

    The research candidate was a within-date percentile mapped onto [-2, +2].
    High prior run-up is therefore penalised and low prior run-up is rewarded.
    """

    percentile = _number(percentile)
    if percentile is None:
        return None
    return _clip_rating(-4.0 * (percentile - 0.50))


def score_locked_research_model(
    row: dict,
    *,
    analogue: dict | None,
    market_regime: str | None,
) -> dict:
    """Return Challenger v1 score and factor-level contributions for one live card."""

    analogue_rating, analogue_quality, analogue_quality_label = _analogue_rating(analogue)
    overheat_rating, overheat_profile = _overheating_rating(row, market_regime)

    raw_factors = [
        (
            "Historical analogues",
            analogue_rating,
            analogue_quality,
            analogue_quality_label,
        ),
        (
            "Momentum profile",
            _momentum_rating(row.get("return_30d"), row.get("return_90d")),
            1.0,
            None,
        ),
        (
            "Validated overheating risk",
            overheat_rating,
            1.0,
            overheat_profile.get("label") or overheat_profile.get("evidence"),
        ),
        (
            "Prior run-up",
            _prior_runup_rating(row.get("prior_runup_percentile")),
            1.0,
            None,
        ),
        (
            "Recovery / drawdown",
            _recovery_rating(row),
            1.0,
            None,
        ),
    ]

    factors: list[dict] = []
    numerator = 0.0
    denominator = 0.0
    for name, rating, quality, note in raw_factors:
        base_weight = float(LOCKED_WEIGHTS[name])
        if rating is None:
            effective_weight = 0.0
            contribution = None
        else:
            effective_weight = base_weight * float(quality)
            contribution = effective_weight * float(rating) / 2.0
            numerator += contribution
            denominator += effective_weight
        factors.append(
            {
                "factor": name,
                "rating": rating,
                "base_weight": base_weight,
                "effective_weight": effective_weight,
                "contribution": contribution,
                "note": note,
            }
        )

    if denominator:
        score = max(0.0, min(100.0, 50.0 + 50.0 * numerator / denominator))
    else:
        score = 50.0

    coverage = denominator / sum(LOCKED_WEIGHTS.values())

    # Keep the V3 research score itself untouched, but do not let a card with
    # only a small fraction of the model available outrank a fully-supported
    # card simply because the surviving factors happen to be strong.  Shrink
    # incomplete evidence toward the neutral score of 50 for cross-card
    # ranking.  At 100% coverage the ranking score is identical to the frozen
    # research score.
    ranking_score = 50.0 + (score - 50.0) * coverage
    ranking_score = max(0.0, min(100.0, ranking_score))

    if score >= 72:
        verdict = "VERY STRONG"
    elif score >= 60:
        verdict = "STRONG"
    elif score >= 45:
        verdict = "MIXED"
    elif score >= 33:
        verdict = "WEAK"
    else:
        verdict = "AVOID"

    return {
        "research_model_version": LOCKED_MODEL_VERSION,
        "research_score": score,
        "research_ranking_score": ranking_score,
        "research_verdict": verdict,
        "research_coverage": coverage,
        "research_factors": factors,
    }
