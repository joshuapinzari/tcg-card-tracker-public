"""Pure presentation calculations; independent of Streamlit and PostgreSQL."""
from __future__ import annotations
import math
import pandas as pd


def number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def text(value, fallback='Not available'):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return fallback
    value = str(value).strip()
    return fallback if value.lower() in {'', 'nan', 'none', '<na>'} else value


def percent(value):
    value = number(value)
    return '\u2014' if value is None else f'{value * 100:+.2f}%'


def money(value, currency='USD'):
    value = number(value)
    symbol = {'GBP': '\u00a3', 'USD': '$'}.get(currency, currency + ' ')
    return '\u2014' if value is None else f'{symbol}{value:,.2f}'


def research_row(snapshot, product_id, printing):
    """Fresh exact-finish rows only; a populated normal row cannot fill a reverse."""
    if snapshot.status != 'ready' or snapshot.frame.empty or not printing:
        return None
    frame = snapshot.frame
    if not {'product_id', 'printing'}.issubset(frame.columns):
        return None
    selected = frame[(frame['product_id'] == int(product_id)) & (frame['printing'] == printing)]
    return selected.iloc[0].to_dict() if not selected.empty else None


def filter_opportunities(snapshot, *, query='', era=None, price_floor=0.0, setup=None,
                         order='preferred_ranking_score', ascending=False):
    """Display-only filtering never rescales, rescans, or mutates saved rankings."""
    frame = snapshot.frame.copy(deep=True)
    if frame.empty:
        return frame
    if query:
        mask = pd.Series(False, index=frame.index)
        for col in ['name', 'set_name', 'card_number']:
            if col in frame:
                mask |= frame[col].astype(str).str.contains(query, case=False, regex=False, na=False)
        frame = frame[mask]
    if era and 'era' in frame:
        frame = frame[frame['era'] == era]
    if 'current_price' in frame:
        frame = frame[pd.to_numeric(frame['current_price'], errors='coerce') >= price_floor]
    if setup and 'all_setups' in frame:
        frame = frame[frame['all_setups'].astype(str).str.contains(setup, regex=False, na=False)]
    if order in frame:
        frame = frame.sort_values(order, ascending=ascending, na_position='last', kind='stable')
    return frame


def weighted_ranking(frame, factors):
    """Same within-view percentile weighting as the original Weighted Search."""
    if not factors or len({f['column'] for f in factors}) != len(factors):
        raise ValueError('Choose distinct metrics.')
    weights = [number(f['weight']) for f in factors]
    if any(w is None or w < 0 for w in weights) or sum(weights) <= 0:
        raise ValueError('Use finite nonnegative weights with at least one positive weight.')
    out = frame.copy()
    for f in factors:
        if f['column'] not in out:
            out[f['column']] = pd.Series(dtype=float)
        out[f['column']] = pd.to_numeric(out[f['column']], errors='coerce').replace([math.inf, -math.inf], math.nan)
    out = out.dropna(subset=[f['column'] for f in factors]).copy()
    out['Weighted score'] = 0.0
    for f, weight in zip(factors, weights):
        score_col = f['column'] + ' percentile'
        out[score_col] = out[f['column']].rank(pct=True, method='average', ascending=f['higher']) * 100
        out['Weighted score'] += out[score_col] * weight / sum(weights)
    return out.sort_values('Weighted score', ascending=False, kind='stable').reset_index(drop=True)


def history_coverage(frame):
    """Coverage, not an invented probability or a forward-filled price history."""
    if frame.empty:
        return {'observations': 0, 'first': None, 'last': None, 'largest_missing_days': 0}
    dates = pd.to_datetime(frame['date'], errors='coerce').dropna().drop_duplicates().sort_values()
    if dates.empty:
        return {'observations': 0, 'first': None, 'last': None, 'largest_missing_days': 0}
    gaps = dates.diff().dt.days.dropna()
    return {'observations': len(dates), 'first': dates.iloc[0].date(), 'last': dates.iloc[-1].date(),
            'largest_missing_days': max(0, int(gaps.max()) - 1) if not gaps.empty else 0}


def profit_scenario(*, purchase, sale, quantity=1, grading=0.0, fee_percent=0.0, extras=0.0):
    values = [number(x) for x in [purchase, sale, grading, fee_percent, extras]]
    if any(x is None or x < 0 for x in values) or not isinstance(quantity, int) or quantity < 1 or fee_percent > 100:
        raise ValueError('Costs/prices must be finite and nonnegative; quantity positive; fee 0-100%.')
    purchase, sale, grading, fee_percent, extras = values
    gross = sale * quantity
    fees = gross * fee_percent / 100
    invested = (purchase + grading) * quantity + extras + fees
    profit = gross - invested
    return {'invested': invested, 'gross': gross, 'fees': fees, 'profit': profit,
            'roi': profit / invested if invested > 0 else None}


def trade_review(items, prices):
    """Missing valuations make the relevant basket return unknown, not -100%."""
    out = []
    for item in items:
        market = item.get('market_type') or 'RAW'
        pid = item.get('product_id')
        pid = int(pid) if number(pid) is not None else None
        printing = item.get('printing') if isinstance(item.get('printing'), str) else None
        key = (market, pid, printing)
        value = number(prices.get(key))
        if market == 'RAW' and not printing:
            value = None
        # An explicit finish on a slab is not a verified provider mapping.
        if market != 'RAW' and printing:
            value = None
        before = number(item.get('market_unit_usd_at_trade'))
        qty = number(item.get('quantity')) or 1.0
        out.append({**item, 'current_usd': value, 'return': value / before - 1 if value is not None and before and before > 0 else None,
                    'baseline_total_usd': before * qty if before is not None else None,
                    'current_total_usd': value * qty if value is not None else None})
    summary = {}
    for side in ['GIVEN', 'RECEIVED']:
        rows = [r for r in out if r['side'] == side]
        complete = bool(rows) and all(r['baseline_total_usd'] is not None and r['current_total_usd'] is not None for r in rows)
        initial = sum(r['baseline_total_usd'] or 0 for r in rows)
        current = sum(r['current_total_usd'] or 0 for r in rows)
        summary[side] = current / initial - 1 if complete and initial > 0 else None
    summary['alpha'] = (summary['RECEIVED'] - summary['GIVEN']) if all(summary[s] is not None for s in ['GIVEN', 'RECEIVED']) else None
    return pd.DataFrame(out), summary
