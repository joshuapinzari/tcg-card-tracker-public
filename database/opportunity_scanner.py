from __future__ import annotations

from services.analysis.opportunity_snapshot import snapshot_input_writer

import numpy as np
import pandas as pd

from database.market_analytics import connect
from pokemon_eras import era_case_sql, era_for_set_name
from services.analysis.opportunity_model import (
    DEFAULT_ANALOGS,
    DEFAULT_CONTEXT_WEIGHT,
    SETUP_LABELS,
    add_setup_flags,
    calibrate_one,
)
from services.analysis.regime_overheating_signal import classify_market_regime
from services.analysis.research_rank import score_locked_research_model
from services.analysis.hybrid_rank import load_hybrid_spec, score_hybrid_v4


HISTORY_TABLE = "raw_opportunity_history_90d"
DEFAULT_PRICE_FLOOR_USD = 5.0
HYBRID_V4_SPEC = load_hybrid_spec()

SCHEMA_STATEMENTS = [
    f"""
    CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
        formation_date DATE NOT NULL,
        product_id BIGINT NOT NULL,
        printing VARCHAR(100) NOT NULL,
        set_name TEXT NOT NULL,
        era VARCHAR(50),
        formation_price DOUBLE PRECISION NOT NULL,
        return_30d DOUBLE PRECISION,
        return_90d DOUBLE PRECISION,
        return_180d DOUBLE PRECISION,
        acceleration_30_vs_90 DOUBLE PRECISION,
        relative_set_30d DOUBLE PRECISION,
        relative_set_90d DOUBLE PRECISION,
        relative_era_30d DOUBLE PRECISION,
        relative_era_90d DOUBLE PRECISION,
        relative_market_30d DOUBLE PRECISION,
        relative_market_90d DOUBLE PRECISION,
        set_return_30d DOUBLE PRECISION,
        set_return_90d DOUBLE PRECISION,
        era_return_30d DOUBLE PRECISION,
        era_return_90d DOUBLE PRECISION,
        market_return_30d DOUBLE PRECISION,
        market_return_90d DOUBLE PRECISION,
        set_breadth DOUBLE PRECISION,
        era_breadth DOUBLE PRECISION,
        market_breadth DOUBLE PRECISION,
        drawdown_1y DOUBLE PRECISION,
        prior_runup DOUBLE PRECISION,
        correction DOUBLE PRECISION,
        recovery_30d DOUBLE PRECISION,
        future_return_90d DOUBLE PRECISION NOT NULL,
        future_set_alpha_90d DOUBLE PRECISION NOT NULL,
        future_era_alpha_90d DOUBLE PRECISION,
        future_market_alpha_90d DOUBLE PRECISION,
        PRIMARY KEY (formation_date, product_id)
    );
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_formation
    ON {HISTORY_TABLE} (formation_date);
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_product
    ON {HISTORY_TABLE} (product_id, formation_date DESC);
    """,
    f"ALTER TABLE {HISTORY_TABLE} ADD COLUMN IF NOT EXISTS market_breadth DOUBLE PRECISION;",
    f"ALTER TABLE {HISTORY_TABLE} ADD COLUMN IF NOT EXISTS future_era_alpha_90d DOUBLE PRECISION;",
    f"ALTER TABLE {HISTORY_TABLE} ADD COLUMN IF NOT EXISTS future_market_alpha_90d DOUBLE PRECISION;",
]


ERA_SQL = era_case_sql("p.set_name")


def initialise_opportunity_schema() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


def _context_lateral(scope_type: str, scope_name_sql: str, days: int, alias: str) -> str:
    """SQL lateral join for a context index point within seven days of target."""
    return f"""
    LEFT JOIN LATERAL (
        SELECT
            i.snapshot_date,
            i.index_value::DOUBLE PRECISION AS index_value,
            i.breadth_positive::DOUBLE PRECISION AS breadth_positive
        FROM tcgplayer_market_index_daily AS i
        WHERE i.scope_type = '{scope_type}'
          AND i.scope_name = {scope_name_sql}
          AND i.snapshot_date <= r.formation_date - {days}
          AND i.snapshot_date >= r.formation_date - {days + 7}
        ORDER BY i.snapshot_date DESC
        LIMIT 1
    ) AS {alias} ON TRUE
    """


