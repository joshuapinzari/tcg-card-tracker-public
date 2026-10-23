"""Transparent evidence synthesis for the Card Analysis dashboard.

This module deliberately does *not* pretend to be a trained expected-return
model.  It combines the project's existing descriptive / validated signals into
an explainable 0-100 evidence score so the UI can answer the practical question
"does this card currently look attractive?" without hiding the ingredients.

Every scoring factor has:
- a visible reading,
- a direction/rating from -2 .. +2,
- an explicit weight,
- an explanation,
- and an optional data-quality qualifier.

Missing factors are excluded rather than silently treated as neutral.  Historical
analogue output remains the only forward-looking block and is labelled as such.
"""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any

import pandas as pd

from services.analysis.regime_overheating_signal import regime_overheating_profile


FACTOR_WEIGHTS = {
    "Historical analogues": 24.0,
    "90D vs set": 14.0,
    "90D vs market": 14.0,
    "Momentum profile": 10.0,
    "Trend structure": 10.0,
    "Recovery / drawdown": 10.0,
    "Set + era context": 8.0,
    "Validated overheating risk": 10.0,
}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _pct(value: Any, digits: int = 1) -> str:
    value = _number(value)
    return "N/A" if value is None else f"{value * 100:+.{digits}f}%"


def _status(rating: float | None) -> str:
    if rating is None:
        return "NO DATA"
    if rating >= 1.5:
        return "VERY POSITIVE"
    if rating >= 0.5:
        return "POSITIVE"
    if rating > -0.5:
        return "NEUTRAL"
    if rating > -1.5:
        return "CAUTION"
    return "NEGATIVE"


def _factor(
    name: str,
    *,
    reading: str,
    rating: float | None,
    explanation: str,
    weight: float | None = None,
    quality: str | None = None,
) -> dict:
    base_weight = float(FACTOR_WEIGHTS[name] if weight is None else weight)
    if rating is None:
        impact = None
        effective_weight = 0.0
    else:
        rating = max(-2.0, min(2.0, float(rating)))
        effective_weight = base_weight
        impact = effective_weight * rating / 2.0
    return {
        "factor": name,
        "reading": reading,
        "assessment": _status(rating),
        "rating": rating,
        "weight": effective_weight,
        "base_weight": base_weight,
        "impact": impact,
        "quality": quality,
        "explanation": explanation,
    }


def _relative_rating(value: Any) -> float | None:
    value = _number(value)
    if value is None:
        return None
    if value >= 0.10:
        return 2.0
    if value >= 0.03:
        return 1.0
    if value > -0.03:
        return 0.0
    if value > -0.10:
        return -1.0
    return -2.0


def _momentum_rating(return_30: Any, return_90: Any) -> float | None:
    r30 = _number(return_30)
    r90 = _number(return_90)
    if r30 is None or r90 is None:
        return None

    if r90 >= 0.08 and r30 >= 0.02:
        return 2.0
    if r90 > 0 and r30 >= 0:
        return 1.0
    if r90 > 0 and r30 < 0:
        # Up over the medium term, but the recent tape has weakened.
        return 0.0
    if r90 <= 0 and r30 > 0:
        # Early reacceleration is encouraging, but not enough for a full green.
        return 0.5
    if r90 <= -0.15 and r30 <= -0.05:
        return -2.0
    return -1.0


def _trend_rating(state: Any) -> float | None:
    if state is None:
        return None
    state = str(state).strip().lower()
    if state == "bullish":
        return 2.0
    if state == "bearish":
        return -2.0
    if state in {"mixed", "neutral"}:
        return 0.0
    return None


