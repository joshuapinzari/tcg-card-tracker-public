from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from services.research.run_card_analyst_regime_research import (
    FACTOR_NAMES,
    HYPOTHESIS_PATH,
    WEIGHT_BOUNDS,
    _candidate_weights,
    _factor_arrays,
    _hybrid_signal_for_regime,
    _ranking_score,
    load_regime_hypothesis,
    regime_for_era,
)


def _panel(rows: int = 12) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "formation_date": [pd.Timestamp("2026-01-01")] * rows,
            "target_rank": np.linspace(0.05, 1.0, rows),
        }
    )
    for factor in FACTOR_NAMES:
        frame[f"rating_{factor}"] = np.linspace(-2.0, 2.0, rows)
        frame[f"quality_{factor}"] = 1.0
    return frame


def test_regime_hypothesis_is_research_only_and_inherits_recent_policy():
    spec = load_regime_hypothesis()
    assert spec["status"] == "research_only"
    assert spec["parent_model"] == "challenger_v1_2026-10-16"
    assert spec["market_regime_start"] == "2024-08-12"
    assert spec["analogue_neighbors"] == 150
    assert tuple(spec["factor_set"]) == FACTOR_NAMES


def test_every_tracked_era_in_hypothesis_belongs_to_exactly_one_regime():
    spec = load_regime_hypothesis()
    assert regime_for_era("Sword & Shield", spec) == "Modern"
    assert regime_for_era("Scarlet & Violet", spec) == "Modern"
    assert regime_for_era("Black & White", spec) == "Mid-era"
    assert regime_for_era("XY", spec) == "Mid-era"
    assert regime_for_era("Sun & Moon", spec) == "Mid-era"
    assert regime_for_era("Diamond & Pearl", spec) == "Vintage"
    assert regime_for_era("Platinum", spec) == "Vintage"
    assert regime_for_era("HGSS", spec) == "Vintage"
    assert regime_for_era("Unknown", spec) is None


def test_candidate_weights_sum_to_100_and_respect_bounds():
    matrix = _candidate_weights(250, seed=123)
    assert matrix.shape == (250, len(FACTOR_NAMES))
    assert np.allclose(matrix.sum(axis=1), 100.0)
    for index, name in enumerate(FACTOR_NAMES):
        lower, upper = WEIGHT_BOUNDS[name]
        assert np.all(matrix[:, index] >= lower - 1e-8)
        assert np.all(matrix[:, index] <= upper + 1e-8)


def test_only_prior_runup_sign_is_flipped_by_direction_switch():
    panel = _panel(4)
    positive, quality_a = _factor_arrays(panel, prior_runup_direction=1)
    reversed_, quality_b = _factor_arrays(panel, prior_runup_direction=-1)
    prior_index = FACTOR_NAMES.index("Prior run-up")
    for index in range(len(FACTOR_NAMES)):
        if index == prior_index:
            assert np.allclose(reversed_[:, index], -positive[:, index])
        else:
            assert np.allclose(reversed_[:, index], positive[:, index])
    assert np.allclose(quality_a, quality_b)


def test_ranking_score_uses_coverage_shrinkage():
    panel = _panel(1)
    for factor in FACTOR_NAMES:
        panel[f"rating_{factor}"] = 2.0
        panel[f"quality_{factor}"] = 0.0
    panel["quality_Momentum profile"] = 1.0
    weights = np.array([32.0, 28.0, 28.0, 9.0, 3.0])
    score = _ranking_score(panel, weights, prior_runup_direction=1)
    assert 50.0 < float(score.iloc[0]) < 100.0


def test_hypothesis_json_does_not_contain_deployable_regime_weights():
    raw = json.loads(Path(HYPOTHESIS_PATH).read_text())
    assert raw["status"] == "research_only"
    assert "weights" not in raw
    assert "production" not in raw


def test_regime_research_source_has_no_production_write_path():
    source = Path("services/research/run_card_analyst_regime_research.py").read_text()
    assert "production V3 remains frozen" in source
    assert "No production weights" in source
    assert "card_analyst_regime_v4.json" in source
    assert "write_text" in source
    assert "services/analysis/research_rank.py" not in source
    assert "dashboard/" not in source
    assert "README" in source


def test_hybrid_keeps_mid_era_on_frozen_v3_and_uses_v4_elsewhere():
    index = pd.RangeIndex(3)
    v4 = pd.Series([80.0, 70.0, 60.0], index=index)
    frozen = pd.Series([55.0, 56.0, 57.0], index=index)

    assert _hybrid_signal_for_regime("Modern", v4, frozen).equals(v4)
    assert _hybrid_signal_for_regime("Vintage", v4, frozen).equals(v4)
    assert _hybrid_signal_for_regime("Mid-era", v4, frozen).equals(frozen)


def test_regime_research_reports_hybrid_and_weight_stability():
    source = Path("services/research/run_card_analyst_regime_research.py").read_text()
    assert "Hybrid V4 challenger" in source
    assert "Modern=regime V4" in source
    assert "Mid-era=frozen V3" in source
    assert "WEIGHT STABILITY — REGIME FITS ACROSS OUTER FOLDS" in source
    assert "hybrid_paired_fold_deltas" in source
