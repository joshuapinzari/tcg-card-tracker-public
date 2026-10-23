from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from services.research.evaluate_locked_card_model import (
    EVALUATION_POLICY_PATH,
    MODEL_SPEC_PATH,
    _prior_runup_rating,
    _stable_hash_sample,
    load_evaluation_policy,
    load_frozen_model_spec,
    metrics_from_signal,
    score_frozen_panel,
)
from services.analysis.research_rank import (
    LOCKED_ANALOGS,
    LOCKED_MODEL_VERSION,
    LOCKED_PRIMARY_PRICE_FLOOR_USD,
    LOCKED_WEIGHTS,
)


def _factor_panel() -> pd.DataFrame:
    rows = 25
    frame = pd.DataFrame(
        {
            "formation_date": [pd.Timestamp("2026-01-01")] * rows,
            "product_id": np.arange(rows),
            "future_set_alpha_90d": np.linspace(-0.20, 0.20, rows),
            "future_return_90d": np.linspace(-0.15, 0.30, rows),
            "target_rank": np.linspace(1 / rows, 1.0, rows),
        }
    )
    for factor in LOCKED_WEIGHTS:
        frame[f"rating_{factor}"] = np.linspace(-2.0, 2.0, rows)
        frame[f"quality_{factor}"] = 1.0
    return frame


def test_registry_matches_live_frozen_constants():
    spec = load_frozen_model_spec()
    assert spec["model_version"] == LOCKED_MODEL_VERSION
    assert spec["analogue_neighbors"] == LOCKED_ANALOGS
    assert spec["primary_price_floor_usd"] == LOCKED_PRIMARY_PRICE_FLOOR_USD
    assert spec["weights"] == LOCKED_WEIGHTS
    assert spec["status"] == "frozen"


def test_registry_json_is_explicitly_frozen():
    raw = json.loads(Path(MODEL_SPEC_PATH).read_text())
    assert raw["status"] == "frozen"
    assert raw["prediction_horizon_days"] == 90
    assert raw["factor_directions"]["Prior run-up"] == -1


def test_frozen_score_maps_full_negative_to_zero_and_positive_to_100():
    panel = _factor_panel().iloc[:2].copy()
    for factor in LOCKED_WEIGHTS:
        panel.loc[panel.index[0], f"rating_{factor}"] = -2.0
        panel.loc[panel.index[1], f"rating_{factor}"] = 2.0
    research, ranking, coverage = score_frozen_panel(panel)
    assert research.tolist() == [0.0, 100.0]
    assert ranking.tolist() == [0.0, 100.0]
    assert coverage.tolist() == [1.0, 1.0]


def test_ranking_score_shrinks_incomplete_evidence_toward_50():
    panel = _factor_panel().iloc[:1].copy()
    for factor in LOCKED_WEIGHTS:
        panel[f"rating_{factor}"] = 2.0
        panel[f"quality_{factor}"] = 0.0
    panel["quality_Momentum profile"] = 1.0

    research, ranking, coverage = score_frozen_panel(panel)
    assert research.iloc[0] == 100.0
    assert 0.0 < coverage.iloc[0] < 1.0
    assert 50.0 < ranking.iloc[0] < research.iloc[0]


def test_prior_runup_direction_is_negative():
    panel = pd.DataFrame(
        {
            "formation_date": [pd.Timestamp("2026-01-01")] * 4,
            "prior_runup": [0.0, 0.1, 0.5, 1.0],
        }
    )
    rating = _prior_runup_rating(panel)
    assert rating.is_monotonic_decreasing
    assert rating.iloc[0] > 0
    assert rating.iloc[-1] < 0


def test_metrics_reward_correct_cross_sectional_ordering():
    panel = _factor_panel()
    signal = pd.Series(np.linspace(0.0, 1.0, len(panel)), index=panel.index)
    metrics = metrics_from_signal(panel, signal)
    assert metrics["n"] == len(panel)
    assert metrics["rank_ic"] > 0.99
    assert metrics["top_bottom_spread"] > 0
    assert metrics["top20_p_crash15"] == 0.0


def test_evaluator_source_contains_no_weight_search_or_fit_call():
    source = Path("services/research/evaluate_locked_card_model.py").read_text()
    assert "_fit(" not in source
    assert "candidate_weights" not in source
    assert "Weight candidates" not in source
    assert "NO FITTING / NO FEATURE SELECTION" in source


def test_evaluation_policy_locks_recent_market_regime():
    policy = load_evaluation_policy()
    assert policy["market_regime_start"] == "2024-08-12"
    assert 0.0 < float(policy["stable_sample_rate"]) <= 1.0
    assert policy["stable_sample_salt"]
    raw = json.loads(Path(EVALUATION_POLICY_PATH).read_text())
    assert "Older cards may be evaluated" in raw["notes"]


def test_stable_hash_sample_does_not_displace_existing_rows_when_universe_expands():
    base = pd.DataFrame(
        {
            "formation_date": [pd.Timestamp("2026-01-01")] * 200,
            "product_id": np.arange(1, 201),
            "printing": ["Holofoil"] * 200,
        }
    )
    extra = pd.DataFrame(
        {
            "formation_date": [pd.Timestamp("2026-01-01")] * 200,
            "product_id": np.arange(1001, 1201),
            "printing": ["Holofoil"] * 200,
        }
    )
    first = _stable_hash_sample(base, 0.25, "fixed-salt")
    expanded = _stable_hash_sample(pd.concat([base, extra], ignore_index=True), 0.25, "fixed-salt")

    first_keys = set(zip(first["formation_date"], first["product_id"], first["printing"]))
    expanded_keys = set(
        zip(expanded["formation_date"], expanded["product_id"], expanded["printing"])
    )
    assert first_keys
    assert first_keys <= expanded_keys


def test_evaluator_uses_recent_regime_stable_sampling_not_row_cap():
    source = Path("services/research/evaluate_locked_card_model.py").read_text()
    assert "_stable_hash_sample" in source
    assert "--formation-start" in source
    assert "stable_sample_rate" in source
    assert "policy-locked" in source
    assert '"--sample-rate"' not in source
    assert '"--sample-salt"' not in source
    assert "_sample_evenly_by_date" not in source
    assert '"--max-rows"' not in source


def test_evaluator_reports_diamond_pearl_with_older_eras():
    source = Path("services/research/evaluate_locked_card_model.py").read_text()
    assert '("Diamond & Pearl", "Platinum", "HGSS")' in source
