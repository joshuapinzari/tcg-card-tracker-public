"""Small command dispatcher replacing the retired one-file-per-alias wrappers."""
from __future__ import annotations
import argparse
import runpy
import sys

COMMANDS = {
    'daily': 'services.pipelines.daily_update',
    'backfill-raw': 'services.pipelines.backfill_tcgplayer_history',
    'backfill-graded': 'services.pipelines.backfill_pokemonpricetracker',
    'build-research': 'services.pipelines.build_raw_opportunity_research',
    'refresh-scan': 'services.pipelines.refresh_opportunity_snapshot',
    'lockbox': 'services.research.model_lockbox',
    'evaluate-v3': 'services.research.evaluate_locked_card_model',
    'evaluate-v4': 'services.research.evaluate_frozen_hybrid_v4',
    'regime-research': 'services.research.run_card_analyst_regime_research',
}


def initialise_local_database():
    """Schema only: no catalogue entries, provider requests, or example prices."""
    from database.operations import initialise_database
    from database.tcgplayer import initialise_tcgplayer_schema
    from database.provider_history import initialise_provider_history_schema
    from database.market_analytics import initialise_market_analytics_schema
    from database.raw_research import initialise_raw_research_schema
    from database.opportunity_scanner import initialise_opportunity_schema
    from database.update_pipeline import initialise_update_pipeline_schema
    from database.trades import initialise_trade_schema
    for initialise in [initialise_database, initialise_tcgplayer_schema,
            initialise_provider_history_schema, initialise_market_analytics_schema,
            initialise_raw_research_schema, initialise_opportunity_schema,
            initialise_update_pipeline_schema, initialise_trade_schema]:
        initialise()
    print('Local schema ready. No card or market data was added.')


def main():
    args = sys.argv[1:]
    if args and args[0] in COMMANDS:
        module = COMMANDS[args[0]]
        sys.argv = [module, *args[1:]]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init-db', *COMMANDS])
    selected = parser.parse_args(args)
    if selected.command == 'init-db':
        initialise_local_database()


if __name__ == '__main__':
    main()
