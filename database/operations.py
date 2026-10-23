"""Small shared PostgreSQL helpers.

The original project used this module for a legacy provider-specific catalogue and
observation store. Production market data now lives in the TCGPlayer/TickerMint
and provider-history schemas, so this file only owns the small generic ``cards``
table still used by supplemental cards and historical trade rows.
"""

from __future__ import annotations

import os


DATABASE_NAME = os.getenv("TCG_DATABASE_NAME", "tcg_card_tracker_public")


def connect():
    import psycopg
    return psycopg.connect(dbname=DATABASE_NAME)


def initialise_database() -> None:
    """Create the minimal shared schema needed by the dashboard."""
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    card_id SERIAL PRIMARY KEY,
                    game VARCHAR(50) NOT NULL,
                    name VARCHAR(150) NOT NULL,
                    set_name VARCHAR(150),
                    card_number VARCHAR(50),
                    language VARCHAR(50) NOT NULL,
                    UNIQUE (game, name, set_name, card_number, language)
                );
                """
            )
        connection.commit()
