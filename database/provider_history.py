"""Persistence and research queries for external provider market history."""

from __future__ import annotations

from collections import Counter
from datetime import date


from database.operations import DATABASE_NAME


SOURCE_POKEMON_PRICE_TRACKER = "pokemonpricetracker"


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS provider_price_history (
        product_id BIGINT NOT NULL
            REFERENCES tcgplayer_products(product_id)
            ON DELETE CASCADE,
        source VARCHAR(50) NOT NULL,
        market_type VARCHAR(30) NOT NULL,
        observed_on DATE NOT NULL,
        series_label VARCHAR(120) NOT NULL DEFAULT '',
        price NUMERIC(14, 4) NOT NULL,
        sale_count INTEGER,
        volume NUMERIC(14, 4),
        total_value NUMERIC(16, 4),
        seven_day_average NUMERIC(14, 4),
        seven_day_volume_avg NUMERIC(14, 4),
        seven_day_value_avg NUMERIC(16, 4),
        collected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (
            product_id,
            source,
            market_type,
            observed_on,
            series_label
        )
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_price_history_market_date
    ON provider_price_history (
        source,
        market_type,
        observed_on DESC
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_price_history_product_date
    ON provider_price_history (
        product_id,
        source,
        observed_on DESC
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_market_snapshots (
        product_id BIGINT NOT NULL
            REFERENCES tcgplayer_products(product_id)
            ON DELETE CASCADE,
        source VARCHAR(50) NOT NULL,
        market_type VARCHAR(30) NOT NULL,
        snapshot_on DATE NOT NULL,
        sales_count INTEGER,
        median_price NUMERIC(14, 4),
        average_price NUMERIC(14, 4),
        min_price NUMERIC(14, 4),
        max_price NUMERIC(14, 4),
        market_price_7d NUMERIC(14, 4),
        market_price_median_7d NUMERIC(14, 4),
        daily_volume_7d NUMERIC(14, 4),
        last_sale_date DATE,
        smart_market_price NUMERIC(14, 4),
        smart_confidence VARCHAR(30),
        smart_method VARCHAR(80),
        smart_days_used INTEGER,
        collected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (
            product_id,
            source,
            market_type,
            snapshot_on
        )
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_market_snapshots_product
    ON provider_market_snapshots (
        product_id,
        source,
        snapshot_on DESC
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_backfill_status (
        product_id BIGINT NOT NULL
            REFERENCES tcgplayer_products(product_id)
            ON DELETE CASCADE,
        source VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL,
        requested_days INTEGER NOT NULL DEFAULT 0,
        observation_count INTEGER NOT NULL DEFAULT 0,
        raw_observations INTEGER NOT NULL DEFAULT 0,
        psa_9_observations INTEGER NOT NULL DEFAULT 0,
        psa_10_observations INTEGER NOT NULL DEFAULT 0,
        ace_10_observations INTEGER NOT NULL DEFAULT 0,
        first_date DATE,
        last_date DATE,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        last_success_at TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (product_id, source)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_backfill_status_source_status
    ON provider_backfill_status (
        source,
        status
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_daily_refresh_status (
        product_id BIGINT NOT NULL
            REFERENCES tcgplayer_products(product_id)
            ON DELETE CASCADE,
        source VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL,
        last_attempt_on DATE NOT NULL,
        last_success_on DATE,
        last_error TEXT,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (product_id, source)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_daily_refresh_source_success
    ON provider_daily_refresh_status (source, last_success_on DESC);
    """,
]


def connect():
    import psycopg
    return psycopg.connect(dbname=DATABASE_NAME)


def initialise_provider_history_schema() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


def get_provider_status_map(connection, source: str) -> dict[int, dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                product_id,
                status,
                requested_days,
                observation_count,
                attempts,
                last_error,
                updated_at
            FROM provider_backfill_status
            WHERE source = %s;
            """,
            (source,),
        )
        rows = cursor.fetchall()

    return {
        int(row[0]): {
            "status": row[1],
            "requested_days": int(row[2] or 0),
            "observation_count": int(row[3] or 0),
            "attempts": int(row[4] or 0),
            "last_error": row[5],
            "updated_at": row[6],
        }
        for row in rows
    }


def _history_tuple(product_id: int, source: str, row: dict) -> tuple:
    return (
        int(product_id),
        source,
        row["market_type"],
        row["observed_on"],
        row.get("series_label") or "",
        row["price"],
        row.get("sale_count"),
        row.get("volume"),
        row.get("total_value"),
        row.get("seven_day_average"),
        row.get("seven_day_volume_avg"),
        row.get("seven_day_value_avg"),
    )


def _snapshot_tuple(product_id: int, source: str, snapshot_on: date, row: dict) -> tuple:
    smart = row.get("smart_market_price") or {}
    return (
        int(product_id),
        source,
        row["market_type"],
        snapshot_on,
        row.get("sales_count"),
        row.get("median_price"),
        row.get("average_price"),
        row.get("min_price"),
        row.get("max_price"),
        row.get("market_price_7d"),
        row.get("market_price_median_7d"),
        row.get("daily_volume_7d"),
        row.get("last_sale_date"),
        smart.get("price") if isinstance(smart, dict) else None,
        smart.get("confidence") if isinstance(smart, dict) else None,
        smart.get("method") if isinstance(smart, dict) else None,
        smart.get("daysUsed") if isinstance(smart, dict) else None,
    )


def store_provider_card_history(
    connection,
    *,
    product_id: int,
    source: str,
    requested_days: int,
    history_rows: list[dict],
    snapshot_rows: list[dict],
    snapshot_on: date,
    update_backfill_status: bool = True,
) -> int:
    """Store provider history/snapshots, optionally updating backfill metadata.

    Historical backfills own ``provider_backfill_status``. Daily refreshes use
    the same storage path but leave that resumability metadata untouched.
    """
    if history_rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO provider_price_history (
                    product_id,
                    source,
                    market_type,
                    observed_on,
                    series_label,
                    price,
                    sale_count,
                    volume,
                    total_value,
                    seven_day_average,
                    seven_day_volume_avg,
                    seven_day_value_avg
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (
                    product_id,
                    source,
                    market_type,
                    observed_on,
                    series_label
                )
                DO UPDATE SET
                    price = EXCLUDED.price,
                    sale_count = EXCLUDED.sale_count,
                    volume = EXCLUDED.volume,
                    total_value = EXCLUDED.total_value,
                    seven_day_average = EXCLUDED.seven_day_average,
                    seven_day_volume_avg = EXCLUDED.seven_day_volume_avg,
                    seven_day_value_avg = EXCLUDED.seven_day_value_avg,
                    collected_at = CURRENT_TIMESTAMP;
                """,
                [_history_tuple(product_id, source, row) for row in history_rows],
            )

    if snapshot_rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO provider_market_snapshots (
                    product_id,
                    source,
                    market_type,
                    snapshot_on,
                    sales_count,
                    median_price,
                    average_price,
                    min_price,
                    max_price,
                    market_price_7d,
                    market_price_median_7d,
                    daily_volume_7d,
                    last_sale_date,
                    smart_market_price,
                    smart_confidence,
                    smart_method,
                    smart_days_used
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT (
                    product_id,
                    source,
                    market_type,
                    snapshot_on
                )
                DO UPDATE SET
                    sales_count = EXCLUDED.sales_count,
                    median_price = EXCLUDED.median_price,
                    average_price = EXCLUDED.average_price,
                    min_price = EXCLUDED.min_price,
                    max_price = EXCLUDED.max_price,
                    market_price_7d = EXCLUDED.market_price_7d,
                    market_price_median_7d = EXCLUDED.market_price_median_7d,
                    daily_volume_7d = EXCLUDED.daily_volume_7d,
                    last_sale_date = EXCLUDED.last_sale_date,
                    smart_market_price = EXCLUDED.smart_market_price,
                    smart_confidence = EXCLUDED.smart_confidence,
                    smart_method = EXCLUDED.smart_method,
                    smart_days_used = EXCLUDED.smart_days_used,
                    collected_at = CURRENT_TIMESTAMP;
                """,
                [
                    _snapshot_tuple(product_id, source, snapshot_on, row)
                    for row in snapshot_rows
                ],
            )

    if update_backfill_status:
        counts = Counter(row["market_type"] for row in history_rows)
        dates = [row["observed_on"] for row in history_rows if row.get("observed_on")]
        status = "completed" if history_rows else "no_history"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO provider_backfill_status (
                    product_id,
                    source,
                    status,
                    requested_days,
                    observation_count,
                    raw_observations,
                    psa_9_observations,
                    psa_10_observations,
                    ace_10_observations,
                    first_date,
                    last_date,
                    attempts,
                    last_error,
                    last_success_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    1, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (product_id, source)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    requested_days = GREATEST(
                        provider_backfill_status.requested_days,
                        EXCLUDED.requested_days
                    ),
                    observation_count = EXCLUDED.observation_count,
                    raw_observations = EXCLUDED.raw_observations,
                    psa_9_observations = EXCLUDED.psa_9_observations,
                    psa_10_observations = EXCLUDED.psa_10_observations,
                    ace_10_observations = EXCLUDED.ace_10_observations,
                    first_date = EXCLUDED.first_date,
                    last_date = EXCLUDED.last_date,
                    attempts = provider_backfill_status.attempts + 1,
                    last_error = NULL,
                    last_success_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (
                    int(product_id),
                    source,
                    status,
                    int(requested_days),
                    len(history_rows),
                    counts.get("RAW", 0),
                    counts.get("PSA_9", 0),
                    counts.get("PSA_10", 0),
                    counts.get("ACE_10", 0),
                    min(dates) if dates else None,
                    max(dates) if dates else None,
                ),
            )

    return len(history_rows)


def get_provider_latest_history_dates(
    connection,
    source: str,
    *,
    product_ids: list[int] | None = None,
) -> dict[tuple[int, str, str], date]:
    """Return the latest stored date for each provider product/market series."""
    where_product = ""
    params: list[object] = [source]
    if product_ids:
        where_product = " AND product_id = ANY(%s)"
        params.append([int(value) for value in product_ids])

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                product_id,
                market_type,
                series_label,
                MAX(observed_on)::date
            FROM provider_price_history
            WHERE source = %s
              {where_product}
            GROUP BY product_id, market_type, series_label;
            """,
            tuple(params),
        )
        rows = cursor.fetchall()

    return {
        (int(product_id), str(market_type), str(series_label or "")): observed_on
        for product_id, market_type, series_label, observed_on in rows
        if observed_on is not None
    }



def get_provider_daily_refresh_dates(
    connection,
    source: str,
) -> dict[int, date]:
    """Return the last successful incremental refresh date per provider product."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('provider_daily_refresh_status');")
        if cursor.fetchone()[0] is None:
            return {}

        cursor.execute(
            """
            SELECT product_id, last_success_on
            FROM provider_daily_refresh_status
            WHERE source = %s
              AND last_success_on IS NOT NULL;
            """,
            (source,),
        )
        rows = cursor.fetchall()

    return {
        int(product_id): refreshed_on
        for product_id, refreshed_on in rows
        if refreshed_on is not None
    }


def mark_provider_daily_refresh(
    connection,
    *,
    product_id: int,
    source: str,
    success: bool,
    refresh_on: date,
    error: str | None = None,
) -> None:
    """Record an incremental refresh independently from graded sale availability."""
    status = "success" if success else "failed"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO provider_daily_refresh_status (
                product_id,
                source,
                status,
                last_attempt_on,
                last_success_on,
                last_error,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s,
                CASE WHEN %s THEN %s ELSE NULL END,
                %s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (product_id, source)
            DO UPDATE SET
                status = EXCLUDED.status,
                last_attempt_on = EXCLUDED.last_attempt_on,
                last_success_on = CASE
                    WHEN %s THEN EXCLUDED.last_success_on
                    ELSE provider_daily_refresh_status.last_success_on
                END,
                last_error = EXCLUDED.last_error,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (
                int(product_id),
                source,
                status,
                refresh_on,
                bool(success),
                refresh_on,
                None if success else str(error or "")[:2000],
                bool(success),
            ),
        )


def mark_provider_failed(
    connection,
    *,
    product_id: int,
    source: str,
    requested_days: int,
    error: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO provider_backfill_status (
                product_id,
                source,
                status,
                requested_days,
                attempts,
                last_error,
                updated_at
            )
            VALUES (
                %s, %s, 'failed', %s, 1, %s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (product_id, source)
            DO UPDATE SET
                status = 'failed',
                requested_days = GREATEST(
                    provider_backfill_status.requested_days,
                    EXCLUDED.requested_days
                ),
                attempts = provider_backfill_status.attempts + 1,
                last_error = EXCLUDED.last_error,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (int(product_id), source, int(requested_days), error[:2000]),
        )


def get_provider_backfill_summary(connection, source: str) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, COUNT(*)
            FROM provider_backfill_status
            WHERE source = %s
            GROUP BY status
            ORDER BY status;
            """,
            (source,),
        )
        statuses = dict(cursor.fetchall())

        cursor.execute(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT product_id),
                MIN(observed_on),
                MAX(observed_on),
                COUNT(*) FILTER (WHERE market_type = 'RAW'),
                COUNT(*) FILTER (WHERE market_type = 'PSA_9'),
                COUNT(*) FILTER (WHERE market_type = 'PSA_10'),
                COUNT(*) FILTER (WHERE market_type = 'ACE_10')
            FROM provider_price_history
            WHERE source = %s;
            """,
            (source,),
        )
        row = cursor.fetchone()

    return {
        "statuses": statuses,
        "observations": int(row[0] or 0),
        "products": int(row[1] or 0),
        "first_date": row[2],
        "last_date": row[3],
        "raw_observations": int(row[4] or 0),
        "psa_9_observations": int(row[5] or 0),
        "psa_10_observations": int(row[6] or 0),
        "ace_10_observations": int(row[7] or 0),
    }


def get_product_provider_history(
    product_id: int,
    *,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
) -> list[tuple]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    observed_on,
                    market_type,
                    price::DOUBLE PRECISION,
                    sale_count,
                    volume::DOUBLE PRECISION,
                    series_label
                FROM provider_price_history
                WHERE product_id = %s
                  AND source = %s
                  AND market_type IN ('RAW', 'PSA_9', 'PSA_10', 'ACE_10')
                ORDER BY observed_on, market_type;
                """,
                (int(product_id), source),
            )
            return cursor.fetchall()


def get_provider_ratio_timeseries(
    *,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
) -> list[tuple]:
    """Return monthly cross-card RAW/PSA/ACE ratio research aggregates."""
    query = """
        WITH monthly AS (
            SELECT
                product_id,
                DATE_TRUNC('month', observed_on)::date AS month,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY price::DOUBLE PRECISION
                ) FILTER (WHERE market_type = 'RAW') AS raw_price,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY price::DOUBLE PRECISION
                ) FILTER (WHERE market_type = 'PSA_10') AS psa_10_price,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY price::DOUBLE PRECISION
                ) FILTER (WHERE market_type = 'ACE_10') AS ace_10_price,
                COALESCE(
                    SUM(sale_count) FILTER (WHERE market_type = 'ACE_10'),
                    0
                )::INTEGER AS ace_sales
            FROM provider_price_history
            WHERE source = %s
              AND market_type IN ('RAW', 'PSA_10', 'ACE_10')
            GROUP BY product_id, DATE_TRUNC('month', observed_on)
        ),
        ratios AS (
            SELECT
                month,
                product_id,
                CASE
                    WHEN raw_price > 0 AND psa_10_price > 0
                    THEN psa_10_price / raw_price
                END AS psa_10_to_raw,
                CASE
                    WHEN raw_price > 0 AND ace_10_price > 0
                    THEN ace_10_price / raw_price
                END AS ace_10_to_raw,
                CASE
                    WHEN psa_10_price > 0 AND ace_10_price > 0
                    THEN ace_10_price / psa_10_price
                END AS ace_10_to_psa_10,
                ace_sales
            FROM monthly
        )
        SELECT
            month,
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY psa_10_to_raw
            ) FILTER (WHERE psa_10_to_raw IS NOT NULL) AS psa_10_to_raw_median,
            AVG(psa_10_to_raw) FILTER (
                WHERE psa_10_to_raw IS NOT NULL
            ) AS psa_10_to_raw_mean,
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY ace_10_to_raw
            ) FILTER (WHERE ace_10_to_raw IS NOT NULL) AS ace_10_to_raw_median,
            AVG(ace_10_to_raw) FILTER (
                WHERE ace_10_to_raw IS NOT NULL
            ) AS ace_10_to_raw_mean,
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY ace_10_to_psa_10
            ) FILTER (WHERE ace_10_to_psa_10 IS NOT NULL) AS ace_10_to_psa_10_median,
            AVG(ace_10_to_psa_10) FILTER (
                WHERE ace_10_to_psa_10 IS NOT NULL
            ) AS ace_10_to_psa_10_mean,
            COUNT(*) FILTER (
                WHERE psa_10_to_raw IS NOT NULL
            )::INTEGER AS psa_raw_cards,
            COUNT(*) FILTER (
                WHERE ace_10_to_raw IS NOT NULL
            )::INTEGER AS ace_raw_cards,
            COUNT(*) FILTER (
                WHERE ace_10_to_psa_10 IS NOT NULL
            )::INTEGER AS ace_psa_cards,
            SUM(ace_sales)::INTEGER AS ace_sales
        FROM ratios
        GROUP BY month
        ORDER BY month;
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (source,))
            return cursor.fetchall()


