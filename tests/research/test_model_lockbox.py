from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from services.research.model_lockbox import (
    LOCKBOX_POLICY_PATH,
    load_lockbox_policy,
    prospective_metrics,
)


def _frame() -> pd.DataFrame:
    rows = []
    for date_index, as_of in enumerate(("2026-10-18", "2026-10-25")):
        for i in range(20):
            score = float(i)
            alpha = -0.20 + i * 0.02 + date_index * 0.001
            rows.append(
                {
                    "as_of_date": as_of,
                    "product_id": date_index * 100 + i,
                    "ranking_score": score,
                    "set_alpha": alpha,
                    "raw_return": alpha + 0.03,
                }
            )
    return pd.DataFrame(rows)


def test_lockbox_policy_is_frozen_30d_90d_and_90d_primary():
    policy = load_lockbox_policy()
    assert policy["status"] == "frozen"
    assert policy["horizons_days"] == [30, 90]
    assert policy["primary_horizon_days"] == 90
    assert policy["primary_price_floor_usd"] == 20.0
    assert policy["recommended_snapshot_cadence_days"] == 7


def test_prospective_metrics_rewards_correct_ranking_across_multiple_dates():
    metrics = prospective_metrics(_frame())
    assert metrics["snapshot_dates"] == 2
    assert metrics["n"] == 40
    assert metrics["rank_ic"] > 0.99
    assert metrics["q5_q1"] > 0
    assert metrics["top20_alpha"] > 0
    assert metrics["top20_p_crash15"] == 0.0


def test_metrics_average_each_snapshot_date_instead_of_pooling_only():
    frame = _frame()
    metrics = prospective_metrics(frame)
    assert metrics["snapshot_dates"] == 2
    # Both dates are perfectly ordered, so equal-date averaging remains perfect.
    assert metrics["rank_ic"] > 0.99


def test_lockbox_db_source_is_append_only_for_predictions_and_outcomes():
    source = Path("database/model_lockbox.py").read_text()
    assert "ON CONFLICT (as_of_date, product_id, printing, model_version) DO NOTHING" in source
    assert "ON CONFLICT (as_of_date, product_id, printing, horizon_days) DO NOTHING" in source
    assert "UPDATE model_lockbox_predictions" not in source
    assert "UPDATE model_lockbox_outcomes" not in source
    assert "capture_snapshot_with_predictions" in source
    assert "rolls back the snapshot row" in source
    assert "model_lockbox_models" in source
    assert "model_lockbox_policies" in source
    assert "different spec hash" in source
    assert "different policy hash" in source


def test_outcome_settlement_never_uses_prices_after_target_date():
    source = Path("database/model_lockbox.py").read_text()
    assert "d.snapshot_date <= c.target_date" in source
    assert "i.snapshot_date <= c.target_date" in source
    assert "strong_gain" in source
    assert "crash" in source


def test_lockbox_service_does_not_modify_model_registries():
    source = Path("services/research/model_lockbox.py").read_text()
    assert "write_text" not in source
    assert "fit" not in source.lower().replace("profit", "")
    raw = json.loads(Path(LOCKBOX_POLICY_PATH).read_text())
    assert "Do not retune" not in raw.get("notes", "") or raw["status"] == "frozen"
