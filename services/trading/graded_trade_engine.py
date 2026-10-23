"""Trade-level PokemonPriceTracker graded relative-value analysis.

This module deliberately does not reuse the retired-provider forward-return
calibration.  It compares each graded card with its *own* provider-native
history, then combines those card-level positions using Local Fair Value.

Interpretation:
- ``own_history_deviation < 0``: the slab/premium is cheaper than its own
  historical median relationship.
- ``relative_value_score > 0``: favourable relative-value positioning.
- RAW is a neutral 0% grading-wrapper baseline.

This is descriptive relative-value evidence, not a predicted return.
"""

from __future__ import annotations

from database.graded_intelligence import get_graded_card_intelligence
from services.analysis.graded_premium_signal import get_live_premium_heat


def item_market_value(item: dict) -> float:
    """Authoritative economic weight = Local Fair Value."""
    from services.trading.trade_balancer import item_local_value

    return item_local_value(item)


def _product_id(item: dict):
    value = item.get("product_id")
    if value is None:
        value = item.get("card_id")
    return int(value) if value is not None else None


def _lookup_signal(item: dict, universe):
    product_id = _product_id(item)
    market_type = str(item.get("market_type", "RAW")).upper()
    if product_id is None or market_type == "RAW":
        return None
    return get_live_premium_heat(
        product_id,
        market_type,
        universe=universe,
    )


def _lookup_intelligence(item: dict):
    product_id = _product_id(item)
    market_type = str(item.get("market_type", "RAW")).upper()
    if product_id is None or market_type == "RAW":
        return None
    try:
        return get_graded_card_intelligence(product_id, market_type)
    except ValueError:
        return None


def card_grading_profile(item: dict, *, universe):
    market_type = str(item.get("market_type", "RAW")).upper()
    value = item_market_value(item)

    profile = {
        "side": item.get("side"),
        "name": item.get("name"),
        "market_type": market_type,
        "value": value,
        "covered": False,
        "own_history_deviation": None,
        "relative_value_score": None,
        "own_history_basis": None,
        "current_ratio": None,
        "historical_ratio_median": None,
        "ratio_months": 0,
        "premium_heat_bucket": None,
        "premium_heat_percentile": None,
        "premium_vs_raw": None,
        "premium_change_90": None,
        "confidence": None,
        "evidence_status": None,
        "sales_90": None,
        "history_months": None,
        "ace_warning": False,
    }

    # RAW has no grading wrapper, so it is neutral in this specific layer.
    if market_type == "RAW":
        profile.update(
            {
                "covered": True,
                "own_history_deviation": 0.0,
                "relative_value_score": 0.0,
                "own_history_basis": "RAW baseline",
                "confidence": "BASELINE",
                "evidence_status": "RAW BASELINE",
            }
        )
        return profile

    signal = _lookup_signal(item, universe)
    intelligence = _lookup_intelligence(item)

    if signal is not None:
        profile.update(
            {
                "premium_heat_bucket": signal["premium_heat_bucket"],
                "premium_heat_percentile": signal["premium_heat_percentile"],
                "premium_vs_raw": signal["premium_vs_raw"],
                "premium_change_90": signal["premium_change_90"],
                "ace_warning": bool(signal["ace_very_hot_warning"]),
            }
        )

    if intelligence is None:
        if signal is not None:
            profile["confidence"] = signal["signal_confidence"]
            profile["evidence_status"] = signal["evidence_status"]
        return profile

    profile["confidence"] = intelligence.get("confidence")
    profile["evidence_status"] = "POKEMONPRICETRACKER OWN HISTORY"
    profile["sales_90"] = intelligence.get("sales_90")
    profile["history_months"] = intelligence.get("history_months")
    if profile["premium_vs_raw"] is None:
        profile["premium_vs_raw"] = intelligence.get("premium_vs_raw")
    if profile["premium_change_90"] is None:
        profile["premium_change_90"] = intelligence.get(
            "premium_vs_raw_change_90"
        )

    if (
        market_type == "ACE_10"
        and intelligence.get("ratio_vs_psa10_vs_history") is not None
        and int(intelligence.get("psa_ratio_months") or 0) >= 2
    ):
        deviation = float(intelligence["ratio_vs_psa10_vs_history"])
        profile.update(
            {
                "own_history_deviation": deviation,
                "relative_value_score": -deviation,
                "own_history_basis": "ACE 10 / PSA 10 vs own monthly median",
                "current_ratio": intelligence.get("ratio_vs_psa10"),
                "historical_ratio_median": intelligence.get(
                    "ratio_vs_psa10_historical_median"
                ),
                "ratio_months": int(intelligence.get("psa_ratio_months") or 0),
                "covered": True,
            }
        )
        return profile

    deviation = intelligence.get("premium_vs_raw_vs_history")
    if (
        deviation is not None
        and int(intelligence.get("premium_ratio_months") or 0) >= 2
    ):
        deviation = float(deviation)
        profile.update(
            {
                "own_history_deviation": deviation,
                "relative_value_score": -deviation,
                "own_history_basis": (
                    market_type.replace("_", " ")
                    + " / RAW vs own monthly median"
                ),
                "current_ratio": intelligence.get("premium_vs_raw"),
                "historical_ratio_median": intelligence.get(
                    "premium_vs_raw_historical_median"
                ),
                "ratio_months": int(
                    intelligence.get("premium_ratio_months") or 0
                ),
                "covered": True,
            }
        )

    return profile


