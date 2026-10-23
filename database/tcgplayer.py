from __future__ import annotations


from database.operations import DATABASE_NAME
from services.analysis.opportunity_snapshot import invalidate_snapshot


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS tcgplayer_products (
        product_id BIGINT PRIMARY KEY,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        set_name TEXT NOT NULL,
        card_number VARCHAR(50),
        rarity VARCHAR(100),
        artist VARCHAR(150),
        image_url TEXT,
        tcgplayer_url TEXT,
        tickermint_url TEXT,
        set_published_on DATE,
        synced_at TIMESTAMP NOT NULL
            DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    ALTER TABLE tcgplayer_products
    ADD COLUMN IF NOT EXISTS
        tickermint_url TEXT;
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_products_group
    ON tcgplayer_products (group_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_products_set
    ON tcgplayer_products (set_name);
    """,
    """
    CREATE TABLE IF NOT EXISTS tcgplayer_daily_prices (
        product_id BIGINT NOT NULL
            REFERENCES tcgplayer_products(product_id)
            ON DELETE CASCADE,
        snapshot_date DATE NOT NULL,
        printing VARCHAR(100) NOT NULL,
        low_price NUMERIC(12, 2),
        mid_price NUMERIC(12, 2),
        high_price NUMERIC(12, 2),
        market_price NUMERIC(12, 2),
        direct_low_price NUMERIC(12, 2),
        currency VARCHAR(10) NOT NULL
            DEFAULT 'USD',
        source VARCHAR(50) NOT NULL
            DEFAULT 'tcgplayer',
        retrieved_via VARCHAR(50) NOT NULL,
        PRIMARY KEY (
            product_id,
            snapshot_date,
            printing
        )
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_prices_date
    ON tcgplayer_daily_prices (
        snapshot_date
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_prices_product_date
    ON tcgplayer_daily_prices (
        product_id,
        snapshot_date DESC
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS
        tcgplayer_history_status (
            product_id BIGINT PRIMARY KEY
                REFERENCES tcgplayer_products(product_id)
                ON DELETE CASCADE,
            status VARCHAR(20) NOT NULL,
            observation_count INTEGER NOT NULL
                DEFAULT 0,
            first_date DATE,
            last_date DATE,
            attempts INTEGER NOT NULL
                DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_history_status
    ON tcgplayer_history_status (
        status
    );
    """,
]


def connect():
    import psycopg
    return psycopg.connect(
        dbname=DATABASE_NAME
    )


def initialise_tcgplayer_schema() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


def upsert_products(
    connection,
    products: list[dict],
) -> int:
    if not products:
        return 0

    rows = [
        (
            row["product_id"],
            row["group_id"],
            row["name"],
            row["set_name"],
            row["card_number"],
            row["rarity"],
            row["artist"],
            row["image_url"],
            row["tcgplayer_url"],
            row["tickermint_url"],
            row["set_published_on"],
        )
        for row in products
    ]

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO tcgplayer_products (
                product_id,
                group_id,
                name,
                set_name,
                card_number,
                rarity,
                artist,
                image_url,
                tcgplayer_url,
                tickermint_url,
                set_published_on
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s
            )
            ON CONFLICT (product_id)
            DO UPDATE SET
                group_id =
                    EXCLUDED.group_id,
                name =
                    EXCLUDED.name,
                set_name =
                    EXCLUDED.set_name,
                card_number =
                    EXCLUDED.card_number,
                rarity =
                    EXCLUDED.rarity,
                artist =
                    EXCLUDED.artist,
                image_url =
                    EXCLUDED.image_url,
                tcgplayer_url =
                    EXCLUDED.tcgplayer_url,
                tickermint_url =
                    EXCLUDED.tickermint_url,
                set_published_on =
                    EXCLUDED.set_published_on,
                synced_at =
                    CURRENT_TIMESTAMP;
            """,
            rows,
        )

    return len(rows)


def get_pending_products(
    connection,
    *,
    refresh: bool = False,
    limit: int | None = None,
) -> list[dict]:
    where_clause = ""

    if not refresh:
        where_clause = """
        WHERE COALESCE(
            status.status,
            ''
        ) NOT IN (
            'completed',
            'no_history'
        )
        """

    sql = f"""
        SELECT
            products.product_id,
            products.name,
            products.set_name,
            products.card_number
        FROM tcgplayer_products AS products
        LEFT JOIN tcgplayer_history_status AS status
            ON status.product_id =
                products.product_id
        {where_clause}
        ORDER BY
            products.set_published_on,
            products.group_id,
            products.product_id
    """

    params = []

    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            params,
        )

        rows = cursor.fetchall()

    return [
        {
            "product_id": row[0],
            "name": row[1],
            "set_name": row[2],
            "card_number": row[3],
        }
        for row in rows
    ]


def store_price_history(
    connection,
    product_id: int,
    rows: list[tuple],
) -> int:
    if not rows:
        mark_no_history(
            connection,
            product_id,
        )
        return 0

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO tcgplayer_daily_prices (
                product_id,
                snapshot_date,
                printing,
                low_price,
                mid_price,
                high_price,
                market_price,
                direct_low_price,
                currency,
                source,
                retrieved_via
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s
            )
            ON CONFLICT (
                product_id,
                snapshot_date,
                printing
            )
            DO UPDATE SET
                low_price =
                    EXCLUDED.low_price,
                market_price =
                    EXCLUDED.market_price,
                source =
                    EXCLUDED.source,
                retrieved_via =
                    EXCLUDED.retrieved_via;
            """,
            rows,
        )

        dates = [
            row[1]
            for row in rows
        ]

        cursor.execute(
            """
            INSERT INTO tcgplayer_history_status (
                product_id,
                status,
                observation_count,
                first_date,
                last_date,
                attempts,
                last_error,
                updated_at
            )
            VALUES (
                %s,
                'completed',
                %s,
                %s,
                %s,
                1,
                NULL,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (product_id)
            DO UPDATE SET
                status =
                    'completed',
                observation_count =
                    EXCLUDED.observation_count,
                first_date =
                    EXCLUDED.first_date,
                last_date =
                    EXCLUDED.last_date,
                attempts =
                    tcgplayer_history_status.attempts
                    + 1,
                last_error =
                    NULL,
                updated_at =
                    CURRENT_TIMESTAMP;
            """,
            (
                product_id,
                len(rows),
                min(dates),
                max(dates),
            ),
        )

    invalidate_snapshot("RAW history backfilled or corrected")
    return len(rows)


def mark_no_history(
    connection,
    product_id: int,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tcgplayer_history_status (
                product_id,
                status,
                observation_count,
                attempts,
                last_error,
                updated_at
            )
            VALUES (
                %s,
                'no_history',
                0,
                1,
                NULL,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (product_id)
            DO UPDATE SET
                status =
                    'no_history',
                attempts =
                    tcgplayer_history_status.attempts
                    + 1,
                last_error =
                    NULL,
                updated_at =
                    CURRENT_TIMESTAMP;
            """,
            (product_id,),
        )


def mark_failed(
    connection,
    product_id: int,
    error: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tcgplayer_history_status (
                product_id,
                status,
                observation_count,
                attempts,
                last_error,
                updated_at
            )
            VALUES (
                %s,
                'failed',
                0,
                1,
                %s,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (product_id)
            DO UPDATE SET
                status =
                    'failed',
                attempts =
                    tcgplayer_history_status.attempts
                    + 1,
                last_error =
                    EXCLUDED.last_error,
                updated_at =
                    CURRENT_TIMESTAMP;
            """,
            (
                product_id,
                error[:2000],
            ),
        )


def get_database_summary(
    connection,
) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM tcgplayer_products;
            """
        )
        product_count = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM tcgplayer_daily_prices;
            """
        )
        observation_count = (
            cursor.fetchone()[0]
        )

        cursor.execute(
            """
            SELECT COUNT(
                DISTINCT product_id
            )
            FROM tcgplayer_daily_prices;
            """
        )
        priced_products = (
            cursor.fetchone()[0]
        )

        cursor.execute(
            """
            SELECT
                MIN(snapshot_date),
                MAX(snapshot_date)
            FROM tcgplayer_daily_prices;
            """
        )
        first_date, last_date = (
            cursor.fetchone()
        )

        cursor.execute(
            """
            SELECT
                status,
                COUNT(*)
            FROM tcgplayer_history_status
            GROUP BY status
            ORDER BY status;
            """
        )

        statuses = dict(
            cursor.fetchall()
        )

    return {
        "products": product_count,
        "observations": observation_count,
        "priced_products": (
            priced_products
        ),
        "first_date": first_date,
        "last_date": last_date,
        "statuses": statuses,
    }



def get_backfill_progress() -> dict:
    """Return a lightweight summary of the TCGPlayer historical backfill."""

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM tcgplayer_products;
                """
            )
            total_products = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status = 'completed'
                    ),
                    COUNT(*) FILTER (
                        WHERE status = 'no_history'
                    ),
                    COUNT(*) FILTER (
                        WHERE status = 'failed'
                    ),
                    COALESCE(
                        SUM(observation_count)
                            FILTER (
                                WHERE status = 'completed'
                            ),
                        0
                    ),
                    MIN(first_date)
                        FILTER (
                            WHERE status = 'completed'
                        ),
                    MAX(last_date)
                        FILTER (
                            WHERE status = 'completed'
                        )
                FROM tcgplayer_history_status;
                """
            )

            (
                completed,
                no_history,
                failed,
                observations,
                first_date,
                last_date,
            ) = cursor.fetchone()

    processed = (
        completed
        + no_history
    )

    percent = (
        processed / total_products * 100
        if total_products
        else 0.0
    )

    return {
        "total_products": total_products,
        "completed": completed,
        "no_history": no_history,
        "failed": failed,
        "processed": processed,
        "observations": observations,
        "first_date": first_date,
        "last_date": last_date,
        "percent": percent,
    }


def get_daily_update_groups(
    connection,
) -> list[dict]:
    """Return the TCGPlayer groups represented in the canonical catalog."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                group_id,
                MIN(set_name) AS set_name,
                COUNT(*) AS product_count
            FROM tcgplayer_products
            GROUP BY group_id
            ORDER BY
                MIN(set_published_on),
                group_id;
            """
        )

        rows = cursor.fetchall()

    return [
        {
            "group_id": int(row[0]),
            "set_name": row[1],
            "product_count": int(row[2]),
        }
        for row in rows
    ]


def get_product_group_map(
    connection,
) -> dict[int, int]:
    """Return product_id -> group_id for canonical card products."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                product_id,
                group_id
            FROM tcgplayer_products;
            """
        )

        rows = cursor.fetchall()

    return {
        int(product_id): int(group_id)
        for product_id, group_id in rows
    }


def get_latest_daily_price_dates(
    connection,
) -> dict[tuple[int, str], object]:
    """Return the newest stored date for every product/printing series."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                product_id,
                printing,
                MAX(snapshot_date)
            FROM tcgplayer_daily_prices
            GROUP BY
                product_id,
                printing;
            """
        )

        rows = cursor.fetchall()

    return {
        (
            int(product_id),
            str(printing),
        ): latest_date
        for (
            product_id,
            printing,
            latest_date,
        ) in rows
    }


def store_daily_price_rows(
    connection,
    rows: list[tuple],
) -> int:
    """
    Append daily TCGPlayer price observations without rewriting history.

    Callers should normally pass only dates newer than the latest stored
    product/printing observation. The primary-key conflict guard makes a
    repeated run idempotent as a second line of defence.
    """
    if not rows:
        return 0

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO tcgplayer_daily_prices (
                product_id,
                snapshot_date,
                printing,
                low_price,
                mid_price,
                high_price,
                market_price,
                direct_low_price,
                currency,
                source,
                retrieved_via
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s
            )
            ON CONFLICT (
                product_id,
                snapshot_date,
                printing
            )
            DO NOTHING;
            """,
            rows,
        )

        inserted = cursor.rowcount

    if inserted is None or inserted < 0:
        invalidate_snapshot("RAW daily observations appended")
        return len(rows)
    if inserted > 0:
        invalidate_snapshot("RAW daily observations appended")
    return int(inserted)


def get_daily_price_date_range(
    connection,
) -> tuple:
    """Return the first and latest dates in the raw TCGPlayer series."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                MIN(snapshot_date),
                MAX(snapshot_date)
            FROM tcgplayer_daily_prices;
            """
        )

        return cursor.fetchone()


def get_latest_product_price(
    connection,
    product_id: int,
):
    """Return the latest market-price row for a TCGPlayer product."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                snapshot_date,
                printing,
                market_price
            FROM tcgplayer_daily_prices
            WHERE
                product_id = %s
                AND market_price IS NOT NULL
            ORDER BY
                snapshot_date DESC,
                printing
            LIMIT 1;
            """,
            (product_id,),
        )

        return cursor.fetchone()