def _recovery_rating(intelligence: dict) -> tuple[float | None, str]:
    recovery = intelligence.get("recovery") or {}
    drawdown = _number(intelligence.get("drawdown_from_1y_high"))
    r30 = _number((intelligence.get("card_momentum") or {}).get(30))
    rel30 = _number((intelligence.get("relative_to_set") or {}).get(30))

    if recovery.get("clean_recovery"):
        return 2.0, "Clean recovery: prior winner, correction, then peer-beating reacceleration."

    if drawdown is None:
        return None, "One-year drawdown is unavailable."

    if drawdown <= -0.20 and r30 is not None and rel30 is not None:
        if r30 > 0 and rel30 > 0:
            return 1.0, "Deep drawdown, but the last 30 days are recovering faster than the set."
        if r30 < -0.05:
            return -2.0, "Deep drawdown is still deteriorating rather than showing a recovery."
        if r30 < 0:
            return -1.0, "Deep drawdown with weak recent momentum."

    if drawdown <= -0.40 and r30 is not None and r30 < 0:
        return -2.0, "Very deep drawdown with continuing negative momentum."

    return 0.0, "No strong recovery or continuing-breakdown pattern is present."


def _context_rating(intelligence: dict) -> tuple[float | None, str]:
    states = [
        intelligence.get("set_trend_state"),
        intelligence.get("era_trend_state"),
    ]
    usable = [str(value).lower() for value in states if value not in (None, "Unknown")]
    if not usable:
        return None, "Set and era trend context are unavailable."

    score = 0.0
    for state in usable:
        if state == "bullish":
            score += 1.0
        elif state == "bearish":
            score -= 1.0

    if len(usable) == 1:
        score *= 1.5
    score = max(-2.0, min(2.0, score))

    set_state = intelligence.get("set_trend_state") or "Unknown"
    era_state = intelligence.get("era_trend_state") or "Unknown"
    return score, f"Set trend: {set_state}; era trend: {era_state}."


def _analogue_factor(analogue: dict | None) -> dict:
    name = "Historical analogues"
    if not analogue:
        return _factor(
            name,
            reading="No calibrated setup",
            rating=None,
            explanation=(
                "The card does not currently match one of the validated opportunity archetypes, "
                "or there are not enough comparable historical states. This is not a negative signal."
            ),
        )

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

    rating = max(-2.0, min(2.0, rating))

    quality_multiplier = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.50}.get(quality, 0.50)
    effective_weight = FACTOR_WEIGHTS[name] * quality_multiplier

    reading = (
        f"{analogue.get('setup_label', 'Setup')} · set alpha {_pct(alpha)} · "
        f"P(+15%) {_pct(strong_gain, 0)} · P(crash) {_pct(crash, 0)}"
    )
    factor = _factor(
        name,
        reading=reading,
        rating=rating,
        weight=effective_weight,
        quality=quality,
        explanation=(
            f"{int(analogue.get('analog_count') or 0):,} context-aware historical analogues; "
            f"similarity {_pct(analogue.get('similarity'), 0)}. Forward-looking evidence is "
            "down-weighted when analogue quality is only medium/low."
        ),
    )
    factor["base_weight"] = FACTOR_WEIGHTS[name]
    return factor


