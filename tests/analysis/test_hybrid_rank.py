from __future__ import annotations

import json
from pathlib import Path

from services.analysis.hybrid_rank import (
    FACTOR_NAMES,
    MODEL_SPEC_PATH,
    load_hybrid_spec,
    regime_for_era,
    score_hybrid_factor_panel,
    score_hybrid_v4,
)


def _v3_result(prior_rating: float = -1.0) -> dict:
    factors = []
    for name, weight in {
        "Historical analogues": 32.0,
        "Momentum profile": 28.0,
        "Validated overheating risk": 28.0,
        "Prior run-up": 9.0,
        "Recovery / drawdown": 3.0,
    }.items():
        factors.append(
            {
                "factor": name,
                "rating": prior_rating if name == "Prior run-up" else 1.0,
                "base_weight": weight,
                "effective_weight": weight,
                "note": None,
            }
        )
    return {
        "research_model_version": "challenger_v1_2026-10-16",
        "research_score": 61.0,
        "research_ranking_score": 59.0,
        "research_coverage": 0.85,
        "research_factors": factors,
    }


def test_hybrid_registry_is_frozen_and_weights_sum_to_100():
    spec = load_hybrid_spec()
    assert spec["status"] == "frozen_challenger"
    assert spec["parent_model"] == "challenger_v1_2026-10-16"
    assert tuple(spec["factor_set"]) == FACTOR_NAMES
    for regime in ("Modern", "Vintage"):
        assert abs(sum(spec["regimes"][regime]["weights"].values()) - 100.0) < 1e-3


def test_mid_era_keeps_exact_v3_score():
    result = score_hybrid_v4(_v3_result(), era="XY")
    assert result is not None
    assert result["regime"] == "Mid-era"
    assert result["uses_parent_exact"] is True
    assert result["research_score"] == 61.0
    assert result["ranking_score"] == 59.0
    assert result["coverage"] == 0.85


def test_modern_reverses_prior_runup_rating_but_vintage_does_not():
    modern = score_hybrid_v4(_v3_result(prior_rating=-1.5), era="Sword & Shield")
    vintage = score_hybrid_v4(_v3_result(prior_rating=-1.5), era="HGSS")
    assert modern is not None and vintage is not None
    modern_prior = next(x for x in modern["factor_payload"] if x["factor"] == "Prior run-up")
    vintage_prior = next(x for x in vintage["factor_payload"] if x["factor"] == "Prior run-up")
    assert modern_prior["rating"] == 1.5
    assert vintage_prior["rating"] == -1.5


def test_every_current_regime_era_maps_and_unknown_does_not():
    spec = load_hybrid_spec()
    assert regime_for_era("Scarlet & Violet", spec) == "Modern"
    assert regime_for_era("Black & White", spec) == "Mid-era"
    assert regime_for_era("Diamond & Pearl", spec) == "Vintage"
    assert regime_for_era("Unknown future era", spec) is None


def test_registry_records_historical_evidence_but_not_lockbox_results():
    raw = json.loads(Path(MODEL_SPEC_PATH).read_text())
    assert raw["research_evidence"]["hybrid_v4_q5_q1"] == 0.0903
    assert "lockbox" not in raw["research_evidence"]


def test_historical_panel_scoring_keeps_mid_exact_and_scores_vintage():
    import pandas as pd

    rows = []
    for era in ("XY", "HGSS"):
        row = {
            "era": era,
            "frozen_v3_research_score": 61.0,
            "frozen_v3_ranking_score": 59.0,
            "frozen_v3_coverage": 0.85,
        }
        for name in FACTOR_NAMES:
            row[f"rating_{name}"] = -1.0 if name == "Prior run-up" else 1.0
            row[f"quality_{name}"] = 1.0
        rows.append(row)
    panel = pd.DataFrame(rows)
    scored = score_hybrid_factor_panel(panel)
    assert scored.loc[0, "hybrid_regime"] == "Mid-era"
    assert scored.loc[0, "hybrid_ranking_score"] == 59.0
    assert scored.loc[1, "hybrid_regime"] == "Vintage"
    assert scored.loc[1, "hybrid_ranking_score"] != 59.0


def test_pre_dp_era_mapping_reflects_completed_transfer_research():
    spec = load_hybrid_spec()

    # EX passed the Vintage V4 transfer test.
    assert regime_for_era("EX", spec) == "Vintage"

    # e-Card did not transfer cleanly enough, so it remains on V3 fallback.
    assert regime_for_era("e-Card", spec) is None


