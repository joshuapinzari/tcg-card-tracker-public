from datetime import date
import math
import pandas as pd
import pytest

from dashboard.logic import (filter_opportunities, history_coverage, money, number, percent,
                             profit_scenario, research_row, text, trade_review, weighted_ranking)
from dashboard.trade_logic import draft_key, make_item
from services.analysis.opportunity_snapshot import Snapshot


def _snapshot(status='ready'):
    frame = pd.DataFrame([
        {'product_id': 1, 'printing': 'Normal', 'name': 'Test A', 'set_name': 'Synthetic', 'current_price': 20., 'era': 'EX', 'preferred_ranking_score': 80., 'all_setups': 'Heading Up'},
        {'product_id': 1, 'printing': 'Reverse Holofoil', 'name': 'Test A', 'set_name': 'Synthetic', 'current_price': 70., 'era': 'EX', 'preferred_ranking_score': 60., 'all_setups': 'Recovery'},
    ])
    return Snapshot(frame, {}, status, 'test only')


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), -float('inf'), 'nan', 'bad'])
def test_missing_numbers_are_not_zero(value):
    assert number(value) is None
    assert money(value) == '\u2014'
    assert percent(value) == '\u2014'


@pytest.mark.parametrize('value', [None, float('nan'), 'nan', '<NA>', ''])
def test_regime_labels_never_display_nan(value):
    assert text(value, 'V3 fallback') == 'V3 fallback'


def test_snapshot_identity_includes_exact_finish():
    saved = _snapshot()
    assert research_row(saved, 1, 'Normal')['current_price'] == 20
    assert research_row(saved, 1, 'Reverse Holofoil')['current_price'] == 70
    assert research_row(saved, 1, 'Holofoil') is None
    assert research_row(saved, 1, None) is None
    assert research_row(_snapshot('stale'), 1, 'Normal') is None


def test_display_filters_keep_original_scores_and_cache():
    saved = _snapshot()
    original = saved.frame.copy(deep=True)
    result = filter_opportunities(saved, price_floor=50., setup='Recovery', era='EX')
    assert result['preferred_ranking_score'].tolist() == [60.]
    result.loc[:, 'current_price'] = 1
    pd.testing.assert_frame_equal(saved.frame, original)
    # A regex metacharacter is treated as text, not an arbitrary regex.
    assert filter_opportunities(saved, query='[').empty


def test_weighted_rank_reproduces_percentile_method():
    frame = pd.DataFrame({'a': [1, 2, 3, None], 'b': [6, 4, 2, 1]})
    factors = [{'column': 'a', 'higher': True, 'weight': 3.}, {'column': 'b', 'higher': False, 'weight': 1.}]
    out = weighted_ranking(frame, factors)
    assert len(out) == 3
    assert out['Weighted score'].tolist() == pytest.approx([100, 200/3, 100/3])
    assert list(frame.columns) == ['a', 'b']


@pytest.mark.parametrize('weights', [[0, 0], [-1, 2], [float('nan'), 1], [float('inf'), 1]])
def test_bad_weights_fail_explicitly(weights):
    with pytest.raises(ValueError):
        weighted_ranking(pd.DataFrame({'a':[1], 'b':[2]}), [
            {'column':'a', 'higher':True, 'weight':weights[0]},
            {'column':'b', 'higher':False, 'weight':weights[1]}])


def test_duplicate_factors_rejected():
    with pytest.raises(ValueError):
        weighted_ranking(pd.DataFrame(), [{'column':'a','higher':True,'weight':1}]*2)


def test_gap_count_reports_missing_dates_not_endpoint_distance():
    frame = pd.DataFrame({'date':['2026-06-29', '2026-08-27', '2026-08-27'], 'price':[1, 2, 2]})
    assert history_coverage(frame) == {'observations':2, 'first':date(2026,6,29), 'last':date(2026,8,27), 'largest_missing_days':58}
    assert history_coverage(frame.iloc[0:0])['first'] is None


def test_manual_profit_includes_grading_fees_and_extras():
    result = profit_scenario(purchase=40., sale=90., quantity=2, grading=10., fee_percent=10., extras=5.)
    assert result == {'gross':180., 'fees':18., 'invested':123., 'profit':57., 'roi':57/123}
    assert profit_scenario(purchase=0., sale=0.)['roi'] is None


