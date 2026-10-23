"""TCG Card Tracker: one real dashboard, empty until local data is enabled."""
import streamlit as st
from dashboard.store import Store
from dashboard.ui import apply_dashboard_style
from dashboard.pages import analysis, cards, market, opportunities, profit, trades

PAGES = {
    'Opportunities': opportunities.render,
    'Rankings': cards.rankings,
    'Weighted Search': cards.weighted,
    'Sets': market.sets,
    'Market': market.market,
    'Trades': trades.render,
    'Compare Cards': cards.compare,
    'Card Analysis': analysis.render,
    'Profit Calculator': profit.render,
}

st.set_page_config(page_title='TCG Card Tracker', layout='wide', initial_sidebar_state='expanded')
apply_dashboard_style()
store = Store.from_environment()
st.sidebar.markdown('## TCG Tracker')
page = st.sidebar.selectbox('Section', list(PAGES), key='main_dashboard_page')
st.sidebar.caption('Local data enabled' if store.enabled else 'Empty mode - no database or provider access')
with st.sidebar.expander('Data and privacy'):
    st.write('This repository contains code and model specifications, not market prices, saved trades or credentials.')
    st.caption('All sections use the same renderer with or without local data. See RUNNING.md for local setup.')
    if st.button('Refresh view', key='refresh_view'):
        st.rerun()
PAGES[page](store)
