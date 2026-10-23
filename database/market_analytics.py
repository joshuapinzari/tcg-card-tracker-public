from __future__ import annotations

from services.analysis.opportunity_snapshot import snapshot_input_writer, invalidate_snapshot


from database.operations import DATABASE_NAME
from pokemon_eras import era_case_sql


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS tcgplayer_primary_printings (
        product_id BIGINT PRIMARY KEY
            REFERENCES tcgplayer_products(product_id)
            ON DELETE CASCADE,
        printing VARCHAR(100) NOT NULL,
        market_observations INTEGER NOT NULL,
        selected_at TIMESTAMP NOT NULL
            DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tcgplayer_market_index_daily (
        scope_type VARCHAR(20) NOT NULL,
        scope_name TEXT NOT NULL,
        snapshot_date DATE NOT NULL,
        index_return DOUBLE PRECISION NOT NULL,
        median_return DOUBLE PRECISION NOT NULL,
        mean_return DOUBLE PRECISION NOT NULL,
        constituent_count INTEGER NOT NULL,
        breadth_positive DOUBLE PRECISION NOT NULL,
        index_value DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (
            scope_type,
            scope_name,
            snapshot_date
        )
    );
    """,
    """
    ALTER TABLE tcgplayer_market_index_daily
    ADD COLUMN IF NOT EXISTS
        index_return DOUBLE PRECISION;
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_market_index_scope_date
    ON tcgplayer_market_index_daily (
        scope_type,
        scope_name,
        snapshot_date DESC
    );
    """,
]


def connect():
    import psycopg
    return psycopg.connect(
        dbname=DATABASE_NAME
    )


def initialise_market_analytics_schema() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


def rebuild_primary_printings(
    connection,
) -> int:
    """
    Select one representative printing for each product.

    Preference:
        Normal
        Holofoil
        Reverse Holofoil
        anything else

    A preferred printing must still have at least 80% of the
    market-price coverage of the best-covered printing.
    """
    invalidate_snapshot("Primary printings or market indices are rebuilding")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE TABLE
                tcgplayer_primary_printings;
            """
        )

        cursor.execute(
            """
            WITH printing_counts AS (
                SELECT
                    product_id,
                    printing,
                    COUNT(*) AS observations
                FROM tcgplayer_daily_prices
                WHERE market_price IS NOT NULL
                GROUP BY
                    product_id,
                    printing
            ),
            coverage AS (
                SELECT
                    product_id,
                    printing,
                    observations,
                    MAX(observations) OVER (
                        PARTITION BY product_id
                    ) AS max_observations
                FROM printing_counts
            ),
            selected AS (
                SELECT DISTINCT ON (product_id)
                    product_id,
                    printing,
                    observations
                FROM coverage
                WHERE
                    observations >=
                        max_observations * 0.80
                ORDER BY
                    product_id,
                    CASE printing
                        WHEN 'Normal' THEN 1
                        WHEN 'Holofoil' THEN 2
                        WHEN 'Reverse Holofoil' THEN 3
                        ELSE 4
                    END,
                    observations DESC,
                    printing
            )
            INSERT INTO tcgplayer_primary_printings (
                product_id,
                printing,
                market_observations
            )
            SELECT
                product_id,
                printing,
                observations
            FROM selected;
            """
        )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM tcgplayer_primary_printings;
            """
        )

        return cursor.fetchone()[0]


def rebuild_market_indices(
    connection,
    *,
    price_floor: float = 5.0,
    min_constituents: int = 5,
) -> int:
    """
    Build chained point-in-time market indices.

    Daily index return:

    1. Select one primary printing per product.
    2. Require consecutive daily TCGPlayer market prices.
    3. Require previous-day price >= price_floor.
    4. Exclude presale dates.
    5. Calculate every eligible card's daily return.
    6. Winsorize the cross-section at 2.5% / 97.5%.
    7. Equal-weight the winsorized returns.
    8. Chain daily returns into an index.

    Raw source observations are never modified.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE TABLE
                tcgplayer_market_index_daily;
            """
        )

        cursor.execute(
            f"""
            WITH canonical AS MATERIALIZED (
                SELECT
                    d.product_id,
                    d.snapshot_date,
                    d.market_price::DOUBLE PRECISION
                        AS market_price,
                    p.set_name,
                    p.set_published_on,
                    {era_case_sql("p.set_name")} AS era

                FROM tcgplayer_daily_prices AS d

                JOIN tcgplayer_primary_printings AS pp
                    ON pp.product_id = d.product_id
                    AND pp.printing = d.printing

                JOIN tcgplayer_products AS p
                    ON p.product_id = d.product_id

                WHERE
                    d.market_price IS NOT NULL
                    AND d.market_price > 0
                    AND p.set_name NOT IN (
                            'MEE: Mega Evolution Energies',
                            'SVE: Scarlet & Violet Energies'
                        )
            ),

            lagged AS MATERIALIZED (
                SELECT
                    product_id,
                    snapshot_date,
                    market_price,
                    set_name,
                    set_published_on,
                    era,

                    LAG(market_price) OVER (
                        PARTITION BY product_id
                        ORDER BY snapshot_date
                    ) AS previous_price,

                    LAG(snapshot_date) OVER (
                        PARTITION BY product_id
                        ORDER BY snapshot_date
                    ) AS previous_date

                FROM canonical
            ),

            eligible AS MATERIALIZED (
                SELECT
                    product_id,
                    snapshot_date,
                    set_name,
                    era,

                    (
                        market_price
                        / previous_price
                        - 1.0
                    ) AS daily_return

                FROM lagged

                WHERE
                    previous_price >= %s
                    AND previous_price > 0

                    AND previous_date IS NOT NULL

                    AND snapshot_date
                        = previous_date + 1

                    AND previous_date
                        >= set_published_on

                    AND era IS NOT NULL
            ),

            scoped AS MATERIALIZED (
                SELECT
                    'market'::TEXT
                        AS scope_type,

                    'Modern English Pokemon'::TEXT
                        AS scope_name,

                    snapshot_date,
                    daily_return

                FROM eligible

                UNION ALL

                SELECT
                    'era'::TEXT,
                    era,
                    snapshot_date,
                    daily_return

                FROM eligible

                UNION ALL

                SELECT
                    'set'::TEXT,
                    set_name,
                    snapshot_date,
                    daily_return

                FROM eligible
            ),

            stats AS MATERIALIZED (
                SELECT
                    scope_type,
                    scope_name,
                    snapshot_date,

                    PERCENTILE_CONT(0.025)
                        WITHIN GROUP (
                            ORDER BY daily_return
                        )::DOUBLE PRECISION
                        AS lower_bound,

                    PERCENTILE_CONT(0.975)
                        WITHIN GROUP (
                            ORDER BY daily_return
                        )::DOUBLE PRECISION
                        AS upper_bound,

                    PERCENTILE_CONT(0.5)
                        WITHIN GROUP (
                            ORDER BY daily_return
                        )::DOUBLE PRECISION
                        AS median_return,

                    AVG(
                        daily_return
                    )::DOUBLE PRECISION
                        AS mean_return,

                    COUNT(*)::INTEGER
                        AS constituent_count,

                    AVG(
                        CASE
                            WHEN daily_return > 0
                                THEN 1.0
                            ELSE 0.0
                        END
                    )::DOUBLE PRECISION
                        AS breadth_positive

                FROM scoped

                GROUP BY
                    scope_type,
                    scope_name,
                    snapshot_date

                HAVING COUNT(*) >= %s
            ),

            daily AS MATERIALIZED (
                SELECT
                    s.scope_type,
                    s.scope_name,
                    s.snapshot_date,

                    AVG(
                        GREATEST(
                            st.lower_bound,
                            LEAST(
                                st.upper_bound,
                                s.daily_return
                            )
                        )
                    )::DOUBLE PRECISION
                        AS index_return,

                    st.median_return,
                    st.mean_return,
                    st.constituent_count,
                    st.breadth_positive

                FROM scoped AS s

                JOIN stats AS st
                    ON st.scope_type = s.scope_type
                    AND st.scope_name = s.scope_name
                    AND st.snapshot_date = s.snapshot_date

                GROUP BY
                    s.scope_type,
                    s.scope_name,
                    s.snapshot_date,
                    st.median_return,
                    st.mean_return,
                    st.constituent_count,
                    st.breadth_positive
            ),

            cumulative AS (
                SELECT
                    scope_type,
                    scope_name,
                    snapshot_date,
                    index_return,
                    median_return,
                    mean_return,
                    constituent_count,
                    breadth_positive,

                    SUM(
                        LN(
                            1.0 + index_return
                        )
                    ) OVER (
                        PARTITION BY
                            scope_type,
                            scope_name

                        ORDER BY snapshot_date
                    ) AS log_index

                FROM daily
            ),

            normalized AS (
                SELECT
                    scope_type,
                    scope_name,
                    snapshot_date,
                    index_return,
                    median_return,
                    mean_return,
                    constituent_count,
                    breadth_positive,

                    100.0
                    * EXP(
                        log_index
                        - FIRST_VALUE(
                            log_index
                        ) OVER (
                            PARTITION BY
                                scope_type,
                                scope_name

                            ORDER BY snapshot_date
                        )
                    ) AS index_value

                FROM cumulative
            )

            INSERT INTO tcgplayer_market_index_daily (
                scope_type,
                scope_name,
                snapshot_date,
                index_return,
                median_return,
                mean_return,
                constituent_count,
                breadth_positive,
                index_value
            )

            SELECT
                scope_type,
                scope_name,
                snapshot_date,
                index_return,
                median_return,
                mean_return,
                constituent_count,
                breadth_positive,
                index_value

            FROM normalized;
            """,
            (
                price_floor,
                min_constituents,
            ),
        )

        cursor.execute(
            """
            ANALYZE
                tcgplayer_primary_printings;
            """
        )

        cursor.execute(
            """
            ANALYZE
                tcgplayer_market_index_daily;
            """
        )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM tcgplayer_market_index_daily;
            """
        )

        return cursor.fetchone()[0]


