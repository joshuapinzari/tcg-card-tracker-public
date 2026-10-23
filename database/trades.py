from __future__ import annotations

from datetime import date


from database.operations import DATABASE_NAME, initialise_database
from database.card_variants import (
    get_current_variant_prices, get_trade_date_variant_prices, raw_item_key,
)


EXCLUDED_SETS = (
    "MEE: Mega Evolution Energies",
    "SVE: Scarlet & Violet Energies",
)


def connect():
    import psycopg
    return psycopg.connect(
        dbname=DATABASE_NAME
    )


def initialise_trade_schema() -> None:
    initialise_database()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS trades (
            trade_id BIGSERIAL PRIMARY KEY,
            trade_date DATE NOT NULL,
            currency VARCHAR(3) NOT NULL,
            notes TEXT,
            signal_version VARCHAR(100),
            cash_given DOUBLE PRECISION NOT NULL DEFAULT 0,
            cash_received DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at TIMESTAMP
                NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );
        """,

        """
        CREATE TABLE IF NOT EXISTS trade_items (
            trade_item_id BIGSERIAL PRIMARY KEY,

            trade_id BIGINT NOT NULL
                REFERENCES trades(trade_id)
                ON DELETE CASCADE,

            side VARCHAR(20) NOT NULL
                CHECK (
                    side IN (
                        'GIVEN',
                        'RECEIVED'
                    )
                ),

            product_id BIGINT,

            card_id INTEGER
                REFERENCES cards(card_id),

            market_type VARCHAR(30)
                NOT NULL
                DEFAULT 'RAW',

            name TEXT NOT NULL,
            set_name TEXT NOT NULL,
            card_number VARCHAR(50),
            rarity VARCHAR(100),
            printing VARCHAR(100),

            quantity INTEGER NOT NULL
                DEFAULT 1
                CHECK (quantity > 0),

            agreed_unit_value DOUBLE PRECISION
                NOT NULL,

            market_unit_usd_at_trade
                DOUBLE PRECISION,

            signal_available BOOLEAN
                NOT NULL
                DEFAULT FALSE,

            momentum_90 DOUBLE PRECISION,
            set_momentum_90 DOUBLE PRECISION,
            market_momentum_90 DOUBLE PRECISION,

            relative_set_90 DOUBLE PRECISION,
            relative_market_90 DOUBLE PRECISION,

            set_signal_percentile
                DOUBLE PRECISION,

            market_signal_percentile
                DOUBLE PRECISION,

            overheat_level VARCHAR(50),
            market_regime VARCHAR(50),

            CONSTRAINT trade_items_market_type_check
                CHECK (
                    market_type IN (
                        'RAW',
                        'PSA_9',
                        'PSA_10',
                        'ACE_10'
                    )
                ),

            CONSTRAINT trade_items_asset_identity_check
                CHECK (
                    product_id IS NOT NULL
                    OR card_id IS NOT NULL
                ),

            created_at TIMESTAMP
                NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );
        """,

        """
        ALTER TABLE trades
        ADD COLUMN IF NOT EXISTS
            cash_given DOUBLE PRECISION NOT NULL DEFAULT 0;
        """,

        """
        ALTER TABLE trades
        ADD COLUMN IF NOT EXISTS
            cash_received DOUBLE PRECISION NOT NULL DEFAULT 0;
        """,

        """
        ALTER TABLE trade_items
        ADD COLUMN IF NOT EXISTS
            card_id INTEGER
            REFERENCES cards(card_id);
        """,

        """
        ALTER TABLE trade_items
        ADD COLUMN IF NOT EXISTS
            market_type VARCHAR(30);
        """,

        """
        UPDATE trade_items
        SET market_type = 'RAW'
        WHERE market_type IS NULL;
        """,

        """
        ALTER TABLE trade_items
        ALTER COLUMN market_type
        SET DEFAULT 'RAW';
        """,

        """
        ALTER TABLE trade_items
        ALTER COLUMN market_type
        SET NOT NULL;
        """,

        """
        ALTER TABLE trade_items
        ALTER COLUMN product_id
        DROP NOT NULL;
        """,

        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname =
                    'trade_items_market_type_check'
            ) THEN
                ALTER TABLE trade_items
                ADD CONSTRAINT
                    trade_items_market_type_check
                CHECK (
                    market_type IN (
                        'RAW',
                        'PSA_9',
                        'PSA_10',
                        'ACE_10'
                    )
                );
            END IF;
        END
        $$;
        """,

        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname =
                    'trade_items_asset_identity_check'
            ) THEN
                ALTER TABLE trade_items
                ADD CONSTRAINT
                    trade_items_asset_identity_check
                CHECK (
                    product_id IS NOT NULL
                    OR card_id IS NOT NULL
                );
            END IF;
        END
        $$;
        """,

        """
        CREATE INDEX IF NOT EXISTS
            idx_trade_items_card_market
        ON trade_items (
            card_id,
            market_type
        );
        """,

        """
        CREATE INDEX IF NOT EXISTS
            idx_trade_items_trade
        ON trade_items (
            trade_id
        );
        """,

        """
        CREATE INDEX IF NOT EXISTS
            idx_trade_items_product
        ON trade_items (
            product_id
        );
        """,
    ]

    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(
                    statement
                )

        connection.commit()


def search_trade_products(query: str, limit: int = 40) -> list[tuple]:
    """Search every priced finish, not just the research primary printing."""
    query = query.strip()
    if len(query) < 2:
        return []
    pattern = f"%{query}%"
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT p.product_id, p.name, p.set_name, p.card_number, p.rarity,
                       latest.printing, latest.snapshot_date, latest.market_price
                FROM tcgplayer_products AS p
                JOIN LATERAL (
                    SELECT DISTINCT ON (d.printing) d.printing, d.snapshot_date,
                           d.market_price::DOUBLE PRECISION
                    FROM tcgplayer_daily_prices AS d
                    WHERE d.product_id = p.product_id
                      AND d.market_price IS NOT NULL AND d.market_price > 0
                    ORDER BY d.printing, d.snapshot_date DESC
                ) AS latest ON TRUE
                WHERE p.set_name NOT IN (%s, %s)
                  AND (p.name ILIKE %s OR p.set_name ILIKE %s
                       OR COALESCE(p.card_number, '') ILIKE %s)
                ORDER BY CASE WHEN p.name ILIKE %s THEN 0 ELSE 1 END,
                         p.name, p.set_name, p.product_id, latest.printing
                LIMIT %s;
            """, (*EXCLUDED_SETS, pattern, pattern, pattern, query + "%", int(limit)))
            return cursor.fetchall()


