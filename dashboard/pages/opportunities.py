"""Saved rankings and exact-finish prices. No scanner work on navigation."""
import pandas as pd
import streamlit as st

from dashboard.logic import filter_opportunities
from dashboard.ui import model_guide, table, no_data

VIEWS = ['Best Opportunities', 'Heading Up', 'Strong Era Cards', 'Recovery', 'Suspected Lows', 'Strong Eras & Sets', 'All finishes & prices']
RANK_COLUMNS = ['name', 'set_name', 'card_number', 'printing', 'current_price', 'preferred_model', 'preferred_ranking_score', 'research_ranking_score', 'hybrid_v4_ranking_score', 'preferred_coverage', 'setup_label', 'expected_set_alpha_90d', 'probability_strong_gain_90d', 'probability_crash_90d']


def render(store):
    st.header('Opportunities')
    st.caption('Saved research rankings, not promised returns. Prices are source USD market references, not NM UK valuations.')
    model_guide()
    view = st.selectbox('View', VIEWS, key='opportunities_view')
    if view == 'All finishes & prices':
        query = st.text_input('Search cards or sets', key='finish_query')
        rows = store.search(query, 'RAW')
        table(pd.DataFrame(rows), ['name', 'set_name', 'card_number', 'printing', 'current_market_usd', 'price_date'])
        if not rows:
            no_data('Enter at least two characters to search your own stored finish-specific prices. The public copy contains none.')
        return
    if view == 'Strong Eras & Sets':
        context = store.contexts()
        table(context, ['scope_type', 'scope_name', 'return_30d', 'return_90d', 'vs_market_90d', 'breadth', 'strengthening'])
        if context.empty:
            no_data('No set or era index data is loaded.')
        return

    snapshot = store.opportunities()
    a, b, c = st.columns([3, 2, 1])
    query = a.text_input('Name or set contains', key='opportunity_query')
    eras = sorted(snapshot.frame['era'].dropna().unique()) if 'era' in snapshot.frame else []
    era = b.selectbox('Era', ['All', *eras], key='opportunity_era')
    floor = c.number_input('Minimum price (USD)', min_value=0.0, value=0.0, key='opportunity_floor')
    sort_options = {'Best applicable ranking': ('preferred_ranking_score', False),
                    'Frozen V3 — baseline': ('research_ranking_score', False), 'Hybrid V4 — challenger': ('hybrid_v4_ranking_score', False),
                    'Expected 90D set alpha': ('expected_set_alpha_90d', False),
                    'Lowest crash probability': ('probability_crash_90d', True), 'Price': ('current_price', True)}
    order, ascending = sort_options[st.selectbox('Sort by', list(sort_options), key='opportunity_sort')]
    setup = {'Heading Up': 'Heading Up', 'Strong Era Cards': 'Strong Era', 'Recovery': 'Recovery', 'Suspected Lows': 'Suspected Low'}.get(view)
    frame = filter_opportunities(snapshot, query=query, era=None if era == 'All' else era,
                                 price_floor=floor, setup=setup, order=order, ascending=ascending)
    if snapshot.status != 'ready' and not frame.empty:
        st.warning('These are older saved rankings: ' + snapshot.reason)
    table(frame, RANK_COLUMNS)
    st.caption(f'{len(frame):,} saved results. Changing filters never recalculates the model.')
    if frame.empty:
        no_data(snapshot.reason or 'No saved rankings match the filters.')
    with st.expander('Research and refresh', expanded=False):
        st.write('Alternate finishes do not inherit another finish\'s score. Historical evidence is not prospective performance.')
        st.caption('Build once from your own local history; no provider requests are made by this command.')
        st.code('python -m services.pipelines.refresh_opportunity_snapshot', language='bash')
        if st.button('Build / refresh saved scan', disabled=not store.enabled, key='opportunity_build'):
            with st.spinner('Building from local history...'):
                store.refresh_opportunities()
            st.rerun()
