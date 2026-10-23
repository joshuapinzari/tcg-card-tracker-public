"""PostgreSQL persistence for prospective model lockbox snapshots and outcomes."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd

from database.operations import connect


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS model_lockbox_policies (
        policy_version TEXT PRIMARY KEY,
        policy_hash CHAR(64) NOT NULL,
        policy_payload JSONB NOT NULL,
        registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS model_lockbox_models (
        model_version TEXT PRIMARY KEY,
        spec_hash CHAR(64) NOT NULL,
        spec_payload JSONB NOT NULL,
        registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS model_lockbox_snapshots (
        as_of_date DATE PRIMARY KEY,
        policy_version TEXT NOT NULL,
        policy_hash CHAR(64) NOT NULL,
        price_floor_usd DOUBLE PRECISION NOT NULL,
        candidate_count INTEGER NOT NULL,
        captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS model_lockbox_predictions (
        as_of_date DATE NOT NULL REFERENCES model_lockbox_snapshots(as_of_date),
        product_id BIGINT NOT NULL,
        printing VARCHAR(100) NOT NULL,
        model_version TEXT NOT NULL REFERENCES model_lockbox_models(model_version),
        regime VARCHAR(30),
        era VARCHAR(50),
        set_name TEXT NOT NULL,
        card_name TEXT NOT NULL,
        card_number VARCHAR(50),
        formation_price DOUBLE PRECISION NOT NULL,
        formation_set_index DOUBLE PRECISION,
        formation_era_index DOUBLE PRECISION,
        formation_market_index DOUBLE PRECISION,
        research_score DOUBLE PRECISION NOT NULL,
        ranking_score DOUBLE PRECISION NOT NULL,
        coverage DOUBLE PRECISION NOT NULL,
        expected_set_alpha_90d DOUBLE PRECISION,
        probability_strong_gain_90d DOUBLE PRECISION,
        probability_crash_90d DOUBLE PRECISION,
        factor_payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (as_of_date, product_id, printing, model_version)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_model_lockbox_predictions_model_date
    ON model_lockbox_predictions (model_version, as_of_date DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS model_lockbox_outcomes (
        as_of_date DATE NOT NULL,
        product_id BIGINT NOT NULL,
        printing VARCHAR(100) NOT NULL,
        horizon_days INTEGER NOT NULL CHECK (horizon_days IN (30, 90)),
        target_date DATE NOT NULL,
        observed_on DATE NOT NULL,
        observed_price DOUBLE PRECISION NOT NULL,
        raw_return DOUBLE PRECISION NOT NULL,
        set_return DOUBLE PRECISION,
        set_alpha DOUBLE PRECISION,
        era_return DOUBLE PRECISION,
        era_alpha DOUBLE PRECISION,
        market_return DOUBLE PRECISION,
        market_alpha DOUBLE PRECISION,
        strong_gain BOOLEAN NOT NULL,
        crash BOOLEAN NOT NULL,
        settled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (as_of_date, product_id, printing, horizon_days)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_model_lockbox_outcomes_horizon_date
    ON model_lockbox_outcomes (horizon_days, as_of_date DESC);
    """,
]


def initialise_lockbox_schema() -> None:
    """Create append-only prospective lockbox tables."""

    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


def register_policy(policy_version: str, policy_hash: str, policy_payload: dict[str, Any]) -> None:
    """Register one immutable lockbox policy version, rejecting hash drift."""

    initialise_lockbox_schema()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_hash FROM model_lockbox_policies WHERE policy_version=%s",
                (policy_version,),
            )
            existing = cursor.fetchone()
            if existing:
                if str(existing[0]).strip() != policy_hash:
                    raise RuntimeError(
                        f"Lockbox policy drift: {policy_version} was already registered with a different policy hash."
                    )
                return
            cursor.execute(
                """
                INSERT INTO model_lockbox_policies (policy_version, policy_hash, policy_payload)
                VALUES (%s, %s, %s::jsonb)
                """,
                (policy_version, policy_hash, json.dumps(policy_payload, sort_keys=True)),
            )


def register_model(model_version: str, spec_hash: str, spec_payload: dict[str, Any]) -> None:
    """Register one immutable model version, rejecting hash drift forever."""

    initialise_lockbox_schema()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT spec_hash FROM model_lockbox_models WHERE model_version=%s",
                (model_version,),
            )
            existing = cursor.fetchone()
            if existing:
                if str(existing[0]).strip() != spec_hash:
                    raise RuntimeError(
                        f"Lockbox model drift: {model_version} was already registered with a different spec hash."
                    )
                return
            cursor.execute(
                """
                INSERT INTO model_lockbox_models (model_version, spec_hash, spec_payload)
                VALUES (%s, %s, %s::jsonb)
                """,
                (model_version, spec_hash, json.dumps(spec_payload, sort_keys=True)),
            )