def build_card_assessment(
    intelligence: dict,
    *,
    analogue: dict | None = None,
    market_regime: dict | None = None,
    raw_observations: Any = None,
    raw_date: Any = None,
) -> dict:
    """Build the transparent overall Card Analyst verdict."""

    momentum = intelligence.get("card_momentum") or {}
    relative_set = intelligence.get("relative_to_set") or {}
    relative_market = intelligence.get("relative_to_market") or {}

    factors: list[dict] = []

    factors.append(_analogue_factor(analogue))

    factors.append(
        _factor(
            "90D vs set",
            reading=_pct(relative_set.get(90)),
            rating=_relative_rating(relative_set.get(90)),
            explanation=(
                "How much the card beat or lagged its own set over 90 days. "
                "This is one of the cleanest ways to separate card-specific strength from a set-wide move."
            ),
        )
    )

    factors.append(
        _factor(
            "90D vs market",
            reading=_pct(relative_market.get(90)),
            rating=_relative_rating(relative_market.get(90)),
            explanation=(
                "How much the card beat or lagged the investable modern-English market over 90 days."
            ),
        )
    )

    r30 = momentum.get(30)
    r90 = momentum.get(90)
    factors.append(
        _factor(
            "Momentum profile",
            reading=f"30D {_pct(r30)} · 90D {_pct(r90)}",
            rating=_momentum_rating(r30, r90),
            explanation=(
                "Combines recent and medium-term price direction. A healthy positive 30D + 90D profile "
                "is supportive; a falling 30D inside an older uptrend is treated as weakening, not bullish."
            ),
        )
    )

    factors.append(
        _factor(
            "Trend structure",
            reading=(
                f"{intelligence.get('card_trend_state') or 'N/A'} · "
                f"vs MA30 {_pct(intelligence.get('distance_ma30'))} · "
                f"vs MA90 {_pct(intelligence.get('distance_ma90'))}"
            ),
            rating=_trend_rating(intelligence.get("card_trend_state")),
            explanation=(
                "Uses the smoothed price versus 30D/90D moving averages and the short moving-average slope."
            ),
        )
    )

    recovery_rating, recovery_explanation = _recovery_rating(intelligence)
    factors.append(
        _factor(
            "Recovery / drawdown",
            reading=(
                f"1Y drawdown {_pct(intelligence.get('drawdown_from_1y_high'))} · "
                f"30D {_pct(r30)}"
            ),
            rating=recovery_rating,
            explanation=recovery_explanation,
        )
    )

    context_rating, context_explanation = _context_rating(intelligence)
    factors.append(
        _factor(
            "Set + era context",
            reading=(
                f"Set {intelligence.get('set_trend_state') or 'N/A'} · "
                f"Era {intelligence.get('era_trend_state') or 'N/A'}"
            ),
            rating=context_rating,
            explanation=context_explanation,
        )
    )

    live_signal = intelligence.get("live_overheating") or {}
    set_pct = _number(live_signal.get("signal_percentile"))
    market_pct = _number(
        (((intelligence.get("market_structure") or {}).get(90) or {}).get("investable_market") or {}).get(
            "card_percentile"
        )
    )
    regime_name = (market_regime or {}).get("regime")
    risk_profile = regime_overheating_profile(set_pct, market_pct, regime_name)
    alpha_reference = _number(risk_profile.get("alpha_reference"))

    if set_pct is None or market_pct is None or regime_name is None:
        overheat_rating = None
        risk_reading = "Insufficient percentile/regime data"
    elif alpha_reference is None:
        overheat_rating = 0.0
        risk_reading = (
            f"{regime_name} regime · set-relative rank {_pct(set_pct, 0)} · "
            f"market rank {_pct(market_pct, 0)} · no validated penalty"
        )
    else:
        if alpha_reference <= -0.15:
            overheat_rating = -2.0
        elif alpha_reference <= -0.08:
            overheat_rating = -1.5
        else:
            overheat_rating = -1.0
        risk_reading = (
            f"{risk_profile.get('label')} · historical set-alpha reference {_pct(alpha_reference)}"
        )

    factors.append(
        _factor(
            "Validated overheating risk",
            reading=risk_reading,
            rating=overheat_rating,
            explanation=(
                "Only validated negative overheating cells create a penalty. Absence of a validated penalty "
                "is neutral, not a positive bonus."
            ),
        )
    )

    scored = [factor for factor in factors if factor["impact"] is not None and factor["weight"] > 0]
    available_weight = sum(factor["weight"] for factor in scored)
    total_possible_weight = sum(FACTOR_WEIGHTS.values())
    net_impact = sum(float(factor["impact"]) for factor in scored)

    if available_weight > 0:
        score = 50.0 + 50.0 * (net_impact / available_weight)
        score = max(0.0, min(100.0, score))
    else:
        score = 50.0

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

    coverage = available_weight / total_possible_weight if total_possible_weight else 0.0

    observations = _number(raw_observations)
    history_quality = 0.0
    if observations is not None:
        if observations >= 180:
            history_quality = 1.0
        elif observations >= 120:
            history_quality = 0.85
        elif observations >= 60:
            history_quality = 0.65
        elif observations >= 30:
            history_quality = 0.45
        else:
            history_quality = 0.25

    freshness = 1.0
    if raw_date is not None and not pd.isna(raw_date):
        try:
            raw_day = pd.Timestamp(raw_date).date()
            age = max(0, (date.today() - raw_day).days)
            if age > 14:
                freshness = 0.45
            elif age > 7:
                freshness = 0.75
        except (TypeError, ValueError):
            freshness = 0.75

    confidence_score = 0.60 * coverage + 0.30 * history_quality + 0.10 * freshness
    if confidence_score >= 0.78:
        confidence = "HIGH"
    elif confidence_score >= 0.58:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    positives = sorted(
        [factor for factor in scored if float(factor["impact"]) > 0],
        key=lambda factor: float(factor["impact"]),
        reverse=True,
    )
    negatives = sorted(
        [factor for factor in scored if float(factor["impact"]) < 0],
        key=lambda factor: float(factor["impact"]),
    )

    return {
        "score": score,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "coverage": coverage,
        "available_weight": available_weight,
        "possible_weight": total_possible_weight,
        "factors": factors,
        "top_positives": positives[:3],
        "top_risks": negatives[:3],
        "risk_profile": risk_profile,
    }


