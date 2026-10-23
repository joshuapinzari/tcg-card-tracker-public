"""Small, independently testable trade-adapter functions."""
from __future__ import annotations
from copy import deepcopy
from datetime import date
from dashboard.logic import number


def make_item(row, *, side, quantity, unit_value, intelligence=None, research=None):
    if side not in {'GIVEN', 'RECEIVED'}:
        raise ValueError('Choose a valid trade side.')
    if not isinstance(quantity, int) or quantity < 1:
        raise ValueError('Quantity must be a positive integer.')
    value = number(unit_value)
    if value is None or value < 0:
        raise ValueError('Local unit value must be finite and nonnegative.')
    market = row.get('market_type', 'RAW')
    printing = row.get('printing') if isinstance(row.get('printing'), str) else None
    if market == 'RAW' and not printing:
        raise ValueError('A RAW trade item requires an exact finish.')
    if market not in {'RAW', 'PSA_9', 'PSA_10', 'ACE_10'}:
        raise ValueError('Unsupported trade market.')
    item = {**deepcopy(row), 'side': side, 'quantity': quantity,
            'agreed_unit_value': value, 'market_type': market, 'printing': printing,
            'market_unit_usd_at_trade': number(row.get('current_market_usd')), 'signal_available': False}
    # Do not map a product-only slab series onto a chosen RAW finish.
    ci = (intelligence or {}) if market == 'RAW' else {}
    momentum = ci.get('card_momentum') or {}
    for window in [30, 90, 180]:
        item[f'momentum_{window}'] = number(momentum.get(window))
    for window in [30, 90]:
        item[f'ci_momentum_{window}'] = item[f'momentum_{window}']
    for name, source in [('set', 'relative_to_set'), ('market', 'relative_to_market')]:
        item[f'ci_relative_{name}_90'] = number((ci.get(source) or {}).get(90))
        item[f'relative_{name}_90'] = item[f'ci_relative_{name}_90']
    item['ci_clean_recovery'] = bool((ci.get('recovery') or {}).get('clean_recovery', False))
    if research and market == 'RAW' and research.get('printing') == printing and int(research.get('product_id', -1)) == int(row['product_id']):
        item.update(signal_available=True,
                    set_signal_percentile=number(research.get('signal_percentile')),
                    market_signal_percentile=number(research.get('market_percentile')),
                    market_regime=research.get('market_regime'))
        pct = item['set_signal_percentile']
        item['ci_signal_percentile'] = pct
        item['ci_top10_overheating'] = pct is not None and pct >= 0.9
    return item


def draft_key(items, cash_given, cash_received, currency):
    """A changed basket must not retain an older analysis."""
    import hashlib
    import json
    payload = {'items': items, 'cash_given': cash_given, 'cash_received': cash_received, 'currency': currency}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def saved_items(items, baselines, trade_date, *, today=None):
    """Date-specific baselines, without attaching today's signals to a past trade."""
    today = today or date.today()
    result = deepcopy(items)
    for item in result:
        key = (item['market_type'], int(item['product_id']), item.get('printing'))
        item['market_unit_usd_at_trade'] = baselines.get(key)
        if trade_date < today:
            item['signal_available'] = False
            for name in list(item):
                if (name.startswith(('momentum_', 'set_momentum_', 'market_momentum_',
                                     'relative_', 'ci_')) or name in {
                        'set_signal_percentile', 'market_signal_percentile',
                        'overheat_level', 'market_regime'}):
                    item[name] = None
    return result
