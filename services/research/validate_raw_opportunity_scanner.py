from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np
import pandas as pd

from database.opportunity_scanner import load_opportunity_history
from services.analysis.opportunity_model import (
    DEFAULT_CONTEXT_WEIGHT,
    SETUP_LABELS,
    add_setup_flags,
    calibrate_one,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Walk-forward validation of the context-aware raw opportunity analogue engine. "
            "Every prediction uses only historical states whose 90D outcome would already "
            "have been known at the validation formation date."
        )
    )
    parser.add_argument("--analogs", type=int, default=150)
    parser.add_argument("--max-per-setup", type=int, default=220)
    parser.add_argument(
        "--context-weights",
        default=f"0,{DEFAULT_CONTEXT_WEIGHT}",
        help="Comma-separated context weights to compare; default tests setup-only vs production context weight.",
    )
    return parser.parse_args()


def _parse_weights(raw: str) -> list[float]:
    weights: list[float] = []
    for token in raw.split(","):
        value = float(token.strip())
        if not 0 <= value <= 0.90:
            raise SystemExit("Context weights must be between 0 and 0.90.")
        if value not in weights:
            weights.append(value)
    return weights


def _sample_rows(frame: pd.DataFrame, maximum: int) -> pd.DataFrame:
    frame = frame.sort_values(["formation_date", "product_id"]).reset_index(drop=True)
    if len(frame) <= maximum:
        return frame
    positions = np.linspace(0, len(frame) - 1, maximum, dtype=int)
    return frame.iloc[positions].reset_index(drop=True)


def _rank_corr(frame: pd.DataFrame, pred: str, actual: str) -> float:
    working = frame[[pred, actual]].dropna()
    if len(working) < 8 or working[pred].nunique() < 2 or working[actual].nunique() < 2:
        return float("nan")
    return float(working[pred].rank().corr(working[actual].rank()))


def _quintile_spread(frame: pd.DataFrame) -> tuple[float, float, float]:
    working = frame[["expected_set_alpha_90d", "actual_set_alpha"]].dropna().copy()
    if len(working) < 25 or working["expected_set_alpha_90d"].nunique() < 5:
        return float("nan"), float("nan"), float("nan")
    ranks = working["expected_set_alpha_90d"].rank(method="first")
    working["bucket"] = pd.qcut(ranks, 5, labels=False, duplicates="drop") + 1
    bottom = float(working.loc[working["bucket"] == working["bucket"].min(), "actual_set_alpha"].mean())
    top = float(working.loc[working["bucket"] == working["bucket"].max(), "actual_set_alpha"].mean())
    return bottom, top, top - bottom


def _market_regime(row: pd.Series) -> str:
    r30 = row.get("market_return_30d")
    r90 = row.get("market_return_90d")
    breadth = row.get("market_breadth")
    if any(pd.isna(v) for v in (r30, r90, breadth)):
        return "Unknown"
    accel = float(r30) - float(r90) / 3.0
    if float(r30) > 0 and float(breadth) >= 0.55 and accel >= 0:
        return "Expanding"
    if float(r30) < 0 and float(breadth) < 0.45 and accel < 0:
        return "Weakening"
    return "Mixed"


def _supportive_context(row: pd.Series) -> str:
    set30 = row.get("set_return_30d")
    era30 = row.get("era_return_30d")
    set_breadth = row.get("set_breadth")
    era_breadth = row.get("era_breadth")
    if any(pd.isna(v) for v in (set30, era30, set_breadth, era_breadth)):
        return "Unknown"
    supportive = int(float(set30) > 0 and float(set_breadth) >= 0.50)
    supportive += int(float(era30) > 0 and float(era_breadth) >= 0.50)
    if supportive == 2:
        return "Set+Era supportive"
    if supportive == 1:
        return "Partly supportive"
    return "Set+Era weak"


def _print_model_summary(label: str, setup: str, frame: pd.DataFrame) -> None:
    strong_actual = (frame["actual_return"] >= 0.15).astype(float)
    crash_actual = (frame["actual_return"] <= -0.15).astype(float)
    strong_brier = float(np.mean((frame["probability_strong_gain_90d"] - strong_actual) ** 2))
    crash_brier = float(np.mean((frame["probability_crash_90d"] - crash_actual) ** 2))
    alpha_direction = float(np.mean(
        (frame["expected_set_alpha_90d"] > 0) == (frame["actual_set_alpha"] > 0)
    ))
    rank_ic = _rank_corr(frame, "expected_set_alpha_90d", "actual_set_alpha")
    bottom, top, spread = _quintile_spread(frame)

    print(
        f"{label:<12} {SETUP_LABELS[setup]:<16} N={len(frame):>4}  "
        f"pred alpha={frame['expected_set_alpha_90d'].mean()*100:+6.1f}%  "
        f"actual={frame['actual_set_alpha'].mean()*100:+6.1f}%  "
        f"rankIC={rank_ic:+.3f}  Q5-Q1={spread*100:+6.1f}pp"
    )
    print(
        f"{'':<29} actual ret={frame['actual_return'].mean()*100:+6.1f}%  "
        f"P(+15) {frame['probability_strong_gain_90d'].mean()*100:5.1f}% -> {strong_actual.mean()*100:5.1f}%  "
        f"P(crash) {frame['probability_crash_90d'].mean()*100:5.1f}% -> {crash_actual.mean()*100:5.1f}%"
    )
    print(
        f"{'':<29} alpha-dir={alpha_direction*100:5.1f}%  "
        f"Brier gain={strong_brier:.3f} crash={crash_brier:.3f}  "
        f"Q1={bottom*100:+.1f}% Q5={top*100:+.1f}%"
    )


