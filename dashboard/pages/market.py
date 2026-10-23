"""Shared market/set index display, with an explicit local card screener."""
import pandas as pd
import streamlit as st
from dashboard.logic import percent
from dashboard.ui import chart, table, no_data


def market(store):
    st.header('Market')
    st.caption('Broad market context uses the primary-printing research universe, not every RAW finish.')
    view = st.selectbox('View', ['Indices', 'Card screener'], key='market_view')
    if view == 'Card screener':
        a, b = st.columns(2)
        days = a.selectbox('Lookback (days)', [30, 90, 180, 365], index=1, key='screener_days')
        floor = b.number_input('Minimum formation price (USD)', min_value=0.0, value=5.0, key='screener_floor')
        frame = store.screener(days, floor)
        table(frame)
        if frame.empty:
            no_data('No primary-printing observations meet these filters.')
        return
    scopes = store.scopes()
    scope = st.selectbox('Scope', ['market', 'era', 'set'], key='market_scope')
    options = scopes.get(scope, [])
    name = st.selectbox('Index', options, index=0 if options else None, disabled=not options, key='market_index')
    frame = store.index(scope, name) if name else pd.DataFrame(columns=['date', 'index_value'])
    chart(frame, y='index_value', y_label='Index', key='market_chart')
    table(frame)
    if frame.empty:
        no_data('No market indices are loaded. Their absence is not a neutral or negative signal.')


def sets(store):
    st.header('Sets')
    st.caption('Compare stored primary-printing set indices; reverse-only benchmarks are not inferred.')
    names = store.scopes().get('set', [])
    selected = st.multiselect('Sets to compare', names, key='sets_compare', max_selections=6)
    normalise = st.checkbox('Rebase to common start date', value=True, key='sets_rebase')
    frames = []
    for name in selected:
        frame = store.index('set', name).copy()
        if not frame.empty:
            frame['Set'] = name
            frames.append(frame)
    if normalise and frames:
        start = max(f['date'].min() for f in frames)
        frames = [f[f['date'] >= start].copy() for f in frames]
        for frame in frames:
            if not frame.empty:
                baseline = float(frame.iloc[0]['index_value'])
                frame['index_value'] = frame['index_value'] / baseline * 100 if baseline > 0 else float('nan')
    joined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=['date', 'index_value', 'Set'])
    chart(joined, y='index_value', color='Set', y_label='Index', key='sets_chart')
    summary = []
    for frame in frames:
        if not frame.empty:
            first, last = frame.iloc[0], frame.iloc[-1]
            change = float(last['index_value']) / float(first['index_value']) - 1 if float(first['index_value']) > 0 else None
            summary.append({'Set': last['Set'], 'First date': first['date'], 'Last date': last['date'], 'Change': percent(change), 'Constituents': last.get('constituent_count')})
    table(pd.DataFrame(summary), ['Set', 'First date', 'Last date', 'Change', 'Constituents'])
    if joined.empty:
        no_data('Select sets once your own market indices have been built.')