@snapshot_input_writer
def rebuild_all_market_analytics(
    *,
    price_floor: float = 5.0,
    min_constituents: int = 5,
) -> dict:
    initialise_market_analytics_schema()

    with connect() as connection:
        primary_products = (
            rebuild_primary_printings(
                connection
            )
        )

        index_rows = rebuild_market_indices(
            connection,
            price_floor=price_floor,
            min_constituents=min_constituents,
        )

        connection.commit()

    return {
        "primary_products": primary_products,
        "index_rows": index_rows,
        "price_floor": price_floor,
        "min_constituents": min_constituents,
    }


def get_market_index(
    scope_type: str,
    scope_name: str,
) -> list[tuple]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    snapshot_date,
                    index_value,
                    index_return,
                    constituent_count,
                    breadth_positive

                FROM tcgplayer_market_index_daily

                WHERE
                    scope_type = %s
                    AND scope_name = %s

                ORDER BY snapshot_date;
                """,
                (
                    scope_type,
                    scope_name,
                ),
            )

            return cursor.fetchall()


def get_latest_market_summary() -> list[tuple]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (
                    scope_type,
                    scope_name
                )
                    scope_type,
                    scope_name,
                    snapshot_date,
                    index_value,
                    constituent_count,
                    breadth_positive

                FROM tcgplayer_market_index_daily

                WHERE scope_type IN (
                    'market',
                    'era'
                )

                ORDER BY
                    scope_type,
                    scope_name,
                    snapshot_date DESC;
                """
            )

            return cursor.fetchall()


