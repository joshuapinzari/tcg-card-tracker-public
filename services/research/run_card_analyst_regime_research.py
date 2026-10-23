"""Research whether frozen V3 should become regime-aware.

This command is development research only. It keeps the exact five V3 factor
*definitions* and asks a narrow question: do Modern, Mid-era and Vintage cards
need different factor weights when judged inside the same recent Pokemon market
regime?

Safeguards
----------
- the recent-market boundary and stable hash sample come from the locked V3
  evaluation policy;
- every outer validation fold is strictly walk-forward with a 90-day embargo;
- factor definitions and analogue count remain identical to frozen V3;
- only weights are refit by regime; the prior-run-up sign may flip because its
  direction was itself a learned V3 choice;
- a unified five-factor refit is evaluated beside the regime split, so any gain
  from merely refitting V3 is not mistaken for evidence that segmentation helps;
- no production model, dashboard score, README, or frozen registry is changed.

Historical results remain development evidence. Prospective 30D/90D lockbox
outcomes are still required before any production replacement of V3.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.research.evaluate_locked_card_model import (
    EVALUATION_POLICY_PATH,
    MODEL_SPEC_PATH,
    build_frozen_factor_panel,
    load_evaluation_policy,
    load_frozen_model_spec,
    metrics_from_signal,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HYPOTHESIS_PATH = PROJECT_ROOT / "models" / "card_rank_v4_regime_hypothesis.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "research_outputs" / "card_analyst_regime_v4.json"

FACTOR_NAMES = (
    "Historical analogues",
    "Momentum profile",
    "Validated overheating risk",
    "Prior run-up",
    "Recovery / drawdown",
)

# Keep the V3 research constraints rather than opening a wide optimiser just
# because the universe has been segmented.
WEIGHT_BOUNDS: dict[str, tuple[float, float]] = {
    "Historical analogues": (8.0, 45.0),
    "Momentum profile": (3.0, 35.0),
    "Validated overheating risk": (3.0, 35.0),
    "Prior run-up": (0.0, 22.0),
    "Recovery / drawdown": (3.0, 35.0),
}




@dataclass(frozen=True)
class Fold:
    """One expanding walk-forward train/validation split."""

    number: int
    train_dates: np.ndarray
    validation_dates: np.ndarray
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


def _build_folds(
    panel: pd.DataFrame,
    *,
    folds: int,
    embargo_days: int,
    min_train_fraction: float,
) -> list[Fold]:
    """Build expanding walk-forward folds with a label-availability embargo."""

    dates = np.array(sorted(panel["formation_date"].dropna().unique()))
    if len(dates) < 12:
        raise SystemExit("Not enough formation dates for multi-fold regime research.")
    if not 0.25 <= float(min_train_fraction) <= 0.70:
        raise SystemExit("--min-train-fraction should be between 0.25 and 0.70")

    first_validation = max(4, int(np.floor(len(dates) * float(min_train_fraction))))
    validation_pool = dates[first_validation:]
    blocks = [block for block in np.array_split(validation_pool, int(folds)) if len(block)]
    result: list[Fold] = []
    for number, block in enumerate(blocks, start=1):
        validation_start = pd.Timestamp(block[0])
        validation_end = pd.Timestamp(block[-1])
        train_cutoff = validation_start - pd.Timedelta(days=int(embargo_days))
        train_dates = dates[pd.to_datetime(dates) <= train_cutoff]
        if len(train_dates) < 8:
            continue
        result.append(
            Fold(
                number=number,
                train_dates=train_dates,
                validation_dates=block,
                train_end=pd.Timestamp(train_dates[-1]),
                validation_start=validation_start,
                validation_end=validation_end,
            )
        )
    if len(result) < 3:
        raise SystemExit(
            "Fold/embargo settings leave fewer than three credible walk-forward folds."
        )
    return result


def _project_bounded_simplex(
    values: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    total: float = 100.0,
) -> np.ndarray:
    """Project a vector onto bounded weights that sum exactly to ``total``."""

    values = np.asarray(values, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    if lower.sum() > total + 1e-9 or upper.sum() < total - 1e-9:
        raise ValueError("Weight bounds cannot satisfy the requested total.")
    lo = float(np.min(values - upper) - total)
    hi = float(np.max(values - lower) + total)
    for _ in range(100):
        mid = (lo + hi) / 2.0
        projected = np.clip(values - mid, lower, upper)
        if projected.sum() > total:
            lo = mid
        else:
            hi = mid
    result = np.clip(values - (lo + hi) / 2.0, lower, upper)
    residue = total - result.sum()
    if abs(residue) > 1e-8:
        free = (result > lower + 1e-8) & (result < upper - 1e-8)
        if free.any():
            result[free] += residue / free.sum()
        else:
            room = upper - result if residue > 0 else result - lower
            result[int(np.argmax(room))] += residue
    return result


def _fast_correlations(
    ratings: np.ndarray,
    quality: np.ndarray,
    target: np.ndarray,
    candidates: np.ndarray,
    chunk: int = 256,
) -> np.ndarray:
    """Vectorised training correlation used only to shortlist weight candidates."""

    target = np.asarray(target, dtype=float)
    target_centered = target - target.mean()
    target_ss = float(np.sum(target_centered ** 2))
    out = np.full(len(candidates), np.nan, dtype=float)
    weighted_ratings = ratings * quality
    for start in range(0, len(candidates), int(chunk)):
        weights = candidates[start : start + int(chunk)].T
        numerator = weighted_ratings @ weights
        denominator = quality @ weights
        signal = np.divide(
            numerator,
            denominator,
            out=np.zeros_like(numerator),
            where=denominator > 0,
        )
        centered = signal - signal.mean(axis=0, keepdims=True)
        cov = np.sum(centered * target_centered[:, None], axis=0)
        signal_ss = np.sum(centered ** 2, axis=0)
        denom = np.sqrt(signal_ss * target_ss)
        corr = np.divide(cov, denom, out=np.zeros_like(cov), where=denom > 0)
        out[start : start + len(corr)] = corr
    return out


@dataclass(frozen=True)
class RegimeFit:
    """One training-only fit of the fixed five-factor V4 hypothesis."""

    weights: np.ndarray
    prior_runup_direction: int
    train_objective: float


def parse_args() -> argparse.Namespace:
    """Parse command-line options for regime research."""

    parser = argparse.ArgumentParser(
        description=(
            "Walk-forward research of Modern/Mid-era/Vintage weight regimes using "
            "the exact frozen-V3 factor definitions. Production V3 is never changed."
        )
    )
    parser.add_argument("--price-floor", type=float, default=20.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--embargo-days", type=int, default=90)
    parser.add_argument("--min-train-fraction", type=float, default=0.42)
    parser.add_argument("--candidates", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument(
        "--min-regime-train-rows",
        type=int,
        default=120,
        help="Minimum rows required to fit one regime inside an outer fold.",
    )
    parser.add_argument(
        "--min-regime-validation-rows",
        type=int,
        default=30,
        help="Minimum rows required to score one regime inside an outer fold.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def load_regime_hypothesis(path: Path = HYPOTHESIS_PATH) -> dict[str, Any]:
    """Load and validate the research-only V4 regime hypothesis."""

    spec = json.loads(path.read_text())
    frozen = load_frozen_model_spec(MODEL_SPEC_PATH)
    policy = load_evaluation_policy(EVALUATION_POLICY_PATH)

    if spec.get("status") != "research_only":
        raise RuntimeError("V4 regime hypothesis must remain research_only.")
    if spec.get("parent_model") != frozen["model_version"]:
        raise RuntimeError("V4 regime hypothesis parent model does not match frozen V3.")
    if int(spec.get("prediction_horizon_days") or 0) != 90:
        raise RuntimeError("V4 regime research currently requires the 90-day V3 horizon.")
    if int(spec.get("analogue_neighbors") or 0) != int(frozen["analogue_neighbors"]):
        raise RuntimeError("V4 hypothesis must keep frozen V3 analogue-neighbour count.")
    if tuple(spec.get("factor_set") or ()) != FACTOR_NAMES:
        raise RuntimeError("V4 hypothesis factor set drifted from the fixed five-factor design.")

    regimes = spec.get("regimes") or {}
    if set(regimes) != {"Modern", "Mid-era", "Vintage"}:
        raise RuntimeError("V4 hypothesis must define Modern, Mid-era and Vintage regimes.")
    all_eras = [era for eras in regimes.values() for era in eras]
    if len(all_eras) != len(set(all_eras)):
        raise RuntimeError("An era may belong to only one V4 regime.")

    # The hypothesis deliberately inherits the market-regime boundary instead
    # of carrying an independently editable historical start date.
    spec["market_regime_start"] = str(policy["market_regime_start"])
    spec["stable_sample_rate"] = float(policy["stable_sample_rate"])
    spec["stable_sample_salt"] = str(policy["stable_sample_salt"])
    return spec


def regime_for_era(era: str | None, hypothesis: dict[str, Any]) -> str | None:
    """Return the V4 market-age regime for a research-era label."""

    value = str(era or "")
    for regime, eras in hypothesis["regimes"].items():
        if value in eras:
            return str(regime)
    return None


def _bounds() -> tuple[np.ndarray, np.ndarray]:
    lower = np.array([WEIGHT_BOUNDS[name][0] for name in FACTOR_NAMES], dtype=float)
    upper = np.array([WEIGHT_BOUNDS[name][1] for name in FACTOR_NAMES], dtype=float)
    return lower, upper


def _candidate_weights(count: int, seed: int) -> np.ndarray:
    """Generate bounded five-factor candidates centred on frozen V3."""

    frozen = load_frozen_model_spec(MODEL_SPEC_PATH)
    lower, upper = _bounds()
    centre = np.array([float(frozen["weights"][name]) for name in FACTOR_NAMES], dtype=float)
    centre = _project_bounded_simplex(centre, lower, upper)
    equal = _project_bounded_simplex(np.repeat(20.0, len(FACTOR_NAMES)), lower, upper)

    rng = np.random.default_rng(seed)
    rows = [centre, equal]
    scales = (3.0, 6.0, 10.0, 16.0, 24.0)
    centres = (centre, equal)
    while len(rows) < max(2, int(count)):
        base = centres[len(rows) % len(centres)]
        scale = scales[len(rows) % len(scales)]
        proposal = base + rng.normal(0.0, scale, size=len(FACTOR_NAMES))
        rows.append(_project_bounded_simplex(proposal, lower, upper))
    return np.vstack(rows[: max(2, int(count))])


def _factor_arrays(
    panel: pd.DataFrame,
    *,
    prior_runup_direction: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return fixed V3 factor ratings/quality with an optional prior-run-up sign flip."""

    direction = 1 if int(prior_runup_direction) >= 0 else -1
    ratings = []
    quality = []
    for name in FACTOR_NAMES:
        values = pd.to_numeric(panel[f"rating_{name}"], errors="coerce").fillna(0.0)
        if name == "Prior run-up":
            values = values * direction
        ratings.append(values)
        quality.append(
            pd.to_numeric(panel[f"quality_{name}"], errors="coerce").fillna(0.0)
        )
    return np.column_stack(ratings).astype(float), np.column_stack(quality).astype(float)


