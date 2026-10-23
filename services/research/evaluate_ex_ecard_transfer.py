"""Test whether EX/e-Card should use the frozen Vintage V4 model."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd

from services.research.evaluate_locked_card_model import (
    build_frozen_factor_panel,
    load_evaluation_policy,
    metrics_from_signal,
)
from services.analysis.hybrid_rank import load_hybrid_spec, score_hybrid_factor_panel


TARGET_ERAS = ("EX", "e-Card")
TIME_BLOCKS = 4


def fmt(value, percent=False):
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value * 100:+.2f}%" if percent else f"{value:+.3f}"


def print_metrics(label, metrics):
    print(
        f"{label:<24} "
        f"N={metrics.get('n', 0):5d} "
        f"cards={metrics.get('cards', 0):4d}  "
        f"IC={fmt(metrics.get('rank_ic'))}  "
        f"Q5-Q1={fmt(metrics.get('top_bottom_spread'), True)}  "
        f"top20={fmt(metrics.get('top20_alpha'), True)}  "
        f"P(+15)={fmt(metrics.get('top20_p_gain15'), True)}  "
        f"P(crash)={fmt(metrics.get('top20_p_crash15'), True)}"
    )


def compare(label, subset):
    print(f"\n{label}")
    print("-" * 118)

    if subset.empty:
        print("No eligible rows.")
        return

    v3 = metrics_from_signal(
        subset,
        subset["frozen_v3_ranking_score"],
    )
    v4 = metrics_from_signal(
        subset,
        subset["transfer_v4_ranking_score"],
    )

    print_metrics("Frozen V3", v3)
    print_metrics("Vintage V4 transfer", v4)

    if v3.get("rank_ic") is not None and v4.get("rank_ic") is not None:
        print(
            "Delta V4-V3: "
            f"IC={v4['rank_ic'] - v3['rank_ic']:+.3f}  "
            f"Q5-Q1="
            f"{(v4['top_bottom_spread'] - v3['top_bottom_spread']) * 100:+.2f}%  "
            f"top20="
            f"{(v4['top20_alpha'] - v3['top20_alpha']) * 100:+.2f}%"
        )


def chronological_blocks(subset, label):
    print(f"\n{label} — chronological blocks")
    print("-" * 118)

    if subset.empty:
        print("No eligible rows.")
        return

    dates = np.array(
        sorted(
            pd.to_datetime(
                subset["formation_date"]
            ).dropna().unique()
        )
    )

    for block_number, date_block in enumerate(
        np.array_split(dates, TIME_BLOCKS),
        start=1,
    ):
        if not len(date_block):
            continue

        block = subset[
            subset["formation_date"].isin(date_block)
        ].copy()

        v3 = metrics_from_signal(
            block,
            block["frozen_v3_ranking_score"],
        )
        v4 = metrics_from_signal(
            block,
            block["transfer_v4_ranking_score"],
        )

        if v3.get("rank_ic") is None or v4.get("rank_ic") is None:
            print(f"B{block_number}: insufficient rows")
            continue

        start = pd.Timestamp(date_block[0]).date()
        end = pd.Timestamp(date_block[-1]).date()

        print(
            f"B{block_number} {start} -> {end}  "
            f"N={len(block):4d}  "
            f"V3 IC={fmt(v3['rank_ic'])} "
            f"spread={fmt(v3['top_bottom_spread'], True)}  |  "
            f"V4 IC={fmt(v4['rank_ic'])} "
            f"spread={fmt(v4['top_bottom_spread'], True)}  |  "
            f"dIC={v4['rank_ic'] - v3['rank_ic']:+.3f}  "
            f"dSpread="
            f"{(v4['top_bottom_spread'] - v3['top_bottom_spread']) * 100:+.2f}%"
        )


def main():
    from database.opportunity_scanner import load_opportunity_history

    policy = load_evaluation_policy()
    frozen_spec = load_hybrid_spec()

    # Make an in-memory copy only.
    # The actual frozen V4 JSON is NOT changed.
    transfer_spec = deepcopy(frozen_spec)

    vintage_eras = list(
        transfer_spec["regimes"]["Vintage"].get("eras") or []
    )

    for era in TARGET_ERAS:
        if era not in vintage_eras:
            vintage_eras.append(era)

    transfer_spec["regimes"]["Vintage"]["eras"] = vintage_eras

    print("=" * 118)
    print(
        "EX / e-CARD -> VINTAGE V4 TRANSFER TEST "
        "— NO FITTING, NO MODEL CHANGES"
    )
    print("=" * 118)
    print(f"Frozen challenger: {frozen_spec['model_version']}")
    print(
        f"Recent market regime: "
        f"{policy['market_regime_start']} onward"
    )
    print(
        f"Stable sample: "
        f"{float(policy['stable_sample_rate']):.1%}"
    )
    print(f"Analogues: {frozen_spec['analogue_neighbors']}")
    print(
        f"Primary cohort: >= "
        f"${float(frozen_spec['primary_price_floor_usd']):.0f}"
    )
    print(
        "Research-only mapping: "
        "EX + e-Card -> exact frozen Vintage V4 weights"
    )
    print(
        "Nothing in models/, the dashboard, "
        "or the lockbox is modified.\n"
    )

    history = load_opportunity_history()

    panel = build_frozen_factor_panel(
        history,
        price_floor=10.0,
        formation_start=policy["market_regime_start"],
        sample_rate=float(policy["stable_sample_rate"]),
        sample_salt=str(policy["stable_sample_salt"]),
    )

    panel = panel[
        panel["formation_price"]
        >= float(frozen_spec["primary_price_floor_usd"])
    ].copy()

    transfer = score_hybrid_factor_panel(
        panel,
        spec=transfer_spec,
    )

    panel = panel.join(transfer)

    target = panel[
        panel["era"].isin(TARGET_ERAS)
    ].copy()

    target["transfer_v4_ranking_score"] = (
        target["hybrid_ranking_score"]
    )

    print(
        f"Target panel: {len(target):,} rows / "
        f"{target['product_id'].nunique():,} cards / "
        f"{target['formation_date'].nunique():,} formation dates"
    )

    if target.empty:
        raise SystemExit(
            "No EX/e-Card rows found in the evaluation panel."
        )

    print("\nCoverage:")
    for era in TARGET_ERAS:
        subset = target[target["era"] == era]
        print(
            f"  {era:<7} "
            f"{len(subset):5d} rows / "
            f"{subset['product_id'].nunique():4d} cards"
        )

    compare("COMBINED EX + e-Card", target)

    for era in TARGET_ERAS:
        compare(
            era,
            target[target["era"] == era],
        )

    chronological_blocks(
        target,
        "COMBINED EX + e-Card",
    )

    for era in TARGET_ERAS:
        chronological_blocks(
            target[target["era"] == era],
            era,
        )

    print("\n" + "=" * 118)
    print("INTERPRETATION")
    print("=" * 118)
    print(
        "If Vintage V4 improves IC and Q5-Q1 overall "
        "and the advantage is reasonably consistent "
        "through time, we have evidence to extend Vintage V4."
    )
    print(
        "If only EX or e-Card transfers well, "
        "we can extend V4 to that era only."
    )
    print(
        "If results are mixed, both remain on the "
        "safe frozen-V3 fallback."
    )


if __name__ == "__main__":
    main()
