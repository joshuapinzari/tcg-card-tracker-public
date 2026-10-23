from datetime import date
from decimal import Decimal

from services.pipelines.update_tcgplayer_daily_prices import (
    prepare_set_price_rows,
)
from sources.tickermint.client import (
    TickerMintClient,
)


class FakeResponse:
    status_code = 200
    headers = {}

    text = (
        "date,product_id,name,set,number,printing,"
        "market_price_usd,url\n"
        "2026-10-13,146698,Mewtwo GX (Secret Shining),"
        "Shining Legends,78/73,Holofoil,824.69,"
        "https://tickermint.cards/card/146698\n"
    )

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(
            (url, kwargs)
        )
        return FakeResponse()


def test_set_csv_client_parses_rows():
    client = TickerMintClient(
        min_interval=0.0
    )
    client.session = FakeSession()

    rows = client.get_set_prices(
        2054
    )

    assert len(rows) == 1
    assert rows[0]["product_id"] == "146698"
    assert rows[0]["printing"] == "Holofoil"
    assert rows[0]["market_price_usd"] == "824.69"
    assert client.request_count == 1


def test_prepare_set_price_rows_is_append_only():
    latest_dates = {
        (146698, "Holofoil"): date(
            2026,
            10,
            11,
        )
    }

    rows, stats = prepare_set_price_rows(
        group_id=2054,
        api_rows=[
            {
                "date": "2026-10-13",
                "product_id": "146698",
                "printing": "Holofoil",
                "market_price_usd": "830.25",
            },
            {
                "date": "2026-10-11",
                "product_id": "146698",
                "printing": "Holofoil",
                "market_price_usd": "824.69",
            },
            {
                "date": "2026-10-13",
                "product_id": "999999",
                "printing": "Holofoil",
                "market_price_usd": "1.00",
            },
        ],
        product_group_map={
            146698: 2054,
        },
        latest_dates=latest_dates,
    )

    assert len(rows) == 1

    row = rows[0]

    assert row[0] == 146698
    assert row[1] == date(
        2026,
        10,
        13,
    )
    assert row[2] == "Holofoil"
    assert row[6] == Decimal(
        "830.25"
    )
    assert row[9] == "tcgplayer"
    assert row[10] == "tickermint_set_csv"

    assert stats["accepted"] == 1
    assert stats["already_current"] == 1
    assert stats["unknown_product"] == 1

    assert latest_dates[
        (146698, "Holofoil")
    ] == date(
        2026,
        10,
        13,
    )


def test_prepare_set_price_rows_rejects_bad_rows_and_duplicates():
    latest_dates = {}

    rows, stats = prepare_set_price_rows(
        group_id=2054,
        api_rows=[
            {
                "date": "2026-10-13",
                "product_id": "146698",
                "printing": "Holofoil",
                "market_price_usd": "824.69",
            },
            {
                "date": "2026-10-13",
                "product_id": "146698",
                "printing": "Holofoil",
                "market_price_usd": "824.69",
            },
            {
                "date": "not-a-date",
                "product_id": "146698",
                "printing": "Holofoil",
                "market_price_usd": "824.69",
            },
            {
                "date": "2026-10-13",
                "product_id": "146698",
                "printing": "Holofoil",
                "market_price_usd": "0",
            },
            {
                "date": "2026-10-13",
                "product_id": "123456",
                "printing": "Normal",
                "market_price_usd": "5.00",
            },
        ],
        product_group_map={
            146698: 2054,
            123456: 9999,
        },
        latest_dates=latest_dates,
    )

    assert len(rows) == 1
    assert stats["duplicate"] == 1
    assert stats["invalid"] == 2
    assert stats["wrong_group"] == 1
