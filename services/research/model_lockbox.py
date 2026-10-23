"""Prospective V3-vs-Hybrid-V4 lockbox runner.

The lockbox is intentionally separate from historical model research.  It stores
what each frozen model said using the latest data available at capture time, then
settles immutable 30-day and 90-day outcomes only after those horizons mature.

30D is an early health check.  The 90D result is the primary model verdict.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.analysis.hybrid_rank import (
    MODEL_SPEC_PATH as HYBRID_SPEC_PATH,
    PARENT_SPEC_PATH,
    load_hybrid_spec,
    regime_for_era,
    score_hybrid_v4,
    spec_hash,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCKBOX_POLICY_PATH = PROJECT_ROOT / "models" / "model_lockbox_policy.json"


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_lockbox_policy(path: Path = LOCKBOX_POLICY_PATH) -> dict[str, Any]:
    """Load and validate the frozen prospective lockbox policy."""

    policy = json.loads(path.read_text())
    if policy.get("status") != "frozen":
        raise RuntimeError("Prospective lockbox policy must remain frozen.")
    if tuple(policy.get("horizons_days") or ()) != (30, 90):
        raise RuntimeError("Lockbox horizons must remain 30D and 90D.")
    if int(policy.get("primary_horizon_days") or 0) != 90:
        raise RuntimeError("90D must remain the primary lockbox verdict.")
    if float(policy.get("primary_price_floor_usd") or 0) != 20.0:
        raise RuntimeError("The lockbox cohort is frozen at the $20 primary price floor.")
    return policy


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def prospective_metrics(frame: pd.DataFrame, signal_col: str = "ranking_score") -> dict[str, Any]:
    """Evaluate one model prospectively without treating snapshot dates as independent rows.

    Metrics are calculated separately inside each ``as_of_date`` cross-section
    and then averaged equally across snapshot dates.  This avoids giving a run
    of near-identical weekly/daily snapshots artificial statistical weight.
    """

    required = {"as_of_date", signal_col, "set_alpha", "raw_return"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Lockbox metrics missing columns: {sorted(missing)}")

    work = frame.copy()
    work["as_of_date"] = pd.to_datetime(work["as_of_date"])
    work[signal_col] = pd.to_numeric(work[signal_col], errors="coerce")
    work["set_alpha"] = pd.to_numeric(work["set_alpha"], errors="coerce")
    work["raw_return"] = pd.to_numeric(work["raw_return"], errors="coerce")
    work = work.dropna(subset=[signal_col, "set_alpha", "raw_return"])
    if work.empty:
        return {
            "n": 0,
            "cards": 0,
            "snapshot_dates": 0,
            "rank_ic": None,
            "q5_q1": None,
            "top20_alpha": None,
            "top20_raw_return": None,
            "top20_p_gain15": None,
            "top20_p_crash15": None,
        }

    per_date: list[dict[str, float]] = []
    for _, group in work.groupby("as_of_date", sort=True):
        if len(group) < 10:
            continue
        signal_rank = group[signal_col].rank(method="average")
        target_rank = group["set_alpha"].rank(method="average")
        rank_ic = signal_rank.corr(target_rank)

        percentile = group[signal_col].rank(method="average", pct=True)
        top = group[percentile >= 0.80]
        bottom = group[percentile <= 0.20]
        if top.empty or bottom.empty:
            continue
        per_date.append(
            {
                "rank_ic": float(rank_ic) if pd.notna(rank_ic) else np.nan,
                "q5_q1": float(top["set_alpha"].mean() - bottom["set_alpha"].mean()),
                "top20_alpha": float(top["set_alpha"].mean()),
                "top20_raw_return": float(top["raw_return"].mean()),
                "top20_p_gain15": float((top["raw_return"] >= 0.15).mean()),
                "top20_p_crash15": float((top["raw_return"] <= -0.15).mean()),
            }
        )

    metrics = pd.DataFrame(per_date)
    result: dict[str, Any] = {
        "n": int(len(work)),
        "cards": int(work["product_id"].nunique()) if "product_id" in work else 0,
        "snapshot_dates": int(work["as_of_date"].nunique()),
    }
    for key in (
        "rank_ic",
        "q5_q1",
        "top20_alpha",
        "top20_raw_return",
        "top20_p_gain15",
        "top20_p_crash15",
    ):
        if metrics.empty or key not in metrics:
            result[key] = None
        else:
            value = metrics[key].mean(skipna=True)
            result[key] = None if pd.isna(value) else float(value)
    return result


def _format_metric(value: Any, *, percent: bool = False) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:+.2f}%" if percent else f"{float(value):+.3f}"


def _print_metrics(label: str, metrics: dict[str, Any]) -> None:
    print(
        f"{label:<28} N={metrics['n']:5d} cards={metrics['cards']:4d} "
        f"dates={metrics['snapshot_dates']:2d}  "
        f"IC={_format_metric(metrics['rank_ic'])}  "
        f"Q5-Q1={_format_metric(metrics['q5_q1'], percent=True)}  "
        f"top20 alpha={_format_metric(metrics['top20_alpha'], percent=True)}  "
        f"P(+15)={_format_metric(metrics['top20_p_gain15'], percent=True)}  "
        f"P(crash)={_format_metric(metrics['top20_p_crash15'], percent=True)}"
    )


def _load_db_module():
    # Lazy import keeps pure lockbox metric tests usable without psycopg.
    from database import model_lockbox as db

    return db


def snapshot_predictions(*, allow_early: bool = False) -> dict[str, Any]:
    """Capture one immutable V3/V4 prediction cohort from the latest raw data date."""

    policy = load_lockbox_policy()
    hybrid_spec = load_hybrid_spec()
    parent_spec = json.loads(PARENT_SPEC_PATH.read_text())
    db = _load_db_module()

    status = db.status_summary()
    latest_data = status.get("latest_data_date")
    if latest_data is None:
        raise SystemExit("No RAW daily price data is available for a lockbox snapshot.")
    cadence = int(policy["recommended_snapshot_cadence_days"])
    last_snapshot = status.get("last_snapshot")
    if last_snapshot is not None and latest_data <= last_snapshot:
        print(f"Snapshot already exists for latest data date {latest_data}; nothing to capture.")
        return {"as_of_date": latest_data, "inserted": 0, "skipped": True}
    if (
        last_snapshot is not None
        and not allow_early
        and (latest_data - last_snapshot).days < cadence
    ):
        next_date = last_snapshot + timedelta(days=cadence)
        print(
            f"Latest lockbox cohort is {last_snapshot}; weekly cadence is policy-recommended. "
            f"Next eligible data date: {next_date}. Use --allow-early only for a deliberate extra cohort."
        )
        return {"as_of_date": latest_data, "inserted": 0, "skipped": True}

    # Heavy/live imports happen only when a snapshot is actually due.
    from database.opportunity_scanner import scan_raw_opportunities

    floor = float(policy["primary_price_floor_usd"])
    print(f"Scoring current RAW universe at >= ${floor:.2f} with frozen V3...")
    scan = scan_raw_opportunities(
        price_floor_usd=floor,
        max_analogs=int(hybrid_spec["analogue_neighbors"]),
    )
    if scan.empty:
        raise SystemExit("Live opportunity scan is empty; no lockbox snapshot captured.")

    scan["analysis_date"] = pd.to_datetime(scan["analysis_date"]).dt.date
    as_of_dates = sorted(set(scan["analysis_date"].dropna()))
    if len(as_of_dates) != 1:
        raise RuntimeError(f"Expected one live analysis date, found {as_of_dates}.")
    as_of_date = as_of_dates[0]
    if as_of_date != latest_data:
        raise RuntimeError(
            f"Live scanner as-of date {as_of_date} disagrees with latest RAW date {latest_data}."
        )
    if as_of_date > date.today():
        raise RuntimeError("Refusing to capture a lockbox snapshot dated in the future.")

    v3_hash = spec_hash(PARENT_SPEC_PATH)
    v4_hash = spec_hash(HYBRID_SPEC_PATH)
    policy_hash = _file_hash(LOCKBOX_POLICY_PATH)
    db.register_policy(policy["policy_version"], policy_hash, policy)
    db.register_model(parent_spec["model_version"], v3_hash, parent_spec)
    db.register_model(hybrid_spec["model_version"], v4_hash, hybrid_spec)

    baselines = db.load_context_baselines(
        as_of_date,
        lookback_days=int(policy["outcome_lookback_days"]),
    )
    market_baseline = baselines.get(("market", "Modern English Pokemon"))

    records: list[dict[str, Any]] = []
    mapped_cards = 0
    for row in scan.to_dict(orient="records"):
        era = row.get("era")
        regime = regime_for_era(era, hybrid_spec)
        if regime is None:
            continue
        mapped_cards += 1
        v3_result = {
            "research_model_version": row.get("research_model_version"),
            "research_score": row.get("research_score"),
            "research_ranking_score": row.get("research_ranking_score"),
            "research_coverage": row.get("research_coverage"),
            "research_factors": row.get("research_factors") or [],
        }
        v4_result = score_hybrid_v4(v3_result, era=era, spec=hybrid_spec)
        if v4_result is None:
            continue

        common = {
            "as_of_date": as_of_date,
            "product_id": int(row["product_id"]),
            "printing": str(row.get("printing") or "Unknown"),
            "regime": regime,
            "era": era,
            "set_name": str(row.get("set_name") or ""),
            "card_name": str(row.get("name") or ""),
            "card_number": row.get("card_number"),
            "formation_price": float(row["current_price"]),
            "formation_set_index": baselines.get(("set", str(row.get("set_name") or ""))),
            "formation_era_index": baselines.get(("era", str(era or ""))),
            "formation_market_index": market_baseline,
            "expected_set_alpha_90d": _safe_float(row.get("expected_set_alpha_90d")),
            "probability_strong_gain_90d": _safe_float(row.get("probability_strong_gain_90d")),
            "probability_crash_90d": _safe_float(row.get("probability_crash_90d")),
        }
        records.append(
            {
                **common,
                "model_version": parent_spec["model_version"],
                "research_score": float(v3_result["research_score"]),
                "ranking_score": float(v3_result["research_ranking_score"]),
                "coverage": float(v3_result["research_coverage"]),
                "factor_payload": v3_result["research_factors"],
            }
        )
        records.append(
            {
                **common,
                "model_version": hybrid_spec["model_version"],
                "research_score": float(v4_result["research_score"]),
                "ranking_score": float(v4_result["ranking_score"]),
                "coverage": float(v4_result["coverage"]),
                "factor_payload": v4_result["factor_payload"],
            }
        )

    inserted = db.capture_snapshot_with_predictions(
        as_of_date=as_of_date,
        policy_version=policy["policy_version"],
        policy_hash=policy_hash,
        price_floor_usd=floor,
        candidate_count=mapped_cards,
        records=records,
    )
    print("=" * 92)
    print("LOCKBOX SNAPSHOT CAPTURED")
    print("=" * 92)
    print(f"As-of data date: {as_of_date}")
    print(f"Eligible mapped cards: {mapped_cards:,}")
    print(f"Prediction rows attempted: {len(records):,} ({len(records)//2:,} cards x 2 models)")
    print(f"New prediction rows inserted: {inserted:,}")
    print(f"V3 spec hash: {v3_hash[:12]}...")
    print(f"V4 spec hash: {v4_hash[:12]}...")
    print("Predictions are append-only; reruns cannot overwrite this cohort.")
    return {"as_of_date": as_of_date, "inserted": inserted, "skipped": False}


def settle_outcomes() -> dict[int, int]:
    """Settle every newly mature 30D/90D outcome without rewriting old outcomes."""

    policy = load_lockbox_policy()
    db = _load_db_module()
    result: dict[int, int] = {}
    for horizon in policy["horizons_days"]:
        inserted = db.settle_matured_outcomes(
            int(horizon),
            lookback_days=int(policy["outcome_lookback_days"]),
        )
        result[int(horizon)] = inserted
        print(f"{int(horizon):>2}D outcomes newly settled: {inserted:,}")
    return result


def report(horizon_days: int) -> None:
    """Print prospective V3-vs-V4 metrics for one matured horizon."""

    policy = load_lockbox_policy()
    horizon_days = int(horizon_days)
    if horizon_days not in set(int(v) for v in policy["horizons_days"]):
        raise SystemExit("Lockbox report horizon must be 30 or 90 days.")
    db = _load_db_module()
    frame = db.load_report_rows(horizon_days)
    if frame.empty:
        label = "early health check" if horizon_days == 30 else "primary verdict"
        print(f"No matured {horizon_days}D outcomes yet ({label}).")
        return

    print("=" * 104)
    descriptor = "EARLY HEALTH CHECK" if horizon_days == 30 else "PRIMARY LOCKBOX VERDICT"
    print(f"PROSPECTIVE MODEL LOCKBOX — {horizon_days}D {descriptor}")
    print("=" * 104)
    print(
        f"Matured prediction rows: {len(frame):,} | cohorts: {frame['as_of_date'].nunique():,} | "
        f"cards: {frame['product_id'].nunique():,}"
    )
    print("Metrics are computed within each snapshot date then averaged equally across dates.")
    print()

    model_versions = list(policy["models"])
    overall: dict[str, dict[str, Any]] = {}
    for version in model_versions:
        subset = frame[frame["model_version"] == version]
        metrics = prospective_metrics(subset)
        overall[version] = metrics
        _print_metrics(version, metrics)

    if len(model_versions) == 2:
        v3, v4 = model_versions
        a, b = overall[v3], overall[v4]
        print()
        print(
            "V4 - V3: "
            f"dIC={_format_metric(None if a['rank_ic'] is None or b['rank_ic'] is None else b['rank_ic']-a['rank_ic'])}  "
            f"dQ5-Q1={_format_metric(None if a['q5_q1'] is None or b['q5_q1'] is None else b['q5_q1']-a['q5_q1'], percent=True)}  "
            f"dTop20={_format_metric(None if a['top20_alpha'] is None or b['top20_alpha'] is None else b['top20_alpha']-a['top20_alpha'], percent=True)}"
        )

    print("\nBY REGIME")
    print("-" * 104)
    for regime in ("Modern", "Mid-era", "Vintage"):
        print(f"{regime}:")
        for version in model_versions:
            subset = frame[(frame["model_version"] == version) & (frame["regime"] == regime)]
            _print_metrics(f"  {version}", prospective_metrics(subset))

    if horizon_days == 30:
        print("\n30D is diagnostic only. Do not retune either frozen model from this result.")
    else:
        print("\n90D is the primary prospective verdict, but multiple snapshot cohorts are still preferable to one.")


def print_status() -> None:
    """Print compact lockbox progress and next maturity dates."""

    policy = load_lockbox_policy()
    db = _load_db_module()
    status = db.status_summary()
    print("=" * 88)
    print("PROSPECTIVE LOCKBOX STATUS")
    print("=" * 88)
    print(f"Latest RAW data: {status['latest_data_date']}")
    print(
        f"Snapshot cohorts: {status['snapshot_count']} | "
        f"range: {status['first_snapshot']} -> {status['last_snapshot']}"
    )
    for version in policy["models"]:
        print(f"Predictions {version}: {status['predictions'].get(version, 0):,}")
    print(f"Settled 30D outcomes: {status['outcomes'].get(30, 0):,}")
    print(f"Settled 90D outcomes: {status['outcomes'].get(90, 0):,}")
    first = status.get("first_prediction")
    latest = status.get("latest_data_date")
    if first is not None:
        for horizon in policy["horizons_days"]:
            target = first + timedelta(days=int(horizon))
            state = "matured in source data" if latest is not None and latest >= target else "pending"
            print(f"First {int(horizon)}D target: {target} ({state})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prospective V3-vs-Hybrid-V4 model lockbox.")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="Capture a new immutable prediction cohort when due.")
    snapshot.add_argument(
        "--allow-early",
        action="store_true",
        help="Allow a cohort earlier than the policy-recommended weekly cadence.",
    )
    sub.add_parser("settle", help="Settle every newly matured 30D/90D outcome.")
    report_parser = sub.add_parser("report", help="Report matured prospective performance.")
    report_parser.add_argument("--horizon", type=int, choices=(30, 90), required=True)
    run = sub.add_parser("run", help="Settle matured outcomes, capture a due cohort, then show status.")
    run.add_argument("--allow-early", action="store_true")
    sub.add_parser("status", help="Show lockbox cohort and maturity status.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "snapshot":
        snapshot_predictions(allow_early=bool(args.allow_early))
    elif args.command == "settle":
        settle_outcomes()
    elif args.command == "report":
        report(int(args.horizon))
    elif args.command == "status":
        print_status()
    elif args.command == "run":
        settle_outcomes()
        snapshot_predictions(allow_early=bool(args.allow_early))
        print_status()


if __name__ == "__main__":
    main()
