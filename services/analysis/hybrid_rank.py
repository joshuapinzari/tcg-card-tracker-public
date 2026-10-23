"""Frozen Hybrid V4 challenger scoring.

The live production score remains frozen V3.  This module is deliberately a
small deterministic scorer used by the prospective lockbox.  Modern and Vintage
cards use regime-specific fold-mean weights selected before the lockbox starts;
Mid-era cards keep the parent V3 score exactly.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_SPEC_PATH = PROJECT_ROOT / "models" / "card_rank_v4_hybrid.json"
PARENT_SPEC_PATH = PROJECT_ROOT / "models" / "card_rank_v3.json"

FACTOR_NAMES = (
    "Historical analogues",
    "Momentum profile",
    "Validated overheating risk",
    "Prior run-up",
    "Recovery / drawdown",
)


def spec_hash(path: Path) -> str:
    """Return a SHA-256 fingerprint of one immutable model/policy file."""

    return sha256(path.read_bytes()).hexdigest()


def load_hybrid_spec(path: Path = MODEL_SPEC_PATH) -> dict[str, Any]:
    """Load and validate the frozen Hybrid V4 challenger registry entry."""

    spec = json.loads(path.read_text())
    if spec.get("status") != "frozen_challenger":
        raise RuntimeError("Hybrid V4 must remain marked as frozen_challenger.")
    if tuple(spec.get("factor_set") or ()) != FACTOR_NAMES:
        raise RuntimeError("Hybrid V4 factor set drift detected.")
    regimes = spec.get("regimes") or {}
    if set(regimes) != {"Modern", "Mid-era", "Vintage"}:
        raise RuntimeError("Hybrid V4 regime registry drift detected.")
    for regime in ("Modern", "Vintage"):
        weights = regimes[regime].get("weights") or {}
        if set(weights) != set(FACTOR_NAMES):
            raise RuntimeError(f"{regime} Hybrid V4 weights are incomplete.")
        if abs(sum(float(v) for v in weights.values()) - 100.0) > 1e-3:
            raise RuntimeError(f"{regime} Hybrid V4 weights must sum to 100.")
        if regimes[regime].get("prior_runup_rating_multiplier") not in {-1, 1}:
            raise RuntimeError(f"{regime} prior-run-up multiplier must be +/-1.")
    if regimes["Mid-era"].get("use_parent_exact") is not True:
        raise RuntimeError("Mid-era must remain on exact frozen V3.")
    return spec


def regime_for_era(era: str | None, spec: dict[str, Any] | None = None) -> str | None:
    """Map one canonical Pokemon era to the frozen Hybrid V4 regime."""

    spec = spec or load_hybrid_spec()
    for regime, config in spec["regimes"].items():
        if era in (config.get("eras") or []):
            return regime
    return None


def _v3_factor_map(v3_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    factors = v3_result.get("research_factors") or []
    return {
        str(item.get("factor")): item
        for item in factors
        if item.get("factor") in FACTOR_NAMES
    }


def score_hybrid_v4(
    v3_result: dict[str, Any],
    *,
    era: str | None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score one live card with the frozen Hybrid V4 challenger.

    Parameters
    ----------
    v3_result:
        Live V3 result produced by ``score_locked_research_model``.  Its factor
        ratings are reused so V3 and V4 differ only in the frozen regime logic,
        not in feature construction.
    era:
        Canonical Pokemon era.
    spec:
        Optional already-loaded Hybrid V4 registry entry.

    Returns
    -------
    dict or None
        Frozen Hybrid V4 score payload, or ``None`` for an unmapped era.  The
        lockbox intentionally refuses to invent a regime for a newly added era.
    """

    spec = spec or load_hybrid_spec()
    regime = regime_for_era(era, spec)
    if regime is None:
        return None

    if regime == "Mid-era":
        return {
            "model_version": spec["model_version"],
            "parent_model_version": v3_result.get("research_model_version"),
            "regime": regime,
            "research_score": float(v3_result.get("research_score", 50.0)),
            "ranking_score": float(v3_result.get("research_ranking_score", 50.0)),
            "coverage": float(v3_result.get("research_coverage", 0.0)),
            "factor_payload": v3_result.get("research_factors") or [],
            "uses_parent_exact": True,
        }

    config = spec["regimes"][regime]
    weights = {name: float(config["weights"][name]) for name in FACTOR_NAMES}
    prior_multiplier = int(config["prior_runup_rating_multiplier"])
    factors = _v3_factor_map(v3_result)

    numerator = 0.0
    denominator = 0.0
    payload: list[dict[str, Any]] = []
    for name in FACTOR_NAMES:
        source = factors.get(name) or {}
        rating = source.get("rating")
        parent_base = float(source.get("base_weight") or 0.0)
        parent_effective = float(source.get("effective_weight") or 0.0)
        quality = (
            parent_effective / parent_base
            if rating is not None and parent_base > 0
            else 0.0
        )
        if name == "Prior run-up" and rating is not None:
            rating = float(rating) * prior_multiplier
        base_weight = weights[name]
        effective_weight = base_weight * quality if rating is not None else 0.0
        contribution = (
            effective_weight * float(rating) / 2.0 if rating is not None else None
        )
        if contribution is not None:
            numerator += contribution
            denominator += effective_weight
        payload.append(
            {
                "factor": name,
                "rating": rating,
                "quality": quality,
                "base_weight": base_weight,
                "effective_weight": effective_weight,
                "contribution": contribution,
                "parent_note": source.get("note"),
            }
        )

    score = 50.0 if denominator <= 0 else 50.0 + 50.0 * numerator / denominator
    score = max(0.0, min(100.0, score))
    coverage = denominator / sum(weights.values())
    ranking = 50.0 + (score - 50.0) * coverage
    ranking = max(0.0, min(100.0, ranking))
    return {
        "model_version": spec["model_version"],
        "parent_model_version": v3_result.get("research_model_version"),
        "regime": regime,
        "research_score": score,
        "ranking_score": ranking,
        "coverage": coverage,
        "factor_payload": payload,
        "uses_parent_exact": False,
    }