def register_snapshot(
    *,
    as_of_date: date,
    policy_version: str,
    policy_hash: str,
    price_floor_usd: float,
    candidate_count: int,
) -> bool:
    """Register one data-date cohort without allowing policy drift."""

    initialise_lockbox_schema()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_version, policy_hash, price_floor_usd FROM model_lockbox_snapshots WHERE as_of_date=%s",
                (as_of_date,),
            )
            existing = cursor.fetchone()
            if existing:
                if (
                    str(existing[0]) != policy_version
                    or str(existing[1]).strip() != policy_hash
                    or abs(float(existing[2]) - float(price_floor_usd)) > 1e-9
                ):
                    raise RuntimeError(
                        f"Lockbox snapshot policy drift detected for {as_of_date}. Existing cohort is immutable."
                    )
                return False
            cursor.execute(
                """
                INSERT INTO model_lockbox_snapshots (
                    as_of_date, policy_version, policy_hash, price_floor_usd, candidate_count
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (as_of_date, policy_version, policy_hash, price_floor_usd, candidate_count),
            )
            return True


def load_context_baselines(as_of_date: date, lookback_days: int = 7) -> dict[tuple[str, str], float]:
    """Load latest set/era/market index levels available by a prediction date."""

    sql = """
        SELECT DISTINCT ON (scope_type, scope_name)
            scope_type, scope_name, index_value::DOUBLE PRECISION
        FROM tcgplayer_market_index_daily
        WHERE snapshot_date <= %s::date
          AND snapshot_date >= %s::date - %s
        ORDER BY scope_type, scope_name, snapshot_date DESC
    """
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (as_of_date, as_of_date, int(lookback_days)))
            return {
                (str(scope_type), str(scope_name)): float(index_value)
                for scope_type, scope_name, index_value in cursor.fetchall()
                if index_value is not None
            }


def insert_predictions(records: list[dict[str, Any]]) -> int:
    """Insert immutable prediction rows; an existing row is never overwritten."""

    if not records:
        return 0
    initialise_lockbox_schema()
    sql = """
        INSERT INTO model_lockbox_predictions (
            as_of_date, product_id, printing, model_version,
            regime, era, set_name, card_name, card_number,
            formation_price, formation_set_index, formation_era_index, formation_market_index,
            research_score, ranking_score, coverage,
            expected_set_alpha_90d, probability_strong_gain_90d, probability_crash_90d,
            factor_payload
        ) VALUES (
            %(as_of_date)s, %(product_id)s, %(printing)s, %(model_version)s,
            %(regime)s, %(era)s, %(set_name)s, %(card_name)s, %(card_number)s,
            %(formation_price)s, %(formation_set_index)s, %(formation_era_index)s, %(formation_market_index)s,
            %(research_score)s, %(ranking_score)s, %(coverage)s,
            %(expected_set_alpha_90d)s, %(probability_strong_gain_90d)s, %(probability_crash_90d)s,
            %(factor_payload)s::jsonb
        )
        ON CONFLICT (as_of_date, product_id, printing, model_version) DO NOTHING
    """
    params = []
    for record in records:
        item = dict(record)
        item["factor_payload"] = json.dumps(item["factor_payload"], sort_keys=True)
        params.append(item)
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(sql, params)
            return max(0, int(cursor.rowcount or 0))



def capture_snapshot_with_predictions(
    *,
    as_of_date: date,
    policy_version: str,
    policy_hash: str,
    price_floor_usd: float,
    candidate_count: int,
    records: list[dict[str, Any]],
) -> int:
    """Atomically create one snapshot cohort and all of its prediction rows.

    A failure while inserting predictions rolls back the snapshot row as well,
    so a rerun can never be blocked by an empty half-created cohort.
    """

    initialise_lockbox_schema()
    sql = """
        INSERT INTO model_lockbox_predictions (
            as_of_date, product_id, printing, model_version,
            regime, era, set_name, card_name, card_number,
            formation_price, formation_set_index, formation_era_index, formation_market_index,
            research_score, ranking_score, coverage,
            expected_set_alpha_90d, probability_strong_gain_90d, probability_crash_90d,
            factor_payload
        ) VALUES (
            %(as_of_date)s, %(product_id)s, %(printing)s, %(model_version)s,
            %(regime)s, %(era)s, %(set_name)s, %(card_name)s, %(card_number)s,
            %(formation_price)s, %(formation_set_index)s, %(formation_era_index)s, %(formation_market_index)s,
            %(research_score)s, %(ranking_score)s, %(coverage)s,
            %(expected_set_alpha_90d)s, %(probability_strong_gain_90d)s, %(probability_crash_90d)s,
            %(factor_payload)s::jsonb
        )
        ON CONFLICT (as_of_date, product_id, printing, model_version) DO NOTHING
    """
    params = []
    for record in records:
        item = dict(record)
        card_number = item.get("card_number")
        if card_number is not None:
            try:
                if pd.isna(card_number):
                    card_number = None
            except (TypeError, ValueError):
                pass
        item["card_number"] = None if card_number is None else str(card_number)
        item["factor_payload"] = json.dumps(item["factor_payload"], sort_keys=True)
        params.append(item)

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_version, policy_hash, price_floor_usd, candidate_count "
                "FROM model_lockbox_snapshots WHERE as_of_date=%s",
                (as_of_date,),
            )
            existing = cursor.fetchone()
            if existing:
                if (
                    str(existing[0]) != policy_version
                    or str(existing[1]).strip() != policy_hash
                    or abs(float(existing[2]) - float(price_floor_usd)) > 1e-9
                    or int(existing[3]) != int(candidate_count)
                ):
                    raise RuntimeError(
                        f"Lockbox snapshot drift detected for {as_of_date}. Existing cohort is immutable."
                    )
            else:
                cursor.execute(
                    """
                    INSERT INTO model_lockbox_snapshots (
                        as_of_date, policy_version, policy_hash, price_floor_usd, candidate_count
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (as_of_date, policy_version, policy_hash, price_floor_usd, candidate_count),
                )
            if params:
                cursor.executemany(sql, params)
                return max(0, int(cursor.rowcount or 0))
            return 0

def settle_matured_outcomes(horizon_days: int, lookback_days: int = 7) -> int:
    """Settle newly mature outcomes once, using no prices after the horizon date."""

    horizon_days = int(horizon_days)
    if horizon_days not in {30, 90}:
        raise ValueError("Lockbox horizon must be 30 or 90 days.")
    initialise_lockbox_schema()
    sql = """
        WITH latest_data AS (
            SELECT MAX(snapshot_date)::DATE AS latest_date
            FROM tcgplayer_daily_prices
            WHERE market_price IS NOT NULL
        ),
        candidates AS (
            SELECT DISTINCT ON (p.as_of_date, p.product_id, p.printing)
                p.as_of_date, p.product_id, p.printing,
                p.set_name, p.era,
                p.formation_price,
                p.formation_set_index,
                p.formation_era_index,
                p.formation_market_index,
                (p.as_of_date + %s) AS target_date
            FROM model_lockbox_predictions p
            CROSS JOIN latest_data l
            WHERE (p.as_of_date + %s) <= l.latest_date
              AND NOT EXISTS (
                  SELECT 1 FROM model_lockbox_outcomes o
                  WHERE o.as_of_date=p.as_of_date
                    AND o.product_id=p.product_id
                    AND o.printing=p.printing
                    AND o.horizon_days=%s
              )
            ORDER BY p.as_of_date, p.product_id, p.printing, p.model_version
        ),
        resolved AS (
            SELECT
                c.*,
                fp.snapshot_date AS observed_on,
                fp.price AS observed_price,
                sf.index_value AS future_set_index,
                ef.index_value AS future_era_index,
                mf.index_value AS future_market_index
            FROM candidates c
            JOIN LATERAL (
                SELECT d.snapshot_date, d.market_price::DOUBLE PRECISION AS price
                FROM tcgplayer_daily_prices d
                WHERE d.product_id=c.product_id
                  AND d.printing=c.printing
                  AND d.market_price IS NOT NULL AND d.market_price > 0
                  AND d.snapshot_date <= c.target_date
                  AND d.snapshot_date >= c.target_date - %s
                ORDER BY d.snapshot_date DESC
                LIMIT 1
            ) fp ON TRUE
            LEFT JOIN LATERAL (
                SELECT i.index_value::DOUBLE PRECISION AS index_value
                FROM tcgplayer_market_index_daily i
                WHERE i.scope_type='set' AND i.scope_name=c.set_name
                  AND i.snapshot_date <= c.target_date
                  AND i.snapshot_date >= c.target_date - %s
                ORDER BY i.snapshot_date DESC LIMIT 1
            ) sf ON TRUE
            LEFT JOIN LATERAL (
                SELECT i.index_value::DOUBLE PRECISION AS index_value
                FROM tcgplayer_market_index_daily i
                WHERE i.scope_type='era' AND i.scope_name=c.era
                  AND i.snapshot_date <= c.target_date
                  AND i.snapshot_date >= c.target_date - %s
                ORDER BY i.snapshot_date DESC LIMIT 1
            ) ef ON TRUE
            LEFT JOIN LATERAL (
                SELECT i.index_value::DOUBLE PRECISION AS index_value
                FROM tcgplayer_market_index_daily i
                WHERE i.scope_type='market' AND i.scope_name='Modern English Pokemon'
                  AND i.snapshot_date <= c.target_date
                  AND i.snapshot_date >= c.target_date - %s
                ORDER BY i.snapshot_date DESC LIMIT 1
            ) mf ON TRUE
        ),
        inserted AS (
            INSERT INTO model_lockbox_outcomes (
                as_of_date, product_id, printing, horizon_days,
                target_date, observed_on, observed_price,
                raw_return, set_return, set_alpha,
                era_return, era_alpha, market_return, market_alpha,
                strong_gain, crash
            )
            SELECT
                as_of_date, product_id, printing, %s,
                target_date, observed_on, observed_price,
                observed_price / formation_price - 1.0,
                CASE WHEN formation_set_index > 0 AND future_set_index > 0
                     THEN future_set_index / formation_set_index - 1.0 END,
                CASE WHEN formation_set_index > 0 AND future_set_index > 0
                     THEN (observed_price / formation_price - 1.0)
                          - (future_set_index / formation_set_index - 1.0) END,
                CASE WHEN formation_era_index > 0 AND future_era_index > 0
                     THEN future_era_index / formation_era_index - 1.0 END,
                CASE WHEN formation_era_index > 0 AND future_era_index > 0
                     THEN (observed_price / formation_price - 1.0)
                          - (future_era_index / formation_era_index - 1.0) END,
                CASE WHEN formation_market_index > 0 AND future_market_index > 0
                     THEN future_market_index / formation_market_index - 1.0 END,
                CASE WHEN formation_market_index > 0 AND future_market_index > 0
                     THEN (observed_price / formation_price - 1.0)
                          - (future_market_index / formation_market_index - 1.0) END,
                (observed_price / formation_price - 1.0) >= 0.15,
                (observed_price / formation_price - 1.0) <= -0.15
            FROM resolved
            ON CONFLICT (as_of_date, product_id, printing, horizon_days) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) FROM inserted
    """
    params = (
        horizon_days,
        horizon_days,
        horizon_days,
        int(lookback_days),
        int(lookback_days),
        int(lookback_days),
        int(lookback_days),
        horizon_days,
    )
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return int(cursor.fetchone()[0])


def load_report_rows(horizon_days: int) -> pd.DataFrame:
    """Load matured prediction/outcome rows for one horizon."""

    sql = """
        SELECT
            p.as_of_date, p.product_id, p.printing, p.model_version,
            p.regime, p.era, p.set_name, p.card_name, p.card_number,
            p.formation_price, p.research_score, p.ranking_score, p.coverage,
            o.target_date, o.observed_on, o.observed_price,
            o.raw_return, o.set_return, o.set_alpha,
            o.era_return, o.era_alpha, o.market_return, o.market_alpha,
            o.strong_gain, o.crash
        FROM model_lockbox_predictions p
        JOIN model_lockbox_outcomes o
          ON o.as_of_date=p.as_of_date
         AND o.product_id=p.product_id
         AND o.printing=p.printing
        WHERE o.horizon_days=%s
        ORDER BY p.as_of_date, p.model_version, p.product_id
    """
    with connect() as connection:
        return pd.read_sql_query(sql, connection, params=(int(horizon_days),))


def status_summary() -> dict[str, Any]:
    """Return compact database status for CLI reporting."""

    initialise_lockbox_schema()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(snapshot_date)::DATE FROM tcgplayer_daily_prices WHERE market_price IS NOT NULL")
            latest_data = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*), MIN(as_of_date), MAX(as_of_date) FROM model_lockbox_snapshots")
            snapshot_count, first_snapshot, last_snapshot = cursor.fetchone()
            cursor.execute("SELECT model_version, COUNT(*) FROM model_lockbox_predictions GROUP BY model_version ORDER BY model_version")
            predictions = {str(k): int(v) for k, v in cursor.fetchall()}
            cursor.execute("SELECT horizon_days, COUNT(*) FROM model_lockbox_outcomes GROUP BY horizon_days ORDER BY horizon_days")
            outcomes = {int(k): int(v) for k, v in cursor.fetchall()}
            cursor.execute("SELECT MIN(as_of_date) FROM model_lockbox_predictions")
            first_prediction = cursor.fetchone()[0]
    return {
        "latest_data_date": latest_data,
        "snapshot_count": int(snapshot_count or 0),
        "first_snapshot": first_snapshot,
        "last_snapshot": last_snapshot,
        "first_prediction": first_prediction,
        "predictions": predictions,
        "outcomes": outcomes,
    }