def get_market_scopes() -> dict[str, list[str]]:
    scopes = {
        "market": [],
        "era": [],
        "set": [],
    }

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    scope_type,
                    scope_name

                FROM tcgplayer_market_index_daily

                GROUP BY
                    scope_type,
                    scope_name

                ORDER BY
                    scope_type,
                    scope_name;
                """
            )

            for (
                scope_type,
                scope_name,
            ) in cursor.fetchall():

                scopes.setdefault(
                    scope_type,
                    [],
                ).append(
                    scope_name
                )

    return scopes



def get_set_investor_snapshot(
    set_name: str,
    days: int = 90,
) -> dict:
    """
    Return investor-oriented metrics for one set.

    Current/start prices use the most recent valid market price
    within a seven-day window. This avoids a one-day source gap
    making a card disappear from the set basket.
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH bounds AS (
                    SELECT
                        MAX(snapshot_date)::DATE AS latest_date
                    FROM tcgplayer_daily_prices
                ),

                scope AS (
                    SELECT
                        p.product_id,
                        p.name,
                        p.card_number,
                        p.rarity,
                        pp.printing
                    FROM tcgplayer_products AS p
                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id = p.product_id
                    WHERE p.set_name = %s
                ),

                current_prices AS (
                    SELECT DISTINCT ON (d.product_id)
                        d.product_id,
                        d.market_price::DOUBLE PRECISION
                            AS current_price
                    FROM tcgplayer_daily_prices AS d
                    JOIN scope AS s
                        ON s.product_id = d.product_id
                        AND s.printing = d.printing
                    CROSS JOIN bounds AS b
                    WHERE
                        d.market_price IS NOT NULL
                        AND d.snapshot_date <= b.latest_date
                        AND d.snapshot_date >=
                            b.latest_date - 7
                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                ),

                start_prices AS (
                    SELECT DISTINCT ON (d.product_id)
                        d.product_id,
                        d.market_price::DOUBLE PRECISION
                            AS start_price
                    FROM tcgplayer_daily_prices AS d
                    JOIN scope AS s
                        ON s.product_id = d.product_id
                        AND s.printing = d.printing
                    CROSS JOIN bounds AS b
                    WHERE
                        d.market_price IS NOT NULL
                        AND d.snapshot_date <=
                            b.latest_date
                            - (%s * INTERVAL '1 day')
                        AND d.snapshot_date >=
                            b.latest_date
                            - (%s * INTERVAL '1 day')
                            - INTERVAL '7 days'
                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                ),

                paired AS (
                    SELECT
                        c.product_id,
                        c.current_price,
                        s.start_price
                    FROM current_prices AS c
                    JOIN start_prices AS s
                        ON s.product_id = c.product_id
                    WHERE s.start_price > 0
                ),

                current_ranked AS (
                    SELECT
                        current_price,
                        ROW_NUMBER() OVER (
                            ORDER BY current_price DESC
                        ) AS rank
                    FROM current_prices
                ),

                latest_returns AS (
                    SELECT
                        today.product_id,
                        (
                            today.market_price::DOUBLE PRECISION
                            / yesterday.market_price::DOUBLE PRECISION
                            - 1.0
                        ) AS daily_return
                    FROM tcgplayer_daily_prices AS today
                    JOIN scope AS s
                        ON s.product_id = today.product_id
                        AND s.printing = today.printing
                    JOIN tcgplayer_daily_prices AS yesterday
                        ON yesterday.product_id = today.product_id
                        AND yesterday.printing = today.printing
                        AND yesterday.snapshot_date =
                            today.snapshot_date - 1
                    CROSS JOIN bounds AS b
                    WHERE
                        today.snapshot_date = b.latest_date
                        AND today.market_price IS NOT NULL
                        AND yesterday.market_price IS NOT NULL
                        AND yesterday.market_price > 0
                )

                SELECT
                    b.latest_date,

                    (
                        SELECT COUNT(*)
                        FROM scope
                    ) AS total_products,

                    (
                        SELECT COUNT(*)
                        FROM current_prices
                    ) AS priced_products,

                    (
                        SELECT COALESCE(
                            SUM(current_price),
                            0
                        )
                        FROM current_prices
                    ) AS current_total_value,

                    (
                        SELECT PERCENTILE_CONT(0.5)
                            WITHIN GROUP (
                                ORDER BY current_price
                            )
                        FROM current_prices
                    ) AS median_price,

                    (
                        SELECT COALESCE(
                            SUM(current_price),
                            0
                        )
                        FROM current_ranked
                        WHERE rank <= 10
                    ) AS top_10_value,

                    (
                        SELECT COUNT(*)
                        FROM paired
                    ) AS matched_products,

                    (
                        SELECT COALESCE(
                            SUM(start_price),
                            0
                        )
                        FROM paired
                    ) AS paired_start_value,

                    (
                        SELECT COALESCE(
                            SUM(current_price),
                            0
                        )
                        FROM paired
                    ) AS paired_current_value,

                    (
                        SELECT AVG(
                            CASE
                                WHEN daily_return > 0
                                    THEN 1.0
                                ELSE 0.0
                            END
                        )
                        FROM latest_returns
                    ) AS breadth_positive

                FROM bounds AS b;
                """,
                (
                    set_name,
                    days,
                    days,
                ),
            )

            row = cursor.fetchone()

    (
        latest_date,
        total_products,
        priced_products,
        current_total_value,
        median_price,
        top_10_value,
        matched_products,
        paired_start_value,
        paired_current_value,
        breadth_positive,
    ) = row

    current_total_value = float(
        current_total_value or 0
    )

    top_10_value = float(
        top_10_value or 0
    )

    paired_start_value = float(
        paired_start_value or 0
    )

    paired_current_value = float(
        paired_current_value or 0
    )

    if paired_start_value > 0:
        period_return = (
            paired_current_value
            / paired_start_value
            - 1.0
        )
    else:
        period_return = None

    top_10_concentration = (
        top_10_value / current_total_value
        if current_total_value > 0
        else None
    )

    coverage = (
        priced_products / total_products
        if total_products
        else 0.0
    )

    return {
        "latest_date": latest_date,
        "total_products": total_products,
        "priced_products": priced_products,
        "coverage": coverage,
        "current_total_value": current_total_value,
        "median_price": (
            float(median_price)
            if median_price is not None
            else None
        ),
        "top_10_value": top_10_value,
        "top_10_concentration": (
            top_10_concentration
        ),
        "matched_products": matched_products,
        "paired_start_value": (
            paired_start_value
        ),
        "paired_current_value": (
            paired_current_value
        ),
        "period_return": period_return,
        "breadth_positive": (
            float(breadth_positive)
            if breadth_positive is not None
            else None
        ),
    }


def get_set_value_history(
    set_name: str,
    days: int = 365,
) -> list[tuple]:
    """
    Daily value of buying one copy of every priced product
    in a set.

    A card can carry its most recent price forward for at most
    seven days, preventing isolated source gaps from creating
    artificial collapses in total set value.
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH bounds AS (
                    SELECT
                        MAX(d.snapshot_date)::DATE
                            AS latest_date,
                        MIN(p.set_published_on)::DATE
                            AS release_date

                    FROM tcgplayer_daily_prices AS d

                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id = d.product_id
                        AND pp.printing = d.printing

                    JOIN tcgplayer_products AS p
                        ON p.product_id = d.product_id

                    WHERE p.set_name = %s
                ),

                scope AS (
                    SELECT
                        p.product_id,
                        pp.printing

                    FROM tcgplayer_products AS p

                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id = p.product_id

                    WHERE p.set_name = %s
                ),

                dates AS (
                    SELECT
                        generate_series(
                            GREATEST(
                                b.latest_date
                                    - (%s * INTERVAL '1 day'),
                                b.release_date::TIMESTAMP
                            ),
                            b.latest_date::TIMESTAMP,
                            INTERVAL '1 day'
                        )::DATE AS snapshot_date

                    FROM bounds AS b
                )

                SELECT
                    dates.snapshot_date,

                    COALESCE(
                        SUM(price.market_price),
                        0
                    )::DOUBLE PRECISION
                        AS total_value,

                    COUNT(
                        price.market_price
                    )::INTEGER
                        AS priced_products,

                    (
                        COUNT(price.market_price)
                        / NULLIF(
                            (
                                SELECT COUNT(*)
                                FROM scope
                            )::DOUBLE PRECISION,
                            0
                        )
                    )::DOUBLE PRECISION
                        AS coverage

                FROM dates

                CROSS JOIN scope AS s

                LEFT JOIN LATERAL (
                    SELECT
                        d.market_price::DOUBLE PRECISION
                            AS market_price

                    FROM tcgplayer_daily_prices AS d

                    WHERE
                        d.product_id = s.product_id
                        AND d.printing = s.printing
                        AND d.market_price IS NOT NULL
                        AND d.snapshot_date
                            <= dates.snapshot_date
                        AND d.snapshot_date
                            >= dates.snapshot_date - 7

                    ORDER BY
                        d.snapshot_date DESC

                    LIMIT 1
                ) AS price
                    ON TRUE

                GROUP BY dates.snapshot_date

                ORDER BY dates.snapshot_date;
                """,
                (
                    set_name,
                    set_name,
                    days,
                ),
            )

            return cursor.fetchall()


def get_set_card_performance(
    set_name: str,
    days: int = 90,
) -> list[tuple]:
    """
    Current card prices and fixed-window returns for one set.
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH bounds AS (
                    SELECT
                        MAX(snapshot_date)::DATE
                            AS latest_date
                    FROM tcgplayer_daily_prices
                ),

                scope AS (
                    SELECT
                        p.product_id,
                        p.name,
                        p.card_number,
                        p.rarity,
                        pp.printing

                    FROM tcgplayer_products AS p

                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id = p.product_id

                    WHERE p.set_name = %s
                ),

                current_prices AS (
                    SELECT DISTINCT ON (d.product_id)
                        d.product_id,
                        d.market_price::DOUBLE PRECISION
                            AS current_price

                    FROM tcgplayer_daily_prices AS d

                    JOIN scope AS s
                        ON s.product_id = d.product_id
                        AND s.printing = d.printing

                    CROSS JOIN bounds AS b

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.snapshot_date <= b.latest_date
                        AND d.snapshot_date
                            >= b.latest_date - 7

                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                ),

                start_prices AS (
                    SELECT DISTINCT ON (d.product_id)
                        d.product_id,
                        d.market_price::DOUBLE PRECISION
                            AS start_price

                    FROM tcgplayer_daily_prices AS d

                    JOIN scope AS s
                        ON s.product_id = d.product_id
                        AND s.printing = d.printing

                    CROSS JOIN bounds AS b

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.snapshot_date <=
                            b.latest_date
                            - (%s * INTERVAL '1 day')
                        AND d.snapshot_date >=
                            b.latest_date
                            - (%s * INTERVAL '1 day')
                            - INTERVAL '7 days'

                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                )

                SELECT
                    s.product_id,
                    s.name,
                    s.card_number,
                    s.rarity,
                    c.current_price,
                    old.start_price,

                    CASE
                        WHEN old.start_price > 0
                        THEN (
                            c.current_price
                            / old.start_price
                            - 1.0
                        )
                        ELSE NULL
                    END AS period_return

                FROM scope AS s

                JOIN current_prices AS c
                    ON c.product_id = s.product_id

                LEFT JOIN start_prices AS old
                    ON old.product_id = s.product_id

                ORDER BY
                    period_return DESC NULLS LAST,
                    c.current_price DESC;
                """,
                (
                    set_name,
                    days,
                    days,
                ),
            )

            return cursor.fetchall()


def get_set_leaderboard(
    days: int = 90,
) -> list[tuple]:
    """
    Compare every tracked set using a fixed matched-card basket
    over the requested period.
    """

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH bounds AS (
                    SELECT
                        MAX(snapshot_date)::DATE
                            AS latest_date
                    FROM tcgplayer_daily_prices
                ),

                scope AS (
                    SELECT
                        p.product_id,
                        p.set_name,
                        pp.printing

                    FROM tcgplayer_products AS p

                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id = p.product_id

                    WHERE p.set_name NOT IN (
                            'MEE: Mega Evolution Energies',
                            'SVE: Scarlet & Violet Energies'
                        )
                ),

                current_prices AS (
                    SELECT DISTINCT ON (d.product_id)
                        d.product_id,
                        d.market_price::DOUBLE PRECISION
                            AS current_price

                    FROM tcgplayer_daily_prices AS d

                    JOIN scope AS s
                        ON s.product_id = d.product_id
                        AND s.printing = d.printing

                    CROSS JOIN bounds AS b

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.snapshot_date <= b.latest_date
                        AND d.snapshot_date
                            >= b.latest_date - 7

                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                ),

                start_prices AS (
                    SELECT DISTINCT ON (d.product_id)
                        d.product_id,
                        d.market_price::DOUBLE PRECISION
                            AS start_price

                    FROM tcgplayer_daily_prices AS d

                    JOIN scope AS s
                        ON s.product_id = d.product_id
                        AND s.printing = d.printing

                    CROSS JOIN bounds AS b

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.snapshot_date <=
                            b.latest_date
                            - (%s * INTERVAL '1 day')
                        AND d.snapshot_date >=
                            b.latest_date
                            - (%s * INTERVAL '1 day')
                            - INTERVAL '7 days'

                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                ),

                product_counts AS (
                    SELECT
                        set_name,
                        COUNT(*)::INTEGER
                            AS total_products
                    FROM scope
                    GROUP BY set_name
                ),

                current_ranked AS (
                    SELECT
                        s.set_name,
                        c.product_id,
                        c.current_price,

                        ROW_NUMBER() OVER (
                            PARTITION BY s.set_name
                            ORDER BY c.current_price DESC
                        ) AS price_rank

                    FROM scope AS s

                    JOIN current_prices AS c
                        ON c.product_id = s.product_id
                ),

                current_agg AS (
                    SELECT
                        set_name,

                        COUNT(*)::INTEGER
                            AS priced_products,

                        SUM(
                            current_price
                        )::DOUBLE PRECISION
                            AS current_total_value,

                        PERCENTILE_CONT(0.5)
                            WITHIN GROUP (
                                ORDER BY current_price
                            )::DOUBLE PRECISION
                            AS median_price,

                        SUM(
                            current_price
                        ) FILTER (
                            WHERE price_rank <= 10
                        )::DOUBLE PRECISION
                            AS top_10_value

                    FROM current_ranked

                    GROUP BY set_name
                ),

                paired_agg AS (
                    SELECT
                        s.set_name,

                        COUNT(*)::INTEGER
                            AS matched_products,

                        SUM(
                            old.start_price
                        )::DOUBLE PRECISION
                            AS paired_start_value,

                        SUM(
                            c.current_price
                        )::DOUBLE PRECISION
                            AS paired_current_value

                    FROM scope AS s

                    JOIN current_prices AS c
                        ON c.product_id = s.product_id

                    JOIN start_prices AS old
                        ON old.product_id = s.product_id

                    WHERE old.start_price > 0

                    GROUP BY s.set_name
                )

                SELECT
                    current_agg.set_name,

                    current_agg.current_total_value,

                    current_agg.median_price,

                    (
                        current_agg.top_10_value
                        / NULLIF(
                            current_agg.current_total_value,
                            0
                        )
                    )::DOUBLE PRECISION
                        AS top_10_concentration,

                    product_counts.total_products,

                    current_agg.priced_products,

                    COALESCE(
                        paired_agg.matched_products,
                        0
                    ),

                    COALESCE(
                        paired_agg.paired_start_value,
                        0
                    ),

                    COALESCE(
                        paired_agg.paired_current_value,
                        0
                    ),

                    CASE
                        WHEN
                            paired_agg.paired_start_value > 0
                        THEN (
                            paired_agg.paired_current_value
                            / paired_agg.paired_start_value
                            - 1.0
                        )
                        ELSE NULL
                    END AS period_return,

                    bounds.latest_date

                FROM current_agg

                JOIN product_counts
                    ON product_counts.set_name =
                        current_agg.set_name

                LEFT JOIN paired_agg
                    ON paired_agg.set_name =
                        current_agg.set_name

                CROSS JOIN bounds

                ORDER BY
                    period_return DESC NULLS LAST;
                """,
                (
                    days,
                    days,
                ),
            )

            return cursor.fetchall()



