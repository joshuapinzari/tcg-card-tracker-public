"""Manual scenario calculator; never inserts a market observation."""
import streamlit as st
from dashboard.logic import money, percent, profit_scenario


def render(store):
    st.header('Profit Calculator')
    st.caption('A scenario using values you enter, not a model prediction. Nothing is stored or sent to a provider.')
    currency = st.selectbox('Currency', ['GBP', 'USD'], key='profit_currency')
    a, b, c = st.columns(3)
    purchase = a.number_input('Purchase price per card', min_value=0.0, value=0.0, key='profit_purchase')
    sale = b.number_input('Assumed sale price per card', min_value=0.0, value=0.0, key='profit_sale')
    quantity = int(c.number_input('Quantity', min_value=1, value=1, step=1, key='profit_quantity'))
    with st.expander('Costs', expanded=True):
        a, b, c = st.columns(3)
        grading = a.number_input('Grading per card', min_value=0.0, value=0.0, key='profit_grading')
        fees = b.number_input('Selling fee (%)', min_value=0.0, max_value=100.0, value=0.0, key='profit_fees')
        extras = c.number_input('Total additional costs', min_value=0.0, value=0.0, key='profit_extras')
    result = profit_scenario(purchase=purchase, sale=sale, quantity=quantity, grading=grading, fee_percent=fees, extras=extras)
    a, b, c, d = st.columns(4)
    entered = any([purchase, sale, grading, fees, extras])
    a.metric('Total outlay incl. fees', money(result['invested'], currency) if entered else '\u2014')
    b.metric('Gross sale value', money(result['gross'], currency) if entered else '\u2014')
    c.metric('Scenario profit', money(result['profit'], currency) if entered else '\u2014')
    d.metric('ROI', percent(result['roi']) if entered else '\u2014')
