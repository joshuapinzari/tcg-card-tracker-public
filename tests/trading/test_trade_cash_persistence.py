from datetime import date

import database.trades as trades


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return (123,)

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def test_create_trade_persists_cash_in_same_insert(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(trades, "initialise_trade_schema", lambda: None)
    monkeypatch.setattr(trades, "connect", lambda: connection)

    trade_id = trades.create_trade(
        trade_date=date(2026, 10, 13),
        currency="GBP",
        notes="cash test",
        items=[],
        cash_given=150.0,
        cash_received=25.0,
    )

    assert trade_id == 123
    assert connection.committed is True
    sql, params = connection.cursor_obj.calls[0]
    assert "cash_given" in sql
    assert "cash_received" in sql
    assert params[-2:] == (150.0, 25.0)


def test_trade_schema_and_list_query_include_cash_columns():
    source = open(trades.__file__).read()
    assert "cash_given DOUBLE PRECISION NOT NULL DEFAULT 0" in source
    assert "cash_received DOUBLE PRECISION NOT NULL DEFAULT 0" in source
    assert "COALESCE(t.cash_given, 0)" in source
    assert "COALESCE(t.cash_received, 0)" in source
