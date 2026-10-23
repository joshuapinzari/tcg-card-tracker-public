"""Persistence for scheduled/incremental data-update health and run history."""

from __future__ import annotations

from datetime import date


from database.operations import DATABASE_NAME
from database.provider_history import SOURCE_POKEMON_PRICE_TRACKER


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS pipeline_component_status (
        component VARCHAR(80) PRIMARY KEY,
        status VARCHAR(20) NOT NULL,
        last_started_at TIMESTAMP,
        last_finished_at TIMESTAMP,
        last_success_at TIMESTAMP,
        details TEXT,
        last_error TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_run_log (
        run_id BIGSERIAL PRIMARY KEY,
        pipeline VARCHAR(80) NOT NULL,
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP,
        status VARCHAR(20) NOT NULL DEFAULT 'running',
        dry_run BOOLEAN NOT NULL DEFAULT FALSE,
        force_run BOOLEAN NOT NULL DEFAULT FALSE,
        details TEXT,
        last_error TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pipeline_run_log_started
    ON pipeline_run_log (started_at DESC);
    """,
]


def connect():
    import psycopg
    return psycopg.connect(dbname=DATABASE_NAME)


def initialise_update_pipeline_schema() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)
        connection.commit()


def start_pipeline_run(
    *,
    pipeline: str,
    dry_run: bool,
    force_run: bool,
    details: str | None = None,
) -> int:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_run_log (
                    pipeline,
                    dry_run,
                    force_run,
                    details
                )
                VALUES (%s, %s, %s, %s)
                RETURNING run_id;
                """,
                (pipeline, bool(dry_run), bool(force_run), details),
            )
            run_id = int(cursor.fetchone()[0])
        connection.commit()
    return run_id


def finish_pipeline_run(
    run_id: int,
    *,
    status: str,
    details: str | None = None,
    error: str | None = None,
) -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pipeline_run_log
                SET
                    status = %s,
                    finished_at = CURRENT_TIMESTAMP,
                    details = COALESCE(%s, details),
                    last_error = %s
                WHERE run_id = %s;
                """,
                (status, details, error, int(run_id)),
            )
        connection.commit()


def mark_component_started(component: str, *, details: str | None = None) -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_component_status (
                    component,
                    status,
                    last_started_at,
                    details,
                    last_error
                )
                VALUES (%s, 'running', CURRENT_TIMESTAMP, %s, NULL)
                ON CONFLICT (component)
                DO UPDATE SET
                    status = 'running',
                    last_started_at = CURRENT_TIMESTAMP,
                    details = EXCLUDED.details,
                    last_error = NULL;
                """,
                (component, details),
            )
        connection.commit()


def mark_component_finished(
    component: str,
    *,
    success: bool,
    details: str | None = None,
    error: str | None = None,
) -> None:
    status = "success" if success else "failed"
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_component_status (
                    component,
                    status,
                    last_finished_at,
                    last_success_at,
                    details,
                    last_error
                )
                VALUES (
                    %s,
                    %s,
                    CURRENT_TIMESTAMP,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    %s,
                    %s
                )
                ON CONFLICT (component)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    last_finished_at = CURRENT_TIMESTAMP,
                    last_success_at = CASE
                        WHEN %s THEN CURRENT_TIMESTAMP
                        ELSE pipeline_component_status.last_success_at
                    END,
                    details = EXCLUDED.details,
                    last_error = EXCLUDED.last_error;
                """,
                (
                    component,
                    status,
                    bool(success),
                    details,
                    error,
                    bool(success),
                ),
            )
        connection.commit()


def mark_component_skipped(component: str, *, details: str) -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_component_status (
                    component,
                    status,
                    last_finished_at,
                    details,
                    last_error
                )
                VALUES (%s, 'skipped', CURRENT_TIMESTAMP, %s, NULL)
                ON CONFLICT (component)
                DO UPDATE SET
                    status = 'skipped',
                    last_finished_at = CURRENT_TIMESTAMP,
                    details = EXCLUDED.details,
                    last_error = NULL;
                """,
                (component, details),
            )
        connection.commit()


def get_pipeline_freshness() -> dict:
    """Return actual data dates plus the last recorded pipeline component states."""
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(snapshot_date)::date FROM tcgplayer_daily_prices;")
            raw_date = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT MAX(snapshot_on)::date
                FROM provider_market_snapshots
                WHERE source = %s;
                """,
                (SOURCE_POKEMON_PRICE_TRACKER,),
            )
            graded_snapshot_date = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT
                    component,
                    status,
                    last_started_at,
                    last_finished_at,
                    last_success_at,
                    details,
                    last_error
                FROM pipeline_component_status;
                """
            )
            components = {
                str(row[0]): {
                    "status": row[1],
                    "last_started_at": row[2],
                    "last_finished_at": row[3],
                    "last_success_at": row[4],
                    "details": row[5],
                    "last_error": row[6],
                }
                for row in cursor.fetchall()
            }

            cursor.execute(
                """
                SELECT
                    run_id,
                    started_at,
                    finished_at,
                    status,
                    dry_run,
                    force_run,
                    details,
                    last_error
                FROM pipeline_run_log
                WHERE pipeline = 'daily_update'
                ORDER BY started_at DESC
                LIMIT 1;
                """
            )
            latest_run = cursor.fetchone()

    return {
        "raw_date": raw_date,
        "graded_snapshot_date": graded_snapshot_date,
        "components": components,
        "latest_run": (
            None
            if latest_run is None
            else {
                "run_id": int(latest_run[0]),
                "started_at": latest_run[1],
                "finished_at": latest_run[2],
                "status": latest_run[3],
                "dry_run": bool(latest_run[4]),
                "force_run": bool(latest_run[5]),
                "details": latest_run[6],
                "last_error": latest_run[7],
            }
        ),
    }


def freshness_label(value: date | None, *, today: date | None = None) -> str:
    """Small display helper shared by tests/dashboard."""
    if value is None:
        return "no data"
    today = today or date.today()
    age = (today - value).days
    if age <= 0:
        return f"{value} · current"
    if age == 1:
        return f"{value} · 1 day old"
    return f"{value} · {age} days old"
