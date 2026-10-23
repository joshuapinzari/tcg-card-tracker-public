"""Shared Pokemon-era classification for the tracked English TCG universe.

Keep Python-side and SQL-side era classification in one place so adding an era
cannot leave the dashboard, research models, and collectors disagreeing about
which sets belong where.
"""

from __future__ import annotations



LEGENDARY_COLLECTION_SET_NAMES = frozenset(
    {
        "Legendary Collection",
    }
)


E_CARD_SET_NAMES = frozenset(
    {
        # TCGPlayer uses the shorter "Expedition" group name. Keep the
        # full expansion name as an alias for hand-entered/research values.
        "Expedition",
        "Expedition Base Set",
        "Aquapolis",
        "Skyridge",
    }
)


EX_SERIES_SET_NAMES = frozenset(
    {
        # Main English EX-series expansions. TCGPlayer has used both "and"
        # and ampersand spellings for a couple of groups over time, so keep
        # harmless aliases while the collector still matches exact names.
        "EX Ruby and Sapphire",
        "EX Ruby & Sapphire",
        "EX Sandstorm",
        "EX Dragon",
        "EX Team Magma vs Team Aqua",
        "EX Hidden Legends",
        "EX FireRed & LeafGreen",
        "EX FireRed and LeafGreen",
        "EX Team Rocket Returns",
        "EX Deoxys",
        "EX Emerald",
        "EX Unseen Forces",
        "EX Delta Species",
        "EX Legend Maker",
        "EX Holon Phantoms",
        "EX Crystal Guardians",
        "EX Dragon Frontiers",
        "EX Power Keepers",
    }
)

DIAMOND_PEARL_SET_NAMES = frozenset(
    {
        # TCGPlayer uses "Diamond and Pearl" as the canonical group name.
        # Keep the common ampersand alias for hand-entered/research values.
        "Diamond and Pearl",
        "Diamond & Pearl",
        "Mysterious Treasures",
        "Secret Wonders",
        "Great Encounters",
        "Majestic Dawn",
        "Legends Awakened",
        "Stormfront",
        "Diamond and Pearl Promos",
    }
)


PLATINUM_SET_NAMES = frozenset(
    {
        "Platinum",
        "Rising Rivals",
        "Supreme Victors",
        "Arceus",
    }
)


HGSS_SET_NAMES = frozenset(
    {
        # TCGPlayer's canonical base-set name omits the ampersand.  Keep the
        # common display alias too so hand-entered/research values classify
        # consistently without weakening the exact collector filter.
        "HeartGold SoulSilver",
        "HeartGold & SoulSilver",
        "Unleashed",
        "Undaunted",
        "Triumphant",
        "Call of Legends",
        "HGSS Promos",
    }
)


BLACK_WHITE_SET_NAMES = frozenset(
    {
        "Black and White",
        "Black and White Promos",
        "Emerging Powers",
        "Noble Victories",
        "Next Destinies",
        "Dark Explorers",
        "Dragons Exalted",
        "Dragon Vault",
        "Boundaries Crossed",
        "Plasma Storm",
        "Plasma Freeze",
        "Plasma Blast",
        "Legendary Treasures",
        "Legendary Treasures: Radiant Collection",
    }
)

XY_SPECIAL_SET_NAMES = frozenset(
    {
        "Kalos Starter Set",
        "Double Crisis",
        "Generations",
        "Generations: Radiant Collection",
        "McDonald's Promos 2014",
        "McDonald's Promos 2015",
        "McDonald's Promos 2016",
    }
)

SUN_MOON_SPECIAL_SET_NAMES = frozenset(
    {
        "Shining Legends",
        "Dragon Majesty",
        "Detective Pikachu",
        "Hidden Fates",
        "Hidden Fates: Shiny Vault",
        "McDonald's Promos 2017",
        "McDonald's Promos 2018",
        "McDonald's Promos 2019",
    }
)

TRACKED_ERA_LABELS = (
    "Legendary Collection",
    "e-Card",
    "EX",
    "Diamond & Pearl",
    "Platinum",
    "HGSS",
    "Black & White",
    "XY",
    "Sun & Moon",
    "Sword & Shield",
    "Scarlet & Violet",
    "Mega Evolution",
)


