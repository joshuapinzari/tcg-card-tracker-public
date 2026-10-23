import pandas as pd
import pytest

import services.trading.graded_trade_engine as engine


def fake_universe():
    return pd.DataFrame(
        [
            {
                "card_id": 1,
                "market_type": "PSA_10",
                "market_label": "PSA 10",
                "as_of": pd.Timestamp("2026-10-12"),
                "premium_vs_raw": 2.0,
                "premium_change_90": -0.10,
                "premium_level_rank": 0.10,
                "premium_change_rank": 0.10,
                "premium_heat_percentile": 0.10,
                "premium_heat_bucket": "COOL",
                "historical_relative_reference": None,
                "signal_confidence": "MEDIUM",
                "evidence_status": "POKEMONPRICETRACKER LIVE",
                "ace_very_hot_warning": False,
            },
            {
                "card_id": 2,
                "market_type": "PSA_10",
                "market_label": "PSA 10",
                "as_of": pd.Timestamp("2026-10-12"),
                "premium_vs_raw": 5.0,
                "premium_change_90": 0.25,
                "premium_level_rank": 0.95,
                "premium_change_rank": 0.95,
                "premium_heat_percentile": 0.95,
                "premium_heat_bucket": "VERY HOT",
                "historical_relative_reference": None,
                "signal_confidence": "MEDIUM",
                "evidence_status": "POKEMONPRICETRACKER LIVE",
                "ace_very_hot_warning": False,
            },
            {
                "card_id": 3,
                "market_type": "ACE_10",
                "market_label": "ACE 10",
                "as_of": pd.Timestamp("2026-10-12"),
                "premium_vs_raw": 3.0,
                "premium_change_90": 0.40,
                "premium_level_rank": 0.95,
                "premium_change_rank": 0.95,
                "premium_heat_percentile": 0.95,
                "premium_heat_bucket": "VERY HOT",
                "historical_relative_reference": None,
                "signal_confidence": "MEDIUM",
                "evidence_status": "POKEMONPRICETRACKER LIVE",
                "ace_very_hot_warning": True,
            },
        ]
    )


def raw(side, value=100.0):
    return {
        "side": side,
        "name": "Raw Card",
        "market_type": "RAW",
        "quantity": 1,
        "agreed_unit_value": value,
    }


def graded(side, card_id, market_type, value=100.0):
    return {
        "side": side,
        "name": f"Card {card_id}",
        "product_id": card_id,
        "market_type": market_type,
        "quantity": 1,
        "agreed_unit_value": value,
    }


def fake_intelligence(product_id, market_type):
    if product_id == 1:
        return {
            "confidence": "HIGH",
            "sales_90": 30,
            "history_months": 5.5,
            "premium_vs_raw": 2.0,
            "premium_vs_raw_change_90": -0.10,
            "premium_vs_raw_vs_history": -0.20,
            "premium_vs_raw_historical_median": 2.5,
            "premium_ratio_months": 6,
            "ratio_vs_psa10_vs_history": None,
        }
    if product_id == 2:
        return {
            "confidence": "HIGH",
            "sales_90": 30,
            "history_months": 5.5,
            "premium_vs_raw": 5.0,
            "premium_vs_raw_change_90": 0.25,
            "premium_vs_raw_vs_history": 0.25,
            "premium_vs_raw_historical_median": 4.0,
            "premium_ratio_months": 6,
            "ratio_vs_psa10_vs_history": None,
        }
    if product_id == 3:
        return {
            "confidence": "MEDIUM",
            "sales_90": 8,
            "history_months": 5.0,
            "premium_vs_raw": 3.0,
            "premium_vs_raw_change_90": 0.40,
            "premium_vs_raw_vs_history": 0.10,
            "premium_vs_raw_historical_median": 2.7,
            "premium_ratio_months": 6,
            "ratio_vs_psa10": 0.50,
            "ratio_vs_psa10_historical_median": 0.625,
            "ratio_vs_psa10_vs_history": -0.20,
            "psa_ratio_months": 5,
        }
    return None


def test_raw_to_cheap_psa_favours_receive(monkeypatch):
    monkeypatch.setattr(engine, "get_graded_card_intelligence", fake_intelligence)

    result = engine.analyse_graded_trade(
        [raw("GIVEN")],
        [graded("RECEIVED", 1, "PSA_10")],
        universe=fake_universe(),
    )

    assert result["reference_edge"] == pytest.approx(0.20)
    assert result["verdict"] == "FAVOURS RECEIVE"


def test_expensive_psa_given_favours_receive_raw(monkeypatch):
    monkeypatch.setattr(engine, "get_graded_card_intelligence", fake_intelligence)

    result = engine.analyse_graded_trade(
        [graded("GIVEN", 2, "PSA_10")],
        [raw("RECEIVED")],
        universe=fake_universe(),
    )

    assert result["reference_edge"] == pytest.approx(0.25)
    assert result["verdict"] == "FAVOURS RECEIVE"


def test_ace_uses_own_ace_to_psa_history(monkeypatch):
    monkeypatch.setattr(engine, "get_graded_card_intelligence", fake_intelligence)

    result = engine.analyse_graded_trade(
        [raw("GIVEN")],
        [graded("RECEIVED", 3, "ACE_10")],
        universe=fake_universe(),
    )

    profile = result["received"]["profiles"][0]
    assert profile["own_history_deviation"] == pytest.approx(-0.20)
    assert profile["relative_value_score"] == pytest.approx(0.20)
    assert "ACE 10 / PSA 10" in profile["own_history_basis"]
    assert result["reference_edge"] == pytest.approx(0.20)
    assert len(result["warnings"]) == 1
