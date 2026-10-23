from __future__ import annotations

from math import exp

import numpy as np
import pandas as pd


MIN_ANALOGS = 60
DEFAULT_ANALOGS = 150
DEFAULT_CONTEXT_WEIGHT = 0.35

# These are deliberately the card/setup features that define the archetype itself.
# Broader set / era / market state is matched separately through CONTEXT_FEATURES so
# we can validate how much contextual conditioning improves the signal rather than
# silently baking arbitrary context weights into every setup.
SETUP_FEATURES = {
    "HEADING_UP": [
        "return_30d",
        "return_90d",
        "acceleration_30_vs_90",
        "relative_set_90d",
        "relative_market_90d",
        "drawdown_1y",
    ],
    "ERA_STRENGTH": [
        "return_30d",
        "relative_era_90d",
        "relative_market_90d",
        "era_return_30d",
        "era_return_90d",
        "era_breadth",
    ],
    "RECOVERY": [
        "prior_runup",
        "correction",
        "recovery_30d",
        "relative_set_30d",
        "relative_market_30d",
        "drawdown_1y",
    ],
    "SUSPECTED_LOW": [
        "drawdown_1y",
        "return_30d",
        "return_90d",
        "acceleration_30_vs_90",
        "relative_set_30d",
        "relative_market_30d",
    ],
}

# Universal point-in-time context. Every production analogue is matched not only
# on the card's setup, but also on the environment that setup occurred in.
# This answers questions such as "does this recovery still work when the set is
# weak?" and "is this breakout occurring in a broad market expansion or in
# isolation?" without comparing absolute prices across eras.
CONTEXT_FEATURES = [
    "set_return_30d",
    "set_return_90d",
    "set_breadth",
    "era_return_30d",
    "era_return_90d",
    "era_breadth",
    "market_return_30d",
    "market_return_90d",
    "market_breadth",
]

SETUP_LABELS = {
    "HEADING_UP": "Heading Up",
    "ERA_STRENGTH": "Strong Era",
    "RECOVERY": "Recovery",
    "SUSPECTED_LOW": "Suspected Low",
}


def classify_setups(row: pd.Series | dict) -> list[str]:
    """Return the opportunity archetypes supported by a point-in-time feature row."""
    get = row.get
    setups: list[str] = []

    r30 = get("return_30d")
    r90 = get("return_90d")
    accel = get("acceleration_30_vs_90")
    rel_set30 = get("relative_set_30d")
    rel_set90 = get("relative_set_90d")
    rel_era90 = get("relative_era_90d")
    rel_mkt30 = get("relative_market_30d")
    rel_mkt90 = get("relative_market_90d")
    drawdown = get("drawdown_1y")
    prior_runup = get("prior_runup")
    correction = get("correction")
    recovery = get("recovery_30d")
    era30 = get("era_return_30d")
    era90 = get("era_return_90d")
    mkt30 = get("market_return_30d")
    mkt90 = get("market_return_90d")
    era_breadth = get("era_breadth")

    def ok(*values: object) -> bool:
        return all(value is not None and pd.notna(value) for value in values)

    # Early/healthy uptrend: already beating peers but not a vertical 90D spike.
    if ok(r30, r90, accel, rel_set90, rel_mkt90, drawdown):
        if (
            r30 > 0.02
            and r90 > 0.03
            and rel_set90 > 0
            and rel_mkt90 > 0
            and accel > -0.01
            and -0.40 < drawdown <= 0.02
            and r90 < 0.80
        ):
            setups.append("HEADING_UP")

    # Card strength confirmed by a strengthening era rather than isolated demand.
    if ok(r30, rel_era90, rel_mkt90, era30, era90, mkt30, mkt90, era_breadth):
        if (
            r30 > 0
            and rel_era90 > 0
            and rel_mkt90 > 0
            and era30 > mkt30
            and era90 > mkt90
            and era_breadth >= 0.52
        ):
            setups.append("ERA_STRENGTH")

    # Prior winner -> meaningful correction -> positive, peer-beating reacceleration.
    if ok(prior_runup, correction, recovery, rel_set30, rel_mkt30, drawdown):
        if (
            prior_runup >= 0.20
            and correction <= -0.10
            and recovery > 0
            and rel_set30 > 0
            and rel_mkt30 > 0
            and drawdown <= -0.08
        ):
            setups.append("RECOVERY")

    # Deep drawdown where the short-term tape is no longer deteriorating with peers.
    if ok(drawdown, r30, r90, accel, rel_set30, rel_mkt30):
        if (
            drawdown <= -0.20
            and r90 <= 0.05
            and r30 >= -0.05
            and accel > 0
            and rel_set30 > 0
            and rel_mkt30 > 0
        ):
            setups.append("SUSPECTED_LOW")

    return setups


