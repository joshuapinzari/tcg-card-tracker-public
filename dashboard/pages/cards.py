"""Graded rankings, user-weighted search and exact-series comparison."""
import pandas as pd
import streamlit as st

from dashboard.logic import weighted_ranking
from dashboard.ui import table, chart, no_data

GRADES = ['PSA_9', 'PSA_10', 'ACE_10']
METRICS = ['PSA_9', 'PSA_10', 'ACE_10', 'PSA 10 / ACE 10', 'PSA_10_SALES_90', 'ACE_10_SALES_90', 'ACE_10_HISTORY_POINTS']
DISPLAY = ['name', 'set_name', 'card_number', 'PSA_9', 'PSA_10', 'ACE_10', 'ACE_10_AGE', 'PSA_10_SALES_90', 'ACE_10_SALES_90']


def _filtered(frame):
    left, right = st.columns(2)
    query = left.text_input('Card name contains', key='graded_query')
    sets = sorted(frame['set_name'].dropna().unique()) if 'set_name' in frame else []
    selection = right.multiselect('Sets', sets, key='graded_sets')
    out = frame.copy()
    if query:
        out = out[out['name'].astype(str).str.contains(query, case=False, regex=False, na=False)]
    if selection:
        out = out[out['set_name'].isin(selection)]
    return out


def rankings(store):
    st.header('Rankings')
    st.caption('Provider-backed graded market references in USD. Stale ACE sale references remain unavailable, not zero.')
    frame = _filtered(store.graded_snapshot())
    a, b = st.columns(2)
    metric = a.selectbox('Rank by', METRICS, key='rank_metric')
    low_first = b.checkbox('Lowest first', key='rank_direction')
    if metric in frame:
        frame = frame.sort_values(metric, ascending=low_first, na_position='last', kind='stable')
    table(frame, list(dict.fromkeys([*DISPLAY, metric])))
    if frame.empty:
        no_data()


def weighted(store):
    st.header('Weighted Search')
    st.caption('A custom descriptive percentile ranking, not the frozen V3/V4 research model.')
    frame = _filtered(store.graded_snapshot())
    count = st.slider('Number of factors', 2, 4, 2, key='factor_count')
    factors = []
    for i in range(count):
        a, b, c = st.columns([3, 2, 1])
        column = a.selectbox(f'Factor {i + 1}', METRICS, index=i, key=f'factor_{i}')
        higher = b.selectbox('Direction', ['Higher is better', 'Lower is better'], key=f'direction_{i}') == 'Higher is better'
        weight = c.number_input('Weight', min_value=0.0, value=1.0, key=f'weight_{i}')
        factors.append({'column': column, 'higher': higher, 'weight': weight})
    try:
        ranked = weighted_ranking(frame, factors)
    except ValueError as error:
        st.warning(str(error))
        return
    table(ranked, ['name', 'set_name', 'Weighted score', *[f['column'] for f in factors]])
    if ranked.empty:
        no_data('No cards have all selected metrics. Missing evidence is not replaced with zero.')


def _choose_series(store, slot):
    market = st.selectbox('Market', ['RAW', *GRADES], key=f'compare_market_{slot}')
    query = st.text_input('Search card', key=f'compare_query_{slot}')
    rows = store.search(query, market)
    choice = st.selectbox('Card and finish', range(len(rows)), index=0 if rows else None,
        format_func=lambda i: f"{rows[i]['name']} | {rows[i]['set_name']} | {rows[i].get('printing') or market}",
        disabled=not rows, key=f'compare_card_{slot}', placeholder='No cards loaded')
    if choice is None:
        return None, pd.DataFrame(columns=['date', 'price'])
    row = rows[choice]
    history = store.history(row['product_id'], market=market, printing=row.get('printing'))
    return row, history


def compare(store):
    st.header('Compare Cards')
    st.caption('Each line keeps its exact card, source and RAW finish. No invented daily prices fill gaps.')
    columns = st.columns(2)
    series = []
    for i, column in enumerate(columns):
        with column:
            row, history = _choose_series(store, i)
        if row is not None and not history.empty:
            history = history.copy()
            history['Card'] = f"{row['name']} | {row.get('printing') or row['market_type']} | {row['product_id']}"
            series.append(history)
    normalise = st.checkbox('Compare percentage change', value=False, key='compare_normalise')
    frame = pd.concat(series, ignore_index=True) if series else pd.DataFrame(columns=['date', 'price', 'Card'])
    if normalise and not frame.empty:
        # Rebase each selected series to its own first positive recorded price.
        frame = frame[frame['price'] > 0].sort_values('date').copy()
        frame['price'] = (frame['price'] / frame.groupby('Card')['price'].transform('first') - 1) * 100
    chart(frame, color='Card', y_label='Change (%)' if normalise else 'USD', key='compare_chart')
    table(frame, ['date', 'Card', 'price'])
    if frame.empty:
        no_data('Choose two cards from your own data to compare them.')
