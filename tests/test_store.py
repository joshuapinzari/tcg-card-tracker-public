"""Exercise the real data adapter without a database or provider connection."""
from datetime import date
import sys
import types
import socket

import pandas as pd
import pytest
from dashboard.store import Store, local_mode, GRADED_COLUMNS


def forbidden(*args,**kwargs):
    raise AssertionError('Unexpected database, network, or full-scanner access')


@pytest.mark.parametrize('mode', [None,'','empty','0','false','LOCAL_TYPO'])
def test_empty_mode_is_fail_closed(monkeypatch,mode):
    if mode is None:monkeypatch.delenv('TCG_DATA_MODE',raising=False)
    else:monkeypatch.setenv('TCG_DATA_MODE',mode)
    monkeypatch.setenv('TCG_PUBLIC_SHOWCASE','0')
    monkeypatch.setenv('POKEMON_PRICE_TRACKER_API_KEY','synthetic-test-not-a-secret')
    monkeypatch.setenv('TCG_API_DATA_ACCESS','1')
    assert not local_mode()
    assert not Store.from_environment().enabled


def test_no_default_read_write_or_network_even_when_keys_and_cache_exist(monkeypatch,tmp_path):
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setenv('TCG_DATABASE_NAME','some_existing_private_database')
    import services.analysis.opportunity_snapshot as cache
    monkeypatch.setattr(cache,'load_snapshot',forbidden)
    db=types.ModuleType('database.card_intelligence')
    db.get_card_intelligence=forbidden
    monkeypatch.setitem(sys.modules,'database.card_intelligence',db)
    store=Store()
    assert store.catalog().empty
    assert store.graded_snapshot().empty
    assert store.history(1,printing='Normal').empty
    assert store.printings(1)==[]
    assert store.intelligence(1,'Normal') is None
    assert store.graded_intelligence(1,'PSA_9') is None
    assert store.opportunities().frame.empty
    assert store.contexts().empty
    assert store.index('market','test').empty
    assert store.screener().empty
    assert store.search('Test')==[]
    assert store.trades().empty and store.trade_items(1).empty
    assert store.current_item_prices([])=={}
    assert store.baseline_prices([],date.today())=={}
    with pytest.raises(PermissionError):store.save_trade()
    with pytest.raises(PermissionError):store.refresh_opportunities()
    with pytest.raises(PermissionError):store.advanced_trade_analysis([],[])


def test_raw_history_keeps_exact_finish_and_filters_grade_provider(monkeypatch):
    raw=types.ModuleType('database.canonical_cards')
    provider=types.ModuleType('database.provider_history')
    calls=[]
    raw.get_canonical_raw_history_rows=lambda pid,printing:(calls.append((pid,printing)) or [(date(2026,1,1),printing,70.)])
    provider.get_product_provider_history=lambda pid:[(date(2026,1,1),'RAW',20,0,None,None),(date(2026,1,1),'PSA_9',90,1,None,None)]
    monkeypatch.setitem(sys.modules,'database.canonical_cards',raw)
    monkeypatch.setitem(sys.modules,'database.provider_history',provider)
    store=Store(enabled=True)
    out=store.history(1,printing='Reverse Holofoil')
    assert calls==[(1,'Reverse Holofoil')]
    assert out.iloc[0]['price']==70
    assert out.iloc[0]['printing']=='Reverse Holofoil'
    assert store.history(1,market='PSA_9')['price'].tolist()==[90]
    with pytest.raises(ValueError):store.history(1)


def test_local_opt_in_is_explicit(monkeypatch):
    monkeypatch.setenv('TCG_DATA_MODE','local')
    assert Store.from_environment().enabled


def test_graded_snapshot_stale_ace_hidden_not_filled(monkeypatch):
    today=date.today()
    from datetime import timedelta
    provider=types.ModuleType('database.provider_history')
    base={x:None for x in GRADED_COLUMNS}
    base.update(product_id=1,name='Test',set_name='Synthetic',PSA_9=90.,PSA_10=200.,ACE_10_GUIDE=100.,ACE_10_LAST_SALE=today-timedelta(days=100))
    provider.get_provider_graded_snapshot_rows=lambda:[tuple(base[c] for c in GRADED_COLUMNS)]
    monkeypatch.setitem(sys.modules,'database.provider_history',provider)
    out=Store(enabled=True).graded_snapshot()
    assert pd.isna(out.iloc[0]['ACE_10'])
    assert pd.isna(out.iloc[0]['PSA 10 / ACE 10'])
    assert out.iloc[0]['ACE_10_GUIDE']==100.


def test_empty_snapshots_have_required_schemas():
    store=Store()
    assert {'name','product_id','printing'}.issubset(store.catalog())
    assert {'PSA_9','PSA_10','ACE_10'}.issubset(store.graded_snapshot())
    assert {'date','price','printing'}.issubset(store.history(1,printing='Normal'))