def _future_context_lateral(scope_type: str, scope_name_sql: str, days: int, alias: str) -> str:
    """SQL lateral join for a future label index; never used as an input feature."""
    return f"""
    LEFT JOIN LATERAL (
        SELECT
            i.snapshot_date,
            i.index_value::DOUBLE PRECISION AS index_value
        FROM tcgplayer_market_index_daily AS i
        WHERE i.scope_type = '{scope_type}'
          AND i.scope_name = {scope_name_sql}
          AND i.snapshot_date <= r.formation_date + {days}
          AND i.snapshot_date >= r.formation_date + {days - 7}
        ORDER BY i.snapshot_date DESC
        LIMIT 1
    ) AS {alias} ON TRUE
    """


@snapshot_input_writer
def rebuild_raw_opportunity_history() -> int:
    """Build a point-in-time raw opportunity panel with a following-90D target.

    The base formation universe comes from ``raw_relative_momentum_90d`` so all
    rows already obey the project's point-in-time safeguards. Additional
    features use only data available on or before the formation date. The
    following 90D return/alpha are labels, never inputs.
    """
    initialise_opportunity_schema()

    set_now = _context_lateral("set", "r.set_name", 0, "set_now")
    set_30 = _context_lateral("set", "r.set_name", 30, "set_30")
    era_now = _context_lateral("era", "r.era", 0, "era_now")
    era_30 = _context_lateral("era", "r.era", 30, "era_30")
    era_90 = _context_lateral("era", "r.era", 90, "era_90")
    market_now = _context_lateral(
        "market", "'Modern English Pokemon'", 0, "market_now"
    )
    market_30 = _context_lateral(
        "market", "'Modern English Pokemon'", 30, "market_30"
    )
    market_90 = _context_lateral(
        "market", "'Modern English Pokemon'", 90, "market_90"
    )
    era_future_90 = _future_context_lateral("era", "r.era", 90, "era_future_90")
    market_future_90 = _future_context_lateral(
        "market", "'Modern English Pokemon'", 90, "market_future_90"
    )

    sql = f"""
        TRUNCATE TABLE {HISTORY_TABLE};

        INSERT INTO {HISTORY_TABLE} (
            formation_date, product_id, printing, set_name, era,
            formation_price,
            return_30d, return_90d, return_180d,
            acceleration_30_vs_90,
            relative_set_30d, relative_set_90d,
            relative_era_30d, relative_era_90d,
            relative_market_30d, relative_market_90d,
            set_return_30d, set_return_90d,
            era_return_30d, era_return_90d,
            market_return_30d, market_return_90d,
            set_breadth, era_breadth, market_breadth,
            drawdown_1y, prior_runup, correction, recovery_30d,
            future_return_90d, future_set_alpha_90d,
            future_era_alpha_90d, future_market_alpha_90d
        )
        SELECT
            r.formation_date,
            r.product_id,
            r.printing,
            r.set_name,
            r.era,
            r.formation_price,
            CASE WHEN p30.price > 0 THEN r.formation_price / p30.price - 1 END,
            r.momentum_90d,
            CASE WHEN p180.price > 0 THEN r.formation_price / p180.price - 1 END,
            CASE WHEN p30.price > 0 THEN
                (r.formation_price / p30.price - 1) - (r.momentum_90d / 3.0)
            END,
            CASE WHEN p30.price > 0 AND set_now.index_value > 0 AND set_30.index_value > 0 THEN
                (r.formation_price / p30.price - 1)
                - (set_now.index_value / set_30.index_value - 1)
            END,
            r.relative_momentum_90d,
            CASE WHEN p30.price > 0 AND era_now.index_value > 0 AND era_30.index_value > 0 THEN
                (r.formation_price / p30.price - 1)
                - (era_now.index_value / era_30.index_value - 1)
            END,
            CASE WHEN era_now.index_value > 0 AND era_90.index_value > 0 THEN
                r.momentum_90d - (era_now.index_value / era_90.index_value - 1)
            END,
            CASE WHEN p30.price > 0 AND market_now.index_value > 0 AND market_30.index_value > 0 THEN
                (r.formation_price / p30.price - 1)
                - (market_now.index_value / market_30.index_value - 1)
            END,
            CASE WHEN market_now.index_value > 0 AND market_90.index_value > 0 THEN
                r.momentum_90d - (market_now.index_value / market_90.index_value - 1)
            END,
            CASE WHEN set_now.index_value > 0 AND set_30.index_value > 0 THEN
                set_now.index_value / set_30.index_value - 1
            END,
            r.set_momentum_90d,
            CASE WHEN era_now.index_value > 0 AND era_30.index_value > 0 THEN
                era_now.index_value / era_30.index_value - 1
            END,
            CASE WHEN era_now.index_value > 0 AND era_90.index_value > 0 THEN
                era_now.index_value / era_90.index_value - 1
            END,
            CASE WHEN market_now.index_value > 0 AND market_30.index_value > 0 THEN
                market_now.index_value / market_30.index_value - 1
            END,
            CASE WHEN market_now.index_value > 0 AND market_90.index_value > 0 THEN
                market_now.index_value / market_90.index_value - 1
            END,
            set_now.breadth_positive,
            era_now.breadth_positive,
            market_now.breadth_positive,
            CASE WHEN high_1y.high_price > 0 THEN r.formation_price / high_1y.high_price - 1 END,
            CASE WHEN p180.price > 0 AND p60.price > 0 THEN p60.price / p180.price - 1 END,
            CASE WHEN p60.price > 0 AND p30.price > 0 THEN p30.price / p60.price - 1 END,
            CASE WHEN p30.price > 0 THEN r.formation_price / p30.price - 1 END,
            r.future_return_90d,
            r.future_alpha_90d,
            CASE WHEN era_now.index_value > 0 AND era_future_90.index_value > 0 THEN
                r.future_return_90d - (era_future_90.index_value / era_now.index_value - 1)
            END,
            CASE WHEN market_now.index_value > 0 AND market_future_90.index_value > 0 THEN
                r.future_return_90d - (market_future_90.index_value / market_now.index_value - 1)
            END
        FROM raw_relative_momentum_90d AS r
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices AS d
            WHERE d.product_id = r.product_id
              AND d.printing = r.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= r.formation_date - 30
              AND d.snapshot_date >= r.formation_date - 37
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) AS p30 ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices AS d
            WHERE d.product_id = r.product_id
              AND d.printing = r.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= r.formation_date - 60
              AND d.snapshot_date >= r.formation_date - 67
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) AS p60 ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices AS d
            WHERE d.product_id = r.product_id
              AND d.printing = r.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= r.formation_date - 180
              AND d.snapshot_date >= r.formation_date - 187
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) AS p180 ON TRUE
        LEFT JOIN LATERAL (
            SELECT MAX(d.market_price)::DOUBLE PRECISION AS high_price
            FROM tcgplayer_daily_prices AS d
            WHERE d.product_id = r.product_id
              AND d.printing = r.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date BETWEEN r.formation_date - 365 AND r.formation_date
        ) AS high_1y ON TRUE
        {set_now}
        {set_30}
        {era_now}
        {era_30}
        {era_90}
        {market_now}
        {market_30}
        {market_90}
        {era_future_90}
        {market_future_90}
        WHERE p30.price IS NOT NULL
          AND p180.price IS NOT NULL
          AND r.era IS NOT NULL;
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute(f"SELECT COUNT(*) FROM {HISTORY_TABLE};")
            return int(cursor.fetchone()[0])


def _era_from_set_name(set_name: str) -> str | None:
    return era_for_set_name(set_name)



def _context_current_rows() -> pd.DataFrame:
    """Return current 30/90/180 index returns and breadth for every scope."""
    sql = """
        WITH latest AS (
            SELECT scope_type, scope_name, MAX(snapshot_date)::DATE AS latest_date
            FROM tcgplayer_market_index_daily
            GROUP BY scope_type, scope_name
        )
        SELECT
            l.scope_type,
            l.scope_name,
            l.latest_date,
            now.index_value::DOUBLE PRECISION,
            now.breadth_positive::DOUBLE PRECISION,
            p30.index_value::DOUBLE PRECISION,
            p90.index_value::DOUBLE PRECISION,
            p180.index_value::DOUBLE PRECISION
        FROM latest AS l
        JOIN LATERAL (
            SELECT index_value, breadth_positive
            FROM tcgplayer_market_index_daily i
            WHERE i.scope_type=l.scope_type AND i.scope_name=l.scope_name
              AND i.snapshot_date <= l.latest_date
            ORDER BY i.snapshot_date DESC LIMIT 1
        ) now ON TRUE
        LEFT JOIN LATERAL (
            SELECT index_value
            FROM tcgplayer_market_index_daily i
            WHERE i.scope_type=l.scope_type AND i.scope_name=l.scope_name
              AND i.snapshot_date <= l.latest_date - 30
              AND i.snapshot_date >= l.latest_date - 37
            ORDER BY i.snapshot_date DESC LIMIT 1
        ) p30 ON TRUE
        LEFT JOIN LATERAL (
            SELECT index_value
            FROM tcgplayer_market_index_daily i
            WHERE i.scope_type=l.scope_type AND i.scope_name=l.scope_name
              AND i.snapshot_date <= l.latest_date - 90
              AND i.snapshot_date >= l.latest_date - 97
            ORDER BY i.snapshot_date DESC LIMIT 1
        ) p90 ON TRUE
        LEFT JOIN LATERAL (
            SELECT index_value
            FROM tcgplayer_market_index_daily i
            WHERE i.scope_type=l.scope_type AND i.scope_name=l.scope_name
              AND i.snapshot_date <= l.latest_date - 180
              AND i.snapshot_date >= l.latest_date - 187
            ORDER BY i.snapshot_date DESC LIMIT 1
        ) p180 ON TRUE;
    """
    with connect() as connection:
        return pd.read_sql_query(sql, connection)


def load_live_raw_features(price_floor_usd: float = DEFAULT_PRICE_FLOOR_USD) -> pd.DataFrame:
    """Build current point-in-time features for the canonical raw universe."""
    sql = f"""
        WITH bounds AS (
            SELECT MAX(snapshot_date)::DATE AS latest_date
            FROM tcgplayer_daily_prices
            WHERE market_price IS NOT NULL
        ),
        universe AS (
            SELECT
                p.product_id, p.name, p.set_name, p.card_number, p.rarity,
                p.set_published_on, pp.printing,
                {ERA_SQL} AS era
            FROM tcgplayer_products p
            JOIN tcgplayer_primary_printings pp ON pp.product_id=p.product_id
            WHERE p.set_name NOT IN (
                'MEE: Mega Evolution Energies', 'SVE: Scarlet & Violet Energies'
            )
        )
        SELECT
            u.product_id, u.name, u.set_name, u.card_number, u.rarity,
            u.set_published_on, u.printing, u.era,
            b.latest_date AS analysis_date,
            cur.price AS current_price,
            p30.price AS price_30,
            p60.price AS price_60,
            p90.price AS price_90,
            p180.price AS price_180,
            high1y.high_price
        FROM universe u
        CROSS JOIN bounds b
        JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices d
            WHERE d.product_id=u.product_id AND d.printing=u.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= b.latest_date
              AND d.snapshot_date >= b.latest_date - 7
              AND d.snapshot_date >= u.set_published_on
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) cur ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices d
            WHERE d.product_id=u.product_id AND d.printing=u.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= b.latest_date - 30
              AND d.snapshot_date >= b.latest_date - 37
              AND d.snapshot_date >= u.set_published_on
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) p30 ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices d
            WHERE d.product_id=u.product_id AND d.printing=u.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= b.latest_date - 60
              AND d.snapshot_date >= b.latest_date - 67
              AND d.snapshot_date >= u.set_published_on
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) p60 ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices d
            WHERE d.product_id=u.product_id AND d.printing=u.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= b.latest_date - 90
              AND d.snapshot_date >= b.latest_date - 97
              AND d.snapshot_date >= u.set_published_on
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) p90 ON TRUE
        LEFT JOIN LATERAL (
            SELECT d.market_price::DOUBLE PRECISION AS price
            FROM tcgplayer_daily_prices d
            WHERE d.product_id=u.product_id AND d.printing=u.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date <= b.latest_date - 180
              AND d.snapshot_date >= b.latest_date - 187
              AND d.snapshot_date >= u.set_published_on
            ORDER BY d.snapshot_date DESC LIMIT 1
        ) p180 ON TRUE
        LEFT JOIN LATERAL (
            SELECT MAX(d.market_price)::DOUBLE PRECISION AS high_price
            FROM tcgplayer_daily_prices d
            WHERE d.product_id=u.product_id AND d.printing=u.printing
              AND d.market_price IS NOT NULL AND d.market_price > 0
              AND d.snapshot_date BETWEEN b.latest_date - 365 AND b.latest_date
              AND d.snapshot_date >= u.set_published_on
        ) high1y ON TRUE
        WHERE cur.price >= %s AND u.era IS NOT NULL;
    """
    with connect() as connection:
        cards = pd.read_sql_query(sql, connection, params=(price_floor_usd,))

    if cards.empty:
        return cards

    contexts = _context_current_rows()
    contexts.columns = [
        "scope_type", "scope_name", "context_date", "index_now", "breadth",
        "index_30", "index_90", "index_180",
    ]
    for days in (30, 90, 180):
        contexts[f"context_return_{days}d"] = (
            contexts["index_now"] / contexts[f"index_{days}"] - 1.0
        )

    market = contexts[
        (contexts["scope_type"] == "market")
        & (contexts["scope_name"] == "Modern English Pokemon")
    ]
    market_row = market.iloc[0] if not market.empty else None

    set_ctx = contexts[contexts["scope_type"] == "set"].set_index("scope_name")
    era_ctx = contexts[contexts["scope_type"] == "era"].set_index("scope_name")

    cards["return_30d"] = cards["current_price"] / cards["price_30"] - 1.0
    cards["return_90d"] = cards["current_price"] / cards["price_90"] - 1.0
    cards["return_180d"] = cards["current_price"] / cards["price_180"] - 1.0
    cards["acceleration_30_vs_90"] = cards["return_30d"] - cards["return_90d"] / 3.0
    cards["drawdown_1y"] = cards["current_price"] / cards["high_price"] - 1.0
    cards["prior_runup"] = cards["price_60"] / cards["price_180"] - 1.0
    cards["correction"] = cards["price_30"] / cards["price_60"] - 1.0
    cards["recovery_30d"] = cards["return_30d"]

    def ctx_value(scope: pd.DataFrame, key: object, column: str) -> float:
        try:
            value = scope.loc[key, column]
            if isinstance(value, pd.Series):
                value = value.iloc[-1]
            return float(value) if pd.notna(value) else np.nan
        except (KeyError, TypeError, ValueError):
            return np.nan

    cards["set_return_30d"] = [
        ctx_value(set_ctx, name, "context_return_30d") for name in cards["set_name"]
    ]
    cards["set_return_90d"] = [
        ctx_value(set_ctx, name, "context_return_90d") for name in cards["set_name"]
    ]
    cards["set_breadth"] = [
        ctx_value(set_ctx, name, "breadth") for name in cards["set_name"]
    ]
    cards["era_return_30d"] = [
        ctx_value(era_ctx, name, "context_return_30d") for name in cards["era"]
    ]
    cards["era_return_90d"] = [
        ctx_value(era_ctx, name, "context_return_90d") for name in cards["era"]
    ]
    cards["era_breadth"] = [
        ctx_value(era_ctx, name, "breadth") for name in cards["era"]
    ]

    market_30 = (
        float(market_row["context_return_30d"])
        if market_row is not None and pd.notna(market_row["context_return_30d"])
        else np.nan
    )
    market_90 = (
        float(market_row["context_return_90d"])
        if market_row is not None and pd.notna(market_row["context_return_90d"])
        else np.nan
    )
    cards["market_return_30d"] = market_30
    cards["market_return_90d"] = market_90
    cards["market_breadth"] = (
        float(market_row["breadth"])
        if market_row is not None and pd.notna(market_row["breadth"])
        else np.nan
    )

    cards["relative_set_30d"] = cards["return_30d"] - cards["set_return_30d"]
    cards["relative_set_90d"] = cards["return_90d"] - cards["set_return_90d"]
    cards["relative_era_30d"] = cards["return_30d"] - cards["era_return_30d"]
    cards["relative_era_90d"] = cards["return_90d"] - cards["era_return_90d"]
    cards["relative_market_30d"] = cards["return_30d"] - market_30
    cards["relative_market_90d"] = cards["return_90d"] - market_90

    return cards.replace([np.inf, -np.inf], np.nan)


def load_opportunity_history() -> pd.DataFrame:
    initialise_opportunity_schema()
    with connect() as connection:
        return pd.read_sql_query(
            f"SELECT * FROM {HISTORY_TABLE} ORDER BY formation_date, product_id;",
            connection,
        )


def history_summary() -> dict:
    initialise_opportunity_schema()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*), MIN(formation_date), MAX(formation_date),
                       COUNT(DISTINCT product_id),
                       COUNT(*) FILTER (
                           WHERE market_breadth IS NOT NULL
                             AND future_era_alpha_90d IS NOT NULL
                             AND future_market_alpha_90d IS NOT NULL
                       )
                FROM {HISTORY_TABLE};
                """
            )
            count, first_date, last_date, products, context_rows = cursor.fetchone()
    return {
        "rows": int(count or 0),
        "first_date": first_date,
        "last_date": last_date,
        "products": int(products or 0),
        "context_rows": int(context_rows or 0),
        "context_ready": bool(count and context_rows and context_rows >= count * 0.80),
    }



