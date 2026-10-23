"""No-fitting historical sanity check for the exact frozen Hybrid V4 weights."""

from __future__ import annotations

from services.research.evaluate_locked_card_model import (
    build_frozen_factor_panel,
    load_evaluation_policy,
    metrics_from_signal,
)
from services.analysis.hybrid_rank import load_hybrid_spec, score_hybrid_factor_panel


def _print(label: str, metrics: dict) -> None:
    def fmt(value, percent=False):
        if value is None:
            return "N/A"
        return f"{value * 100:+.2f}%" if percent else f"{value:+.3f}"

    print(
        f"{label:<26} N={metrics['n']:5d} cards={metrics['cards']:4d}  "
        f"IC={fmt(metrics['rank_ic'])}  "
        f"Q5-Q1={fmt(metrics['top_bottom_spread'], True)}  "
        f"top20={fmt(metrics['top20_alpha'], True)}"
    )


def main() -> None:
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    # Lazy DB import keeps the module importable for pure tests.
    from database.opportunity_scanner import load_opportunity_history

    policy = load_evaluation_policy()
    spec = load_hybrid_spec()
    print("=" * 104)
    print("FROZEN HYBRID V4 SANITY CHECK — EXACT FOLD-MEAN WEIGHTS, NO FITTING")
    print("=" * 104)
    print(f"Model: {spec['model_version']} | status: {spec['status']}")
    print(f"Recent market regime: {policy['market_regime_start']} onward")
    print(f"Stable sample: {float(policy['stable_sample_rate']):.1%} | analogues: {spec['analogue_neighbors']}")
    print("No weights, signs, factors, cohorts or model registries will be changed.\n")

    history = load_opportunity_history()
    panel = build_frozen_factor_panel(
        history,
        price_floor=10.0,
        formation_start=policy["market_regime_start"],
        sample_rate=float(policy["stable_sample_rate"]),
        sample_salt=str(policy["stable_sample_salt"]),
    )
    panel = panel[panel["formation_price"] >= float(spec["primary_price_floor_usd"])].copy()
    hybrid = score_hybrid_factor_panel(panel, spec=spec)
    panel = panel.join(hybrid)
    panel = panel[panel["hybrid_regime"].notna()].copy()

    print(f"Evaluation panel: {len(panel):,} rows / {panel['product_id'].nunique():,} cards")
    print("\nOVERALL")
    print("-" * 104)
    v3 = metrics_from_signal(panel, panel["frozen_v3_ranking_score"])
    v4 = metrics_from_signal(panel, panel["hybrid_ranking_score"])
    _print("Frozen V3", v3)
    _print("Frozen Hybrid V4", v4)
    print(
        "Delta V4-V3: "
        f"IC={((v4['rank_ic'] or 0)-(v3['rank_ic'] or 0)):+.3f}  "
        f"Q5-Q1={((v4['top_bottom_spread'] or 0)-(v3['top_bottom_spread'] or 0))*100:+.2f}%  "
        f"top20={((v4['top20_alpha'] or 0)-(v3['top20_alpha'] or 0))*100:+.2f}%"
    )

    print("\nBY REGIME")
    print("-" * 104)
    for regime in ("Modern", "Mid-era", "Vintage"):
        subset = panel[panel["hybrid_regime"] == regime]
        if subset.empty:
            continue
        _print(f"{regime} V3", metrics_from_signal(subset, subset["frozen_v3_ranking_score"]))
        _print(f"{regime} Hybrid V4", metrics_from_signal(subset, subset["hybrid_ranking_score"]))

    print("\nThis is still historical robustness evidence, not the prospective lockbox.")


if __name__ == "__main__":
    main()
