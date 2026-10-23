"""Renderer unit tests with recorded widgets, not a browser/Streamlit substitute.

These run the real pages against an empty Store and explicitly synthetic data.
Native Streamlit execution is tested separately in test_streamlit_app.py.
"""
from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
import types

import pandas as pd
import pytest
from dashboard.store import Store
from tests.ui_support import FixtureStore

ROOT = Path(__file__).resolve().parents[1]
PAGES = ['Opportunities','Rankings','Weighted Search','Sets','Market','Trades',
         'Compare Cards','Card Analysis','Profit Calculator']
CASES = [(p,{}) for p in PAGES] + [
    ('Opportunities', {'opportunities_view': view}) for view in
    ['Heading Up','Strong Era Cards','Recovery','Suspected Lows','Strong Eras & Sets','All finishes & prices']
] + [('Market', {'market_view':'Card screener'}),
     ('Trades', {'trade_view':'Review Trades'}), ('Trades', {'trade_view':'Model'}),
     ('Card Analysis', {'analysis_view':'Graded Deep Dive'})]


class Rerun(Exception):
    pass


class Recorder(types.ModuleType):
    def __init__(self, inputs=None, state=None):
        super().__init__('streamlit')
        self.inputs = inputs or {}
        self.session_state = state if state is not None else {}
        self.events = []
        self.keys = set()
        self.sidebar = self

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def _widget(self, kind, label, value, **kwargs):
        key = kwargs.get('key')
        if key:
            assert key not in self.keys, f'Duplicate widget key: {key}'
            self.keys.add(key)
        self.events.append((kind, label, value, kwargs))
        value = self.inputs.get(key, self.session_state.get(key, value))
        if kwargs.get('disabled'):
            assert not (kind=='button' and value), 'Disabled button was invoked'
        if key and kind != 'button': self.session_state[key] = value
        return value

    def selectbox(self, label, options, index=0, **kwargs):
        options = list(options)
        formatter = kwargs.get('format_func', str)
        for option in options: formatter(option)
        value = options[index] if index is not None and options else None
        value = self._widget('selectbox',label,value,options=options,**kwargs)
        assert value is None or value in options, (label,value,options)
        return value

    def multiselect(self, label, options, default=None, **kwargs):
        return self._widget('multiselect',label,default or [],options=list(options),**kwargs)

    def number_input(self,label, *args, value=0., **kwargs):
        return self._widget('number_input',label,value,**kwargs)

    def text_input(self,label,value='',**kwargs): return self._widget('text_input',label,value,**kwargs)
    def text_area(self,label,value='',**kwargs): return self._widget('text_area',label,value,**kwargs)
    def checkbox(self,label,value=False,**kwargs): return self._widget('checkbox',label,value,**kwargs)
    def date_input(self,label,value=None,**kwargs): return self._widget('date_input',label,value,**kwargs)
    def slider(self,label,min_value=None,max_value=None,value=None,**kwargs): return self._widget('slider',label,value,**kwargs)
    def button(self,label,**kwargs): return self._widget('button',label,False,**kwargs)
    def columns(self,spec,**kwargs): return [self]*(spec if isinstance(spec,int) else len(spec))
    def expander(self,*args,**kwargs): return self
    def spinner(self,*args,**kwargs): return self
    def rerun(self): raise Rerun()
    def __getattr__(self,name):
        if name.startswith('__'): raise AttributeError(name)
        return lambda *args,**kwargs: self.events.append((name,*args,kwargs))


@contextmanager
def page_modules(monkeypatch, recorder):
    """Restore module cache after each test; never poison native AppTest imports."""
    prefixes = ('dashboard.pages', 'dashboard.ui')
    saved = {k:v for k,v in sys.modules.items() if k.startswith(prefixes)}
    for key in saved: sys.modules.pop(key)
    import dashboard
    old_attrs = {k:dashboard.__dict__.get(k) for k in ['pages','ui']}
    for k in old_attrs: dashboard.__dict__.pop(k,None)
    with monkeypatch.context() as m:
        m.setitem(sys.modules,'streamlit',recorder)
        try:
            yield
        finally:
            for key in list(sys.modules):
                if key.startswith(prefixes): sys.modules.pop(key)
            sys.modules.update(saved)
            for k,v in old_attrs.items():
                if v is not None: setattr(dashboard,k,v)
                else: dashboard.__dict__.pop(k,None)


def run_app(monkeypatch,recorder,store):
    with page_modules(monkeypatch,recorder):
        monkeypatch.setattr(Store,'from_environment',classmethod(lambda cls: store))
        exec(compile((ROOT/'app.py').read_text(),str(ROOT/'app.py'),'exec'),{'__name__':'__test_app__'})


