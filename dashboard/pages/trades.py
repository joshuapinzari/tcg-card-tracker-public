"""One trade-entry component reused for GIVE and RECEIVE, plus persisted review."""
from datetime import date
import pandas as pd
import streamlit as st

from dashboard.logic import money, percent, research_row, trade_review
from dashboard.trade_logic import draft_key, make_item, saved_items
from dashboard.ui import no_data, table
from services.trading.trade_balancer import balance_trade
from services.trading.trade_engine import analyse_trade, trade_card_rows

COLUMNS = ['name', 'set_name', 'card_number', 'market_type', 'printing', 'quantity', 'agreed_unit_value']


def _side(store, side, label, currency):
    key = 'basket_' + side
    st.session_state.setdefault(key, [])
    st.subheader(label)
    market = st.selectbox('Market', ['RAW', 'PSA_9', 'PSA_10', 'ACE_10'], key=key + '_market')
    query = st.text_input('Search card or set', key=key + '_query')
    rows = store.search(query, market)
    selected = st.selectbox('Card / finish', range(len(rows)), index=0 if rows else None,
        format_func=lambda i: f"{rows[i]['name']} | {rows[i]['set_name']} | {rows[i].get('printing') or market}",
        disabled=not rows, key=key + '_card', placeholder='No cards loaded')
    row = rows[selected] if selected is not None else None
    st.caption('Source reference: ' + money(row.get('current_market_usd') if row else None) + ' USD')
    a, b = st.columns(2)
    quantity = int(a.number_input('Quantity', min_value=1, value=1, step=1, key=key + '_quantity'))
    value = b.number_input(f'Your unit value ({currency})', min_value=0.0, value=0.0, key=key + '_value')
    if st.button('Add card to ' + label, disabled=not store.enabled or row is None, key=key + '_add'):
        ci = store.intelligence(row['product_id'], row['printing']) if market == 'RAW' else None
        research = research_row(store.opportunities(), row['product_id'], row.get('printing'))
        st.session_state[key].append(make_item(row, side=side, quantity=quantity, unit_value=value,
                                               intelligence=ci, research=research))
        st.rerun()
    items = st.session_state[key]
    table(pd.DataFrame(items), COLUMNS, key=key + '_table')
    if items:
        which = st.selectbox('Remove item', range(len(items)), format_func=lambda i: f"{i+1}. {items[i]['name']}", key=key + '_remove_choice')
        if st.button('Remove selected', key=key + '_remove'):
            items.pop(which)
            st.rerun()
    cash = st.number_input(f'Cash {label.lower()} ({currency})', min_value=0.0, value=0.0, key=key + '_cash')
    return items, cash