def main() -> None:
    args = parse_args()
    context_weights = _parse_weights(args.context_weights)
    history = load_opportunity_history()
    if history.empty:
        raise SystemExit(
            "No opportunity history exists. Run python -m services.pipelines.build_raw_opportunity_research --rebuild-base first."
        )

    required = {"market_breadth", "future_era_alpha_90d", "future_market_alpha_90d"}
    if not required.issubset(history.columns) or history[list(required)].notna().sum().min() == 0:
        raise SystemExit(
            "The opportunity panel predates context-v2 labels. Run: "
            "python -m services.pipelines.build_raw_opportunity_research --rebuild-base"
        )

    history["formation_date"] = pd.to_datetime(history["formation_date"])
    history = add_setup_flags(history)
    history["market_regime"] = history.apply(_market_regime, axis=1)
    history["context_support"] = history.apply(_supportive_context, axis=1)

    dates = sorted(history["formation_date"].dropna().unique())
    if len(dates) < 8:
        raise SystemExit("Not enough formation dates for walk-forward validation.")

    validation_start = dates[int(len(dates) * 0.60)]
    outputs: dict[tuple[float, str], list[dict]] = defaultdict(list)

    for setup in ("HEADING_UP", "ERA_STRENGTH", "RECOVERY", "SUSPECTED_LOW"):
        candidates = history[
            history[f"is_{setup.lower()}"]
            & (history["formation_date"] >= validation_start)
        ].copy()
        candidates = _sample_rows(candidates, args.max_per_setup)

        for _, row in candidates.iterrows():
            cutoff = row["formation_date"] - pd.Timedelta(days=90)
            train = history[history["formation_date"] <= cutoff]
            for context_weight in context_weights:
                calibrated = calibrate_one(
                    row,
                    train,
                    setup,
                    max_analogs=args.analogs,
                    context_weight=context_weight,
                )
                if calibrated is None:
                    continue
                outputs[(context_weight, setup)].append(
                    {
                        **calibrated,
                        "formation_date": row["formation_date"],
                        "product_id": row["product_id"],
                        "market_regime": row["market_regime"],
                        "context_support": row["context_support"],
                        "actual_return": float(row["future_return_90d"]),
                        "actual_set_alpha": float(row["future_set_alpha_90d"]),
                        "actual_era_alpha": float(row["future_era_alpha_90d"]),
                        "actual_market_alpha": float(row["future_market_alpha_90d"]),
                    }
                )

    print()
    print("=" * 126)
    print("RAW OPPORTUNITY SCANNER — CONTEXT-AWARE WALK-FORWARD VALIDATION V2")
    print("=" * 126)
    print(
        "Strict point-in-time validation. Context matching uses set, era and whole-market 30D/90D returns plus breadth. "
        "Q5-Q1 tests whether the model actually ranks better candidates higher."
    )
    print()

    for context_weight in context_weights:
        label = "SETUP ONLY" if context_weight == 0 else f"CTX {context_weight:.2f}"
        print(f"--- {label} ---")
        for setup in ("HEADING_UP", "ERA_STRENGTH", "RECOVERY", "SUSPECTED_LOW"):
            rows = outputs.get((context_weight, setup), [])
            if rows:
                _print_model_summary(label, setup, pd.DataFrame(rows))
        print()

    # Context diagnostics use the production context weight if present; otherwise
    # the highest requested weight. They reveal whether a setup is robust across
    # regimes rather than only working in a rising market.
    chosen_weight = DEFAULT_CONTEXT_WEIGHT if DEFAULT_CONTEXT_WEIGHT in context_weights else max(context_weights)
    chosen_frames = [
        pd.DataFrame(rows).assign(setup=setup)
        for (weight, setup), rows in outputs.items()
        if weight == chosen_weight and rows
    ]
    if chosen_frames:
        combined = pd.concat(chosen_frames, ignore_index=True)
        print("CONTEXT ROBUSTNESS — production-weight predictions")
        print("-" * 126)
        for column in ("market_regime", "context_support"):
            grouped = combined.groupby(column, dropna=False).agg(
                n=("actual_return", "size"),
                actual_return=("actual_return", "mean"),
                set_alpha=("actual_set_alpha", "mean"),
                era_alpha=("actual_era_alpha", "mean"),
                market_alpha=("actual_market_alpha", "mean"),
                strong_gain=("actual_return", lambda x: (x >= 0.15).mean()),
                crash=("actual_return", lambda x: (x <= -0.15).mean()),
            )
            print(column.replace("_", " ").title())
            for name, row in grouped.iterrows():
                print(
                    f"  {str(name):<22} N={int(row['n']):>4}  ret={row['actual_return']*100:+6.1f}%  "
                    f"set α={row['set_alpha']*100:+6.1f}%  era α={row['era_alpha']*100:+6.1f}%  "
                    f"market α={row['market_alpha']*100:+6.1f}%  +15={row['strong_gain']*100:5.1f}%  "
                    f"crash={row['crash']*100:4.1f}%"
                )
            print()

        print("Combined production-weight predictions:", f"{len(combined):,}")
        print("Actual mean next-90D return:", f"{combined['actual_return'].mean()*100:+.1f}%")
        print("Actual mean next-90D set alpha:", f"{combined['actual_set_alpha'].mean()*100:+.1f}%")
        print("Actual mean next-90D era alpha:", f"{combined['actual_era_alpha'].mean()*100:+.1f}%")
        print("Actual mean next-90D market alpha:", f"{combined['actual_market_alpha'].mean()*100:+.1f}%")

    print("=" * 126)


if __name__ == "__main__":
    main()