def get_trade_date_prices(
    product_ids: list[int],
    trade_date: date,
) -> dict[int, float]:
    if not product_ids:
        return {}

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.product_id,

                    PERCENTILE_CONT(0.5)
                        WITHIN GROUP (
                            ORDER BY
                                d.market_price
                        )::DOUBLE PRECISION
                            AS market_price

                FROM tcgplayer_products AS p

                JOIN tcgplayer_primary_printings AS pp
                    ON pp.product_id =
                        p.product_id

                JOIN tcgplayer_daily_prices AS d
                    ON
                        d.product_id =
                            p.product_id

                        AND d.printing =
                            pp.printing

                WHERE
                    p.product_id =
                        ANY(%s)

                    AND d.market_price
                        IS NOT NULL

                    AND d.market_price > 0

                    AND d.snapshot_date
                        BETWEEN
                            %s::DATE - 6
                            AND %s::DATE

                GROUP BY
                    p.product_id;
                """,
                (
                    product_ids,
                    trade_date,
                    trade_date,
                ),
            )

            return {
                int(product_id):
                    float(price)
                for (
                    product_id,
                    price,
                ) in cursor.fetchall()
                if price is not None
            }


def get_current_prices(
    product_ids: list[int],
) -> dict[int, tuple]:
    if not product_ids:
        return {}

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.product_id,
                    latest.snapshot_date,
                    latest.market_price

                FROM tcgplayer_products AS p

                JOIN tcgplayer_primary_printings AS pp
                    ON pp.product_id =
                        p.product_id

                JOIN LATERAL (
                    SELECT
                        d.snapshot_date,
                        d.market_price
                            ::DOUBLE PRECISION
                            AS market_price

                    FROM tcgplayer_daily_prices AS d

                    WHERE
                        d.product_id =
                            p.product_id

                        AND d.printing =
                            pp.printing

                        AND d.market_price
                            IS NOT NULL

                        AND d.market_price > 0

                    ORDER BY
                        d.snapshot_date DESC

                    LIMIT 1
                ) AS latest
                    ON TRUE

                WHERE
                    p.product_id =
                        ANY(%s);
                """,
                (
                    product_ids,
                ),
            )

            return {
                int(product_id): (
                    snapshot_date,
                    float(price),
                )
                for (
                    product_id,
                    snapshot_date,
                    price,
                ) in cursor.fetchall()
            }



GRADED_TRADE_MARKETS = (
    "PSA_9",
    "PSA_10",
    "ACE_10",
)