def score_hybrid_factor_panel(
    panel,
    *,
    era_col: str = "era",
    spec: dict[str, Any] | None = None,
):
    """Score a historical frozen-factor panel with the exact Hybrid V4 spec.

    This helper performs no fitting. It exists so the fold-mean challenger can
    be sanity-checked historically before the prospective lockbox begins.
    """

    import numpy as np
    import pandas as pd

    spec = spec or load_hybrid_spec()
    required = {era_col, "frozen_v3_research_score", "frozen_v3_ranking_score", "frozen_v3_coverage"}
    for name in FACTOR_NAMES:
        required.add(f"rating_{name}")
        required.add(f"quality_{name}")
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"Hybrid historical panel missing columns: {sorted(missing)}")

    out = pd.DataFrame(index=panel.index)
    out["hybrid_regime"] = panel[era_col].map(lambda era: regime_for_era(era, spec))
    out["hybrid_research_score"] = np.nan
    out["hybrid_ranking_score"] = np.nan
    out["hybrid_coverage"] = np.nan

    mid = out["hybrid_regime"] == "Mid-era"
    out.loc[mid, "hybrid_research_score"] = panel.loc[mid, "frozen_v3_research_score"]
    out.loc[mid, "hybrid_ranking_score"] = panel.loc[mid, "frozen_v3_ranking_score"]
    out.loc[mid, "hybrid_coverage"] = panel.loc[mid, "frozen_v3_coverage"]

    for regime in ("Modern", "Vintage"):
        mask = out["hybrid_regime"] == regime
        if not mask.any():
            continue
        config = spec["regimes"][regime]
        weights = np.array([float(config["weights"][name]) for name in FACTOR_NAMES])
        ratings = np.column_stack([
            pd.to_numeric(panel.loc[mask, f"rating_{name}"], errors="coerce").fillna(0.0)
            for name in FACTOR_NAMES
        ]).astype(float)
        quality = np.column_stack([
            pd.to_numeric(panel.loc[mask, f"quality_{name}"], errors="coerce").fillna(0.0)
            for name in FACTOR_NAMES
        ]).astype(float)
        prior_index = FACTOR_NAMES.index("Prior run-up")
        ratings[:, prior_index] *= int(config["prior_runup_rating_multiplier"])
        weighted_quality = quality * weights.reshape(1, -1)
        denominator = weighted_quality.sum(axis=1)
        numerator = (ratings * weighted_quality).sum(axis=1)
        normalized = np.divide(
            numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0
        )
        research = np.clip(50.0 + 25.0 * normalized, 0.0, 100.0)
        coverage = denominator / float(weights.sum())
        ranking = np.clip(50.0 + (research - 50.0) * coverage, 0.0, 100.0)
        out.loc[mask, "hybrid_research_score"] = research
        out.loc[mask, "hybrid_ranking_score"] = ranking
        out.loc[mask, "hybrid_coverage"] = coverage
    return out