def analyse_grading_side(items: list[dict], *, universe):
    profiles = [
        card_grading_profile(item, universe=universe)
        for item in items
    ]

    total_value = sum(profile["value"] for profile in profiles)
    covered = [
        profile
        for profile in profiles
        if profile["covered"] and profile["relative_value_score"] is not None
    ]
    covered_value = sum(profile["value"] for profile in covered)

    weighted_score = None
    weighted_deviation = None
    if covered_value > 0:
        weighted_score = sum(
            profile["value"] * profile["relative_value_score"]
            for profile in covered
        ) / covered_value
        weighted_deviation = sum(
            profile["value"] * profile["own_history_deviation"]
            for profile in covered
        ) / covered_value

    coverage = covered_value / total_value if total_value > 0 else 0.0

    return {
        "profiles": profiles,
        "total_value": total_value,
        "covered_value": covered_value,
        "coverage": coverage,
        "weighted_own_history_deviation": weighted_deviation,
        "weighted_relative_value_score": weighted_score,
        # Compatibility alias used by older dashboard code paths.
        "weighted_reference": weighted_score,
    }


def analyse_graded_trade(
    given_items: list[dict],
    received_items: list[dict],
    *,
    universe,
):
    given = analyse_grading_side(given_items, universe=universe)
    received = analyse_grading_side(received_items, universe=universe)

    has_graded_assets = any(
        str(item.get("market_type", "RAW")).upper() != "RAW"
        for item in list(given_items) + list(received_items)
    )

    edge = None
    if (
        given["weighted_relative_value_score"] is not None
        and received["weighted_relative_value_score"] is not None
    ):
        edge = (
            received["weighted_relative_value_score"]
            - given["weighted_relative_value_score"]
        )

    common_coverage = min(given["coverage"], received["coverage"])

    if edge is None:
        verdict = "INSUFFICIENT PROVIDER HISTORY"
    elif common_coverage < 0.50:
        verdict = "LOW COVERAGE"
    elif edge >= 0.03:
        verdict = "FAVOURS RECEIVE"
    elif edge <= -0.03:
        verdict = "FAVOURS GIVE"
    else:
        verdict = "ROUGHLY NEUTRAL"

    warnings = []
    for side_name, side in (("GIVE", given), ("RECEIVE", received)):
        for profile in side["profiles"]:
            if not profile["ace_warning"]:
                continue
            warnings.append(
                {
                    "side": side_name,
                    "name": profile["name"],
                    "message": (
                        f"{side_name}: {profile['name']} is VERY HOT ACE 10. "
                        "Its current ACE premium is in the hottest live "
                        "PokemonPriceTracker quintile. Treat this as relative-"
                        "value risk, not a forward-return forecast."
                    ),
                    "research_reference": None,
                }
            )

    return {
        "has_graded_assets": has_graded_assets,
        "given": given,
        "received": received,
        "reference_edge": edge,
        "common_coverage": common_coverage,
        "verdict": verdict,
        "warnings": warnings,
    }


def graded_trade_card_rows(analysis: dict):
    rows = []

    for side_name, side_key in (("GIVE", "given"), ("RECEIVE", "received")):
        for profile in analysis[side_key]["profiles"]:
            market_type = profile["market_type"]
            if market_type == "RAW":
                bucket = "RAW baseline"
                percentile = None
            else:
                bucket = profile["premium_heat_bucket"]
                percentile = profile["premium_heat_percentile"]

            rows.append(
                {
                    "Side": side_name,
                    "Card": profile["name"],
                    "Market": market_type.replace("_", " "),
                    "Market Value USD": profile["value"],
                    "Vs Own History": profile["own_history_deviation"],
                    "Relative Value Score": profile["relative_value_score"],
                    "Own-History Basis": profile["own_history_basis"],
                    "Current Ratio": profile["current_ratio"],
                    "Historical Median Ratio": profile[
                        "historical_ratio_median"
                    ],
                    "History Months": profile["ratio_months"],
                    "Observed Sales 90D": profile["sales_90"],
                    "Premium Heat": bucket,
                    "Heat Percentile": percentile,
                    "Premium / Raw": profile["premium_vs_raw"],
                    "Premium Change 90D": profile["premium_change_90"],
                    "Evidence": profile["evidence_status"],
                    "Confidence": profile["confidence"],
                }
            )

    return rows