def _builder(store):
    currency = st.selectbox('Local trade currency', ['GBP', 'USD'], key='trade_currency')
    st.caption('Your agreed values determine the cash balance. Source prices are context only; they remain in USD.')
    # Changing the currency cannot silently reinterpret an existing monetary basket.
    previous = st.session_state.get('basket_currency', currency)
    existing = st.session_state.get('basket_GIVEN', []) + st.session_state.get('basket_RECEIVED', [])
    has_values = bool(existing) or any(st.session_state.get('basket_' + side + suffix, 0)
        for side in ['GIVEN', 'RECEIVED'] for suffix in ['_cash', '_value'])
    if previous != currency and has_values:
        st.warning('Clear the current basket before changing its currency. Existing agreed values have not been converted.')
        if st.button('Clear basket', key='trade_currency_clear'):
            st.session_state['basket_GIVEN'] = []
            st.session_state['basket_RECEIVED'] = []
            for side in ['GIVEN', 'RECEIVED']:
                st.session_state['basket_' + side + '_cash'] = 0.0
                st.session_state['basket_' + side + '_value'] = 0.0
            st.session_state['basket_currency'] = currency
            st.rerun()
        return
    st.session_state['basket_currency'] = currency
    left, right = st.columns(2)
    with left:
        given, cash_given = _side(store, 'GIVEN', 'GIVE', currency)
    with right:
        received, cash_received = _side(store, 'RECEIVED', 'RECEIVE', currency)
    balance = balance_trade(given, received, cash_given=cash_given, cash_received=cash_received)
    a, b, c = st.columns(3)
    a.metric('GIVE total', money(balance['given_total'], currency))
    b.metric('RECEIVE total', money(balance['received_total'], currency))
    c.metric('Net to RECEIVE', money(balance['difference'], currency))
    if not given or not received:
        no_data('Both baskets need cards before a comparison is meaningful. Empty values are not a trading recommendation.')
    else:
        risk = analyse_trade(given, received)
        st.subheader('RAW risk comparison')
        st.write(risk.get('verdict', risk.get('reason', 'Insufficient evidence')))
        st.caption('This compares historical overheating risk, not a predicted profit. Cash balancing is displayed separately.')
        rows = trade_card_rows(given, received)
        display = pd.DataFrame(rows).rename(columns={'Market Value USD': f'Local Fair Value ({currency})'})
        table(display)

    key = draft_key(given + received, cash_given, cash_received, currency)
    with st.expander('Graded relative value and 90D outlook details'):
        st.caption('Explicit analysis uses your local provider history and broad grade context. '
                   'This can be expensive; it is not run automatically or on ordinary widget changes.')
        if st.button('Analyse graded evidence', disabled=not store.enabled or not given or not received, key='trade_advanced'):
            with st.spinner('Analysing local graded history...'):
                st.session_state['advanced_trade_result'] = (key, store.advanced_trade_analysis(given, received))
        result = st.session_state.get('advanced_trade_result')
        if result and result[0] == key:
            details = result[1]
            st.write(details['graded']['verdict'])
            from services.trading.graded_trade_engine import graded_trade_card_rows
            table(pd.DataFrame(graded_trade_card_rows(details['graded'])).rename(columns={'Market Value USD': f'Local Fair Value ({currency})'}))
            st.metric('90D descriptive outlook difference', percent(details['hybrid']['edge_90']))
            st.caption('Descriptive history-based outlook, not a validated forward-return forecast.')
        elif result:
            st.info('The basket changed. Re-run analysis to refresh these results.')

    with st.expander('Save trade'):
        trade_date = st.date_input('Trade date', value=date.today(), max_value=date.today(), key='trade_date')
        notes = st.text_area('Notes', key='trade_notes')
        st.caption('Backdated trades use historical price anchors but do not store today\'s research as a past prediction.')
        if st.button('Save trade', disabled=not store.enabled or not given or not received, key='trade_save'):
            items = [dict(x) for x in given + received]
            baselines = store.baseline_prices(items, trade_date)
            items = saved_items(items, baselines, trade_date)
            trade_id = store.save_trade(trade_date=trade_date, currency=currency, notes=notes, items=items,
                cash_given=cash_given, cash_received=cash_received, signal_version='overheating_v2_regime_conditioned')
            st.success(f'Saved trade #{trade_id}.')


def _review(store):
    trades = store.trades()
    table(trades)
    choices = trades['trade_id'].tolist() if not trades.empty else []
    trade_id = st.selectbox('Trade', choices, index=0 if choices else None, disabled=not choices, key='review_trade')
    if trade_id is None:
        no_data('No trades are saved in this public copy.')
        return
    items = store.trade_items(trade_id).to_dict('records')
    rows, metrics = trade_review(items, store.current_item_prices(items))
    table(rows, ['side', 'name', 'market_type', 'printing', 'quantity', 'market_unit_usd_at_trade', 'current_usd', 'return'])
    a, b, c = st.columns(3)
    a.metric('Given card basket return', percent(metrics['GIVEN']))
    b.metric('Received card basket return', percent(metrics['RECEIVED']))
    c.metric('Card trade alpha', percent(metrics['alpha']))
    st.caption('Returns compare stored USD source references and exclude cash, fees and currency moves. '
               'An unknown finish or missing valuation makes the basket return unavailable, not -100%.')


def render(store):
    st.header('Trades')
    view = st.selectbox('View', ['Trade Builder', 'Review Trades', 'Model'], key='trade_view')
    if view == 'Trade Builder':
        _builder(store)
    elif view == 'Review Trades':
        _review(store)
    else:
        st.subheader('How trade analysis works')
        st.write('Current local value and cash are balanced separately from research evidence. '
                 'RAW overheating references, graded relative value and historical momentum are different signals, '
                 'not interchangeable promises of future profit. Missing evidence lowers coverage.')
        st.write('Normal, holo and reverse holo keep separate identities. A product-only PSA/ACE series '
                 'does not establish which RAW finish it matches. No regional watchlist or unsupported price feed is used.')