def _raw_signal(ratings: np.ndarray, quality: np.ndarray, weights: np.ndarray) -> np.ndarray:
    weighted_quality = quality * weights.reshape(1, -1)
    denominator = weighted_quality.sum(axis=1)
    numerator = (ratings * weighted_quality).sum(axis=1)
    return np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=float),
        where=denominator > 0,
    )


def _ranking_score(
    panel: pd.DataFrame,
    weights: np.ndarray,
    prior_runup_direction: int,
) -> pd.Series:
    """Score one panel using production-style coverage shrinkage toward 50."""

    ratings, quality = _factor_arrays(
        panel,
        prior_runup_direction=prior_runup_direction,
    )
    raw = _raw_signal(ratings, quality, weights)
    weighted_quality = quality * weights.reshape(1, -1)
    coverage = weighted_quality.sum(axis=1) / float(np.sum(weights))
    research = np.clip(50.0 + 25.0 * raw, 0.0, 100.0)
    ranking = np.clip(50.0 + (research - 50.0) * coverage, 0.0, 100.0)
    return pd.Series(ranking, index=panel.index, dtype=float)


def _training_objective(
    panel: pd.DataFrame,
    ratings: np.ndarray,
    quality: np.ndarray,
    weights: np.ndarray,
) -> float:
    """Prefer high IC/spread that is stable through time inside training only."""

    dates = np.array(sorted(panel["formation_date"].dropna().unique()))
    blocks = [block for block in np.array_split(dates, min(4, len(dates))) if len(block)]
    ics: list[float] = []
    spreads: list[float] = []
    raw = _raw_signal(ratings, quality, weights)
    full_signal = pd.Series(raw, index=panel.index)

    for block in blocks:
        mask = panel["formation_date"].isin(block)
        metrics = metrics_from_signal(panel.loc[mask], full_signal.loc[mask])
        ic = metrics.get("rank_ic")
        spread = metrics.get("top_bottom_spread")
        if ic is not None and np.isfinite(float(ic)):
            ics.append(float(ic))
        if spread is not None and np.isfinite(float(spread)):
            spreads.append(float(spread))
    if not ics:
        return -9.0
    return (
        float(np.mean(ics))
        - 0.50 * float(np.std(ics))
        + 0.50 * (float(np.mean(spreads)) if spreads else 0.0)
    )


