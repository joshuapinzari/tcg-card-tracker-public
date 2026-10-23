# Running the simplified tracker

This is the full-code, **empty-by-default** distribution, not the earlier reduced
sample. The same dashboard page functions serve both empty mode and local data.
There is no second imitation dashboard and no Chinese watchlist.

Your original README wording is preserved. Some of its original technical
commands and regional-watchlist sections describe the older private tracker.
**Use this guide for the supported commands in this distribution.**

## Empty dashboard: no database or API key needed

From this directory, using Python 3.13:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m streamlit run app.py
```

All nine sections open with empty tables and blank charts. Counts of zero mean
there are no records; missing prices and scores are shown as unavailable, not
fabricated zero-valued market observations. The calculator accepts your own
hypothetical values but starts blank and does not store them.

No cards, pricing database, research-result datasets, saved trades, keys, or
synthetic demonstration datasets are shipped. Tests use explicitly synthetic
fixtures; those do not seed the running application.

The original `tcg` command is your personal shell alias for the private tracker.
It is not changed by this package. `./run.sh` still launches this copy.

## Your own local data (optional)

Use a separate local database. The default database name in this public copy is
`tcg_card_tracker_public`, **not** the private project's database.

```bash
createdb tcg_card_tracker_public
cp .env.example .env
```

Enter your own settings in `.env`, then load them:

```bash
set -a
. ./.env
set +a
python -m services init-db
```

The initialization command creates schemas only. To enable the same dashboard
pages against your local database, explicitly set `TCG_DATA_MODE=local`. An API
key, old cache file or the earlier `TCG_PUBLIC_SHOWCASE=0` flag cannot enable reads.
A newly initialized database still has no prices. Local tables must be populated
through the explicit collector commands before prices/analysis can appear.

## Supported commands

The grouped modules remain directly runnable. One dispatcher replaces the many
old forwarding files:

| Task | Command |
| --- | --- |
| RAW history | `python -m services backfill-raw --help` |
| Graded history | `python -m services backfill-graded --help` |
| Daily collection | `python -m services daily --graded-credit-budget 500` |
| Rebuild research | `python -m services build-research --rebuild-base` |
| Save rankings | `python -m services refresh-scan` |
| Lockbox status | `python -m services lockbox status` |
| Frozen V3 evaluation | `python -m services evaluate-v3 --help` |
| Hybrid V4 evaluation | `python -m services evaluate-v4` |

Collectors can make provider requests and the graded collector can consume paid
credits. Nothing schedules or runs them automatically. A saved-ranking refresh
uses existing local history; it is never triggered by navigation or a cache miss.
Do not run historical rebuilds just to open the empty dashboard.

## Optional read-only API

```bash
python -m pip install -r requirements-api.txt
python -m uvicorn api.main:app --host 127.0.0.1
```

The API returns empty market results by default. Data access requires **both**
`TCG_DATA_MODE=local` and `TCG_API_DATA_ACCESS=1`. Keep the data-enabled API local:
this package is not an authenticated public pricing service and does not grant
redistribution rights to provider data. The empty API has OpenAPI docs at `/docs`.

## Checks before publishing

```bash
python tools/verify_repository.py
python tools/check_public_release.py
python -m pytest -q
```

The source check detects common credentials and unwanted artifacts, not every
possible secret. Create new Git history from this clean folder, never copy `.git`
from your private repository. Do not extract this package over an older copy:
ZIP extraction overwrites matching paths but leaves obsolete files behind.
