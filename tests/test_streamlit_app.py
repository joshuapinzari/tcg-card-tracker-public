"""Native Streamlit execution. Requires the real SDK, installed by CI and setup.

These are not screenshot tests: AppTest runs the real Streamlit ScriptRunner,
widgets and session state. No browser or real market database is required.
"""
import importlib.util
import json
from pathlib import Path
import pytest
from dashboard.store import Store
from tests.ui_support import FixtureStore

pytestmark = pytest.mark.skipif(importlib.util.find_spec('streamlit') is None,
                               reason='Native Streamlit SDK is not installed')
APP = str(Path(__file__).resolve().parents[1] / 'app.py')
PAGES = ['Opportunities','Rankings','Weighted Search','Sets','Market','Trades',
         'Compare Cards','Card Analysis','Profit Calculator']
SUBVIEWS = [('Opportunities','opportunities_view',x) for x in
    ['Heading Up','Strong Era Cards','Recovery','Suspected Lows','Strong Eras & Sets','All finishes & prices']]
SUBVIEWS += [('Market','market_view','Card screener'), ('Trades','trade_view','Review Trades'),
             ('Trades','trade_view','Model'), ('Card Analysis','analysis_view','Graded Deep Dive')]


def new_app(monkeypatch, fixture=None):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('TCG_DATA_MODE','empty')
    if fixture is not None:
        monkeypatch.setattr(Store,'from_environment',classmethod(lambda cls: fixture))
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert len(at.exception)==0, [e.message for e in at.exception]
    return at


def healthy(at):
    assert len(at.exception)==0, [e.message for e in at.exception]
    assert len(at.error)==0
    for select in at.selectbox:
        assert not any('chinese' in x.lower() for x in select.options)


@pytest.mark.parametrize('page',PAGES)
def test_native_empty_navigation(monkeypatch,page):
    at = new_app(monkeypatch)
    at.selectbox(key='main_dashboard_page').select(page).run()
    healthy(at)
    for frame in at.dataframe:
        assert frame.value.empty
    for plot in at.get('plotly_chart'):
        assert json.loads(plot.proto.spec)['data']==[]


@pytest.mark.parametrize('page,key,value',SUBVIEWS)
def test_native_empty_subviews(monkeypatch,page,key,value):
    at = new_app(monkeypatch)
    at.selectbox(key='main_dashboard_page').select(page).run()
    at.selectbox(key=key).select(value).run()
    healthy(at)


@pytest.mark.parametrize('page',PAGES)
def test_native_populated_navigation_uses_same_pages(monkeypatch,page):
    at = new_app(monkeypatch,FixtureStore())
    at.selectbox(key='main_dashboard_page').select(page).run()
    healthy(at)


def test_native_finish_switch_changes_price_not_borrowed_score(monkeypatch):
    store=FixtureStore()
    at=new_app(monkeypatch,store)
    at.selectbox(key='main_dashboard_page').select('Card Analysis').run()
    at.selectbox(key='analysis_printing').select('Reverse Holofoil').run()
    healthy(at)
    metrics={m.label:m.value for m in at.metric}
    assert metrics['Current RAW (USD)']=='$70.00'
    assert metrics['Frozen V3 (baseline)']=='\u2014'
    assert (1,'RAW','Reverse Holofoil') in store.reads


def test_native_trade_add_and_remove_preserves_finish(monkeypatch):
    at=new_app(monkeypatch,FixtureStore())
    at.selectbox(key='main_dashboard_page').select('Trades').run()
    at.text_input(key='basket_GIVEN_query').input('Sy').run()
    at.selectbox(key='basket_GIVEN_card').select(1).run()
    at.number_input(key='basket_GIVEN_value').set_value(65.).run()
    at.button(key='basket_GIVEN_add').click().run()
    healthy(at)
    assert at.session_state['basket_GIVEN'][0]['printing']=='Reverse Holofoil'
    assert at.session_state['basket_GIVEN'][0]['agreed_unit_value']==65.
    at.button(key='basket_GIVEN_remove').click().run()
    healthy(at)
    assert at.session_state['basket_GIVEN']==[]


def test_native_profit_calculator_uses_entered_values(monkeypatch):
    at=new_app(monkeypatch)
    at.selectbox(key='main_dashboard_page').select('Profit Calculator').run()
    for key,value in [('profit_purchase',40.),('profit_sale',90.),('profit_quantity',2),
                      ('profit_grading',10.),('profit_fees',10.),('profit_extras',5.)]:
        at.number_input(key=key).set_value(value).run()
    healthy(at)
    assert {m.label:m.value for m in at.metric}['Scenario profit']=='\u00a357.00'
