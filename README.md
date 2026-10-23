# TCG Card Tracker

A Python/PostgreSQL research platform for Pokémon market data, relative-value analysis and trade decisions.

## A little bit about me and the project:

I have collected Pokemon cards from a young age and got back into it in the last few years.

Everyone loves collecting, however, it is much nicer when you don't see the card you just bought for £150 on the market for £80 a week later. Over time from being in the space, I have learnt many different factors which contribute to price changes.

This project is a quantitative approach to collecting Pokemon, in which hundreds of thousands of data points are analysed to estimate potential future price movements. As a result, there are also many other functions such as weighing up trades based on those estimates.

My personal use will be to maintain a stable collection, make smart trades and grow my collection organically in a direction I choose (for example, if I have a set of cards I like, I will analyse them through here to figure out which one would be a good buy at that moment).

## What the project does

- Maintains a canonical English TCGPlayer product universe across selected sets from Legendary Collection onward, with dated RAW price history where available.
- Backfills matched graded history where available without merging providers silently.
- Builds point-in-time historical states for the raw Opportunity Scanner.
- Compares cards against set, era and whole-market context.
- Tracks grading premiums, ACE/PSA relationships, liquidity and history depth.
- Analyses both sides of a proposed trade using value, momentum, context and graded-market evidence.
- Keeps a small editable Simplified-Chinese identity watchlist separate from the English model.
- Presents the research through a Streamlit dashboard.

## Dashboard

```bash
./run.sh
```

Main views:

- **Opportunities** — saved, historically matched RAW-card rankings, with **All finishes & prices** for browsing available printings separately.
- **Rankings / Weighted Search / Sets / Market** — descriptive market analytics.
- **Trades** — GIVE/RECEIVE basket analysis, preserving each item's selected RAW finish alongside graded evidence.
- **Card Analysis** — V3/V4 research where eligible, historical analogues, market-relative momentum, finish-specific RAW charts and separate graded-market intelligence.

### Full Card Analyst

The primary RAW research display uses the **frozen V3 model** and, where the card's era is supported, the **Hybrid V4 challenger**. V3 uses five weighted factors: historical analogues, momentum, validated overheating risk, prior run-up, and recovery/drawdown. Research coverage is shown separately; missing evidence is not automatically a negative signal. V4 compares alternate weights by era while keeping V3 unchanged for ongoing evaluation.

The earlier hand-weighted eight-factor **Legacy Evidence Score** remains available for comparison and descriptive analysis; it is **not** the current validated research ranking. The dashboard displays individual factor readings and the quality/coverage of available evidence. Graded intelligence stays separate rather than automatically improving the RAW research verdict.

### Normal, holo and reverse-holo prices

The RAW price data keeps **Normal**, **Holofoil** and **Reverse Holofoil** as separate printings when the provider has them. Card Analysis has a **RAW finish** selector, and the Opportunities page has **All finishes & prices**. Each finish uses its own current price and historical chart; trades also retain the selected printing. These are market references, not condition-specific NM or UK listing valuations.

The frozen V3/V4 research universe still covers designated primary printings. A reverse holo outside that universe may have its own price history and descriptive metrics but **does not inherit the normal card's model score or analogues**. Product-level graded series also cannot automatically be assigned to a particular RAW finish. Some indicators may be unavailable when a provider has gaps in its historical prices; missing observations are not invented.

## Important modelling conventions

- **Historical analogue** = a past card/date state with similar features to the current state.
- **Alpha** = card return minus a chosen benchmark return (set/era/market).
- **Breadth** = fraction of constituents participating in a move.
- **Drawdown** = distance below a previous high.
- **Coverage** = fraction of an analysis/basket supported by usable data.
- Percentiles are **ranks**, not probabilities.
- `Analogue Quality` describes sample closeness/depth, not certainty that a prediction is correct.

## Project layout

```text
tcg-card-tracker/
├── app.py                  # Streamlit entry point
├── dashboard/              # Views and display-layer caching
├── database/               # PostgreSQL queries, schema and persistence
├── services/
│   ├── analysis/           # Card research scores and signals
│   ├── trading/            # Trade and graded-market analysis
│   ├── pipelines/          # Daily updates, backfills and scan refreshes
│   ├── research/           # Evaluation, experiments and model lockbox
│   └── maintenance/        # Explicit legacy-data cleanup
├── sources/                # TickerMint, TCGCSV and PokemonPriceTracker clients
├── models/                 # Frozen model specifications and policies
├── docs/                   # Project map, development and engineering notes
├── data/                   # Curated supplemental card identities
├── tests/                  # Offline tests grouped by feature
└── tools/                  # Repository checks and audit utilities
```

For a more detailed map, see [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md). The small modules directly under `services/` are compatibility entry points for existing commands; the implementations are grouped in the subfolders above.

## Current architecture decisions

