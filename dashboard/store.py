"""Single data boundary used by the real dashboard pages.

The default store is empty, including when credentials or old cache files exist.
Only an explicit TCG_DATA_MODE=local enables reads. Providers are never called by
this module: local mode reads the owner's database and saved research only.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import os

import pandas as pd

from services.analysis.opportunity_snapshot import Snapshot

CATALOG_COLUMNS = ['product_id', 'name', 'set_name', 'card_number', 'rarity', 'printing', 'latest_raw_usd', 'latest_raw_date', 'market_observations']
HISTORY_COLUMNS = ['date', 'printing', 'market_type', 'price', 'source']
GRADED_COLUMNS = [
    'product_id', 'name', 'set_name', 'card_number', 'rarity', 'artist',
    'image_url', 'tcgplayer_url', 'set_published_on', 'printing',
    'RAW', 'RAW_DATE', 'PSA_9', 'PSA_9_DATE', 'PSA_10', 'PSA_10_DATE',
    'ACE_10_GUIDE', 'ACE_10_DATE', 'PSA_9_LAST_SALE', 'PSA_10_LAST_SALE', 'ACE_10_LAST_SALE',
    'PSA_9_SALES_30', 'PSA_9_SALES_90', 'PSA_9_SALES_180',
    'PSA_10_SALES_30', 'PSA_10_SALES_90', 'PSA_10_SALES_180',
    'ACE_10_SALES_30', 'ACE_10_SALES_90', 'ACE_10_SALES_180',
    'ACE_10_HISTORY_POINTS', 'ACE_PSA_HIST_MEDIAN', 'ACE_RAW_HIST_MEDIAN',
    'PSA_RAW_HIST_MEDIAN', 'ACE_RATIO_MONTHS', 'PROVIDER_AS_OF',
]
INDEX_COLUMNS = ['date', 'index_value', 'index_return', 'constituent_count', 'breadth_positive']
SCREENER_COLUMNS = ['product_id', 'name', 'set_name', 'card_number', 'rarity', 'start_date', 'start_price', 'current_date', 'current_price', 'dollar_change', 'period_return']
TRADE_COLUMNS = ['trade_id', 'trade_date', 'currency', 'notes', 'created_at', 'cash_given', 'cash_received', 'item_count', 'given_value', 'received_value']
ITEM_COLUMNS = ['trade_item_id', 'side', 'product_id', 'card_id', 'market_type', 'name', 'set_name', 'card_number', 'rarity', 'printing', 'quantity', 'agreed_unit_value', 'market_unit_usd_at_trade', 'signal_available', 'momentum_90', 'set_momentum_90', 'market_momentum_90', 'relative_set_90', 'relative_market_90', 'set_signal_percentile', 'market_signal_percentile', 'overheat_level', 'market_regime']


def local_mode() -> bool:
    """Fail closed: no credential detection or legacy flags enable data access."""
    return os.getenv('TCG_DATA_MODE', 'empty').strip().lower() == 'local'


def _frame(rows, columns):
    return pd.DataFrame(rows, columns=columns)


class Store:
    """Read adapter. The UI and API can inject a Store in tests."""

    def __init__(self, *, enabled: bool = False):
        self.enabled = bool(enabled)

    @classmethod
    def from_environment(cls):
        return cls(enabled=local_mode())

    def catalog(self) -> pd.DataFrame:
        if not self.enabled:
            return _frame([], CATALOG_COLUMNS)
        from database.canonical_cards import get_canonical_card_universe
        cards = [asdict(card) for card in get_canonical_card_universe()]
        return pd.DataFrame(cards).reindex(columns=CATALOG_COLUMNS)

    def graded_snapshot(self) -> pd.DataFrame:
        columns = [*GRADED_COLUMNS, 'ACE_10', 'ACE_10_AGE', 'PSA 9 / ACE 10', 'PSA 10 / ACE 10']
        if not self.enabled:
            return _frame([], columns)
        from database.provider_history import get_provider_graded_snapshot_rows
        frame = _frame(get_provider_graded_snapshot_rows(), GRADED_COLUMNS)
        for name in ['RAW', 'PSA_9', 'PSA_10', 'ACE_10_GUIDE', 'ACE_PSA_HIST_MEDIAN']:
            frame[name] = pd.to_numeric(frame[name], errors='coerce')
        frame['ACE_10_AGE'] = (pd.Timestamp(date.today()) - pd.to_datetime(frame['ACE_10_LAST_SALE'], errors='coerce')).dt.days
        frame['ACE_10'] = frame['ACE_10_GUIDE'].where(frame['ACE_10_AGE'].between(0, 90))
        # No raw/slab multipliers: provider slab data does not establish RAW finish.
        for grade in ['PSA_9', 'PSA_10']:
            name = grade.replace('_', ' ') + ' / ACE 10'
            frame[name] = frame[grade].div(frame['ACE_10'].where(frame['ACE_10'] > 0))
        return frame

    def printings(self, product_id: int):
        if not self.enabled:
            return []
        from database.card_variants import get_raw_printings
        return get_raw_printings(int(product_id))

    def history(self, product_id: int, *, market: str = 'RAW', printing: str | None = None) -> pd.DataFrame:
        if market not in {'RAW', 'PSA_9', 'PSA_10', 'ACE_10'}:
            raise ValueError('Unsupported market')
        if market == 'RAW' and not printing:
            raise ValueError('Select an exact RAW finish; no automatic Normal fallback.')
        if not self.enabled:
            return _frame([], HISTORY_COLUMNS)
        if market == 'RAW':
            from database.canonical_cards import get_canonical_raw_history_rows
            rows = [(d, finish, market, price, 'TickerMint/TCGPlayer')
                    for d, finish, price in get_canonical_raw_history_rows(int(product_id), printing)]
        else:
            from database.provider_history import get_product_provider_history
            rows = [(d, None, grade, price, 'PokemonPriceTracker')
                    for d, grade, price, *_ in get_product_provider_history(int(product_id)) if grade == market]
        frame = _frame(rows, HISTORY_COLUMNS)
        frame['date'] = pd.to_datetime(frame['date'], errors='coerce')
        frame['price'] = pd.to_numeric(frame['price'], errors='coerce')
        return frame.dropna(subset=['date', 'price']).sort_values('date').reset_index(drop=True)

    def intelligence(self, product_id: int, printing: str) -> dict | None:
        if not printing:
            raise ValueError('An exact RAW finish is required.')
        if not self.enabled:
            return None
        from database.card_intelligence import get_card_intelligence
        # Never request the full-universe screener from an interactive card view.
        return get_card_intelligence(int(product_id), printing=printing, include_market_structure=False)

    def graded_intelligence(self, product_id: int, market: str):
        if not self.enabled:
            return None
        from database.graded_intelligence import get_graded_card_intelligence
        return get_graded_card_intelligence(int(product_id), market)

    def opportunities(self) -> Snapshot:
        if not self.enabled:
            return Snapshot(pd.DataFrame(), {}, 'empty', 'No market data is included in this public copy.')
        from services.analysis.opportunity_snapshot import load_snapshot
        return load_snapshot()

    def refresh_opportunities(self):
        if not self.enabled:
            raise PermissionError('Building research is disabled in empty mode.')
        from services.pipelines.refresh_opportunity_snapshot import refresh_snapshot
        return refresh_snapshot()

    def contexts(self) -> pd.DataFrame:
        if not self.enabled:
            return pd.DataFrame()
        from database.opportunity_scanner import load_context_strength
        return load_context_strength()

    def scopes(self) -> dict[str, list[str]]:
        if not self.enabled:
            return {'market': [], 'era': [], 'set': []}
        from database.market_analytics import get_market_scopes
        return get_market_scopes()

    def index(self, scope_type: str, scope_name: str) -> pd.DataFrame:
        if not self.enabled or not scope_name:
            return _frame([], INDEX_COLUMNS)
        from database.market_analytics import get_market_index
        frame = _frame(get_market_index(scope_type, scope_name), INDEX_COLUMNS)
        for c in INDEX_COLUMNS[1:]:
            frame[c] = pd.to_numeric(frame[c], errors='coerce')
        frame['date'] = pd.to_datetime(frame['date'], errors='coerce')
        return frame.sort_values('date')

    def screener(self, days: int = 90, price_floor: float = 5.0) -> pd.DataFrame:
        if not self.enabled:
            return _frame([], SCREENER_COLUMNS)
        from database.market_analytics import get_card_screener
        return _frame(get_card_screener(days=days, min_start_price=price_floor), SCREENER_COLUMNS)

    def search(self, query: str, market: str = 'RAW') -> list[dict]:
        if not self.enabled or len(query.strip()) < 2:
            return []
        from database.trades import search_trade_products, search_graded_trade_cards
        if market == 'RAW':
            columns = ['product_id', 'name', 'set_name', 'card_number', 'rarity', 'printing', 'price_date', 'current_market_usd']
            rows = search_trade_products(query.strip())
        else:
            columns = ['product_id', 'name', 'set_name', 'card_number', 'language', 'price_date', 'current_market_usd', 'last_sale_date']
            rows = search_graded_trade_cards(query.strip(), market)
        return [{**dict(zip(columns, r)), 'market_type': market} for r in rows]

    def trades(self) -> pd.DataFrame:
        if not self.enabled:
            return _frame([], TRADE_COLUMNS)
        from database.trades import list_trades
        return _frame(list_trades(), TRADE_COLUMNS)

    def trade_items(self, trade_id: int) -> pd.DataFrame:
        if not self.enabled:
            return _frame([], ITEM_COLUMNS)
        from database.trades import get_trade_items
        return _frame(get_trade_items(int(trade_id)), ITEM_COLUMNS)

    def current_item_prices(self, items: list[dict]) -> dict:
        if not self.enabled:
            return {}
        from database.card_variants import get_current_variant_prices, raw_item_key, has_unmapped_graded_finish
        from database.trades import get_current_graded_prices
        raw = [raw_item_key(x) for x in items]
        result = {('RAW', p, f): price for (p, f), (_, price) in
                  get_current_variant_prices([x for x in raw if x is not None]).items()}
        graded = {(int(x['product_id']), x['market_type']) for x in items
                  if x.get('product_id') is not None and x.get('market_type') in {'PSA_9', 'PSA_10', 'ACE_10'}
                  and not has_unmapped_graded_finish(x)}
        result.update({(grade, p, None): value[1]
                       for (p, grade), value in get_current_graded_prices(list(graded)).items()})
        return result

    def save_trade(self, **kwargs) -> int:
        if not self.enabled:
            raise PermissionError('Saving trades is disabled in empty mode.')
        from database.trades import create_trade
        return create_trade(**kwargs)

    def baseline_prices(self, items: list[dict], trade_date: date) -> dict:
        if not self.enabled:
            return {}
        from database.card_variants import raw_item_key, has_unmapped_graded_finish, get_trade_date_variant_prices
        from database.trades import get_trade_date_graded_prices
        keys = [raw_item_key(item) for item in items]
        prices = {('RAW', pid, printing): price for (pid, printing), price in
                  get_trade_date_variant_prices([k for k in keys if k is not None], trade_date).items()}
        graded = {(int(x['product_id']), x['market_type']) for x in items
                  if x.get('product_id') is not None and x.get('market_type') in {'PSA_9', 'PSA_10', 'ACE_10'}
                  and not has_unmapped_graded_finish(x)}
        prices.update({(grade, pid, None): price for (pid, grade), price in
                       get_trade_date_graded_prices(list(graded), trade_date).items()})
        return prices

    def advanced_trade_analysis(self, given, received):
        if not self.enabled:
            raise PermissionError('Market analysis is disabled in empty mode.')
        from services.trading.graded_trade_engine import analyse_graded_trade
        from services.analysis.graded_premium_signal import build_live_premium_heat_universe
        from services.trading.graded_hybrid_outlook import analyse_hybrid_trade, build_grade_market_wrappers, fetch_hybrid_price_history
        # Expensive broad context is explicit, never run on startup or each keystroke.
        prices = fetch_hybrid_price_history()
        wrappers = build_grade_market_wrappers(prices=prices)
        return {'graded': analyse_graded_trade(given, received, universe=build_live_premium_heat_universe()),
                'hybrid': analyse_hybrid_trade(given, received, prices=prices, wrappers=wrappers)}