@pytest.mark.parametrize('field,value', [('purchase',-1),('sale',float('nan')),('fee_percent',101),('quantity',0)])
def test_profit_invalid_inputs(field,value):
    kwargs={'purchase':1.,'sale':2.,field:value}
    with pytest.raises(ValueError):profit_scenario(**kwargs)


def test_trade_item_never_borrows_other_finish_score():
    row={'product_id':1,'name':'Test','set_name':'Synthetic','market_type':'RAW','printing':'Reverse Holofoil','current_market_usd':70.}
    research=research_row(_snapshot(),1,'Normal')
    item=make_item(row,side='GIVEN',quantity=2,unit_value=65.,research=research)
    assert item['printing']=='Reverse Holofoil'
    assert item['signal_available'] is False
    assert item['agreed_unit_value']==65.
    assert item['current_market_usd']==70.
    assert row.get('side') is None


def test_trade_item_requires_finish_and_finite_values():
    row={'product_id':1,'name':'Test','set_name':'Synthetic','market_type':'RAW'}
    with pytest.raises(ValueError):make_item(row,side='GIVEN',quantity=1,unit_value=1.)
    row['printing']='Normal'
    with pytest.raises(ValueError):make_item(row,side='GIVEN',quantity=1,unit_value=float('inf'))


def test_product_only_graded_entry_does_not_inherit_raw_evidence():
    item=make_item({'product_id':1,'name':'Test','market_type':'PSA_10'},side='GIVEN',quantity=1,unit_value=100.,intelligence={'card_momentum':{90:0.5}})
    assert item['ci_momentum_90'] is None
    assert item['signal_available'] is False


def test_review_keeps_normal_and_reverse_returns_distinct():
    items=[{'side':'GIVEN','product_id':1,'market_type':'RAW','printing':'Normal','quantity':1,'market_unit_usd_at_trade':20},
           {'side':'RECEIVED','product_id':1,'market_type':'RAW','printing':'Reverse Holofoil','quantity':1,'market_unit_usd_at_trade':50}]
    rows,metrics=trade_review(items,{('RAW',1,'Normal'):22,('RAW',1,'Reverse Holofoil'):70})
    assert metrics['GIVEN']==pytest.approx(.1)
    assert metrics['RECEIVED']==pytest.approx(.4)
    assert metrics['alpha']==pytest.approx(.3)
    _,missing=trade_review(items,{('RAW',1,'Normal'):22})
    assert missing['RECEIVED'] is None and missing['alpha'] is None


def test_unmapped_slab_finish_and_unknown_raw_finish_remain_missing():
    items=[{'side':'GIVEN','product_id':1,'market_type':'RAW','printing':None,'quantity':1,'market_unit_usd_at_trade':20},
           {'side':'RECEIVED','product_id':2,'market_type':'PSA_10','printing':'Reverse Holofoil','quantity':1,'market_unit_usd_at_trade':50}]
    _,metrics=trade_review(items,{('RAW',1,None):100,('PSA_10',2,'Reverse Holofoil'):100})
    assert metrics=={'GIVEN':None,'RECEIVED':None,'alpha':None}


def test_editing_basket_or_cash_invalidates_analysis():
    assert draft_key([],0,0,'GBP')!=draft_key([],1,0,'GBP')
    assert draft_key([],0,0,'GBP')!=draft_key([],0,0,'USD')


def test_backdated_trade_never_attaches_current_signals():
    from dashboard.trade_logic import saved_items
    item={'side':'GIVEN','product_id':1,'market_type':'RAW','printing':'Normal',
          'market_unit_usd_at_trade':40.,'signal_available':True,'momentum_90':.3,
          'relative_set_90':.2,'ci_clean_recovery':True,'market_regime':'Strong',
          'set_signal_percentile':.8,'quantity':1,'agreed_unit_value':20.}
    baseline={('RAW',1,'Normal'):10.}
    saved=saved_items([item],baseline,date(2026,1,1),today=date(2026,2,1))[0]
    assert saved['market_unit_usd_at_trade']==10.
    assert not saved['signal_available'] and saved['momentum_90'] is None
    assert saved['market_regime'] is None and saved['ci_clean_recovery'] is None
    assert item['signal_available']  # original live draft remains unmodified
    same_day=saved_items([item],baseline,date(2026,2,1),today=date(2026,2,1))[0]
    assert same_day['signal_available'] and same_day['momentum_90']==.3