@pytest.mark.parametrize('page,inputs',CASES)
@pytest.mark.parametrize('populated',[False,True])
def test_same_app_pages_empty_and_populated(monkeypatch,page,inputs,populated):
    values={'main_dashboard_page':page, **inputs}
    if populated:
        values.update(graded_query='',finish_query='Sy',analysis_query='',
            basket_GIVEN_query='Sy',basket_RECEIVED_query='Sy',compare_query_0='Sy',
            compare_query_1='Sy',sets_compare=['Synthetic Set'])
    rec=Recorder(values)
    store=FixtureStore() if populated else Store()
    run_app(monkeypatch,rec,store)
    assert any(e[0]=='header' for e in rec.events)
    assert not any(e[0] in {'error','exception'} for e in rec.events)
    for event in rec.events:
        if event[0]=='selectbox':
            assert all('chinese' not in str(x).lower() for x in event[-1].get('options',[]))
        if not populated and event[0]=='dataframe': assert event[1].empty
        if not populated and event[0]=='plotly_chart': assert len(event[1].data)==0


def test_analysis_reverse_uses_own_history_not_normal_score(monkeypatch):
    rec=Recorder({'main_dashboard_page':'Card Analysis','analysis_printing':'Reverse Holofoil'})
    store=FixtureStore()
    run_app(monkeypatch,rec,store)
    assert (1,'RAW','Reverse Holofoil') in store.reads
    assert any(e[0]=='metric' and e[1]=='Frozen V3 (baseline)' and e[2]=='\u2014' for e in rec.events)
    assert any(e[0]=='metric' and e[1]=='Current RAW (USD)' and e[2]=='$70.00' for e in rec.events)


def test_analysis_nan_regime_is_labelled_fallback(monkeypatch):
    rec=Recorder({'main_dashboard_page':'Card Analysis'})
    run_app(monkeypatch,rec,FixtureStore())
    assert any(e[0]=='metric' and e[1]=='Regime' and e[2]=='V3 fallback - e-Card' for e in rec.events)


def test_adding_reverse_trade_retains_finish_and_manual_currency_value(monkeypatch):
    rec=Recorder({'main_dashboard_page':'Trades','basket_GIVEN_query':'Sy','basket_GIVEN_card':1,
                  'basket_GIVEN_value':65.,'basket_GIVEN_quantity':2,'basket_GIVEN_add':True})
    with pytest.raises(Rerun): run_app(monkeypatch,rec,FixtureStore())
    item=rec.session_state['basket_GIVEN'][0]
    assert item['printing']=='Reverse Holofoil' and item['agreed_unit_value']==65.
    assert item['quantity']==2 and not item['signal_available']


def test_currency_change_with_cash_only_is_blocked(monkeypatch):
    rec=Recorder({'main_dashboard_page':'Trades','trade_currency':'USD'},
                 {'basket_currency':'GBP','basket_GIVEN_cash':20.})
    run_app(monkeypatch,rec,FixtureStore())
    assert any(e[0]=='warning' and 'currency' in e[1] for e in rec.events)
    assert rec.session_state['basket_GIVEN_cash']==20.


def test_saving_backdated_trade_uses_historical_baseline_without_current_signal(monkeypatch):
    from datetime import date
    from dashboard.trade_logic import make_item
    store=FixtureStore()
    raw=store.search('Sy')[0]
    given=make_item(raw,side='GIVEN',quantity=1,unit_value=20.,
        intelligence=store.intelligence(1,'Normal'),research=store.opportunities().frame.iloc[0].to_dict())
    reverse=make_item(store.search('Sy')[1],side='RECEIVED',quantity=1,unit_value=60.)
    state={'basket_GIVEN':[given],'basket_RECEIVED':[reverse],'basket_currency':'GBP'}
    rec=Recorder({'main_dashboard_page':'Trades','trade_save':True,'trade_date':date(2025,1,1)},state)
    run_app(monkeypatch,rec,store)
    assert len(store.saved)==1
    saved=store.saved[0]
    assert saved['currency']=='GBP'
    assert [x['market_unit_usd_at_trade'] for x in saved['items']]==[20.,60.]
    assert not any(x['signal_available'] for x in saved['items'])
    assert state['basket_GIVEN'][0]['signal_available']


def test_explicit_advanced_trade_button_uses_real_analysis_presenter(monkeypatch):
    from dashboard.trade_logic import make_item
    store=FixtureStore()
    state={'basket_GIVEN':[make_item(store.search('Sy')[0],side='GIVEN',quantity=1,unit_value=20.)],
           'basket_RECEIVED':[make_item(store.search('Sy')[1],side='RECEIVED',quantity=1,unit_value=60.)],
           'basket_currency':'GBP'}
    rec=Recorder({'main_dashboard_page':'Trades','trade_advanced':True},state)
    run_app(monkeypatch,rec,store)
    assert 'advanced_trade_result' in state
    assert any(e[0]=='metric' and e[1]=='90D descriptive outlook difference' for e in rec.events)