def get_market_rarities() -> list[str]:
    """Return rarity labels present in the tracked TCGPlayer universe."""

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT
                    p.rarity

                FROM tcgplayer_products AS p

                JOIN tcgplayer_primary_printings AS pp
                    ON pp.product_id = p.product_id

                WHERE
                    p.rarity IS NOT NULL
                    AND BTRIM(p.rarity) <> ''
                    AND p.set_name NOT IN (
                            'MEE: Mega Evolution Energies',
                            'SVE: Scarlet & Violet Energies'
                        )

                ORDER BY p.rarity;
                """
            )

            return [
                row[0]
                for row in cursor.fetchall()
            ]


def get_card_screener(
    *,
    days: int = 90,
    set_name: str | None = None,
    rarities: list[str] | tuple[str, ...] | None = None,
    min_start_price: float = 0.0,
    min_current_price: float = 0.0,
    min_absolute_move: float = 0.0,
) -> list[tuple]:
    """
    Return matched card performance over a fixed historical window.

    Price floors are deliberately separated.

    min_start_price:
        Applied to the historical formation-date price. This is
        the preferred investment-universe filter because it avoids
        selecting historical winners using today's price.

    min_current_price:
        Optional current-price filter for browsing.

    min_absolute_move:
        Optional minimum absolute USD price movement.
    """

    rarity_list = (
        list(rarities)
        if rarities
        else None
    )

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH bounds AS (
                    SELECT
                        MAX(snapshot_date)::DATE
                            AS latest_date

                    FROM tcgplayer_daily_prices
                ),

                scope AS (
                    SELECT
                        p.product_id,
                        p.name,
                        p.set_name,
                        p.card_number,
                        p.rarity,
                        p.set_published_on,
                        pp.printing

                    FROM tcgplayer_products AS p

                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id = p.product_id

                    WHERE
                        p.set_name NOT IN (
                            'MEE: Mega Evolution Energies',
                            'SVE: Scarlet & Violet Energies'
                        )

                        AND (
                            %s::TEXT IS NULL
                            OR p.set_name = %s
                        )

                        AND (
                            %s::TEXT[] IS NULL
                            OR p.rarity = ANY(
                                %s::TEXT[]
                            )
                        )
                ),

                current_prices AS (
                    SELECT DISTINCT ON (
                        d.product_id
                    )
                        d.product_id,

                        d.snapshot_date
                            AS current_date,

                        d.market_price::DOUBLE PRECISION
                            AS current_price

                    FROM tcgplayer_daily_prices AS d

                    JOIN scope AS s
                        ON s.product_id =
                            d.product_id

                        AND s.printing =
                            d.printing

                    CROSS JOIN bounds AS b

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.market_price > 0

                        AND d.snapshot_date
                            <= b.latest_date

                        AND d.snapshot_date
                            >= b.latest_date - 7

                        AND d.snapshot_date
                            >= s.set_published_on

                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                ),

                start_prices AS (
                    SELECT DISTINCT ON (
                        d.product_id
                    )
                        d.product_id,

                        d.snapshot_date
                            AS start_date,

                        d.market_price::DOUBLE PRECISION
                            AS start_price

                    FROM tcgplayer_daily_prices AS d

                    JOIN scope AS s
                        ON s.product_id =
                            d.product_id

                        AND s.printing =
                            d.printing

                    CROSS JOIN bounds AS b

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.market_price > 0

                        AND d.snapshot_date
                            <= (
                                b.latest_date
                                - (
                                    %s
                                    * INTERVAL '1 day'
                                )
                            )

                        AND d.snapshot_date
                            >= (
                                b.latest_date
                                - (
                                    %s
                                    * INTERVAL '1 day'
                                )
                                - INTERVAL '7 days'
                            )

                        AND d.snapshot_date
                            >= s.set_published_on

                    ORDER BY
                        d.product_id,
                        d.snapshot_date DESC
                )

                SELECT
                    s.product_id,
                    s.name,
                    s.set_name,
                    s.card_number,
                    s.rarity,

                    old.start_date,
                    old.start_price,

                    current.current_date,
                    current.current_price,

                    (
                        current.current_price
                        - old.start_price
                    ) AS dollar_change,

                    (
                        current.current_price
                        / old.start_price
                        - 1.0
                    ) AS period_return

                FROM scope AS s

                JOIN current_prices AS current
                    ON current.product_id =
                        s.product_id

                JOIN start_prices AS old
                    ON old.product_id =
                        s.product_id

                WHERE
                    old.start_price >= %s

                    AND current.current_price
                        >= %s

                    AND ABS(
                        current.current_price
                        - old.start_price
                    ) >= %s

                ORDER BY
                    period_return DESC,
                    dollar_change DESC;
                """,
                (
                    set_name,
                    set_name,
                    rarity_list,
                    rarity_list,
                    days,
                    days,
                    min_start_price,
                    min_current_price,
                    min_absolute_move,
                ),
            )

            return cursor.fetchall()