def _fit_direction(
    train: pd.DataFrame,
    *,
    prior_runup_direction: int,
    candidates_count: int,
    seed: int,
) -> RegimeFit:
    ratings, quality = _factor_arrays(
        train,
        prior_runup_direction=prior_runup_direction,
    )
    target = pd.to_numeric(train["target_rank"], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(target)
    if int(valid.sum()) < 30:
        raise ValueError("Not enough labelled rows to fit regime weights.")

    candidates = _candidate_weights(candidates_count, seed)
    corr = _fast_correlations(ratings[valid], quality[valid], target[valid], candidates)
    shortlist = np.argsort(np.nan_to_num(corr, nan=-9.0))[-min(250, len(candidates)) :]

    best_index = int(shortlist[-1])
    best_objective = -np.inf
    for idx in shortlist:
        objective = _training_objective(train, ratings, quality, candidates[int(idx)])
        if objective > best_objective:
            best_objective = float(objective)
            best_index = int(idx)

    return RegimeFit(
        weights=candidates[best_index],
        prior_runup_direction=1 if prior_runup_direction >= 0 else -1,
        train_objective=best_objective,
    )


def fit_regime_model(
    train: pd.DataFrame,
    *,
    candidates_count: int,
    seed: int,
) -> RegimeFit:
    """Fit weights on training data and let only prior-run-up sign compete."""

    frozen_direction = _fit_direction(
        train,
        prior_runup_direction=1,
        candidates_count=candidates_count,
        seed=seed,
    )
    reversed_direction = _fit_direction(
        train,
        prior_runup_direction=-1,
        candidates_count=candidates_count,
        seed=seed + 7919,
    )
    return max(
        (frozen_direction, reversed_direction),
        key=lambda item: item.train_objective,
    )


def _weight_dict(fit: RegimeFit) -> dict[str, float]:
    return {
        name: round(float(value), 3)
        for name, value in zip(FACTOR_NAMES, fit.weights)
    }


def _aggregate_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values_ic = []
    values_spread = []
    for row in rows:
        metrics = row.get(key) or {}
        ic = metrics.get("rank_ic")
        spread = metrics.get("top_bottom_spread")
        if ic is not None and np.isfinite(float(ic)):
            values_ic.append(float(ic))
        if spread is not None and np.isfinite(float(spread)):
            values_spread.append(float(spread))
    return {
        "mean_rank_ic": float(np.mean(values_ic)) if values_ic else None,
        "std_rank_ic": float(np.std(values_ic)) if values_ic else None,
        "mean_top_bottom_spread": float(np.mean(values_spread)) if values_spread else None,
        "positive_ic_fold_rate": float(np.mean(np.array(values_ic) > 0)) if values_ic else None,
        "positive_spread_fold_rate": float(np.mean(np.array(values_spread) > 0))
        if values_spread
        else None,
    }


def _fit_stability(fits: list[RegimeFit]) -> dict[str, Any]:
    if not fits:
        return {}
    matrix = np.vstack([fit.weights for fit in fits])
    weights = {}
    for index, name in enumerate(FACTOR_NAMES):
        values = matrix[:, index]
        weights[name] = {
            "mean": round(float(np.mean(values)), 3),
            "median": round(float(np.median(values)), 3),
            "std": round(float(np.std(values)), 3),
            "min": round(float(np.min(values)), 3),
            "max": round(float(np.max(values)), 3),
        }
    directions = [int(fit.prior_runup_direction) for fit in fits]
    return {
        "weights": weights,
        "prior_runup_direction": {
            "frozen_negative_direction_folds": int(sum(value == 1 for value in directions)),
            "reversed_positive_direction_folds": int(sum(value == -1 for value in directions)),
        },
    }


def _fmt(value: Any, *, pct: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not np.isfinite(number):
        return "N/A"
    return f"{number * 100:+.2f}%" if pct else f"{number:+.3f}"


def _print_metric_line(label: str, metrics: dict[str, Any]) -> None:
    print(
        f"{label:<24} N={metrics.get('n', 0):>5} cards={metrics.get('cards', 0):>4}  "
        f"IC={_fmt(metrics.get('rank_ic'))}  "
        f"Q5-Q1={_fmt(metrics.get('top_bottom_spread'), pct=True)}  "
        f"top20={_fmt(metrics.get('top20_alpha'), pct=True)}"
    )


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_json(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _hybrid_signal_for_regime(
    regime: str,
    regime_signal: pd.Series,
    frozen_signal: pd.Series,
) -> pd.Series:
    """Return the research-only hybrid signal for one regime.

    Modern and Vintage use their regime-specific V4 fit. Mid-era deliberately
    keeps frozen V3 because the first regime experiment showed no incremental
    benefit from refitting that already-strong segment.
    """

    if regime == "Mid-era":
        return frozen_signal.astype(float)
    if regime in {"Modern", "Vintage"}:
        return regime_signal.astype(float)
    raise ValueError(f"Unknown V4 regime: {regime}")


def _direction_label(direction: int) -> str:
    """Human-readable label for the prior-run-up direction choice."""

    return "frozen" if int(direction) >= 0 else "reversed"


def main() -> None:
    """Run V4 regime research without modifying the frozen production model."""

    args = parse_args()
    hypothesis = load_regime_hypothesis()
    policy = load_evaluation_policy(EVALUATION_POLICY_PATH)
    recent_start = pd.Timestamp(policy["market_regime_start"])

    print("=" * 108)
    print("CARD ANALYST V4 RESEARCH — REGIME-AWARE WEIGHTS, SAME FIVE V3 FACTORS")
    print("=" * 108)
    print(f"Hypothesis: {hypothesis['model_version']} ({hypothesis['status']})")
    print(f"Parent: {hypothesis['parent_model']} — production V3 remains frozen")
    print(f"Recent market regime only: {recent_start.date()} onward")
    print(
        f"Stable hash sample: {float(policy['stable_sample_rate']):.1%} | "
        f"analogue neighbours: {hypothesis['analogue_neighbors']}"
    )
    print(f"Primary cohort: >= ${args.price_floor:.2f}")
    print(f"Walk-forward folds: {args.folds} | 90D label embargo: {args.embargo_days} days")
    print(f"Weight candidates per sign/fold/model: {args.candidates:,}")
    print("No production weights, factors, dashboard scores, or registries will be changed.")
    print()

    # Database import is deliberately lazy so the pure helpers remain unit-testable.
    from services.research.history import _load_history

    history = _load_history()
    panel = build_frozen_factor_panel(
        history,
        price_floor=10.0,
        formation_start=recent_start,
        sample_rate=float(policy["stable_sample_rate"]),
        sample_salt=str(policy["stable_sample_salt"]),
    )
    panel["v4_regime"] = [regime_for_era(era, hypothesis) for era in panel["era"]]
    panel = panel[panel["v4_regime"].notna()].copy()
    primary = panel[panel["formation_price"] >= float(args.price_floor)].copy()
    if primary.empty:
        raise SystemExit("No tracked regime rows remain in the primary cohort.")

    folds = _build_folds(
        primary,
        folds=args.folds,
        embargo_days=args.embargo_days,
        min_train_fraction=args.min_train_fraction,
    )

    print(
        f"Research panel: {len(primary):,} rows / {primary['product_id'].nunique():,} cards / "
        f"{primary['formation_date'].nunique():,} dates"
    )
    print(
        f"Formation range: {primary['formation_date'].min().date()} -> "
        f"{primary['formation_date'].max().date()}"
    )
    print()
    print("REGIME COMPOSITION")
    print("-" * 108)
    for regime in ("Modern", "Mid-era", "Vintage"):
        subset = primary[primary["v4_regime"] == regime]
        eras = ", ".join(hypothesis["regimes"][regime])
        print(
            f"{regime:<10} rows={len(subset):>5,} cards={subset['product_id'].nunique():>4,}  "
            f"eras: {eras}"
        )
    print()

    fold_reports: list[dict[str, Any]] = []
    regime_fits: dict[str, list[RegimeFit]] = {name: [] for name in hypothesis["regimes"]}
    unified_fits: list[RegimeFit] = []
    frozen_oof: list[pd.DataFrame] = []
    unified_oof: list[pd.DataFrame] = []
    regime_oof: list[pd.DataFrame] = []
    hybrid_oof: list[pd.DataFrame] = []

    for fold in folds:
        train_all = primary[primary["formation_date"].isin(fold.train_dates)].copy()
        validation_all = primary[primary["formation_date"].isin(fold.validation_dates)].copy()
        if len(train_all) < args.min_regime_train_rows:
            raise SystemExit(f"Fold {fold.number} has too few pooled training rows.")

        unified_fit = fit_regime_model(
            train_all,
            candidates_count=args.candidates,
            seed=args.seed + 100003 * fold.number,
        )
        unified_fits.append(unified_fit)
        unified_signal = _ranking_score(
            validation_all,
            unified_fit.weights,
            unified_fit.prior_runup_direction,
        )
        frozen_signal = pd.to_numeric(
            validation_all["frozen_v3_ranking_score"], errors="coerce"
        )

        frozen_piece = validation_all.copy()
        frozen_piece["signal"] = frozen_signal
        frozen_oof.append(frozen_piece)
        unified_piece = validation_all.copy()
        unified_piece["signal"] = unified_signal
        unified_oof.append(unified_piece)

        fold_row: dict[str, Any] = {
            "fold": fold.number,
            "train_end": str(fold.train_end.date()),
            "validation_start": str(fold.validation_start.date()),
            "validation_end": str(fold.validation_end.date()),
            "frozen_v3": metrics_from_signal(validation_all, frozen_signal),
            "unified_refit": metrics_from_signal(validation_all, unified_signal),
            "unified_weights": _weight_dict(unified_fit),
            "unified_prior_runup_direction": unified_fit.prior_runup_direction,
            "regimes": {},
        }

        print(
            f"Fold {fold.number}: train through {fold.train_end.date()} | "
            f"validate {fold.validation_start.date()}..{fold.validation_end.date()}"
        )
        fold_hybrid_pieces: list[pd.DataFrame] = []

        for regime in ("Modern", "Mid-era", "Vintage"):
            train = train_all[train_all["v4_regime"] == regime].copy()
            validation = validation_all[validation_all["v4_regime"] == regime].copy()
            if len(train) < args.min_regime_train_rows or len(validation) < args.min_regime_validation_rows:
                raise SystemExit(
                    f"Fold {fold.number} {regime} has insufficient rows "
                    f"(train={len(train)}, validation={len(validation)}). "
                    "Keep the current segmentation but lower thresholds only after inspecting coverage."
                )
            fit = fit_regime_model(
                train,
                candidates_count=args.candidates,
                seed=args.seed + 100003 * fold.number + 1009 * (1 + list(hypothesis["regimes"]).index(regime)),
            )
            regime_fits[regime].append(fit)
            signal = _ranking_score(validation, fit.weights, fit.prior_runup_direction)
            frozen_regime = pd.to_numeric(validation["frozen_v3_ranking_score"], errors="coerce")
            metrics = metrics_from_signal(validation, signal)
            frozen_metrics = metrics_from_signal(validation, frozen_regime)

            piece = validation.copy()
            piece["signal"] = signal
            regime_oof.append(piece)

            hybrid_piece = validation.copy()
            hybrid_piece["signal"] = _hybrid_signal_for_regime(
                regime,
                signal,
                frozen_regime,
            )
            hybrid_oof.append(hybrid_piece)
            fold_hybrid_pieces.append(hybrid_piece)

            fold_row["regimes"][regime] = {
                "train_rows": int(len(train)),
                "validation_rows": int(len(validation)),
                "weights": _weight_dict(fit),
                "prior_runup_direction": int(fit.prior_runup_direction),
                "regime_model": metrics,
                "frozen_v3": frozen_metrics,
                "delta_rank_ic": None
                if metrics.get("rank_ic") is None or frozen_metrics.get("rank_ic") is None
                else float(metrics["rank_ic"] - frozen_metrics["rank_ic"]),
                "delta_spread": None
                if metrics.get("top_bottom_spread") is None
                or frozen_metrics.get("top_bottom_spread") is None
                else float(metrics["top_bottom_spread"] - frozen_metrics["top_bottom_spread"]),
            }
            print(
                f"  {regime:<8} train={len(train):>4} val={len(validation):>4}  "
                f"IC {_fmt(metrics.get('rank_ic'))} vs V3 {_fmt(frozen_metrics.get('rank_ic'))}  "
                f"spread {_fmt(metrics.get('top_bottom_spread'), pct=True)} vs "
                f"{_fmt(frozen_metrics.get('top_bottom_spread'), pct=True)}"
            )
            print(
                f"           weights={_weight_dict(fit)}  "
                f"prior_runup={_direction_label(fit.prior_runup_direction)}"
            )

        fold_hybrid = pd.concat(fold_hybrid_pieces, ignore_index=True)
        fold_hybrid_metrics = metrics_from_signal(fold_hybrid, fold_hybrid["signal"])
        fold_row["hybrid_v4"] = fold_hybrid_metrics
        fold_row["hybrid_delta_rank_ic"] = (
            None
            if fold_hybrid_metrics.get("rank_ic") is None
            or fold_row["frozen_v3"].get("rank_ic") is None
            else float(
                fold_hybrid_metrics["rank_ic"] - fold_row["frozen_v3"]["rank_ic"]
            )
        )
        fold_row["hybrid_delta_spread"] = (
            None
            if fold_hybrid_metrics.get("top_bottom_spread") is None
            or fold_row["frozen_v3"].get("top_bottom_spread") is None
            else float(
                fold_hybrid_metrics["top_bottom_spread"]
                - fold_row["frozen_v3"]["top_bottom_spread"]
            )
        )
        print(
            f"  Hybrid   IC {_fmt(fold_hybrid_metrics.get('rank_ic'))} vs V3 "
            f"{_fmt(fold_row['frozen_v3'].get('rank_ic'))}  spread "
            f"{_fmt(fold_hybrid_metrics.get('top_bottom_spread'), pct=True)} vs "
            f"{_fmt(fold_row['frozen_v3'].get('top_bottom_spread'), pct=True)}"
        )
        print()
        fold_reports.append(fold_row)

    frozen_panel = pd.concat(frozen_oof, ignore_index=True)
    unified_panel = pd.concat(unified_oof, ignore_index=True)
    regime_panel = pd.concat(regime_oof, ignore_index=True)
    hybrid_panel = pd.concat(hybrid_oof, ignore_index=True)

    frozen_combined = metrics_from_signal(frozen_panel, frozen_panel["signal"])
    unified_combined = metrics_from_signal(unified_panel, unified_panel["signal"])
    regime_combined = metrics_from_signal(regime_panel, regime_panel["signal"])
    hybrid_combined = metrics_from_signal(hybrid_panel, hybrid_panel["signal"])

    print("OUT-OF-FOLD COMBINED COMPARISON")
    print("-" * 108)
    _print_metric_line("Frozen V3", frozen_combined)
    _print_metric_line("Unified 5-factor refit", unified_combined)
    _print_metric_line("3-regime V4 hypothesis", regime_combined)
    _print_metric_line("Hybrid V4 challenger", hybrid_combined)
    print(
        "Hybrid definition: Modern=regime V4 · Mid-era=frozen V3 · "
        "Vintage=regime V4"
    )
    print()

    regime_summaries: dict[str, Any] = {}
    print("REGIME-BY-REGIME OUT-OF-FOLD COMPARISON")
    print("-" * 108)
    for regime in ("Modern", "Mid-era", "Vintage"):
        v4 = regime_panel[regime_panel["v4_regime"] == regime]
        v3 = frozen_panel[frozen_panel["v4_regime"] == regime]
        v4_metrics = metrics_from_signal(v4, v4["signal"])
        v3_metrics = metrics_from_signal(v3, v3["signal"])
        regime_summaries[regime] = {
            "regime_model": v4_metrics,
            "frozen_v3": v3_metrics,
            "delta_rank_ic": None
            if v4_metrics.get("rank_ic") is None or v3_metrics.get("rank_ic") is None
            else float(v4_metrics["rank_ic"] - v3_metrics["rank_ic"]),
            "delta_spread": None
            if v4_metrics.get("top_bottom_spread") is None
            or v3_metrics.get("top_bottom_spread") is None
            else float(v4_metrics["top_bottom_spread"] - v3_metrics["top_bottom_spread"]),
            "fit_stability": _fit_stability(regime_fits[regime]),
        }
        _print_metric_line(f"{regime} V4", v4_metrics)
        _print_metric_line(f"{regime} frozen V3", v3_metrics)
        print(
            f"  delta: IC={_fmt(regime_summaries[regime]['delta_rank_ic'])}  "
            f"Q5-Q1={_fmt(regime_summaries[regime]['delta_spread'], pct=True)}"
        )
    print()

    print("WEIGHT STABILITY — REGIME FITS ACROSS OUTER FOLDS")
    print("-" * 108)
    for regime in ("Modern", "Mid-era", "Vintage"):
        stability = _fit_stability(regime_fits[regime])
        print(f"{regime}:")
        for fold_number, fit in enumerate(regime_fits[regime], start=1):
            print(
                f"  F{fold_number}: {_weight_dict(fit)}  "
                f"prior_runup={_direction_label(fit.prior_runup_direction)}"
            )
        summary = []
        for name in FACTOR_NAMES:
            item = stability["weights"][name]
            summary.append(
                f"{name} {item['mean']:.1f}±{item['std']:.1f} "
                f"[{item['min']:.1f},{item['max']:.1f}]"
            )
        print("  stability: " + " | ".join(summary))
        directions = stability["prior_runup_direction"]
        print(
            "  prior-run-up direction: "
            f"frozen={directions['frozen_negative_direction_folds']} folds · "
            f"reversed={directions['reversed_positive_direction_folds']} folds"
        )
    print()

    # Paired-fold summaries make the regime claim harder to win through a single
    # lucky period. These are descriptive research gates, not production rules.
    paired: dict[str, Any] = {}
    for regime in ("Modern", "Mid-era", "Vintage"):
        deltas_ic = []
        deltas_spread = []
        for row in fold_reports:
            item = row["regimes"][regime]
            if item["delta_rank_ic"] is not None:
                deltas_ic.append(float(item["delta_rank_ic"]))
            if item["delta_spread"] is not None:
                deltas_spread.append(float(item["delta_spread"]))
        paired[regime] = {
            "mean_delta_rank_ic": float(np.mean(deltas_ic)) if deltas_ic else None,
            "ic_win_fold_rate": float(np.mean(np.array(deltas_ic) > 0)) if deltas_ic else None,
            "mean_delta_spread": float(np.mean(deltas_spread)) if deltas_spread else None,
            "spread_win_fold_rate": float(np.mean(np.array(deltas_spread) > 0))
            if deltas_spread
            else None,
        }

    print("PAIRED FOLD DELTAS VS FROZEN V3")
    print("-" * 108)
    for regime, row in paired.items():
        print(
            f"{regime:<10} mean dIC={_fmt(row['mean_delta_rank_ic'])}  "
            f"IC-win folds={_fmt(row['ic_win_fold_rate'], pct=True)}  "
            f"mean dSpread={_fmt(row['mean_delta_spread'], pct=True)}  "
            f"spread-win folds={_fmt(row['spread_win_fold_rate'], pct=True)}"
        )

    hybrid_delta_ic = [
        float(row["hybrid_delta_rank_ic"])
        for row in fold_reports
        if row.get("hybrid_delta_rank_ic") is not None
    ]
    hybrid_delta_spread = [
        float(row["hybrid_delta_spread"])
        for row in fold_reports
        if row.get("hybrid_delta_spread") is not None
    ]
    hybrid_paired = {
        "mean_delta_rank_ic": float(np.mean(hybrid_delta_ic)) if hybrid_delta_ic else None,
        "ic_win_fold_rate": float(np.mean(np.array(hybrid_delta_ic) > 0))
        if hybrid_delta_ic
        else None,
        "mean_delta_spread": float(np.mean(hybrid_delta_spread))
        if hybrid_delta_spread
        else None,
        "spread_win_fold_rate": float(np.mean(np.array(hybrid_delta_spread) > 0))
        if hybrid_delta_spread
        else None,
    }
    print(
        f"{'Hybrid':<10} mean dIC={_fmt(hybrid_paired['mean_delta_rank_ic'])}  "
        f"IC-win folds={_fmt(hybrid_paired['ic_win_fold_rate'], pct=True)}  "
        f"mean dSpread={_fmt(hybrid_paired['mean_delta_spread'], pct=True)}  "
        f"spread-win folds={_fmt(hybrid_paired['spread_win_fold_rate'], pct=True)}"
    )
    print()

    print("INTERPRETATION GUARDRAIL")
    print("-" * 108)
    print(
        "This run tests whether segmentation adds value beyond (a) frozen V3 and "
        "(b) simply refitting the same five factors once across all eras, and whether "
        "the narrower Modern/Vintage-only hybrid keeps more of V3's broad ranking strength."
    )
    print(
        "Do not promote V4 from this historical development evidence alone. A regime "
        "split should improve multiple folds and survive the prospective lockbox."
    )
    print(
        "Stable panel membership is preserved across era additions, but analogue scores "
        "use the current expanded historical pool and may change when a new era is added."
    )

    report = {
        "hypothesis": hypothesis,
        "config": {
            "price_floor": float(args.price_floor),
            "folds": int(args.folds),
            "embargo_days": int(args.embargo_days),
            "min_train_fraction": float(args.min_train_fraction),
            "candidates": int(args.candidates),
            "seed": int(args.seed),
            "recent_market_regime_start": str(recent_start.date()),
            "stable_sample_rate": float(policy["stable_sample_rate"]),
        },
        "panel": {
            "rows": int(len(primary)),
            "cards": int(primary["product_id"].nunique()),
            "dates": int(primary["formation_date"].nunique()),
            "formation_start": str(primary["formation_date"].min().date()),
            "formation_end": str(primary["formation_date"].max().date()),
        },
        "folds": fold_reports,
        "combined": {
            "frozen_v3": frozen_combined,
            "unified_refit": unified_combined,
            "regime_v4": regime_combined,
            "hybrid_v4": hybrid_combined,
            "hybrid_definition": {
                "Modern": "regime_v4",
                "Mid-era": "frozen_v3",
                "Vintage": "regime_v4",
            },
            "unified_fit_stability": _fit_stability(unified_fits),
        },
        "regimes": regime_summaries,
        "paired_fold_deltas": paired,
        "hybrid_paired_fold_deltas": hybrid_paired,
        "methodology_note": (
            "Research-only development evidence using the policy-locked recent 2024+ market regime, "
            "stable hash sample, walk-forward folds and 90-day outcome embargo. Production V3 is unchanged."
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_clean_json(report), indent=2))
    print()
    print(f"Saved report: {output}")


if __name__ == "__main__":
    main()