def prepare_live_opportunity_universe(
    *,
    price_floor_usd: float = DEFAULT_PRICE_FLOOR_USD,
) -> tuple[pd.DataFrame, str | None, float | None]:
    """Return the live investable feature universe with shared research context.

    The cross-sectional percentiles used by Challenger v1 must be computed on the
    same fixed investable universe for every card.  Keeping this preparation
    separate lets Card Analysis cache it once and calibrate only the selected
    card instead of rescoring thousands of cards on every selection.
    """
    universe = add_setup_flags(load_live_raw_features(price_floor_usd=price_floor_usd))
    if universe.empty:
        return universe, None, None

    universe = universe.copy()
    universe["signal_percentile"] = universe["relative_set_90d"].rank(
        method="average", pct=True
    )
    universe["market_percentile"] = universe["return_90d"].rank(
        method="average", pct=True
    )
    universe["prior_runup_percentile"] = universe["prior_runup"].rank(
        method="average", pct=True
    )
    market_median_90 = pd.to_numeric(
        universe["return_90d"], errors="coerce"
    ).median()
    market_median_90 = (
        None if pd.isna(market_median_90) else float(market_median_90)
    )
    market_regime = classify_market_regime(market_median_90)
    return universe, market_regime, market_median_90


def prepare_opportunity_history() -> pd.DataFrame:
    """Return historical opportunity rows with setup flags attached."""
    return add_setup_flags(load_opportunity_history())