def build_graded_market_rows(graded_by_market: dict[str, dict | None]) -> list[dict]:
    """Create a concise, source-faithful graded-market comparison table.

    This is intentionally separate from the overall card score: a cheap slab
    premium can make a *market* attractive without proving the underlying card
    itself will appreciate.
    """

    rows: list[dict] = []
    for market_type in ("PSA_9", "PSA_10", "ACE_10"):
        item = graded_by_market.get(market_type)
        if not item:
            continue

        deviation = _number(item.get("premium_vs_raw_vs_history"))
        confidence = str(item.get("confidence") or "LOW").upper()

        if confidence == "LOW" or deviation is None:
            relative_read = "LOW DATA" if confidence == "LOW" else "NO HISTORY"
            relative_status = "NO DATA"
        elif deviation <= -0.15:
            relative_read = "CHEAP VS OWN HISTORY"
            relative_status = "VERY POSITIVE"
        elif deviation <= -0.05:
            relative_read = "SLIGHTLY CHEAP"
            relative_status = "POSITIVE"
        elif deviation >= 0.15:
            relative_read = "EXPENSIVE VS OWN HISTORY"
            relative_status = "NEGATIVE"
        elif deviation >= 0.05:
            relative_read = "SLIGHTLY EXPENSIVE"
            relative_status = "CAUTION"
        else:
            relative_read = "NORMAL RANGE"
            relative_status = "NEUTRAL"

        rows.append(
            {
                "market_type": market_type,
                "market": str(item.get("market_label") or market_type.replace("_", " ")),
                "current_price": _number(item.get("current_price")),
                "return_30": _number(item.get("return_30")),
                "return_90": _number(item.get("return_90")),
                "drawdown": _number(item.get("drawdown_from_high")),
                "premium_vs_raw": _number(item.get("premium_vs_raw")),
                "premium_vs_history": deviation,
                "ratio_vs_psa10": _number(item.get("ratio_vs_psa10")),
                "ratio_vs_psa10_vs_history": _number(item.get("ratio_vs_psa10_vs_history")),
                "sales_90": int(item.get("sales_90") or 0),
                "days_since_sale": item.get("days_since_sale"),
                "confidence": confidence,
                "relative_value_read": relative_read,
                "relative_value_status": relative_status,
            }
        )

    return rows
