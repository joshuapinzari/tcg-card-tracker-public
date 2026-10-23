"""Exact-finishing query contracts; synthetic prices, no live DB/API requests."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest

from database import card_variants, canonical_cards, trades
from services.pipelines.update_tcgplayer_daily_prices import prepare_set_price_rows
from services.pipelines.backfill_tcgplayer_history import history_to_rows


class Cursor:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, params=None): self.calls.append((sql, params))
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, cursor): self.c = cursor
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self.c


def connect_fake(monkeypatch, module, rows):
    cursor = Cursor(rows)
    monkeypatch.setattr(module, "connect", lambda: Connection(cursor))
    return cursor


@pytest.mark.parametrize("finish", ["Normal", "Holofoil", "Reverse Holofoil", "1st Edition Holofoil"])
def test_printing_key_is_explicit(finish):
    assert card_variants.raw_asset_key(123, finish) == (123, finish)


@pytest.mark.parametrize("finish", [None, "", "   ", 1])
def test_missing_finish_is_not_normal(finish):
    with pytest.raises(ValueError):
        card_variants.raw_asset_key(123, finish)


def test_current_price_keys_keep_normal_and_reverse_separate(monkeypatch):
    c = connect_fake(monkeypatch, card_variants, [
        (123, "Normal", date(2026, 10, 19), 20),
        (123, "Reverse Holofoil", date(2026, 10, 19), 70),
    ])
    result = card_variants.get_current_variant_prices([(123,"Normal"), (123,"Reverse Holofoil")])
    assert result[(123,"Normal")][1] == 20
    assert result[(123,"Reverse Holofoil")][1] == 70
    assert result.get((123, "Holofoil")) is None
    sql, params = c.calls[0]
    assert "d.printing = w.printing" in sql
    assert params == [123,"Normal",123,"Reverse Holofoil"]
    assert "tcgplayer_primary_printings" not in sql


def test_historical_trade_prices_group_by_finish(monkeypatch):
    c = connect_fake(monkeypatch, card_variants, [(123,"Normal",20),(123,"Reverse Holofoil",70)])
    day = date(2026,10,19)
    result = card_variants.get_trade_date_variant_prices([(123,"Normal"),(123,"Reverse Holofoil")],day)
    assert result == {(123,"Normal"):20.0,(123,"Reverse Holofoil"):70.0}
    sql, params = c.calls[0]
    assert "GROUP BY w.product_id, w.printing" in sql
    assert "d.printing = w.printing" in sql
    assert params[-2:] == [day, day]
    assert "PERCENTILE_CONT(0.5)" in sql


def test_no_rows_does_not_fall_back_to_primary(monkeypatch):
    connect_fake(monkeypatch, card_variants, [])
    assert card_variants.get_current_variant_prices([(123,"Reverse Holofoil")]) == {}


def test_unknown_old_trade_printing_stays_unknown():
    assert card_variants.raw_item_key({"product_id":123, "market_type":"RAW"}) is None
    assert card_variants.raw_item_key({"product_id":123,"printing":"Normal"}) == (123,"Normal")
    assert card_variants.raw_item_key({"product_id":123,"printing":"Normal","market_type":"PSA_10"}) is None


def test_raw_finish_history_passes_exact_finish_without_primary_join(monkeypatch):
    cursor = Cursor([(date(2026,10,19),"Reverse Holofoil",70)])
    monkeypatch.setattr(canonical_cards,"connect",lambda:Connection(cursor))
    rows=canonical_cards.get_canonical_raw_history_rows(123,printing="Reverse Holofoil")
    assert rows[0][2] == 70
    sql, params=cursor.calls[0]
    assert "printing = %s" in sql and "primary_printings" not in sql
    assert params == (123,"Reverse Holofoil")


def test_omitted_finish_preserves_legacy_research_default(monkeypatch):
    cursor=Cursor([])
    monkeypatch.setattr(canonical_cards,"connect",lambda:Connection(cursor))
    canonical_cards.get_canonical_raw_history_rows(123)
    assert cursor.calls[0][0] == canonical_cards.CANONICAL_RAW_HISTORY_QUERY


def test_search_lists_all_finishes_instead_of_one_primary(monkeypatch):
    c=connect_fake(monkeypatch,trades,[(123,"Poliwag","Skyridge","test",None,"Reverse Holofoil",date(2026,10,19),70)])
    result=trades.search_trade_products("Poliwag")
    assert result[0][5] == "Reverse Holofoil"
    sql, params=c.calls[0]
    assert "DISTINCT ON (d.printing)" in sql
    assert "primary_printings" not in sql
    assert "%Poliwag%" in params


def test_daily_updater_deduplicates_within_not_across_finishes():
    api=[{"date":"2026-10-19","product_id":"123","printing":finish,"market_price_usd":price}
         for finish,price in [("Normal","20"),("Holofoil","40"),("Reverse Holofoil","70")]]
    latest={}
    rows,stats=prepare_set_price_rows(group_id=1,api_rows=api,product_group_map={123:1},latest_dates=latest)
    assert len(rows)==3
    assert {row[2]:row[6] for row in rows} == {"Normal":Decimal("20"),"Holofoil":Decimal("40"),"Reverse Holofoil":Decimal("70")}
    assert len(latest)==3
    repeated,_=prepare_set_price_rows(group_id=1,api_rows=api,product_group_map={123:1},latest_dates=latest)
    assert repeated==[]


def test_latest_normal_date_does_not_skip_new_reverse_history():
    api=[{"date":"2026-10-18","product_id":"123","printing":"Reverse Holofoil","market_price_usd":"70"}]
    rows,_=prepare_set_price_rows(group_id=1,api_rows=api,product_group_map={123:1},
                                  latest_dates={(123,"Normal"):date(2026,10,19)})
    assert len(rows)==1 and rows[0][2]=="Reverse Holofoil"


def test_printing_details_keep_primary_flag_and_own_price(monkeypatch):
    connect_fake(monkeypatch,card_variants,[("Normal",150,date(2026,1,1),date(2026,10,19),20,True),
                                           ("Reverse Holofoil",130,date(2026,2,1),date(2026,10,18),70,False)])
    normal,reverse=card_variants.get_raw_printings(123)
    assert normal.is_primary and not reverse.is_primary
    assert reverse.latest_usd==70 and reverse.observations==130


def test_empty_batch_never_opens_a_database(monkeypatch):
    monkeypatch.setattr(card_variants,"connect",lambda:pytest.fail("Unexpected DB call"))
    assert card_variants.get_current_variant_prices([])=={}
    assert card_variants.get_trade_date_variant_prices([],date.today())=={}