def get_segment_value_history(
    set_name: str,
    *,
    days: int = 90,
    rarities: list[str] | tuple[str, ...] | None = None,
    min_start_price: float = 0.0,
) -> list[tuple]:
    """
    Track a fixed investable segment of a set through time.

    Basket membership is determined at the start of the requested
    period using only information available at that time.

    This avoids hindsight bias: a card cannot enter the historical
    basket simply because it became valuable later.

    Prices may carry forward for up to seven days to prevent isolated
    source gaps from creating artificial basket-value drops.
    """

    rarity_list = (
        list(rarities)
        if rarities
        else None
    )

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH bounds AS (
                    SELECT
                        MAX(snapshot_date)::DATE
                            AS latest_date
                    FROM tcgplayer_daily_prices
                ),

                release_info AS (
                    SELECT
                        MIN(set_published_on)::DATE
                            AS release_date
                    FROM tcgplayer_products
                    WHERE set_name = %s
                ),

                period AS (
                    SELECT
                        b.latest_date,

                        CASE
                            WHEN
                                b.latest_date
                                - %s::INTEGER
                                < r.release_date
                            THEN LEAST(
                                b.latest_date,
                                r.release_date + 7
                            )

                            ELSE
                                b.latest_date
                                - %s::INTEGER
                        END AS start_date

                    FROM bounds AS b
                    CROSS JOIN release_info AS r
                ),

                scope AS (
                    SELECT
                        p.product_id,
                        p.name,
                        p.card_number,
                        p.rarity,
                        p.set_published_on,
                        pp.printing

                    FROM tcgplayer_products AS p

                    JOIN tcgplayer_primary_printings AS pp
                        ON pp.product_id =
                            p.product_id

                    WHERE
                        p.set_name = %s

                        AND (
                            %s::TEXT[] IS NULL
                            OR p.rarity = ANY(
                                %s::TEXT[]
                            )
                        )
                ),

                formation AS (
                    SELECT
                        s.product_id,
                        s.name,
                        s.card_number,
                        s.rarity,
                        s.printing,
                        p.start_date,
                        start_price.snapshot_date
                            AS formation_price_date,
                        start_price.market_price
                            AS start_price

                    FROM scope AS s

                    CROSS JOIN period AS p

                    JOIN LATERAL (
                        SELECT
                            d.snapshot_date,
                            d.market_price::DOUBLE PRECISION
                                AS market_price

                        FROM tcgplayer_daily_prices AS d

                        WHERE
                            d.product_id =
                                s.product_id

                            AND d.printing =
                                s.printing

                            AND d.market_price
                                IS NOT NULL

                            AND d.market_price > 0

                            AND d.snapshot_date
                                <= p.start_date

                            AND d.snapshot_date
                                >= p.start_date - 7

                            AND d.snapshot_date
                                >= s.set_published_on

                        ORDER BY
                            d.snapshot_date DESC

                        LIMIT 1
                    ) AS start_price
                        ON TRUE

                    WHERE
                        start_price.market_price
                            >= %s
                ),

                dates AS (
                    SELECT
                        generate_series(
                            p.start_date::TIMESTAMP,
                            p.latest_date::TIMESTAMP,
                            INTERVAL '1 day'
                        )::DATE
                            AS snapshot_date

                    FROM period AS p
                ),

                daily AS (
                    SELECT
                        dates.snapshot_date,

                        SUM(
                            price.market_price
                        )::DOUBLE PRECISION
                            AS basket_value,

                        COUNT(
                            price.market_price
                        )::INTEGER
                            AS priced_products,

                        COUNT(*)::INTEGER
                            AS cohort_size,

                        (
                            COUNT(
                                price.market_price
                            )::DOUBLE PRECISION
                            / NULLIF(
                                COUNT(*)::DOUBLE PRECISION,
                                0
                            )
                        ) AS coverage

                    FROM dates

                    CROSS JOIN formation AS f

                    LEFT JOIN LATERAL (
                        SELECT
                            d.market_price::DOUBLE PRECISION
                                AS market_price

                        FROM tcgplayer_daily_prices AS d

                        WHERE
                            d.product_id =
                                f.product_id

                            AND d.printing =
                                f.printing

                            AND d.market_price
                                IS NOT NULL

                            AND d.market_price > 0

                            AND d.snapshot_date
                                <= dates.snapshot_date

                            AND d.snapshot_date
                                >= dates.snapshot_date - 7

                        ORDER BY
                            d.snapshot_date DESC

                        LIMIT 1
                    ) AS price
                        ON TRUE

                    GROUP BY
                        dates.snapshot_date
                )

                SELECT
                    snapshot_date,
                    basket_value,
                    priced_products,
                    cohort_size,
                    coverage

                FROM daily

                ORDER BY snapshot_date;
                """,
                (
                    set_name,
                    days,
                    days,
                    set_name,
                    rarity_list,
                    rarity_list,
                    min_start_price,
                ),
            )

            return cursor.fetchall()