def add_setup_flags(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    setup_lists = result.apply(classify_setups, axis=1)
    for setup in SETUP_FEATURES:
        result[f"is_{setup.lower()}"] = setup_lists.map(lambda values: setup in values)
    result["setups"] = setup_lists
    return result


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    cumulative = np.cumsum(weights)
    if cumulative[-1] <= 0:
        return float("nan")
    target = q * cumulative[-1]
    return float(values[np.searchsorted(cumulative, target, side="left")])


def _robust_scale(reference: pd.DataFrame, columns: list[str]) -> tuple[pd.Series, pd.Series]:
    median = reference[columns].median()
    q75 = reference[columns].quantile(0.75)
    q25 = reference[columns].quantile(0.25)
    scale = (q75 - q25).replace(0, np.nan)
    fallback = reference[columns].std().replace(0, np.nan)
    return median, scale.fillna(fallback).fillna(1.0)


def _normalised_distance(
    current: pd.Series,
    reference: pd.DataFrame,
    columns: list[str],
) -> pd.Series:
    """Robust Euclidean distance in a feature block."""
    median, scale = _robust_scale(reference, columns)
    hist_values = (reference[columns] - median) / scale
    current_values = (current[columns].astype(float) - median) / scale
    return np.sqrt(((hist_values - current_values) ** 2).mean(axis=1))


def calibrate_one(
    current: pd.Series,
    history: pd.DataFrame,
    setup: str,
    *,
    max_analogs: int = DEFAULT_ANALOGS,
    context_weight: float = DEFAULT_CONTEXT_WEIGHT,
) -> dict | None:
    """Estimate following-90D outcomes from context-aware historical analogues.

    ``context_weight=0`` reproduces the original setup-only matcher. Production
    defaults to 0.35 so a candidate is compared with historical cards that looked
    similar *and* lived in a similar set/era/market regime. The walk-forward
    validator tests multiple weights before we treat the exact weighting as fixed.
    """
    if setup not in SETUP_FEATURES:
        raise ValueError(f"Unknown opportunity setup: {setup}")
    if not 0.0 <= float(context_weight) <= 0.90:
        raise ValueError("context_weight must be between 0.0 and 0.90")

    core_columns = SETUP_FEATURES[setup]
    target_columns = [
        "future_return_90d",
        "future_set_alpha_90d",
        "future_era_alpha_90d",
        "future_market_alpha_90d",
    ]
    required_columns = core_columns + target_columns
    context_columns: list[str] = []
    if context_weight > 0:
        context_columns = CONTEXT_FEATURES
        required_columns += context_columns

    missing = [column for column in required_columns if column not in history.columns]
    if missing:
        return None

    subset = history[history[f"is_{setup.lower()}"]].dropna(subset=required_columns)
    if len(subset) < MIN_ANALOGS:
        return None
    if any(pd.isna(current.get(column)) for column in core_columns + context_columns):
        return None

    core_distances = _normalised_distance(current, subset, core_columns)
    if context_columns:
        context_distances = _normalised_distance(current, subset, context_columns)
        distances = np.sqrt(
            (1.0 - context_weight) * np.square(core_distances)
            + context_weight * np.square(context_distances)
        )
    else:
        context_distances = pd.Series(0.0, index=subset.index, dtype=float)
        distances = core_distances

    nearest_n = min(max_analogs, len(subset))
    nearest_idx = np.argpartition(distances.to_numpy(), nearest_n - 1)[:nearest_n]
    analogs = subset.iloc[nearest_idx].copy()
    analog_distances = distances.iloc[nearest_idx].to_numpy(dtype=float)
    core_nearest = core_distances.iloc[nearest_idx].to_numpy(dtype=float)
    context_nearest = context_distances.iloc[nearest_idx].to_numpy(dtype=float)

    weights = np.exp(-analog_distances)
    if float(weights.sum()) <= 0:
        weights = np.ones_like(weights)

    future = analogs["future_return_90d"].to_numpy(dtype=float)
    set_alpha = analogs["future_set_alpha_90d"].to_numpy(dtype=float)
    era_alpha = analogs["future_era_alpha_90d"].to_numpy(dtype=float)
    market_alpha = analogs["future_market_alpha_90d"].to_numpy(dtype=float)

    expected_return = float(np.average(future, weights=weights))
    expected_set_alpha = float(np.average(set_alpha, weights=weights))
    expected_era_alpha = float(np.average(era_alpha, weights=weights))
    expected_market_alpha = float(np.average(market_alpha, weights=weights))
    median_return = _weighted_quantile(future, weights, 0.5)
    probability_positive = float(np.average(future > 0, weights=weights))
    probability_strong_gain = float(np.average(future >= 0.15, weights=weights))
    probability_crash = float(np.average(future <= -0.15, weights=weights))
    probability_set_alpha_positive = float(np.average(set_alpha > 0, weights=weights))
    probability_era_alpha_positive = float(np.average(era_alpha > 0, weights=weights))
    probability_market_alpha_positive = float(np.average(market_alpha > 0, weights=weights))

    mean_distance = float(np.average(analog_distances, weights=weights))
    mean_core_distance = float(np.average(core_nearest, weights=weights))
    mean_context_distance = float(np.average(context_nearest, weights=weights))
    similarity = exp(-mean_distance)
    core_similarity = exp(-mean_core_distance)
    context_similarity = exp(-mean_context_distance) if context_columns else 1.0

    if nearest_n >= 150 and similarity >= 0.55:
        analogue_quality = "HIGH"
    elif nearest_n >= 80 and similarity >= 0.35:
        analogue_quality = "MEDIUM"
    else:
        analogue_quality = "LOW"

    return {
        "setup": setup,
        "setup_label": SETUP_LABELS[setup],
        "analog_count": int(nearest_n),
        "similarity": similarity,
        "core_similarity": core_similarity,
        "context_similarity": context_similarity,
        "context_weight": float(context_weight),
        "analogue_quality": analogue_quality,
        "expected_return_90d": expected_return,
        "median_return_90d": median_return,
        "expected_set_alpha_90d": expected_set_alpha,
        "expected_era_alpha_90d": expected_era_alpha,
        "expected_market_alpha_90d": expected_market_alpha,
        "probability_positive_90d": probability_positive,
        "probability_strong_gain_90d": probability_strong_gain,
        "probability_crash_90d": probability_crash,
        "probability_set_alpha_positive_90d": probability_set_alpha_positive,
        "probability_era_alpha_positive_90d": probability_era_alpha_positive,
        "probability_market_alpha_positive_90d": probability_market_alpha_positive,
    }