def get_provider_fixed_cohort_analysis(
    *,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
) -> dict:
    """Analyse the same cards across all complete provider-history months.

    A complete month is one where the stored provider history reaches at least
    the 5th and the 25th day. This automatically excludes partial boundary
    months such as the first/last month of a rolling backfill.

    The fixed cohort contains only products with usable monthly RAW, PSA 10,
    and ACE 10 prices in every complete month. Liquidity bands are based on a
    card's average monthly ACE 10 sale count across those months.
    """
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH month_bounds AS (
                    SELECT
                        DATE_TRUNC('month', observed_on)::date AS month,
                        MIN(observed_on) AS first_day,
                        MAX(observed_on) AS last_day
                    FROM provider_price_history
                    WHERE source = %s
                    GROUP BY DATE_TRUNC('month', observed_on)
                )
                SELECT month
                FROM month_bounds
                WHERE EXTRACT(DAY FROM first_day) <= 5
                  AND EXTRACT(DAY FROM last_day) >= 25
                ORDER BY month;
                """,
                (source,),
            )
            months = [row[0] for row in cursor.fetchall()]

            if not months:
                return {
                    "months": [],
                    "cohort_size": 0,
                    "monthly": [],
                    "liquidity_bands": [],
                    "start_to_end": None,
                }

            cursor.execute(
                """
                WITH complete_months AS (
                    SELECT UNNEST(%s::date[]) AS month
                ),
                monthly AS (
                    SELECT
                        product_id,
                        DATE_TRUNC('month', observed_on)::date AS month,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'RAW') AS raw_price,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'PSA_10') AS psa_10_price,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'ACE_10') AS ace_10_price,
                        COALESCE(
                            SUM(sale_count) FILTER (WHERE market_type = 'ACE_10'),
                            0
                        )::INTEGER AS ace_sales
                    FROM provider_price_history
                    WHERE source = %s
                      AND DATE_TRUNC('month', observed_on)::date = ANY(%s::date[])
                      AND market_type IN ('RAW', 'PSA_10', 'ACE_10')
                    GROUP BY product_id, DATE_TRUNC('month', observed_on)
                ),
                eligible AS (
                    SELECT product_id
                    FROM monthly
                    WHERE raw_price > 0
                      AND psa_10_price > 0
                      AND ace_10_price > 0
                    GROUP BY product_id
                    HAVING COUNT(*) = (SELECT COUNT(*) FROM complete_months)
                ),
                cohort AS (
                    SELECT
                        m.product_id,
                        m.month,
                        m.raw_price,
                        m.psa_10_price,
                        m.ace_10_price,
                        m.ace_sales,
                        m.psa_10_price / m.raw_price AS psa_to_raw,
                        m.ace_10_price / m.raw_price AS ace_to_raw,
                        m.ace_10_price / m.psa_10_price AS ace_to_psa
                    FROM monthly m
                    JOIN eligible e USING (product_id)
                ),
                changes AS (
                    SELECT
                        *,
                        LAG(ace_to_psa) OVER (
                            PARTITION BY product_id ORDER BY month
                        ) AS prior_ace_to_psa
                    FROM cohort
                )
                SELECT
                    month,
                    COUNT(*)::INTEGER AS cards,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY psa_to_raw) AS psa_raw_median,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY ace_to_raw) AS ace_raw_median,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY ace_to_psa) AS ace_psa_median,
                    AVG(ace_to_psa) AS ace_psa_mean,
                    SUM(ace_sales)::INTEGER AS ace_sales,
                    AVG(ace_sales::DOUBLE PRECISION) AS avg_ace_sales_per_card,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY ace_sales::DOUBLE PRECISION
                    ) AS median_ace_sales_per_card,
                    COUNT(*) FILTER (
                        WHERE prior_ace_to_psa IS NOT NULL
                          AND ace_to_psa > prior_ace_to_psa
                    )::INTEGER AS cards_up,
                    COUNT(*) FILTER (
                        WHERE prior_ace_to_psa IS NOT NULL
                          AND ace_to_psa < prior_ace_to_psa
                    )::INTEGER AS cards_down,
                    COUNT(*) FILTER (
                        WHERE prior_ace_to_psa IS NOT NULL
                          AND ace_to_psa = prior_ace_to_psa
                    )::INTEGER AS cards_flat
                FROM changes
                GROUP BY month
                ORDER BY month;
                """,
                (months, source, months),
            )
            monthly = cursor.fetchall()

            cursor.execute(
                """
                WITH complete_months AS (
                    SELECT UNNEST(%s::date[]) AS month
                ),
                monthly AS (
                    SELECT
                        product_id,
                        DATE_TRUNC('month', observed_on)::date AS month,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'RAW') AS raw_price,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'PSA_10') AS psa_10_price,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'ACE_10') AS ace_10_price,
                        COALESCE(
                            SUM(sale_count) FILTER (WHERE market_type = 'ACE_10'),
                            0
                        )::INTEGER AS ace_sales
                    FROM provider_price_history
                    WHERE source = %s
                      AND DATE_TRUNC('month', observed_on)::date = ANY(%s::date[])
                      AND market_type IN ('RAW', 'PSA_10', 'ACE_10')
                    GROUP BY product_id, DATE_TRUNC('month', observed_on)
                ),
                eligible AS (
                    SELECT product_id
                    FROM monthly
                    WHERE raw_price > 0
                      AND psa_10_price > 0
                      AND ace_10_price > 0
                    GROUP BY product_id
                    HAVING COUNT(*) = (SELECT COUNT(*) FROM complete_months)
                ),
                per_card AS (
                    SELECT
                        m.product_id,
                        AVG(m.ace_sales::DOUBLE PRECISION) AS avg_monthly_ace_sales,
                        MIN(m.ace_10_price / m.psa_10_price) FILTER (
                            WHERE m.month = (SELECT MIN(month) FROM complete_months)
                        ) AS start_ratio,
                        MIN(m.ace_10_price / m.psa_10_price) FILTER (
                            WHERE m.month = (SELECT MAX(month) FROM complete_months)
                        ) AS end_ratio
                    FROM monthly m
                    JOIN eligible e USING (product_id)
                    GROUP BY m.product_id
                ),
                banded AS (
                    SELECT
                        *,
                        CASE
                            WHEN avg_monthly_ace_sales < 3 THEN '1-2/month'
                            WHEN avg_monthly_ace_sales < 6 THEN '3-5/month'
                            WHEN avg_monthly_ace_sales < 11 THEN '6-10/month'
                            ELSE '10+/month'
                        END AS liquidity_band,
                        CASE
                            WHEN avg_monthly_ace_sales < 3 THEN 1
                            WHEN avg_monthly_ace_sales < 6 THEN 2
                            WHEN avg_monthly_ace_sales < 11 THEN 3
                            ELSE 4
                        END AS band_order
                    FROM per_card
                )
                SELECT
                    liquidity_band,
                    COUNT(*)::INTEGER AS cards,
                    AVG(avg_monthly_ace_sales) AS avg_monthly_ace_sales,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY start_ratio) AS start_median,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY end_ratio) AS end_median,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY ((end_ratio / start_ratio) - 1.0)
                    ) AS median_change,
                    COUNT(*) FILTER (WHERE end_ratio > start_ratio)::INTEGER AS cards_up,
                    COUNT(*) FILTER (WHERE end_ratio < start_ratio)::INTEGER AS cards_down,
                    COUNT(*) FILTER (WHERE end_ratio = start_ratio)::INTEGER AS cards_flat
                FROM banded
                GROUP BY liquidity_band, band_order
                ORDER BY band_order;
                """,
                (months, source, months),
            )
            bands = cursor.fetchall()

            cursor.execute(
                """
                WITH complete_months AS (
                    SELECT UNNEST(%s::date[]) AS month
                ),
                monthly AS (
                    SELECT
                        product_id,
                        DATE_TRUNC('month', observed_on)::date AS month,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'RAW') AS raw_price,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'PSA_10') AS psa_10_price,
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY price::DOUBLE PRECISION
                        ) FILTER (WHERE market_type = 'ACE_10') AS ace_10_price
                    FROM provider_price_history
                    WHERE source = %s
                      AND DATE_TRUNC('month', observed_on)::date = ANY(%s::date[])
                      AND market_type IN ('RAW', 'PSA_10', 'ACE_10')
                    GROUP BY product_id, DATE_TRUNC('month', observed_on)
                ),
                eligible AS (
                    SELECT product_id
                    FROM monthly
                    WHERE raw_price > 0
                      AND psa_10_price > 0
                      AND ace_10_price > 0
                    GROUP BY product_id
                    HAVING COUNT(*) = (SELECT COUNT(*) FROM complete_months)
                ),
                ratios AS (
                    SELECT
                        m.product_id,
                        m.month,
                        m.ace_10_price / m.psa_10_price AS ace_to_psa
                    FROM monthly m
                    JOIN eligible e USING (product_id)
                ),
                per_card AS (
                    SELECT
                        product_id,
                        MIN(ace_to_psa) FILTER (
                            WHERE month = (SELECT MIN(month) FROM complete_months)
                        ) AS start_ratio,
                        MIN(ace_to_psa) FILTER (
                            WHERE month = (SELECT MAX(month) FROM complete_months)
                        ) AS end_ratio
                    FROM ratios
                    GROUP BY product_id
                )
                SELECT
                    COUNT(*)::INTEGER AS cards,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY start_ratio) AS start_median,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY end_ratio) AS end_median,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY ((end_ratio / start_ratio) - 1.0)
                    ) AS median_change,
                    COUNT(*) FILTER (WHERE end_ratio > start_ratio)::INTEGER AS cards_up,
                    COUNT(*) FILTER (WHERE end_ratio < start_ratio)::INTEGER AS cards_down,
                    COUNT(*) FILTER (WHERE end_ratio = start_ratio)::INTEGER AS cards_flat
                FROM per_card;
                """,
                (months, source, months),
            )
            start_to_end = cursor.fetchone()

    cohort_size = int(monthly[0][1]) if monthly else 0
    return {
        "months": months,
        "cohort_size": cohort_size,
        "monthly": monthly,
        "liquidity_bands": bands,
        "start_to_end": start_to_end,
    }


def get_provider_graded_snapshot_rows(
    *,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
) -> list[tuple]:
    """Return one current provider-backed market snapshot per TCGPlayer product.

    Prices come only from ``provider_price_history``.  This is the production
    graded snapshot used by the dashboard; legacy pricing tables are not
    consulted.
    """
    query = """
        WITH anchor AS (
            SELECT MAX(observed_on)::date AS as_of
            FROM provider_price_history
            WHERE source = %s
        ),
        latest AS (
            SELECT DISTINCT ON (product_id, market_type)
                product_id,
                market_type,
                observed_on,
                price::DOUBLE PRECISION AS price
            FROM provider_price_history
            WHERE source = %s
              AND market_type IN ('RAW', 'PSA_9', 'PSA_10', 'ACE_10')
              AND price > 0
            ORDER BY product_id, market_type, observed_on DESC
        ),
        liquidity AS (
            SELECT
                h.product_id,
                h.market_type,
                MAX(h.observed_on) FILTER (
                    WHERE COALESCE(h.sale_count, 0) > 0
                ) AS last_sale_date,
                COALESCE(SUM(h.sale_count) FILTER (
                    WHERE h.observed_on > a.as_of - 30
                ), 0)::INTEGER AS sales_30,
                COALESCE(SUM(h.sale_count) FILTER (
                    WHERE h.observed_on > a.as_of - 90
                ), 0)::INTEGER AS sales_90,
                COALESCE(SUM(h.sale_count) FILTER (
                    WHERE h.observed_on > a.as_of - 180
                ), 0)::INTEGER AS sales_180,
                COUNT(*)::INTEGER AS history_points,
                MIN(h.observed_on) AS first_observed_on,
                MAX(h.observed_on) AS last_observed_on
            FROM provider_price_history AS h
            CROSS JOIN anchor AS a
            WHERE h.source = %s
              AND h.market_type IN ('PSA_9', 'PSA_10', 'ACE_10')
            GROUP BY h.product_id, h.market_type
        ),
        monthly AS (
            SELECT
                product_id,
                DATE_TRUNC('month', observed_on)::date AS month,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY price::DOUBLE PRECISION)
                    FILTER (WHERE market_type = 'RAW') AS raw_price,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY price::DOUBLE PRECISION)
                    FILTER (WHERE market_type = 'PSA_10') AS psa_10_price,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY price::DOUBLE PRECISION)
                    FILTER (WHERE market_type = 'ACE_10') AS ace_10_price
            FROM provider_price_history
            WHERE source = %s
              AND market_type IN ('RAW', 'PSA_10', 'ACE_10')
            GROUP BY product_id, DATE_TRUNC('month', observed_on)
        ),
        ratio_stats AS (
            SELECT
                product_id,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY ace_10_price / psa_10_price
                ) FILTER (WHERE ace_10_price > 0 AND psa_10_price > 0)
                    AS ace_psa_historical_median,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY ace_10_price / raw_price
                ) FILTER (WHERE ace_10_price > 0 AND raw_price > 0)
                    AS ace_raw_historical_median,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY psa_10_price / raw_price
                ) FILTER (WHERE psa_10_price > 0 AND raw_price > 0)
                    AS psa_raw_historical_median,
                COUNT(*) FILTER (WHERE ace_10_price > 0 AND psa_10_price > 0)::INTEGER
                    AS ace_ratio_months
            FROM monthly
            GROUP BY product_id
        )
        SELECT
            p.product_id,
            p.name,
            p.set_name,
            p.card_number,
            p.rarity,
            p.artist,
            p.image_url,
            p.tcgplayer_url,
            p.set_published_on,
            pp.printing,
            MAX(l.price) FILTER (WHERE l.market_type = 'RAW') AS raw,
            MAX(l.observed_on) FILTER (WHERE l.market_type = 'RAW') AS raw_date,
            MAX(l.price) FILTER (WHERE l.market_type = 'PSA_9') AS psa_9,
            MAX(l.observed_on) FILTER (WHERE l.market_type = 'PSA_9') AS psa_9_date,
            MAX(l.price) FILTER (WHERE l.market_type = 'PSA_10') AS psa_10,
            MAX(l.observed_on) FILTER (WHERE l.market_type = 'PSA_10') AS psa_10_date,
            MAX(l.price) FILTER (WHERE l.market_type = 'ACE_10') AS ace_10,
            MAX(l.observed_on) FILTER (WHERE l.market_type = 'ACE_10') AS ace_10_date,
            MAX(q.last_sale_date) FILTER (WHERE q.market_type = 'PSA_9') AS psa_9_last_sale,
            MAX(q.last_sale_date) FILTER (WHERE q.market_type = 'PSA_10') AS psa_10_last_sale,
            MAX(q.last_sale_date) FILTER (WHERE q.market_type = 'ACE_10') AS ace_10_last_sale,
            MAX(q.sales_30) FILTER (WHERE q.market_type = 'PSA_9') AS psa_9_sales_30,
            MAX(q.sales_90) FILTER (WHERE q.market_type = 'PSA_9') AS psa_9_sales_90,
            MAX(q.sales_180) FILTER (WHERE q.market_type = 'PSA_9') AS psa_9_sales_180,
            MAX(q.sales_30) FILTER (WHERE q.market_type = 'PSA_10') AS psa_10_sales_30,
            MAX(q.sales_90) FILTER (WHERE q.market_type = 'PSA_10') AS psa_10_sales_90,
            MAX(q.sales_180) FILTER (WHERE q.market_type = 'PSA_10') AS psa_10_sales_180,
            MAX(q.sales_30) FILTER (WHERE q.market_type = 'ACE_10') AS ace_10_sales_30,
            MAX(q.sales_90) FILTER (WHERE q.market_type = 'ACE_10') AS ace_10_sales_90,
            MAX(q.sales_180) FILTER (WHERE q.market_type = 'ACE_10') AS ace_10_sales_180,
            MAX(q.history_points) FILTER (WHERE q.market_type = 'ACE_10') AS ace_10_history_points,
            rs.ace_psa_historical_median::DOUBLE PRECISION,
            rs.ace_raw_historical_median::DOUBLE PRECISION,
            rs.psa_raw_historical_median::DOUBLE PRECISION,
            rs.ace_ratio_months,
            a.as_of
        FROM tcgplayer_products AS p
        LEFT JOIN tcgplayer_primary_printings AS pp
            ON pp.product_id = p.product_id
        CROSS JOIN anchor AS a
        LEFT JOIN latest AS l
            ON l.product_id = p.product_id
        LEFT JOIN liquidity AS q
            ON q.product_id = p.product_id
        LEFT JOIN ratio_stats AS rs
            ON rs.product_id = p.product_id
        GROUP BY
            p.product_id, p.name, p.set_name, p.card_number, p.rarity,
            p.artist, p.image_url, p.tcgplayer_url, p.set_published_on,
            pp.printing, a.as_of,
            rs.ace_psa_historical_median, rs.ace_raw_historical_median,
            rs.psa_raw_historical_median, rs.ace_ratio_months
        ORDER BY p.set_name, p.name, p.card_number, p.product_id;
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (source, source, source, source))
            return cursor.fetchall()


