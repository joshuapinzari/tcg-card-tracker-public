# Project map

The supported public application has one empty/local data boundary and one set
of page renderers. The private repository is not modified by this distribution.

```text
app.py                         Nine-page navigation, no database initialization
api/main.py                    Optional read-only API, separately gated
 dashboard/store.py            Empty/local reads and explicit local writes
 dashboard/logic.py            Pure filtering, formatting, coverage and returns
 dashboard/trade_logic.py      Trade item identity and analysis invalidation
 dashboard/pages/              Real page renderers, grouped by responsibility
 database/                     PostgreSQL schema and query implementations
 services/analysis/            Frozen scores, analogues, saved rankings
 services/trading/             Value balancing and RAW/graded evidence
 services/pipelines/           Explicit backfills and incremental updates
 services/research/            Current evaluation, regime research and lockbox
 sources/                      Provider clients (never imported for empty views)
 models/                       Model specifications, not a price dataset
 tests/                        Retained core tests plus adapter/UI/API tests
 tools/                        Structure and public-artifact checks
```

`python -m services --help` lists the maintained CLI commands. There are no
one-file-per-command compatibility wrappers. One-off provider-retirement
migrations, the dormant Chinese watchlist and superseded V1/V2/V3 fitting scripts
are not supported public features. The shared historical-panel helpers needed by
current evaluators were extracted into `services/research/history.py`.

Database and numerical services remain separate from Streamlit. Page rendering
uses `Store`, which returns structurally valid empty results until local data is
explicitly enabled. Empty mode neither reads saved rankings nor opens PostgreSQL,
even if a credential is present in the environment.

See [the running guide](../RUNNING.md), [data boundaries](engineering/STARTUP_AND_FINISHES.md)
and [research notes](RESEARCH.md).