def _score_live_opportunity_card(
    card: pd.Series,
    history: pd.DataFrame,
    *,
    market_regime: str | None,
    market_median_90: float | None,
    max_analogs: int,
    context_weight: float,
) -> dict:
    analogue_defaults = {
        "setup": None,
        "setup_label": "No calibrated setup",
        "analog_count": 0,
        "analogue_quality": "NONE",
        "similarity": np.nan,
        "context_similarity": np.nan,
        "expected_return_90d": np.nan,
        "expected_set_alpha_90d": np.nan,
        "expected_era_alpha_90d": np.nan,
        "expected_market_alpha_90d": np.nan,
        "probability_strong_gain_90d": np.nan,
        "probability_crash_90d": np.nan,
        "probability_set_alpha_positive_90d": np.nan,
    }

    calibrations = []
    for setup in card["setups"]:
        calibrated = calibrate_one(
            card,
            history,
            setup,
            max_analogs=max_analogs,
            context_weight=context_weight,
        )
        if calibrated is not None:
            calibrations.append(calibrated)

    best = None
    if calibrations:
        best = max(
            calibrations,
            key=lambda item: (
                item["expected_set_alpha_90d"],
                item["probability_strong_gain_90d"],
                -item["probability_crash_90d"],
            ),
        )

    row = card.to_dict()
    row.update(analogue_defaults)
    if best is not None:
        row.update(best)
    row["all_setups"] = (
        ", ".join(SETUP_LABELS[s] for s in card["setups"])
        if card["setups"]
        else "None"
    )
    row["market_regime"] = market_regime
    row["market_median_90"] = market_median_90

    research = score_locked_research_model(
        row,
        analogue=best,
        market_regime=market_regime,
    )
    row.update(research)

    # Score the exact same live factor payload with the frozen Hybrid V4
    # challenger.  Mid-era is intentionally identical to V3; Modern and
    # Vintage use their regime-specific frozen weights.  Keep both models on
    # the row so the dashboard can use V4 where applicable without hiding the
    # V3 baseline that the prospective lockbox is comparing against.
    hybrid = score_hybrid_v4(
        research,
        era=row.get("era"),
        spec=HYBRID_V4_SPEC,
    )
    if hybrid is not None:
        hybrid_score = float(hybrid["research_score"])
        if hybrid_score >= 72:
            hybrid_verdict = "VERY STRONG"
        elif hybrid_score >= 60:
            hybrid_verdict = "STRONG"
        elif hybrid_score >= 45:
            hybrid_verdict = "MIXED"
        elif hybrid_score >= 33:
            hybrid_verdict = "WEAK"
        else:
            hybrid_verdict = "AVOID"

        row.update(
            {
                "hybrid_v4_model_version": hybrid["model_version"],
                "hybrid_v4_regime": hybrid["regime"],
                "hybrid_v4_research_score": hybrid_score,
                "hybrid_v4_ranking_score": float(hybrid["ranking_score"]),
                "hybrid_v4_coverage": float(hybrid["coverage"]),
                "hybrid_v4_verdict": hybrid_verdict,
                "hybrid_v4_factors": hybrid.get("factor_payload") or [],
                "hybrid_v4_uses_parent_exact": bool(hybrid.get("uses_parent_exact")),
                "preferred_model": "Hybrid V4",
                "preferred_regime": hybrid["regime"],
                "preferred_research_score": hybrid_score,
                "preferred_ranking_score": float(hybrid["ranking_score"]),
                "preferred_coverage": float(hybrid["coverage"]),
                "preferred_verdict": hybrid_verdict,
            }
        )
    else:
        # Newly-added/unmapped eras fall back to frozen V3 rather than being
        # silently assigned to a regime that was never researched.
        row.update(
            {
                "hybrid_v4_model_version": None,
                "hybrid_v4_regime": None,
                "hybrid_v4_research_score": np.nan,
                "hybrid_v4_ranking_score": np.nan,
                "hybrid_v4_coverage": np.nan,
                "hybrid_v4_verdict": None,
                "hybrid_v4_factors": [],
                "hybrid_v4_uses_parent_exact": False,
                "preferred_model": "Frozen V3",
                "preferred_regime": None,
                "preferred_research_score": float(research["research_score"]),
                "preferred_ranking_score": float(research["research_ranking_score"]),
                "preferred_coverage": float(research["research_coverage"]),
                "preferred_verdict": research["research_verdict"],
            }
        )
    return row


