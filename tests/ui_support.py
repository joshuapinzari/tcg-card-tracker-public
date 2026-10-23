"""Synthetic test inputs only: never loaded or seeded by the application."""
from datetime import date
import pandas as pd
from dashboard.store import Store, CATALOG_COLUMNS, GRADED_COLUMNS, INDEX_COLUMNS, SCREENER_COLUMNS
from database.card_variants import RawPrinting
from services.analysis.opportunity_snapshot import Snapshot
from tests.analysis.test_card_analyst import _base_intelligence


class FixtureStore(Store):
    def __init__(self):
        super().__init__(enabled=True)
        self.reads = []
        self.saved = []

    def catalog(self):
        return pd.DataFrame([dict(product_id=1, name='Synthetic Alpha', set_name='Synthetic Set',
            card_number='001', rarity='Test', printing='Normal', latest_raw_usd=20.,
            latest_raw_date=date(2026, 1, 2), market_observations=200)], columns=CATALOG_COLUMNS)

    def printings(self, product_id):
        return [RawPrinting(1, p, 200, date(2025, 1, 1), date(2026, 1, 2), v, p=='Normal')
                for p, v in [('Normal', 20.), ('Reverse Holofoil', 70.)]]

    def history(self, product_id, *, market='RAW', printing=None):
        self.reads.append((product_id, market, printing))
        price = 70. if printing == 'Reverse Holofoil' else 20. if market == 'RAW' else 90.
        return pd.DataFrame({'date': pd.to_datetime(['2026-01-01', '2026-01-02']),
            'price': [price-1, price], 'printing': [printing]*2, 'market_type': [market]*2})

    def intelligence(self, product_id, printing):
        ci = _base_intelligence()
        ci['era'] = 'e-Card'
        return ci

    def graded_intelligence(self, product_id, market):
        return {'market_type': market, 'current_price': 90., 'confidence': 'LOW'}

    def graded_snapshot(self):
        frame = pd.DataFrame([{**dict.fromkeys(GRADED_COLUMNS), 'product_id': i,
            'name': 'Synthetic ' + str(i), 'set_name': 'Synthetic Set', 'card_number': str(i),
            'PSA_9': 10.*i, 'PSA_10': 20.*i, 'ACE_10': 9.*i, 'ACE_10_AGE': 2,
            'PSA_10_SALES_90': i, 'ACE_10_SALES_90': i, 'ACE_10_HISTORY_POINTS': 20,
            'PSA 10 / ACE 10': 2.} for i in [1, 2]])
        return frame

    def opportunities(self):
        return Snapshot(pd.DataFrame([{'product_id': 1, 'name': 'Synthetic Alpha',
            'set_name': 'Synthetic Set', 'card_number': '001', 'printing': 'Normal',
            'current_price': 20., 'era': 'e-Card', 'preferred_model': 'Frozen V3',
            'preferred_regime': float('nan'), 'preferred_ranking_score': 73.,
            'preferred_coverage': 1., 'research_score': 73., 'research_ranking_score': 73.,
            'research_coverage': 1., 'hybrid_v4_research_score': float('nan'),
            'research_factors': [], 'hybrid_v4_factors': [], 'setup_label': 'Heading Up',
            'all_setups': 'Heading Up', 'signal_percentile': .8, 'market_percentile': .7,
            'market_regime': 'Neutral', 'expected_set_alpha_90d': .1,
            'probability_strong_gain_90d': .55, 'probability_crash_90d': .05,
            'analogue_quality': 'HIGH', 'analog_count': 150, 'similarity': .7}]),
            {}, 'ready', 'Synthetic fixture')

    def scopes(self):
        return {'market': ['Synthetic Market'], 'era': ['e-Card'], 'set': ['Synthetic Set']}

    def index(self, scope_type, scope_name):
        return pd.DataFrame([(pd.Timestamp('2026-01-01'), 100., 0., 2, .5),
                             (pd.Timestamp('2026-01-02'), 105., .05, 2, .5)], columns=INDEX_COLUMNS)

    def screener(self, days=90, price_floor=5.):
        return pd.DataFrame([(1,'Synthetic Alpha','Synthetic Set','001','Test',
            date(2025, 1, 1),20.,date(2026,1,2),21.,1.,.05)], columns=SCREENER_COLUMNS)

    def contexts(self):
        return pd.DataFrame([{'scope_type':'set','scope_name':'Synthetic Set',
            'return_30d':.1,'return_90d':.2,'vs_market_90d':.05,'breadth':.5,'strengthening':True}])

    def search(self, query, market='RAW'):
        if len(query) < 2:
            return []
        finishes = ['Normal', 'Reverse Holofoil'] if market == 'RAW' else [None]
        return [dict(product_id=1, name='Synthetic Alpha', set_name='Synthetic Set',
            card_number='001', rarity='Test', printing=p, market_type=market,
            current_market_usd=70. if p=='Reverse Holofoil' else 20., price_date=date(2026,1,2))
            for p in finishes]

    def trades(self):
        return pd.DataFrame([{'trade_id': 1, 'trade_date': date(2026,1,1), 'currency':'GBP'}])

    def trade_items(self, trade_id):
        return pd.DataFrame([{'side':side,'product_id':1,'name':'Synthetic Alpha',
            'market_type':'RAW','printing':p,'quantity':1,'agreed_unit_value':v,
            'market_unit_usd_at_trade':v} for side,p,v in [('GIVEN','Normal',20.),('RECEIVED','Reverse Holofoil',60.)]])

    def current_item_prices(self, items):
        return {('RAW',1,'Normal'):21.,('RAW',1,'Reverse Holofoil'):70.}

    def baseline_prices(self, items, trade_date):
        return {('RAW',1,'Normal'):20.,('RAW',1,'Reverse Holofoil'):60.}

    def save_trade(self, **kwargs):
        self.saved.append(kwargs)
        return 1

    def advanced_trade_analysis(self, given, received):
        return {'graded': {'verdict':'LOW DATA', 'given':{'profiles':[]}, 'received':{'profiles':[]}},
                'hybrid': {'edge_90':None}}