def search_graded_trade_cards(
    query: str,
    market_type: str,
    limit: int = 40,
) -> list[tuple]:
    """Search canonical TCGPlayer cards with PokemonPriceTracker history."""
    query = query.strip()
    market_type = str(market_type).upper()
    if market_type not in GRADED_TRADE_MARKETS:
        raise ValueError(f"Unsupported graded market: {market_type}")
    if len(query) < 2:
        return []

    pattern = f"%{query}%"
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.product_id,
                    p.name,
                    p.set_name,
                    p.card_number,
                    'English'::TEXT AS language,
                    latest.observed_on::TIMESTAMP,
                    latest.price::DOUBLE PRECISION,
                    last_sale.last_sale_date
                FROM tcgplayer_products AS p
                JOIN LATERAL (
                    SELECT h.observed_on, h.price
                    FROM provider_price_history AS h
                    WHERE h.product_id = p.product_id
                      AND h.source = 'pokemonpricetracker'
                      AND h.market_type = %s
                      AND h.price > 0
                    ORDER BY h.observed_on DESC
                    LIMIT 1
                ) AS latest ON TRUE
                LEFT JOIN LATERAL (
                    SELECT MAX(h.observed_on) AS last_sale_date
                    FROM provider_price_history AS h
                    WHERE h.product_id = p.product_id
                      AND h.source = 'pokemonpricetracker'
                      AND h.market_type = %s
                      AND COALESCE(h.sale_count, 0) > 0
                ) AS last_sale ON TRUE
                WHERE (
                    p.name ILIKE %s
                    OR p.set_name ILIKE %s
                    OR COALESCE(p.card_number, '') ILIKE %s
                )
                ORDER BY
                    CASE WHEN p.name ILIKE %s THEN 0 ELSE 1 END,
                    p.name, p.set_name, p.card_number
                LIMIT %s;
                """,
                (market_type, market_type, pattern, pattern, pattern, query + "%", limit),
            )
            return cursor.fetchall()


def get_current_graded_prices(
    asset_keys: list[tuple[int, str]],
) -> dict[tuple[int, str], tuple]:
    """Return latest PokemonPriceTracker price for (product_id, market)."""
    asset_keys = [
        (int(product_id), str(market_type).upper())
        for product_id, market_type in asset_keys
        if product_id is not None
    ]
    if not asset_keys:
        return {}

    result = {}
    with connect() as connection:
        with connection.cursor() as cursor:
            for product_id, market_type in asset_keys:
                if market_type not in GRADED_TRADE_MARKETS:
                    continue
                cursor.execute(
                    """
                    SELECT observed_on::TIMESTAMP, price::DOUBLE PRECISION
                    FROM provider_price_history
                    WHERE product_id = %s
                      AND source = 'pokemonpricetracker'
                      AND market_type = %s
                      AND price > 0
                    ORDER BY observed_on DESC
                    LIMIT 1;
                    """,
                    (product_id, market_type),
                )
                row = cursor.fetchone()
                if row is not None:
                    result[(product_id, market_type)] = (row[0], float(row[1]))
    return result


def get_trade_date_graded_prices(
    asset_keys: list[tuple[int, str]],
    trade_date: date,
) -> dict[tuple[int, str], float]:
    """Return latest provider observation on/before a historical trade date."""
    asset_keys = [
        (int(product_id), str(market_type).upper())
        for product_id, market_type in asset_keys
        if product_id is not None
    ]
    if not asset_keys:
        return {}

    result = {}
    with connect() as connection:
        with connection.cursor() as cursor:
            for product_id, market_type in asset_keys:
                if market_type not in GRADED_TRADE_MARKETS:
                    continue
                cursor.execute(
                    """
                    SELECT price::DOUBLE PRECISION
                    FROM provider_price_history
                    WHERE product_id = %s
                      AND source = 'pokemonpricetracker'
                      AND market_type = %s
                      AND price > 0
                      AND observed_on <= %s
                    ORDER BY observed_on DESC
                    LIMIT 1;
                    """,
                    (product_id, market_type, trade_date),
                )
                row = cursor.fetchone()
                if row is not None:
                    result[(product_id, market_type)] = float(row[0])
    return result


def create_trade(
    *,
    trade_date: date,
    currency: str,
    notes: str,
    items: list[dict],
    cash_given: float = 0.0,
    cash_received: float = 0.0,
    signal_version: str = (
        "overheating_v1"
    ),
) -> int:
    initialise_trade_schema()

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO trades (
                    trade_date,
                    currency,
                    notes,
                    signal_version,
                    cash_given,
                    cash_received
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING trade_id;
                """,
                (
                    trade_date,
                    currency,
                    notes or None,
                    signal_version,
                    float(cash_given or 0.0),
                    float(cash_received or 0.0),
                ),
            )

            trade_id = int(
                cursor.fetchone()[0]
            )

            for item in items:
                cursor.execute(
                    """
                    INSERT INTO trade_items (
                        trade_id,
                        side,

                        product_id,
                        card_id,
                        market_type,

                        name,
                        set_name,
                        card_number,
                        rarity,
                        printing,

                        quantity,
                        agreed_unit_value,

                        market_unit_usd_at_trade,

                        signal_available,

                        momentum_90,
                        set_momentum_90,
                        market_momentum_90,

                        relative_set_90,
                        relative_market_90,

                        set_signal_percentile,
                        market_signal_percentile,

                        overheat_level,
                        market_regime
                    )
                    VALUES (
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s,
                        %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s
                    );
                    """,
                    (
                        trade_id,
                        item["side"],

                        item.get(
                            "product_id"
                        ),

                        item.get(
                            "card_id"
                        ),

                        item.get(
                            "market_type",
                            "RAW",
                        ),

                        item["name"],
                        item["set_name"],
                        item.get(
                            "card_number"
                        ),
                        item.get(
                            "rarity"
                        ),
                        item.get(
                            "printing"
                        ),

                        item["quantity"],
                        item[
                            "agreed_unit_value"
                        ],

                        item.get(
                            "market_unit_usd_at_trade"
                        ),

                        item.get(
                            "signal_available",
                            False,
                        ),

                        item.get(
                            "momentum_90"
                        ),
                        item.get(
                            "set_momentum_90"
                        ),
                        item.get(
                            "market_momentum_90"
                        ),

                        item.get(
                            "relative_set_90"
                        ),
                        item.get(
                            "relative_market_90"
                        ),

                        item.get(
                            "set_signal_percentile"
                        ),
                        item.get(
                            "market_signal_percentile"
                        ),

                        item.get(
                            "overheat_level"
                        ),
                        item.get(
                            "market_regime"
                        ),
                    ),
                )

        connection.commit()

    return trade_id