def scan_raw_opportunity_card(
    product_id: int,
    *,
    history: pd.DataFrame | None = None,
    universe: pd.DataFrame | None = None,
    market_regime: str | None = None,
    market_median_90: float | None = None,
    max_analogs: int = DEFAULT_ANALOGS,
    context_weight: float = DEFAULT_CONTEXT_WEIGHT,
) -> dict | None:
    """Score one live card without rescoring the full current universe.

    ``history`` and ``universe`` can be supplied by a caller-side cache.  This is
    the fast path used by Card Analysis; the full scanner remains the right tool
    for Best Opportunities where every card genuinely needs a score.
    """
    if history is None:
        history = prepare_opportunity_history()
    if universe is None:
        universe, market_regime, market_median_90 = prepare_live_opportunity_universe(
            price_floor_usd=DEFAULT_PRICE_FLOOR_USD
        )

    if history.empty or universe.empty:
        return None

    matches = universe[universe["product_id"] == int(product_id)]
    if matches.empty:
        return None

    return _score_live_opportunity_card(
        matches.iloc[0],
        history,
        market_regime=market_regime,
        market_median_90=market_median_90,
        max_analogs=max_analogs,
        context_weight=context_weight,
    )


def scan_raw_opportunities(
    *,
    price_floor_usd: float = DEFAULT_PRICE_FLOOR_USD,
    max_analogs: int = DEFAULT_ANALOGS,
    context_weight: float = DEFAULT_CONTEXT_WEIGHT,
) -> pd.DataFrame:
    """Score the current raw universe with the frozen research challenger.

    Historical-analogue forecasts are attached when a card matches one of the
    calibrated opportunity archetypes. Cards without a calibrated setup are
    retained so the challenger can rank the broader investable universe.
    """
    history = prepare_opportunity_history()

    # Challenger v1 was researched on an investable universe rather than on the
    # user's current browsing filter. Compute cross-sectional percentiles once
    # on the fixed >= $5 live universe, then apply the requested display floor.
    universe, market_regime, market_median_90 = prepare_live_opportunity_universe(
        price_floor_usd=DEFAULT_PRICE_FLOOR_USD
    )
    if history.empty or universe.empty:
        return pd.DataFrame()

    current = universe[universe["current_price"] >= float(price_floor_usd)].copy()
    if current.empty:
        return current

    rows = [
        _score_live_opportunity_card(
            card,
            history,
            market_regime=market_regime,
            market_median_90=market_median_90,
            max_analogs=max_analogs,
            context_weight=context_weight,
        )
        for _, card in current.iterrows()
    ]

    result = pd.DataFrame(rows)
    return result.sort_values(
        ["preferred_ranking_score", "research_ranking_score", "expected_set_alpha_90d", "probability_crash_90d"],
        ascending=[False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)


def load_context_strength() -> pd.DataFrame:
    contexts = _context_current_rows()
    if contexts.empty:
        return contexts
    contexts.columns = [
        "scope_type", "scope_name", "date", "index_now", "breadth",
        "index_30", "index_90", "index_180",
    ]
    for days in (30, 90, 180):
        contexts[f"return_{days}d"] = contexts["index_now"] / contexts[f"index_{days}"] - 1.0
    contexts["acceleration"] = contexts["return_30d"] - contexts["return_90d"] / 3.0

    market = contexts[
        (contexts["scope_type"] == "market")
        & (contexts["scope_name"] == "Modern English Pokemon")
    ]
    market_90 = float(market.iloc[0]["return_90d"]) if not market.empty else np.nan
    contexts["vs_market_90d"] = contexts["return_90d"] - market_90
    contexts["strengthening"] = (
        (contexts["acceleration"] > 0)
        & (contexts["breadth"] >= 0.50)
        & (contexts["return_30d"] > 0)
    )
    return contexts.replace([np.inf, -np.inf], np.nan)
