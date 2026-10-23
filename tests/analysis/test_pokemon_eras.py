import pytest

from pokemon_eras import (
    BLACK_WHITE_SET_NAMES,
    E_CARD_SET_NAMES,
    EX_SERIES_SET_NAMES,
    DIAMOND_PEARL_SET_NAMES,
    HGSS_SET_NAMES,
    PLATINUM_SET_NAMES,
    TRACKED_ERA_LABELS,
    period_for_era,
    era_case_sql,
    era_for_set_name,
)


@pytest.mark.parametrize(
    "set_name",
    sorted(BLACK_WHITE_SET_NAMES),
)
def test_black_white_sets_classify_consistently(set_name):
    assert era_for_set_name(set_name) == "Black & White"


@pytest.mark.parametrize(
    "set_name",
    sorted(HGSS_SET_NAMES),
)
def test_hgss_sets_classify_consistently(set_name):
    assert era_for_set_name(set_name) == "HGSS"


@pytest.mark.parametrize(
    "set_name",
    sorted(DIAMOND_PEARL_SET_NAMES),
)
def test_diamond_pearl_sets_classify_consistently(set_name):
    assert era_for_set_name(set_name) == "Diamond & Pearl"


@pytest.mark.parametrize(
    "set_name",
    sorted(PLATINUM_SET_NAMES),
)
def test_platinum_sets_classify_consistently(set_name):
    assert era_for_set_name(set_name) == "Platinum"


@pytest.mark.parametrize(
    "set_name",
    sorted(E_CARD_SET_NAMES),
)
def test_e_card_sets_classify_consistently(set_name):
    assert era_for_set_name(set_name) == "e-Card"


@pytest.mark.parametrize(
    "set_name",
    sorted(EX_SERIES_SET_NAMES),
)
def test_ex_series_sets_classify_consistently(set_name):
    assert era_for_set_name(set_name) == "EX"


def test_existing_eras_still_classify():
    assert era_for_set_name("XY - Evolutions") == "XY"
    assert era_for_set_name("Hidden Fates") == "Sun & Moon"
    assert era_for_set_name("SWSH07: Evolving Skies") == "Sword & Shield"
    assert era_for_set_name("SV04: Paradox Rift") == "Scarlet & Violet"
    assert era_for_set_name("ME01: Mega Evolution") == "Mega Evolution"


def test_legendary_collection_through_black_white_are_exposed_as_tracked_eras():
    assert TRACKED_ERA_LABELS[:7] == (
        "Legendary Collection",
        "e-Card",
        "EX",
        "Diamond & Pearl",
        "Platinum",
        "HGSS",
        "Black & White",
    )


def test_sql_classifier_contains_dp_platinum_hgss_black_white_and_existing_prefixes():
    sql = era_case_sql("p.set_name")
    assert "'Expedition'" in sql
    assert "THEN 'e-Card'" in sql
    assert "'EX Power Keepers'" in sql
    assert "THEN 'EX'" in sql
    assert "'Diamond and Pearl'" in sql
    assert "'Stormfront'" in sql
    assert "THEN 'Diamond & Pearl'" in sql
    assert "'Platinum'" in sql
    assert "'Supreme Victors'" in sql
    assert "THEN 'Platinum'" in sql
    assert "'HeartGold SoulSilver'" in sql
    assert "'Call of Legends'" in sql
    assert "THEN 'HGSS'" in sql
    assert "'Black and White'" in sql
    assert "THEN 'Black & White'" in sql
    assert "LIKE 'XY%%'" in sql
    assert "LIKE 'SWSH%%'" in sql


def test_broad_period_taxonomy_treats_e_card_as_vintage():
    assert period_for_era("e-Card") == "Vintage"
    assert period_for_era("EX") == "Vintage"
    assert period_for_era("HGSS") == "Vintage"
    assert period_for_era("Black & White") == "Mid-era"
    assert period_for_era("Scarlet & Violet") == "Modern"


def test_legendary_collection_is_tracked_as_vintage():
    assert era_for_set_name("Legendary Collection") == "Legendary Collection"
    assert period_for_era("Legendary Collection") == "Vintage"


def test_legendary_collection_is_present_in_sql_classifier():
    sql = era_case_sql("p.set_name")
    assert "Legendary Collection" in sql