1. TCGPlayer identity is the canonical backbone for the main universe.
2. Raw and graded provider histories stay separate at ingestion.
3. The dashboard never fabricates a grade or source match when data is missing.
4. Regional/exclusive cards without reliable canonical coverage live in a small provider-neutral supplemental watchlist.
5. Asking-price feeds are not acceptable substitutes for sold-market history in thin markets.
6. Research scripts must preserve point-in-time correctness before a signal is promoted into production scoring.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
createdb tcg_card_tracker
cp .env.example .env
```

After entering your own credentials/settings in `.env`, load them into your shell:

```bash
set -a
. ./.env
set +a
```

The default database name is `tcg_card_tracker`. Override it with `TCG_DATABASE_NAME`. The application reads exported environment variables; it does not automatically load `.env`. Keep API keys and your `.env` file out of Git. For an existing installation, keep your current `.env` and database rather than replacing them.

## Core data workflows

The production data model is deliberately source-separated:

- **TCGPlayer product IDs** are the canonical identity for the main English universe.
- **TCGCSV** provides TCGPlayer product/set catalogue data; **TickerMint** provides RAW TCGPlayer market-price history.
- **PokemonPriceTracker** provides TCGPlayer-ID matched RAW, PSA 9, PSA 10 and ACE 10 history.
- **Simplified-Chinese exclusives** are kept in a small identity-only watchlist, but automated market prices are currently absent as I couldn't find a good source (and didn't want to go back to scraping pricecharting). Since it's only a handful I have left automated price analysis out for now. It was previously attempted with CardTrader.

PriceCharting is retired from production, and CardTrader pricing is retired from the Chinese watchlist.

### Canonical TCGPlayer raw history

```bash
python -m services.backfill_tcgplayer_history
python -m services.update_tcgplayer_daily_prices
```

`backfill_tcgplayer_history` supports selected English sets from Legendary Collection onward, including e-Card, EX, Diamond & Pearl, Platinum, HGSS, Black & White (main sets, BW Promos, and Legendary Treasures: Radiant Collection), XY, SM, SWSH, SV and Mega Evolution. The available history for each card and printing depends on provider coverage. The `update_tcgplayer_daily_prices` command is incremental, idempotent and append-only for new dates, but it does not automatically fill older gaps; it is not scheduled by the project itself, so run it daily or attach it to an external scheduler.

The combined daily job can update RAW and graded data, refresh market research, and rebuild the saved opportunity rankings:

```bash
python -m services.daily_update --graded-credit-budget 500
```

This command can use paid PokemonPriceTracker credits. The `500` cap is just an example; check your provider quota before running it. The default budget of `0` means no credit cap unless configured otherwise. To rebuild only the saved scanner results with **no provider requests**, use the separate command below.

### PokemonPriceTracker graded history

```bash
export POKEMON_PRICE_TRACKER_API_KEY="..."
python -m services.backfill_pokemonpricetracker
```

The provider history remains keyed to the canonical TCGPlayer `product_id`. Missing ACE history is stored as missing data, never as a fabricated value.

### Raw Opportunity Scanner research panel

```bash
python -m services.build_raw_opportunity_research --rebuild-base
python -m services.validate_raw_opportunity_scanner
```

The research builder creates point-in-time states. Validation is walk-forward: future outcomes are labels only and are not available to the formation-date features.

The Opportunities dashboard reads a **saved ranking** rather than rerunning historical-analogue matching every time the page opens. Build or refresh that snapshot explicitly with:

```bash
python -m services.pipelines.refresh_opportunity_snapshot
```

This snapshot command uses data already in PostgreSQL and makes **no external provider requests**. The combined daily pipeline also refreshes saved rankings after successful research updates. The dashboard warns when the saved scan is stale. See [`docs/engineering/STARTUP_AND_FINISHES.md`](docs/engineering/STARTUP_AND_FINISHES.md) for details.

The research ranking uses **frozen V3** and a **regime-aware Hybrid V4 challenger**. V3 is retained as the lockbox baseline; V4 is research-only in the model registry, although the dashboard shows the applicable V4 challenger score for supported eras. Mid-era uses V3's exact weights; Legendary Collection and e-Card are not mapped to a V4 regime, so eligible cards in those eras continue to use V3. These are relative rankings, not guaranteed returns or calibrated probabilities.

### Simplified-Chinese watchlist

The Chinese side is intentionally small. Edit `data/chinese_watchlist.csv` to add/remove cards, then sync identities only:

```bash
python -m services.sync_chinese_watchlist
```

No automated Chinese market price is currently used. This is deliberate. CardTrader live asking prices were retired after proving materially unreliable for thin Chinese exclusives. Until a trustworthy sold-sales source is validated, Chinese Card Analysis remains catalog-only and Chinese price-based Opportunities/Trades stay disabled.

### Retire CardTrader Chinese data

Audit the live PostgreSQL database:

```bash
python -m services.retire_cardtrader_chinese
```

Then permanently remove every CardTrader-derived Chinese observation and provider mapping while keeping the six curated card identities:

```bash
python -m services.retire_cardtrader_chinese --apply
```

The command verifies that no CardTrader-derived Chinese observation, provider ID or URL remains.

## Tests

```bash
python tools/verify_repository.py
python -m pytest -q
```

The tests are designed to run offline; external HTTP calls are mocked or tested through parsers/fixtures.

## One-time legacy PriceCharting cleanup

The cleaned codebase no longer contains the old PriceCharting collector. Existing databases can be audited with:

```bash
python -m services.cleanup_legacy_data
```

and cleaned with:

```bash
python -m services.cleanup_legacy_data --apply
```
