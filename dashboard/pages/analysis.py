"""Exact-finish Card Analysis with the same renderer in empty and local modes."""
import pandas as pd
import streamlit as st

from dashboard.logic import history_coverage, money, number, percent, research_row, text
from dashboard.ui import chart, table, model_guide, no_data


def _scores(row, era):
    a, b, c, d = st.columns(4)
    if row is None:
        a.metric('Frozen V3 (baseline)', '\u2014')
        b.metric('Hybrid V4 (challenger)', '\u2014')
        c.metric('Coverage', '\u2014')
        d.metric('Regime', 'Not scored')
        no_data('No fresh validated research row exists for this exact finish. It does not borrow another finish\'s score.')
        return
    a.metric('Frozen V3 (baseline)', f"{number(row.get('research_score')):.1f}/100" if number(row.get('research_score')) is not None else '\u2014')
    b.metric('Hybrid V4 (challenger)', f"{number(row.get('hybrid_v4_research_score')):.1f}/100" if number(row.get('hybrid_v4_research_score')) is not None else 'Not applicable')
    c.metric('Coverage', percent(row.get('preferred_coverage', row.get('research_coverage'))))
    d.metric('Regime', text(row.get('preferred_regime'), f'V3 fallback - {text(era, "unmapped era")}'))
    st.caption(f"{text(row.get('preferred_model'), 'Frozen V3')} - {text(row.get('preferred_verdict'), 'Research ranking')}. Scores are rankings, not probabilities.")
    for label, key in [('Hybrid V4 factors', 'hybrid_v4_factors'), ('Frozen V3 factors', 'research_factors')]:
        with st.expander(label):
            table(pd.DataFrame(row.get(key) or []))
    a, b, c = st.columns(3)
    a.metric('Expected 90D set alpha', percent(row.get('expected_set_alpha_90d')))
    b.metric('P(+15% in 90D)', percent(row.get('probability_strong_gain_90d')))
    c.metric('P(-15% or worse)', percent(row.get('probability_crash_90d')))
    st.caption('These are historical-analogue estimates, not guaranteed outcomes or separately validated V4 probabilities.')


def render(store):
    st.header('Card Analysis')
    st.caption('Exact-finish market history and research. Missing evidence is never filled with another printing or grade.')
    model_guide()
    view = st.selectbox('View', ['Full Card Analyst', 'Graded Deep Dive'], key='analysis_view')
    catalog = store.catalog()
    query = st.text_input('Filter card or set', key='analysis_query')
    if query and not catalog.empty:
        mask = catalog['name'].astype(str).str.contains(query, regex=False, case=False) | catalog['set_name'].astype(str).str.contains(query, regex=False, case=False)
        catalog = catalog[mask].reset_index(drop=True)
    choices = list(range(len(catalog)))
    chosen = st.selectbox('Select a card', choices, index=0 if choices else None, disabled=not choices,
        format_func=lambda i: f"{catalog.iloc[i]['name']} | {catalog.iloc[i]['set_name']} | #{catalog.iloc[i]['card_number']} | {catalog.iloc[i]['product_id']}",
        key='analysis_card', placeholder='No cards loaded')
    card = catalog.iloc[chosen].to_dict() if chosen is not None else None
    if card is None:
        no_data(
            'No card data is included in the public repository. Connect your own local '
            'PostgreSQL dataset to view price history, model scores and comparisons.'
        )
        return
    if view == 'Graded Deep Dive':
        market = st.selectbox('Grade', ['PSA_9', 'PSA_10', 'ACE_10'], key='analysis_grade')
        history = store.history(int(card['product_id']), market=market) if card else pd.DataFrame(columns=['date', 'price'])
        details = store.graded_intelligence(int(card['product_id']), market) if card else None
        chart(history, key='grade_history')
        table(history)
        if details:
            with st.expander('Provider-native graded evidence', expanded=True):
                st.json(details)
        else:
            no_data('No graded history is loaded. Product-level slab data does not verify a RAW finish.')
        return

    finishes = store.printings(int(card['product_id'])) if card else []
    labels = [x.printing for x in finishes]
    selected = st.selectbox('RAW finish', labels, index=0 if labels else None, disabled=not labels,
                            key='analysis_printing', placeholder='No finish observations')
    finish = next((x for x in finishes if x.printing == selected), None)
    if finish is None:
        no_data('This card has no stored RAW finish observations. No score or price history is inferred from another finish.')
        return
    history = store.history(finish.product_id, printing=selected)
    snapshot = store.opportunities()
    research = research_row(snapshot, finish.product_id, selected) if finish else None
    ci = store.intelligence(finish.product_id, selected) if finish else None
    _scores(research, (ci or {}).get('era'))
    st.subheader('Current market snapshot')
    a, b, c = st.columns(3)
    a.metric('Current RAW (USD)', money(finish.latest_usd if finish else None))
    b.metric('Latest observation', str(finish.latest_date) if finish and finish.latest_date else '\u2014')
    c.metric('Stored observations', finish.observations if finish else 0)
    st.caption('Source market references, not condition-specific NM prices or UK listing valuations.')
    chart(history, title='RAW price history', key='raw_history')
    coverage = history_coverage(history)
    with st.expander('History coverage and descriptive evidence', expanded=not bool(research)):
        st.write(coverage)
        if ci:
            rows = [{'Window': f'{days}D', 'Card': percent(ci.get('card_momentum', {}).get(days)),
                     'Versus set': percent(ci.get('relative_to_set', {}).get(days)),
                     'Versus market': percent(ci.get('relative_to_market', {}).get(days))} for days in [30, 90, 180, 365]]
            table(pd.DataFrame(rows))
        else:
            table(pd.DataFrame(columns=['Window', 'Card', 'Versus set', 'Versus market']))
        st.caption('Missing observations stay missing. A long gap can invalidate a 90D comparison even when total history is large.')
    if ci:
        with st.expander('Legacy evidence (comparison only)'):
            from services.analysis.card_analyst import build_card_assessment
            legacy = build_card_assessment(ci, analogue=research, raw_observations=finish.observations, raw_date=finish.latest_date)
            table(pd.DataFrame(legacy.get('factors') or []))
            st.caption('This older descriptive score is not interchangeable with V3/V4, particularly for alternate finishes.')