def list_trades() -> list[tuple]:
    initialise_trade_schema()

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    t.trade_id,
                    t.trade_date,
                    t.currency,
                    t.notes,
                    t.created_at,
                    COALESCE(t.cash_given, 0)::DOUBLE PRECISION AS cash_given,
                    COALESCE(t.cash_received, 0)::DOUBLE PRECISION AS cash_received,

                    COUNT(
                        ti.trade_item_id
                    )::INTEGER
                        AS item_count,

                    COALESCE(
                        SUM(
                            ti.quantity
                            * ti.agreed_unit_value
                        ) FILTER (
                            WHERE
                                ti.side =
                                    'GIVEN'
                        ),
                        0
                    )::DOUBLE PRECISION
                        AS given_value,

                    COALESCE(
                        SUM(
                            ti.quantity
                            * ti.agreed_unit_value
                        ) FILTER (
                            WHERE
                                ti.side =
                                    'RECEIVED'
                        ),
                        0
                    )::DOUBLE PRECISION
                        AS received_value

                FROM trades AS t

                LEFT JOIN trade_items AS ti
                    ON ti.trade_id =
                        t.trade_id

                GROUP BY
                    t.trade_id

                ORDER BY
                    t.trade_date DESC,
                    t.trade_id DESC;
                """
            )

            return cursor.fetchall()


def get_trade_items(
    trade_id: int,
) -> list[tuple]:
    initialise_trade_schema()

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    trade_item_id,
                    side,

                    product_id,
                    card_id,
                    market_type,

                    name,
                    set_name,
                    card_number,
                    rarity,
                    printing,

                    quantity,
                    agreed_unit_value,

                    market_unit_usd_at_trade,

                    signal_available,

                    momentum_90,
                    set_momentum_90,
                    market_momentum_90,

                    relative_set_90,
                    relative_market_90,

                    set_signal_percentile,
                    market_signal_percentile,

                    overheat_level,
                    market_regime

                FROM trade_items

                WHERE
                    trade_id = %s

                ORDER BY
                    CASE side
                        WHEN 'GIVEN'
                            THEN 1
                        ELSE 2
                    END,
                    trade_item_id;
                """,
                (
                    trade_id,
                ),
            )

            return cursor.fetchall()



def set_trade_cash(
    trade_id: int,
    *,
    cash_given: float = 0.0,
    cash_received: float = 0.0,
) -> None:
    """Persist cash included on either side of a trade."""

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE trades
                ADD COLUMN IF NOT EXISTS
                    cash_given
                    DOUBLE PRECISION
                    NOT NULL
                    DEFAULT 0;
                """
            )

            cursor.execute(
                """
                ALTER TABLE trades
                ADD COLUMN IF NOT EXISTS
                    cash_received
                    DOUBLE PRECISION
                    NOT NULL
                    DEFAULT 0;
                """
            )

            cursor.execute(
                """
                UPDATE trades

                SET
                    cash_given = %s,
                    cash_received = %s

                WHERE
                    trade_id = %s;
                """,
                (
                    float(
                        cash_given
                        or 0.0
                    ),
                    float(
                        cash_received
                        or 0.0
                    ),
                    int(
                        trade_id
                    ),
                ),
            )

        connection.commit()
