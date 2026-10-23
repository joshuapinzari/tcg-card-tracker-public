"""Shared history construction for the current frozen-model evaluations.

Extracted without changing the point-in-time analogue construction or embargo.
No optimisation runs and no model files are written by these helpers.
"""
import numpy as np
import pandas as pd
from database.market_analytics import connect
from services.analysis.card_analyst import FACTOR_WEIGHTS, _analogue_factor
from services.analysis.opportunity_model import add_setup_flags, calibrate_one

def _load_history() -> pd.DataFrame:
    sql = """
        SELECT
            h.*,
            r.release_age_days,
            r.signal_percentile
        FROM raw_opportunity_history_90d AS h
        LEFT JOIN raw_relative_momentum_90d AS r
          ON r.formation_date = h.formation_date
         AND r.product_id = h.product_id
        ORDER BY h.formation_date, h.product_id;
    """
    with connect() as connection:
        frame = pd.read_sql_query(sql, connection)
    if frame.empty:
        raise SystemExit(
            "No raw opportunity history exists. Run: "
            "python -m services.pipelines.build_raw_opportunity_research --rebuild-base"
        )
    frame["formation_date"] = pd.to_datetime(frame["formation_date"])
    numeric = [
        column
        for column in frame.columns
        if column not in {"formation_date", "printing", "set_name", "era"}
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.replace([np.inf, -np.inf], np.nan)


def _analogue_ratings(sample: pd.DataFrame, full_history: pd.DataFrame, analogs: int) -> tuple[pd.Series, pd.Series]:
    print("Building strictly walk-forward historical-analogue factors...")
    full = add_setup_flags(full_history.copy())
    ratings = pd.Series(np.nan, index=sample.index, dtype=float)
    quality = pd.Series(0.0, index=sample.index, dtype=float)
    train_cache: dict[pd.Timestamp, pd.DataFrame] = {}

    for position, (idx, row) in enumerate(sample.iterrows(), start=1):
        setups = row.get("setups")
        if not isinstance(setups, list):
            setups = []
        if not setups:
            continue
        formation_date = pd.Timestamp(row["formation_date"])
        cutoff = formation_date - pd.Timedelta(days=90)
        if cutoff not in train_cache:
            train_cache[cutoff] = full[full["formation_date"] <= cutoff]
        train = train_cache[cutoff]
        calibrations = []
        for setup in setups:
            calibrated = calibrate_one(row, train, setup, max_analogs=analogs)
            if calibrated is not None:
                calibrations.append(calibrated)
        if calibrations:
            best = max(
                calibrations,
                key=lambda item: (
                    item["expected_set_alpha_90d"],
                    item["probability_strong_gain_90d"],
                    -item["probability_crash_90d"],
                ),
            )
            factor = _analogue_factor(best)
            ratings.at[idx] = factor["rating"]
            base = float(factor.get("base_weight") or FACTOR_WEIGHTS["Historical analogues"])
            quality.at[idx] = float(factor.get("weight") or 0.0) / base if base else 0.0
        if position % 250 == 0 or position == len(sample):
            print(f"  analogue rows: {position:,}/{len(sample):,}")

    return ratings, quality

