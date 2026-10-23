from __future__ import annotations

from services.analysis.opportunity_snapshot import snapshot_input_writer


from database.operations import DATABASE_NAME
from pokemon_eras import era_case_sql


EXCLUDED_ANALYTICAL_SETS = (
    "MEE: Mega Evolution Energies",
    "SVE: Scarlet & Violet Energies",
)


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS raw_relative_momentum_90d (
        formation_date DATE NOT NULL,
        product_id BIGINT NOT NULL,
        printing VARCHAR(100) NOT NULL,

        set_name TEXT NOT NULL,
        era VARCHAR(50),
        rarity VARCHAR(100),
        release_age_days INTEGER NOT NULL,

        lookback_price_date DATE NOT NULL,
        lookback_price DOUBLE PRECISION NOT NULL,

        formation_price_date DATE NOT NULL,
        formation_price DOUBLE PRECISION NOT NULL,

        future_price_date DATE NOT NULL,
        future_price DOUBLE PRECISION NOT NULL,

        momentum_90d DOUBLE PRECISION NOT NULL,
        set_momentum_90d DOUBLE PRECISION NOT NULL,
        relative_momentum_90d DOUBLE PRECISION NOT NULL,

        signal_percentile DOUBLE PRECISION NOT NULL,
        quintile SMALLINT NOT NULL,

        future_return_90d DOUBLE PRECISION NOT NULL,
        set_future_return_90d DOUBLE PRECISION NOT NULL,
        future_alpha_90d DOUBLE PRECISION NOT NULL,

        PRIMARY KEY (
            formation_date,
            product_id
        )
    );
    """,

    """
    CREATE INDEX IF NOT EXISTS
        idx_raw_relative_momentum_formation
    ON raw_relative_momentum_90d (
        formation_date
    );
    """,

    """
    CREATE INDEX IF NOT EXISTS
        idx_raw_relative_momentum_signal
    ON raw_relative_momentum_90d (
        relative_momentum_90d
    );
    """,

    """
    CREATE INDEX IF NOT EXISTS
        idx_raw_relative_momentum_set
    ON raw_relative_momentum_90d (
        set_name,
        formation_date
    );
    """,

    """
    CREATE INDEX IF NOT EXISTS
        idx_raw_relative_momentum_era
    ON raw_relative_momentum_90d (
        era,
        formation_date
    );
    """,

    """
    CREATE INDEX IF NOT EXISTS
        idx_tcgplayer_research_lookup
    ON tcgplayer_daily_prices (
        product_id,
        printing,
        snapshot_date DESC
    )
    WHERE market_price IS NOT NULL;
    """,
]


def connect():
    import psycopg
    return psycopg.connect(
        dbname=DATABASE_NAME
    )


def initialise_raw_research_schema() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


@snapshot_input_writer
def rebuild_raw_relative_momentum_90d(
    *,
    price_floor: float = 5.0,
    minimum_release_age: int = 90,
    formation_step_days: int = 7,
    minimum_set_size: int = 5,
) -> dict:
    """
    Build a historical point-in-time raw-card research panel.

    Signal:
        previous 90D card return
        minus previous 90D set median return

    Target:
        following 90D card return
        minus following 90D set median return

    Important safeguards:
    - Formation price floor is applied historically, not today.
    - Cards must already have been released for minimum_release_age.
    - Rarity/set information is static metadata only.
    - Printing is selected using prices available at formation time.
    - The selected printing remains fixed through the future target.
    - Price lookups can use at most seven days of backward carry.
    - No future price is used to determine signal rank/quintile.
    """

    initialise_raw_research_schema()

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    raw_relative_momentum_90d;
                """
            )

            cursor.execute(
                f"""
                WITH bounds AS MATERIALIZED (
                    SELECT
                        MIN(snapshot_date)::DATE
                            AS min_date,

                        MAX(snapshot_date)::DATE
                            AS max_date

                    FROM tcgplayer_daily_prices

                    WHERE market_price IS NOT NULL
                ),

                formation_dates AS MATERIALIZED (
                    SELECT
                        generated::DATE
                            AS formation_date

                    FROM bounds AS b

                    CROSS JOIN LATERAL
                        generate_series(
                            (
                                date_trunc(
                                    'week',
                                    b.min_date::TIMESTAMP
                                    + INTERVAL '90 days'
                                )
                                + INTERVAL '7 days'
                            ),

                            date_trunc(
                                'week',
                                b.max_date::TIMESTAMP
                                - INTERVAL '90 days'
                            ),

                            make_interval(
                                days => %s
                            )
                        ) AS generated
                ),

                available_printings AS MATERIALIZED (
                    SELECT DISTINCT
                        d.product_id,
                        d.printing

                    FROM tcgplayer_daily_prices AS d

                    WHERE
                        d.market_price IS NOT NULL
                        AND d.market_price > 0
                ),

                universe AS MATERIALIZED (
                    SELECT
                        p.product_id,
                        ap.printing,
                        p.set_name,
                        p.rarity,
                        p.set_published_on,

                        {era_case_sql("p.set_name")} AS era

                    FROM available_printings AS ap

                    JOIN tcgplayer_products AS p
                        ON p.product_id =
                            ap.product_id

                    WHERE
                        p.set_name NOT IN (
                            'MEE: Mega Evolution Energies',
                            'SVE: Scarlet & Violet Energies'
                        )
                ),

                candidates AS MATERIALIZED (
                    SELECT
                        f.formation_date,
                        u.product_id,
                        u.printing,
                        u.set_name,
                        u.rarity,
                        u.set_published_on,
                        u.era,

                        (
                            f.formation_date
                            - u.set_published_on
                        )::INTEGER
                            AS release_age_days

                    FROM formation_dates AS f

                    JOIN universe AS u
                        ON
                            u.set_published_on
                            <= (
                                f.formation_date
                                - %s::INTEGER
                            )

                    WHERE
                        u.era IS NOT NULL
                ),

                signal_candidates AS MATERIALIZED (
                    SELECT
                        c.*,

                        old_price.snapshot_date
                            AS lookback_price_date,

                        old_price.market_price
                            AS lookback_price,

                        now_price.snapshot_date
                            AS formation_price_date,

                        now_price.market_price
                            AS formation_price

                    FROM candidates AS c

                    JOIN LATERAL (
                        SELECT
                            d.snapshot_date,
                            d.market_price::DOUBLE PRECISION
                                AS market_price

                        FROM tcgplayer_daily_prices AS d

                        WHERE
                            d.product_id =
                                c.product_id

                            AND d.printing =
                                c.printing

                            AND d.market_price
                                IS NOT NULL

                            AND d.market_price > 0

                            AND d.snapshot_date
                                <= (
                                    c.formation_date
                                    - 90
                                )

                            AND d.snapshot_date
                                >= (
                                    c.formation_date
                                    - 97
                                )

                            AND d.snapshot_date
                                >= c.set_published_on

                        ORDER BY
                            d.snapshot_date DESC

                        LIMIT 1
                    ) AS old_price
                        ON TRUE

                    JOIN LATERAL (
                        SELECT
                            d.snapshot_date,
                            d.market_price::DOUBLE PRECISION
                                AS market_price

                        FROM tcgplayer_daily_prices AS d

                        WHERE
                            d.product_id =
                                c.product_id

                            AND d.printing =
                                c.printing

                            AND d.market_price
                                IS NOT NULL

                            AND d.market_price > 0

                            AND d.snapshot_date
                                <= c.formation_date

                            AND d.snapshot_date
                                >= (
                                    c.formation_date
                                    - 7
                                )

                            AND d.snapshot_date
                                >= c.set_published_on

                        ORDER BY
                            d.snapshot_date DESC

                        LIMIT 1
                    ) AS now_price
                        ON TRUE

                    WHERE
                        now_price.market_price
                            >= %s
                ),

                preferred_printing AS MATERIALIZED (
                    SELECT
                        signal_candidates.*,

                        ROW_NUMBER() OVER (
                            PARTITION BY
                                formation_date,
                                product_id

                            ORDER BY
                                CASE printing
                                    WHEN 'Normal'
                                        THEN 1
                                    WHEN 'Holofoil'
                                        THEN 2
                                    WHEN 'Reverse Holofoil'
                                        THEN 3
                                    ELSE 4
                                END,

                                formation_price_date
                                    DESC,

                                printing
                        ) AS printing_rank

                    FROM signal_candidates
                ),

                signal_base AS MATERIALIZED (
                    SELECT
                        formation_date,
                        product_id,
                        printing,
                        set_name,
                        rarity,
                        era,
                        release_age_days,

                        lookback_price_date,
                        lookback_price,

                        formation_price_date,
                        formation_price,

                        (
                            formation_price
                            / lookback_price
                            - 1.0
                        )::DOUBLE PRECISION
                            AS momentum_90d

                    FROM preferred_printing

                    WHERE printing_rank = 1
                ),

                set_signal_stats AS MATERIALIZED (
                    SELECT
                        formation_date,
                        set_name,

                        PERCENTILE_CONT(0.5)
                            WITHIN GROUP (
                                ORDER BY momentum_90d
                            )::DOUBLE PRECISION
                                AS set_momentum_90d,

                        COUNT(*)::INTEGER
                            AS set_constituents

                    FROM signal_base

                    GROUP BY
                        formation_date,
                        set_name

                    HAVING
                        COUNT(*) >= %s
                ),

                relative_signal AS MATERIALIZED (
                    SELECT
                        s.*,
                        ss.set_momentum_90d,

                        (
                            s.momentum_90d
                            - ss.set_momentum_90d
                        )::DOUBLE PRECISION
                            AS relative_momentum_90d

                    FROM signal_base AS s

                    JOIN set_signal_stats AS ss
                        ON
                            ss.formation_date =
                                s.formation_date

                            AND ss.set_name =
                                s.set_name
                ),

                ranked_signal AS MATERIALIZED (
                    SELECT
                        relative_signal.*,

                        PERCENT_RANK() OVER (
                            PARTITION BY
                                formation_date

                            ORDER BY
                                relative_momentum_90d
                        )::DOUBLE PRECISION
                            AS signal_percentile

                    FROM relative_signal
                ),

                formed_signal AS MATERIALIZED (
                    SELECT
                        ranked_signal.*,

                        LEAST(
                            5,
                            (
                                FLOOR(
                                    signal_percentile
                                    * 5.0
                                )::INTEGER
                                + 1
                            )
                        )::SMALLINT
                            AS quintile

                    FROM ranked_signal
                ),

                labeled AS MATERIALIZED (
                    SELECT
                        s.*,

                        future_price.snapshot_date
                            AS future_price_date,

                        future_price.market_price
                            AS future_price,

                        (
                            future_price.market_price
                            / s.formation_price
                            - 1.0
                        )::DOUBLE PRECISION
                            AS future_return_90d

                    FROM formed_signal AS s

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
                                <= (
                                    s.formation_date
                                    + 90
                                )

                            AND d.snapshot_date
                                >= (
                                    s.formation_date
                                    + 83
                                )

                        ORDER BY
                            d.snapshot_date DESC

                        LIMIT 1
                    ) AS future_price
                        ON TRUE
                ),

                set_target_stats AS MATERIALIZED (
                    SELECT
                        formation_date,
                        set_name,

                        PERCENTILE_CONT(0.5)
                            WITHIN GROUP (
                                ORDER BY future_return_90d
                            )::DOUBLE PRECISION
                                AS set_future_return_90d,

                        COUNT(*)::INTEGER
                            AS target_constituents

                    FROM labeled

                    GROUP BY
                        formation_date,
                        set_name

                    HAVING
                        COUNT(*) >= %s
                )

                INSERT INTO raw_relative_momentum_90d (
                    formation_date,
                    product_id,
                    printing,

                    set_name,
                    era,
                    rarity,
                    release_age_days,

                    lookback_price_date,
                    lookback_price,

                    formation_price_date,
                    formation_price,

                    future_price_date,
                    future_price,

                    momentum_90d,
                    set_momentum_90d,
                    relative_momentum_90d,

                    signal_percentile,
                    quintile,

                    future_return_90d,
                    set_future_return_90d,
                    future_alpha_90d
                )

                SELECT
                    l.formation_date,
                    l.product_id,
                    l.printing,

                    l.set_name,
                    l.era,
                    l.rarity,
                    l.release_age_days,

                    l.lookback_price_date,
                    l.lookback_price,

                    l.formation_price_date,
                    l.formation_price,

                    l.future_price_date,
                    l.future_price,

                    l.momentum_90d,
                    l.set_momentum_90d,
                    l.relative_momentum_90d,

                    l.signal_percentile,
                    l.quintile,

                    l.future_return_90d,
                    st.set_future_return_90d,

                    (
                        l.future_return_90d
                        - st.set_future_return_90d
                    )::DOUBLE PRECISION
                        AS future_alpha_90d

                FROM labeled AS l

                JOIN set_target_stats AS st
                    ON
                        st.formation_date =
                            l.formation_date

                        AND st.set_name =
                            l.set_name;
                """,
                (
                    formation_step_days,
                    minimum_release_age,
                    price_floor,
                    minimum_set_size,
                    minimum_set_size,
                ),
            )

            cursor.execute(
                """
                ANALYZE
                    raw_relative_momentum_90d;
                """
            )

            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(
                        DISTINCT formation_date
                    ),
                    COUNT(
                        DISTINCT product_id
                    ),
                    MIN(formation_date),
                    MAX(formation_date)

                FROM raw_relative_momentum_90d;
                """
            )

            (
                rows,
                formation_dates,
                products,
                first_date,
                last_date,
            ) = cursor.fetchone()

        connection.commit()

    return {
        "rows": rows,
        "formation_dates": formation_dates,
        "products": products,
        "first_date": first_date,
        "last_date": last_date,
        "price_floor": price_floor,
        "minimum_release_age":
            minimum_release_age,
        "formation_step_days":
            formation_step_days,
        "minimum_set_size":
            minimum_set_size,
    }


def fetch_raw_relative_momentum_90d():
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    formation_date,
                    product_id,
                    printing,
                    set_name,
                    era,
                    rarity,
                    release_age_days,
                    lookback_price,
                    formation_price,
                    future_price,
                    momentum_90d,
                    set_momentum_90d,
                    relative_momentum_90d,
                    signal_percentile,
                    quintile,
                    future_return_90d,
                    set_future_return_90d,
                    future_alpha_90d

                FROM raw_relative_momentum_90d

                ORDER BY
                    formation_date,
                    product_id;
                """
            )

            columns = [
                description.name
                for description
                in cursor.description
            ]

            rows = cursor.fetchall()

    return columns, rows