def get_all_provider_price_history(
    *,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
    markets: tuple[str, ...] = ('RAW', 'PSA_9', 'PSA_10', 'ACE_10'),
) -> list[tuple]:
    """Return provider history joined to canonical TCGPlayer identity."""
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.product_id,
                    p.name,
                    p.set_name,
                    p.card_number,
                    h.market_type,
                    h.price::DOUBLE PRECISION,
                    h.observed_on,
                    h.sale_count,
                    h.volume::DOUBLE PRECISION,
                    h.series_label
                FROM provider_price_history AS h
                JOIN tcgplayer_products AS p
                    ON p.product_id = h.product_id
                WHERE h.source = %s
                  AND h.market_type = ANY(%s)
                  AND h.price > 0
                ORDER BY h.observed_on, p.product_id, h.market_type;
                """,
                (source, list(markets)),
            )
            return cursor.fetchall()


def get_provider_sale_rows(
    product_id: int,
    *,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
) -> list[tuple]:
    """Return daily graded sale aggregates for one card.

    PokemonPriceTracker exposes daily grade-level average price and sale count,
    not individual listing titles on this plan.  The dashboard therefore shows
    each dated aggregate as one row with its observed sale count.
    """
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    market_type,
                    price::DOUBLE PRECISION,
                    observed_on,
                    COALESCE(sale_count, 0)::INTEGER,
                    source
                FROM provider_price_history
                WHERE product_id = %s
                  AND source = %s
                  AND market_type IN ('PSA_9', 'PSA_10', 'ACE_10')
                  AND price > 0
                  AND COALESCE(sale_count, 0) > 0
                ORDER BY observed_on DESC, market_type;
                """,
                (int(product_id), source),
            )
            return cursor.fetchall()


