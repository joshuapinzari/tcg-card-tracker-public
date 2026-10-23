import pytest

from services.trading.graded_hybrid_outlook import (
    _blend_returns,
    synthetic_return,
)


def test_synthetic_return_compounds():
    result = synthetic_return(
        0.10,
        0.05,
    )

    assert result == pytest.approx(
        0.155
    )


def test_negative_wrapper_can_offset_raw_gain():
    result = synthetic_return(
        0.10,
        -0.08,
    )

    assert result == pytest.approx(
        0.012
    )


def test_blend_endpoints():
    assert _blend_returns(
        0.20,
        0.10,
        1.0,
    ) == pytest.approx(
        0.20
    )

    assert _blend_returns(
        0.20,
        0.10,
        0.0,
    ) == pytest.approx(
        0.10
    )


def test_missing_direct_uses_synthetic():
    assert _blend_returns(
        None,
        0.12,
        0.75,
    ) == pytest.approx(
        0.12
    )


def test_manual_slab_uses_underlying_raw_for_all_horizons():
    import pandas as pd

    from services.trading.graded_hybrid_outlook import (
        item_hybrid_outlook,
    )

    prices = pd.DataFrame(
        [
            {
                "card_id": 1,
                "market_type": "RAW",
                "price": 10.0,
                "source": "pokemonpricetracker",
                "recorded_at": pd.Timestamp("2026-10-13"),
            }
        ]
    )

    wrappers = {
        "PSA_10": {
            30: {
                "wrapper_return": 0.02,
                "n_cards": 150,
            },
            90: {
                "wrapper_return": -0.03,
                "n_cards": 150,
            },
            180: {
                "wrapper_return": 0.04,
                "n_cards": 150,
            },
        }
    }

    item = {
        "market_type": "PSA_10",
        "card_id": None,
        "quantity": 1,
        "agreed_unit_value": 270.0,
        "graded_outlook_mode": "AUTO",
        "momentum_30": -0.10,
        "momentum_90": -0.16,
        "momentum_180": 0.02,
    }

    outlook = item_hybrid_outlook(
        item,
        prices=prices,
        wrappers=wrappers,
    )

    assert outlook is not None
    assert outlook["effective_source"] == "RAW + PROVIDER GRADE FALLBACK"
    assert outlook["raw_returns"][30] == pytest.approx(-0.10)
    assert outlook["raw_returns"][90] == pytest.approx(-0.16)
    assert outlook["raw_returns"][180] == pytest.approx(0.02)
    assert outlook["final_returns"][30] == pytest.approx(
        (1 - 0.10) * (1 + 0.02) - 1
    )
    assert outlook["final_returns"][90] == pytest.approx(
        (1 - 0.16) * (1 - 0.03) - 1
    )
    assert outlook["final_returns"][180] == pytest.approx(
        (1 + 0.02) * (1 + 0.04) - 1
    )



def test_auto_prefers_direct_provider_slab_history_over_market_fallback():
    import pandas as pd

    from services.trading.graded_hybrid_outlook import item_hybrid_outlook

    prices = pd.DataFrame(
        [
            {
                "card_id": 42,
                "market_type": "RAW",
                "price": 100.0,
                "source": "pokemonpricetracker",
                "recorded_at": pd.Timestamp("2026-07-15"),
            },
            {
                "card_id": 42,
                "market_type": "RAW",
                "price": 80.0,
                "source": "pokemonpricetracker",
                "recorded_at": pd.Timestamp("2026-10-13"),
            },
            {
                "card_id": 42,
                "market_type": "ACE_10",
                "price": 200.0,
                "source": "pokemonpricetracker",
                "recorded_at": pd.Timestamp("2026-07-15"),
            },
            {
                "card_id": 42,
                "market_type": "ACE_10",
                "price": 300.0,
                "source": "pokemonpricetracker",
                "recorded_at": pd.Timestamp("2026-10-13"),
            },
        ]
    )

    wrappers = {
        "ACE_10": {
            30: {"wrapper_return": 0.0, "n_cards": 100},
            90: {"wrapper_return": 0.50, "n_cards": 100},
            180: {"wrapper_return": 0.0, "n_cards": 100},
        }
    }

    item = {
        "product_id": 42,
        "market_type": "ACE_10",
        "quantity": 1,
        "agreed_unit_value": 250.0,
        "graded_outlook_mode": "AUTO",
    }

    outlook = item_hybrid_outlook(
        item,
        prices=prices,
        wrappers=wrappers,
    )

    assert outlook is not None
    assert outlook["direct_returns"][90] == pytest.approx(0.50)
    assert outlook["synthetic_returns"][90] == pytest.approx(0.20)
    assert outlook["final_returns"][90] == pytest.approx(0.50)
    assert outlook["effective_source"] == "DIRECT POKEMONPRICETRACKER"
    assert outlook["direct_weight"] == pytest.approx(1.0)


def test_manual_reverse_slab_does_not_borrow_product_only_slab_or_raw_history():
    import pandas as pd
    from services.trading.graded_hybrid_outlook import item_hybrid_outlook
    prices=pd.DataFrame([
        {"card_id":42,"market_type":market,"price":price,"source":"pokemonpricetracker",
         "recorded_at":pd.Timestamp(day)}
        for market,price,day in [("RAW",100,"2026-07-15"),("RAW",50,"2026-10-13"),
                                 ("PSA_10",200,"2026-07-15"),("PSA_10",400,"2026-10-13")]
    ])
    item={"product_id":42,"market_type":"PSA_10","printing":"Reverse Holofoil",
          "momentum_90":.2,"quantity":1,"agreed_unit_value":100}
    wrappers={"PSA_10":{90:{"wrapper_return":0.0,"n_cards":100}}}
    result=item_hybrid_outlook(item,prices=prices,wrappers=wrappers)
    assert result["direct_returns"][90] is None
    assert result["raw_returns"][90] == pytest.approx(.2)
    assert result["final_returns"][90] == pytest.approx(.2)
    direct=item_hybrid_outlook(item,mode="DIRECT",prices=prices,wrappers=wrappers)
    assert direct["final_returns"][90] is None
