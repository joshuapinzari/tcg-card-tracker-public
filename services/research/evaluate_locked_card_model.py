"""Evaluate the frozen V3 Card Analyst specification without retuning it.

This is a historical robustness evaluator, not a new model search.  It loads the
immutable V3 registry entry, reconstructs the exact frozen factors point-in-time,
and measures realised 90-day outcomes.  No weights, directions, thresholds,
analogue count, or feature set are fitted by this command.

Historical outcomes in the research database are development-era information, so
this report must not be presented as a prospective lockbox.  Its purpose is to
answer a narrower question: does the already-frozen specification remain useful
when the historical universe is expanded, for example by adding older-era cards?
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.analysis.opportunity_model import add_setup_flags
from services.analysis.regime_overheating_signal import classify_market_regime
from services.analysis.research_rank import (
    LOCKED_ANALOGS,
    LOCKED_MODEL_VERSION,
    LOCKED_PRIMARY_PRICE_FLOOR_USD,
    LOCKED_WEIGHTS,
    _momentum_rating,
    _overheating_rating,
    _recovery_rating,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_SPEC_PATH = PROJECT_ROOT / "models" / "card_rank_v3.json"
EVALUATION_POLICY_PATH = PROJECT_ROOT / "models" / "frozen_v3_evaluation_policy.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "research_outputs" / "frozen_v3_historical_evaluation.json"


def load_evaluation_policy(path: Path = EVALUATION_POLICY_PATH) -> dict[str, Any]:
    """Load the fixed historical-evaluation policy for frozen V3.

    The policy is deliberately separate from the immutable model specification.
    It controls which market regime is considered relevant and how evaluation
    rows are selected without allowing later universe expansions to reshuffle
    previously selected rows.
    """

    policy = json.loads(path.read_text())
    start = pd.Timestamp(policy.get("market_regime_start"))
    rate = float(policy.get("stable_sample_rate", 0.0))
    salt = str(policy.get("stable_sample_salt") or "")
    if pd.isna(start):
        raise RuntimeError("Evaluation policy requires a valid market_regime_start.")
    if not 0.0 < rate <= 1.0:
        raise RuntimeError("Evaluation policy stable_sample_rate must be in (0, 1].")
    if not salt:
        raise RuntimeError("Evaluation policy requires a stable_sample_salt.")
    return policy


def _stable_hash_sample(
    frame: pd.DataFrame,
    sample_rate: float,
    salt: str,
) -> pd.DataFrame:
    """Select rows with an append-stable deterministic hash gate.

    Each row is admitted independently using only immutable identity fields.
    Therefore adding a new era can add new sampled rows but cannot evict or
    replace rows that were already selected from existing eras.
    """

    rate = float(sample_rate)
    if rate >= 1.0:
        return frame.sort_values(["formation_date", "product_id"]).reset_index(drop=True)
    if rate <= 0.0:
        raise ValueError("sample_rate must be greater than zero")

    threshold = int(rate * (1 << 64))

    def include(row: pd.Series) -> bool:
        formation = pd.Timestamp(row["formation_date"]).date().isoformat()
        product_id = int(row["product_id"])
        printing = str(row.get("printing") or "")
        payload = f"{salt}|{formation}|{product_id}|{printing}".encode("utf-8")
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        return int.from_bytes(digest, "big") < threshold

    mask = frame.apply(include, axis=1)
    sampled = frame.loc[mask].copy()
    return sampled.sort_values(["formation_date", "product_id"]).reset_index(drop=True)


def _within_date_rank(values: pd.Series, dates: pd.Series) -> pd.Series:
    frame = pd.DataFrame(
        {"date": dates.to_numpy(), "value": values.to_numpy()},
        index=values.index,
    )
    return frame.groupby("date")["value"].rank(method="average", pct=True)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the frozen-model evaluator."""

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the exact frozen V3 Card Analyst specification on point-in-time "
            "historical states. No fitting or feature selection is performed."
        )
    )
    parser.add_argument(
        "--price-floor",
        type=float,
        default=10.0,
        help="Lowest formation-price cohort to build; $20/$50 robustness is also reported.",
    )
    policy = load_evaluation_policy()
    parser.add_argument(
        "--formation-start",
        default=str(policy["market_regime_start"]),
        help=(
            "Earliest formation date admitted to the historical robustness panel. "
            "May be moved later for sensitivity checks but cannot precede the fixed recent-market-regime boundary."
        ),
    )
    parser.add_argument(
        "--time-blocks",
        type=int,
        default=4,
        help="Chronological blocks used to show fixed-model stability over time.",
    )
    parser.add_argument(
        "--min-era-rows",
        type=int,
        default=80,
        help="Minimum sampled states required before an era is reported separately.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def load_frozen_model_spec(path: Path = MODEL_SPEC_PATH) -> dict[str, Any]:
    """Load and validate the immutable V3 registry entry.

    Parameters
    ----------
    path:
        JSON model-registry entry to load.

    Returns
    -------
    dict
        Validated model specification.

    Raises
    ------
    RuntimeError
        If the registry has drifted from the production constants used by the
        live dashboard scorer.
    """

    spec = json.loads(path.read_text())
    expected = {
        "model_version": LOCKED_MODEL_VERSION,
        "analogue_neighbors": LOCKED_ANALOGS,
        "primary_price_floor_usd": LOCKED_PRIMARY_PRICE_FLOOR_USD,
        "weights": LOCKED_WEIGHTS,
    }
    mismatches = [key for key, value in expected.items() if spec.get(key) != value]
    if mismatches:
        names = ", ".join(mismatches)
        raise RuntimeError(
            f"Frozen model registry drift detected ({names}). "
            "Do not evaluate until the registry and production scorer agree."
        )
    if spec.get("status") != "frozen":
        raise RuntimeError("V3 registry entry must remain marked as frozen.")
    if int(spec.get("prediction_horizon_days") or 0) != 90:
        raise RuntimeError("V3 evaluation currently requires the frozen 90-day horizon.")
    return spec


def _prior_runup_rating(panel: pd.DataFrame) -> pd.Series:
    values = pd.to_numeric(panel["prior_runup"], errors="coerce")
    frame = pd.DataFrame(
        {"date": panel["formation_date"].to_numpy(), "value": values.to_numpy()},
        index=panel.index,
    )
    percentile = frame.groupby("date")["value"].rank(method="average", pct=True)
    # Production V3 learned a negative direction: large prior run-ups are penalised.
    return -4.0 * (percentile - 0.50)


def build_frozen_factor_panel(
    full_history: pd.DataFrame,
    *,
    price_floor: float,
    formation_start: str | pd.Timestamp,
    sample_rate: float,
    sample_salt: str,
) -> pd.DataFrame:
    """Reconstruct only the five factors used by frozen V3.

    Parameters
    ----------
    full_history:
        Point-in-time raw opportunity history with realised 90-day outcomes.
    price_floor:
        Formation-price floor applied before sampling.
    formation_start:
        Fixed recent-market-regime boundary. Older cards remain eligible, but
        historical market states before this date are excluded.
    sample_rate:
        Append-stable deterministic inclusion rate.
    sample_salt:
        Fixed salt used to make row inclusion reproducible across expansions.

    Returns
    -------
    pandas.DataFrame
        Historical rows with exact frozen-factor ratings, scores and coverage.
    """

    history = full_history.copy()
    history["formation_date"] = pd.to_datetime(history["formation_date"])
    regime_start = pd.Timestamp(formation_start)
    history = history[history["formation_date"] >= regime_start].copy()
    if history.empty:
        raise SystemExit(
            f"No historical rows remain on or after recent-regime start {regime_start.date()}."
        )

    history["market_percentile"] = history.groupby("formation_date")["return_90d"].rank(
        method="average", pct=True
    )
    history["market_median_return_90d"] = history.groupby("formation_date")[
        "return_90d"
    ].transform("median")
    history["target_rank"] = history.groupby("formation_date")["future_set_alpha_90d"].rank(
        method="average", pct=True
    )

    eligible = history[
        (pd.to_numeric(history["formation_price"], errors="coerce") >= float(price_floor))
        & history["future_set_alpha_90d"].notna()
    ].copy()
    if eligible.empty:
        raise SystemExit(f"No historical rows at or above ${price_floor:.2f} formation price.")

    eligible = add_setup_flags(eligible)
    panel = _stable_hash_sample(eligible, float(sample_rate), str(sample_salt)).copy()
    if panel.empty:
        raise SystemExit(
            "Stable evaluation sample is empty. Increase --sample-rate or check the regime window."
        )

    panel["rating_Momentum profile"] = [
        _momentum_rating(r30, r90)
        for r30, r90 in zip(panel["return_30d"], panel["return_90d"])
    ]

    recovery: list[float | None] = []
    overheating: list[float | None] = []
    for row in panel.to_dict(orient="records"):
        recovery.append(_recovery_rating(row))
        market_median = row.get("market_median_return_90d")
        if market_median is None or pd.isna(market_median):
            market_regime = None
        else:
            market_regime = classify_market_regime(float(market_median))
        rating, _ = _overheating_rating(row, market_regime)
        overheating.append(rating)

    panel["rating_Recovery / drawdown"] = recovery
    panel["rating_Validated overheating risk"] = overheating
    panel["rating_Prior run-up"] = _prior_runup_rating(panel)

    # Lazy import keeps the pure scoring/evaluation helpers usable without a
    # database driver; the heavy research dependency is needed only here.
    from services.research.history import _analogue_ratings

    analogue_rating, analogue_quality = _analogue_ratings(
        panel,
        history,
        LOCKED_ANALOGS,
    )
    panel["rating_Historical analogues"] = analogue_rating
    panel["quality_Historical analogues"] = analogue_quality

    for factor in LOCKED_WEIGHTS:
        rating_col = f"rating_{factor}"
        quality_col = f"quality_{factor}"
        if quality_col not in panel.columns:
            panel[quality_col] = panel[rating_col].notna().astype(float)
        else:
            panel[quality_col] = np.where(
                panel[rating_col].notna(),
                pd.to_numeric(panel[quality_col], errors="coerce").fillna(0.0),
                0.0,
            )

    research_score, ranking_score, coverage = score_frozen_panel(panel)
    panel["frozen_v3_research_score"] = research_score
    panel["frozen_v3_ranking_score"] = ranking_score
    panel["frozen_v3_coverage"] = coverage
    return panel


def score_frozen_panel(panel: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Score a factor panel with the exact immutable V3 weights.

    Parameters
    ----------
    panel:
        DataFrame containing ``rating_<factor>`` and ``quality_<factor>`` columns.

    Returns
    -------
    tuple of pandas.Series
        Research score, coverage-adjusted ranking score, and model coverage.
    """

    names = list(LOCKED_WEIGHTS)
    weights = np.array([LOCKED_WEIGHTS[name] for name in names], dtype=float)
    ratings = np.column_stack(
        [pd.to_numeric(panel[f"rating_{name}"], errors="coerce").fillna(0.0) for name in names]
    ).astype(float)
    quality = np.column_stack(
        [pd.to_numeric(panel[f"quality_{name}"], errors="coerce").fillna(0.0) for name in names]
    ).astype(float)

    weighted_quality = quality * weights.reshape(1, -1)
    denominator = weighted_quality.sum(axis=1)
    numerator = (ratings * weighted_quality).sum(axis=1)
    normalized_rating = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 0,
    )

    research = np.clip(50.0 + 25.0 * normalized_rating, 0.0, 100.0)
    coverage = denominator / float(weights.sum())
    ranking = np.clip(50.0 + (research - 50.0) * coverage, 0.0, 100.0)
    return (
        pd.Series(research, index=panel.index, dtype=float),
        pd.Series(ranking, index=panel.index, dtype=float),
        pd.Series(coverage, index=panel.index, dtype=float),
    )


def metrics_from_signal(panel: pd.DataFrame, signal: pd.Series) -> dict[str, Any]:
    """Calculate rank and realised-return diagnostics for one fixed signal.

    Parameters
    ----------
    panel:
        Historical evaluation rows.
    signal:
        Fixed model or baseline score aligned to ``panel``.

    Returns
    -------
    dict
        Rank IC, Q5-Q1 alpha spread, top-20 realised outcomes and coverage counts.
    """

    if panel.empty:
        return {"n": 0}

    numeric_signal = pd.to_numeric(signal, errors="coerce")
    score_rank = _within_date_rank(numeric_signal, panel["formation_date"])
    target_rank = pd.to_numeric(panel["target_rank"], errors="coerce")
    valid = (
        numeric_signal.notna()
        & score_rank.notna()
        & target_rank.notna()
        & panel["future_set_alpha_90d"].notna()
    )
    if int(valid.sum()) < 20:
        return {"n": int(valid.sum())}

    working = panel.loc[valid].copy()
    working["score_rank"] = score_rank.loc[valid]
    rank_ic = float(working["score_rank"].corr(working["target_rank"]))
    bottom = working[working["score_rank"] <= 0.20]
    top = working[working["score_rank"] >= 0.80]
    bottom_alpha = float(bottom["future_set_alpha_90d"].mean()) if not bottom.empty else np.nan
    top_alpha = float(top["future_set_alpha_90d"].mean()) if not top.empty else np.nan

    return {
        "n": int(len(working)),
        "cards": int(working["product_id"].nunique()),
        "dates": int(working["formation_date"].nunique()),
        "rank_ic": rank_ic,
        "bottom20_alpha": bottom_alpha,
        "top20_alpha": top_alpha,
        "top_bottom_spread": top_alpha - bottom_alpha,
        "top20_return": float(top["future_return_90d"].mean()) if not top.empty else np.nan,
        "top20_p_gain15": float((top["future_return_90d"] >= 0.15).mean())
        if not top.empty
        else np.nan,
        "top20_p_crash15": float((top["future_return_90d"] <= -0.15).mean())
        if not top.empty
        else np.nan,
    }


def _time_block_metrics(panel: pd.DataFrame, signal_col: str, blocks: int) -> list[dict[str, Any]]:
    dates = np.array(sorted(panel["formation_date"].dropna().unique()))
    result: list[dict[str, Any]] = []
    for number, date_block in enumerate(np.array_split(dates, max(1, int(blocks))), start=1):
        if not len(date_block):
            continue
        subset = panel[panel["formation_date"].isin(date_block)]
        metrics = metrics_from_signal(subset, subset[signal_col])
        result.append(
            {
                "block": number,
                "start": str(pd.Timestamp(date_block[0]).date()),
                "end": str(pd.Timestamp(date_block[-1]).date()),
                **metrics,
            }
        )
    return result


def _era_metrics(panel: pd.DataFrame, signal_col: str, min_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for era, group in panel.groupby("era", dropna=False):
        if len(group) < int(min_rows):
            continue
        metrics = metrics_from_signal(group, group[signal_col])
        rows.append({"era": "Unknown" if pd.isna(era) else str(era), **metrics})
    return sorted(rows, key=lambda row: int(row.get("n") or 0), reverse=True)


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _fmt(value: Any, *, percent: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not np.isfinite(number):
        return "N/A"
    return f"{number * 100:+.2f}%" if percent else f"{number:+.3f}"


def _print_metrics(label: str, metrics: dict[str, Any]) -> None:
    print(
        f"{label:<24} N={metrics.get('n', 0):>5} cards={metrics.get('cards', 0):>4}  "
        f"IC={_fmt(metrics.get('rank_ic'))}  "
        f"Q5-Q1={_fmt(metrics.get('top_bottom_spread'), percent=True)}  "
        f"top20 alpha={_fmt(metrics.get('top20_alpha'), percent=True)}  "
        f"P(+15)={_fmt(metrics.get('top20_p_gain15'), percent=True)}  "
        f"P(crash)={_fmt(metrics.get('top20_p_crash15'), percent=True)}"
    )


def main() -> None:
    """Run frozen V3 historical robustness evaluation and save a JSON report."""

    args = parse_args()
    spec = load_frozen_model_spec()
    policy = load_evaluation_policy()
    policy_start = pd.Timestamp(policy["market_regime_start"])
    requested_start = pd.Timestamp(args.formation_start)
    if requested_start < policy_start:
        raise SystemExit(
            f"formation-start {requested_start.date()} predates the locked recent-market-regime "
            f"boundary {policy_start.date()}. Move it later, not earlier."
        )
    stable_sample_rate = float(policy["stable_sample_rate"])
    stable_sample_salt = str(policy["stable_sample_salt"])

    print("=" * 104)
    print("FROZEN V3 EVALUATION — NO FITTING / NO FEATURE SELECTION")
    print("=" * 104)
    print(f"Model: {spec['model_version']} | frozen: {spec['frozen_on']}")
    print(f"Weights: {spec['weights']}")
    print(f"Analogue neighbours: {LOCKED_ANALOGS}")
    print(f"Historical sample floor: ${args.price_floor:.2f}")
    print(
        f"Recent market regime: {requested_start.date()} onward | "
        f"stable hash sample: {stable_sample_rate:.1%} (policy-locked)"
    )
    print("Production weights and model structure will NOT be changed.")
    print(
        "NOTE: this is a historical robustness check on the frozen specification, "
        "not the prospective 30D/90D lockbox."
    )
    print()

    # Database access is deliberately lazy so importing this module does not
    # require PostgreSQL just to inspect the frozen registry or unit-test scoring.
    from services.research.history import _load_history

    history = _load_history()
    panel = build_frozen_factor_panel(
        history,
        price_floor=args.price_floor,
        formation_start=requested_start,
        sample_rate=stable_sample_rate,
        sample_salt=stable_sample_salt,
    )

    print()
    print(
        f"Evaluation panel: {len(panel):,} rows / {panel['product_id'].nunique():,} cards / "
        f"{panel['formation_date'].nunique():,} dates"
    )
    print(
        f"Formation range: {panel['formation_date'].min().date()} -> "
        f"{panel['formation_date'].max().date()}"
    )
    print(f"Mean frozen-model coverage: {panel['frozen_v3_coverage'].mean() * 100:.1f}%")

    price_floor_results: dict[str, Any] = {}
    print("\nPRICE-FLOOR ROBUSTNESS — EXACT FROZEN V3")
    print("-" * 104)
    for floor in (10.0, 20.0, 50.0):
        if floor < args.price_floor:
            continue
        subset = panel[panel["formation_price"] >= floor].copy()
        research_metrics = metrics_from_signal(subset, subset["frozen_v3_research_score"])
        ranking_metrics = metrics_from_signal(subset, subset["frozen_v3_ranking_score"])
        price_floor_results[f"{floor:.0f}"] = {
            "research_score": research_metrics,
            "coverage_adjusted_ranking_score": ranking_metrics,
        }
        _print_metrics(f">= ${floor:.0f} research", research_metrics)
        _print_metrics(f">= ${floor:.0f} ranking", ranking_metrics)

    primary = panel[panel["formation_price"] >= LOCKED_PRIMARY_PRICE_FLOOR_USD].copy()
    baselines = {
        "momentum_only": metrics_from_signal(
            primary,
            pd.to_numeric(primary["rating_Momentum profile"], errors="coerce"),
        ),
        "analogue_only": metrics_from_signal(
            primary,
            pd.to_numeric(primary["rating_Historical analogues"], errors="coerce"),
        ),
        "raw_90d_momentum": metrics_from_signal(
            primary,
            pd.to_numeric(primary["return_90d"], errors="coerce"),
        ),
    }

    print("\nPRIMARY $20+ BASELINES")
    print("-" * 104)
    for name, metrics in baselines.items():
        _print_metrics(name, metrics)

    time_blocks = _time_block_metrics(
        primary,
        "frozen_v3_ranking_score",
        args.time_blocks,
    )
    print("\nTIME STABILITY — $20+ COVERAGE-ADJUSTED FROZEN V3")
    print("-" * 104)
    for row in time_blocks:
        _print_metrics(f"B{row['block']} {row['start']}..", row)

    eras = _era_metrics(
        primary,
        "frozen_v3_ranking_score",
        args.min_era_rows,
    )
    print("\nERA ROBUSTNESS — $20+ WITHIN-ERA RANKING")
    print("-" * 104)
    for row in eras:
        _print_metrics(str(row["era"]), row)

    older_era_checks = [
        row
        for label in ("Diamond & Pearl", "Platinum", "HGSS")
        for row in eras
        if str(row.get("era")) == label
    ]
    if older_era_checks:
        print("\nOLDER-ERA CHECKS")
        print("-" * 104)
        for row in older_era_checks:
            _print_metrics(f"{row['era']} frozen V3", row)

    report = {
        "report_type": "frozen_v3_historical_robustness",
        "prospective_lockbox": False,
        "warning": (
            "Historical outcomes were already available when this report was produced. "
            "Do not describe these metrics as prospective lockbox performance."
        ),
        "model_spec": spec,
        "evaluation_policy": policy,
        "evaluation": {
            "sample_price_floor_usd": float(args.price_floor),
            "formation_start_policy": str(requested_start.date()),
            "stable_sample_rate": stable_sample_rate,
            "stable_sample_salt": stable_sample_salt,
            "rows": int(len(panel)),
            "cards": int(panel["product_id"].nunique()),
            "dates": int(panel["formation_date"].nunique()),
            "formation_start": str(panel["formation_date"].min().date()),
            "formation_end": str(panel["formation_date"].max().date()),
            "mean_coverage": float(panel["frozen_v3_coverage"].mean()),
        },
        "price_floor_robustness": price_floor_results,
        "primary_20_plus_baselines": baselines,
        "time_blocks_20_plus": time_blocks,
        "era_robustness_20_plus": eras,
    }

    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_clean_json(report), indent=2, sort_keys=True) + "\n")

    print("\n" + "=" * 104)
    print("FROZEN SPECIFICATION PRESERVED")
    print("No weights, directions, factors, or analogue settings were fitted or changed.")
    print(f"Saved report: {output}")


if __name__ == "__main__":
    main()