def get_provider_grade_benchmark_context(
    product_id: int,
    market_type: str,
    *,
    days: int = 90,
    source: str = SOURCE_POKEMON_PRICE_TRACKER,
) -> dict | None:
    """Return grade-matched set/market momentum context for one card.

    The comparison is apples-to-apples: an ACE 10 is compared with other
    ACE 10s, PSA 10 with PSA 10s, and PSA 9 with PSA 9s.  The benchmark is
    anchored to the target card's latest provider observation so sparse slab
    markets are not compared across different end dates.

    A peer must have a reasonably fresh end observation and a historical
    observation on/before the requested lookback target.  The start-point
    tolerance intentionally matches the direct graded-intelligence logic.
    """
    market_type = str(market_type).upper()
    if market_type not in {"PSA_9", "PSA_10", "ACE_10"}:
        raise ValueError(f"Unsupported graded market: {market_type}")

    days = max(1, int(days))
    end_lag_days = 45 if market_type == "ACE_10" else 30
    start_lag_days = 62

    query = """
        WITH anchor AS (
            SELECT
                p.set_name,
                MAX(h.observed_on)::date AS anchor_date
            FROM provider_price_history AS h
            JOIN tcgplayer_products AS p
                ON p.product_id = h.product_id
            WHERE h.product_id = %s
              AND h.source = %s
              AND h.market_type = %s
              AND h.price > 0
            GROUP BY p.set_name
        ),
        end_points AS (
            SELECT DISTINCT ON (h.product_id)
                h.product_id,
                h.price::DOUBLE PRECISION AS end_price,
                h.observed_on::date AS end_date
            FROM provider_price_history AS h
            CROSS JOIN anchor AS a
            WHERE h.source = %s
              AND h.market_type = %s
              AND h.price > 0
              AND h.observed_on <= a.anchor_date
              AND h.observed_on >= a.anchor_date - (%s * INTERVAL '1 day')
            ORDER BY h.product_id, h.observed_on DESC
        ),
        start_points AS (
            SELECT DISTINCT ON (h.product_id)
                h.product_id,
                h.price::DOUBLE PRECISION AS start_price,
                h.observed_on::date AS start_date
            FROM provider_price_history AS h
            CROSS JOIN anchor AS a
            WHERE h.source = %s
              AND h.market_type = %s
              AND h.price > 0
              AND h.observed_on <= a.anchor_date - (%s * INTERVAL '1 day')
              AND h.observed_on >= (
                    a.anchor_date
                    - ((%s + %s) * INTERVAL '1 day')
              )
            ORDER BY h.product_id, h.observed_on DESC
        ),
        returns AS (
            SELECT
                e.product_id,
                p.set_name,
                e.end_date,
                s.start_date,
                (e.end_price / NULLIF(s.start_price, 0.0)) - 1.0 AS period_return
            FROM end_points AS e
            JOIN start_points AS s
                ON s.product_id = e.product_id
            JOIN tcgplayer_products AS p
                ON p.product_id = e.product_id
            WHERE s.start_price > 0
        ),
        ranked AS (
            SELECT
                r.*,
                PERCENT_RANK() OVER (
                    ORDER BY r.period_return
                ) AS market_percentile,
                PERCENT_RANK() OVER (
                    PARTITION BY r.set_name
                    ORDER BY r.period_return
                ) AS set_percentile,
                COUNT(*) OVER ()::INTEGER AS market_count,
                COUNT(*) OVER (
                    PARTITION BY r.set_name
                )::INTEGER AS set_count
            FROM returns AS r
        ),
        market_summary AS (
            SELECT
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY period_return
                ) AS market_median
            FROM returns
        ),
        set_summary AS (
            SELECT
                r.set_name,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY r.period_return
                ) AS set_median
            FROM returns AS r
            GROUP BY r.set_name
        )
        SELECT
            a.anchor_date,
            a.set_name,
            t.period_return::DOUBLE PRECISION,
            CASE WHEN t.set_count >= 3
                THEN ss.set_median::DOUBLE PRECISION
                ELSE NULL
            END AS set_median,
            ms.market_median::DOUBLE PRECISION,
            CASE WHEN t.set_count >= 3
                THEN t.set_percentile::DOUBLE PRECISION
                ELSE NULL
            END AS set_percentile,
            t.market_percentile::DOUBLE PRECISION,
            t.set_count,
            t.market_count,
            t.start_date,
            t.end_date
        FROM anchor AS a
        JOIN ranked AS t
            ON t.product_id = %s
        CROSS JOIN market_summary AS ms
        LEFT JOIN set_summary AS ss
            ON ss.set_name = a.set_name;
    """

    params = (
        int(product_id), source, market_type,
        source, market_type, end_lag_days,
        source, market_type, days, days, start_lag_days,
        int(product_id),
    )

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()

    if not row:
        return None

    (
        anchor_date,
        set_name,
        card_return,
        set_median,
        market_median,
        set_percentile,
        market_percentile,
        set_count,
        market_count,
        start_date,
        end_date,
    ) = row

    def relative(value, benchmark):
        if value is None or benchmark is None:
            return None
        return float(value) - float(benchmark)

    return {
        "product_id": int(product_id),
        "source": source,
        "market_type": market_type,
        "days": days,
        "anchor_date": anchor_date,
        "set_name": set_name,
        "card_return": None if card_return is None else float(card_return),
        "set_return": None if set_median is None else float(set_median),
        "market_return": None if market_median is None else float(market_median),
        "relative_set": relative(card_return, set_median),
        "relative_market": relative(card_return, market_median),
        "set_percentile": None if set_percentile is None else float(set_percentile),
        "market_percentile": (
            None if market_percentile is None else float(market_percentile)
        ),
        "set_count": int(set_count or 0),
        "market_count": int(market_count or 0),
        "start_date": start_date,
        "end_date": end_date,
        "end_lag_days": end_lag_days,
        "start_lag_days": start_lag_days,
    }
