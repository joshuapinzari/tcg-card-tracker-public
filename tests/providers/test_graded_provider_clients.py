from sources.pokemonpricetracker.client import (
    graded_price_history_by_tier,
    graded_sales_by_tier,
    normalize_grade_key,
    sold_listings_by_tier,
    tcgplayer_id_from_card as ppt_tcgplayer_id,
)


def test_normalize_pokemon_price_tracker_grade_keys():
    assert normalize_grade_key("psa10") == "PSA_10"
    assert normalize_grade_key("cgc9_5") == "CGC_9_5"
    assert normalize_grade_key("ACE10") == "ACE_10"
    assert normalize_grade_key("not-a-grade") is None


def test_pokemon_price_tracker_graded_sections():
    card = {
        "tcgPlayerId": "490294",
        "ebay": {
            "salesByGrade": {
                "psa10": {"count": 4, "medianPrice": 250},
                "psa9": {"count": 9, "medianPrice": 125},
                "ace10": {"count": 2, "medianPrice": 180},
            },
            "soldListings": {
                "ace10": [
                    {"price": 175, "soldDate": "2026-09-01T00:00:00.000Z"},
                    {"price": 185, "soldDate": "2026-09-20T00:00:00.000Z"},
                ]
            },
        },
    }

    assert ppt_tcgplayer_id(card) == 490294
    sales = graded_sales_by_tier(card)
    assert sales["ACE_10"]["count"] == 2
    assert sales["PSA_10"]["medianPrice"] == 250

    sold = sold_listings_by_tier(card)
    assert len(sold["ACE_10"]) == 2


def test_pokemon_price_tracker_graded_price_history():
    card = {
        "ebay": {
            "priceHistory": {
                "psa10": {
                    "2026-08-01": {"average": 100, "count": 1},
                    "2026-08-04": {"average": 120, "count": 2},
                },
                "ace10": {
                    "2026-08-03": {"average": 90, "count": 1},
                },
            }
        }
    }

    history = graded_price_history_by_tier(card)
    assert sorted(history["PSA_10"]) == ["2026-08-01", "2026-08-04"]
    assert history["ACE_10"]["2026-08-03"]["average"] == 90


def test_pokemon_price_tracker_raw_history_prefers_primary_printing():
    from sources.pokemonpricetracker.client import raw_price_history

    card = {
        "priceHistory": {
            "conditions": {
                "Near Mint": {
                    "history": [
                        {"date": "2026-10-01T00:00:00.000Z", "market": 10.0}
                    ]
                }
            },
            "variants": {
                "Holofoil": {
                    "Near Mint": {
                        "history": [
                            {
                                "date": "2026-10-01T00:00:00.000Z",
                                "market": 12.0,
                                "volume": 3,
                            }
                        ]
                    }
                }
            },
        }
    }

    label, history = raw_price_history(card, preferred_printing="Holofoil")
    assert label == "Holofoil | Near Mint"
    assert history[0]["market"] == 12.0


def test_pokemon_price_tracker_raw_history_falls_back_to_default_condition():
    from sources.pokemonpricetracker.client import raw_price_history

    card = {
        "priceHistory": {
            "conditions": {
                "Near Mint": {
                    "history": [
                        {"date": "2026-10-01T00:00:00.000Z", "market": 8.5}
                    ]
                }
            },
            "variants": {},
        }
    }

    label, history = raw_price_history(card, preferred_printing="Normal")
    assert label == "Near Mint"
    assert history[0]["market"] == 8.5