VINTAGE_ERA_LABELS = frozenset(
    {"Legendary Collection", "e-Card", "EX", "Diamond & Pearl", "Platinum", "HGSS"}
)
MID_ERA_LABELS = frozenset({"Black & White", "XY", "Sun & Moon"})
MODERN_ERA_LABELS = frozenset({"Sword & Shield", "Scarlet & Violet", "Mega Evolution"})


def period_for_era(era: str | None) -> str | None:
    """Return the broad collecting/time-period label for an era.

    This is deliberately separate from the Hybrid V4 scoring regime. For
    example, e-Card is historically Vintage but currently remains on the
    frozen-V3 scoring fallback because Vintage V4 did not transfer cleanly in
    research.
    """
    label = str(era or "").strip()
    if label in VINTAGE_ERA_LABELS:
        return "Vintage"
    if label in MID_ERA_LABELS:
        return "Mid-era"
    if label in MODERN_ERA_LABELS:
        return "Modern"
    return None


def era_for_set_name(set_name: str | None) -> str | None:
    """Return the research-era label for a canonical TCGPlayer set name."""
    name = str(set_name or "").strip()

    if name in LEGENDARY_COLLECTION_SET_NAMES:
        return "Legendary Collection"
    if name in E_CARD_SET_NAMES:
        return "e-Card"
    if name in EX_SERIES_SET_NAMES:
        return "EX"
    if name in DIAMOND_PEARL_SET_NAMES:
        return "Diamond & Pearl"
    if name in PLATINUM_SET_NAMES:
        return "Platinum"
    if name in HGSS_SET_NAMES:
        return "HGSS"
    if name in BLACK_WHITE_SET_NAMES:
        return "Black & White"
    if name.startswith("XY") or name in XY_SPECIAL_SET_NAMES:
        return "XY"
    if name.startswith("SM") or name in SUN_MOON_SPECIAL_SET_NAMES:
        return "Sun & Moon"
    if name.startswith("SWSH"):
        return "Sword & Shield"
    if name.startswith(("SV", "SVE")):
        return "Scarlet & Violet"
    if name.startswith(("ME", "MEE")):
        return "Mega Evolution"
    return None


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_in(values) -> str:
    return ",\n                    ".join(_sql_literal(value) for value in sorted(values))


def era_case_sql(column: str = "p.set_name") -> str:
    """Return a CASE expression matching :func:`era_for_set_name`.

    The doubled percent signs are intentional because psycopg uses ``%s``
    placeholders and therefore requires literal LIKE wildcards to be escaped.
    """
    legendary_collection = _sql_in(LEGENDARY_COLLECTION_SET_NAMES)
    e_card = _sql_in(E_CARD_SET_NAMES)
    ex_series = _sql_in(EX_SERIES_SET_NAMES)
    diamond_pearl = _sql_in(DIAMOND_PEARL_SET_NAMES)
    platinum = _sql_in(PLATINUM_SET_NAMES)
    hgss = _sql_in(HGSS_SET_NAMES)
    black_white = _sql_in(BLACK_WHITE_SET_NAMES)
    xy_special = _sql_in(XY_SPECIAL_SET_NAMES)
    sun_moon_special = _sql_in(SUN_MOON_SPECIAL_SET_NAMES)

    return f"""
CASE
    WHEN {column} IN (
                    {legendary_collection}
                ) THEN 'Legendary Collection'
    WHEN {column} IN (
                    {e_card}
                ) THEN 'e-Card'
    WHEN {column} IN (
                    {ex_series}
                ) THEN 'EX'
    WHEN {column} IN (
                    {diamond_pearl}
                ) THEN 'Diamond & Pearl'
    WHEN {column} IN (
                    {platinum}
                ) THEN 'Platinum'
    WHEN {column} IN (
                    {hgss}
                ) THEN 'HGSS'
    WHEN {column} IN (
                    {black_white}
                ) THEN 'Black & White'
    WHEN (
        {column} LIKE 'XY%%'
        OR {column} IN (
                    {xy_special}
                )
    ) THEN 'XY'
    WHEN (
        {column} LIKE 'SM%%'
        OR {column} IN (
                    {sun_moon_special}
                )
    ) THEN 'Sun & Moon'
    WHEN {column} LIKE 'SWSH%%' THEN 'Sword & Shield'
    WHEN {column} LIKE 'SV%%' OR {column} LIKE 'SVE%%' THEN 'Scarlet & Violet'
    WHEN {column} LIKE 'ME%%' OR {column} LIKE 'MEE%%' THEN 'Mega Evolution'
    ELSE NULL
END
""".strip()
