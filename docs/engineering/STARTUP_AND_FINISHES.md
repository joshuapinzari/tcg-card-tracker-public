# Startup, privacy and exact finishes

`dashboard/store.py` is the data boundary. All nine sections use the same actual
renderers in empty and local modes. There is no `public_empty.py` UI branch.
The public default returns empty dataframes/lists with valid schemas. Explicit
local mode reads the database or saved rankings; it never contacts a price API.

Opportunity ranking is a separate batch calculation. UI filters sort/filter a
copy of the stored output without recomputing scores. Card Analysis accepts a
research row only if its saved snapshot is fresh and its `(product_id, printing)`
identity exactly matches the selection. An alternate finish cannot reuse a
Normal score. A stale scan can still be viewed with a warning in Opportunities,
but is not presented as a fresh Card Analysis verdict.

RAW histories and persisted trade items keep exact finish labels. Provider-level
PSA/ACE data is not mapped to an explicit RAW finish without evidence. Missing
finish prices and missing historical anchors stay missing. In trade review, an
unvalued item makes its basket return unavailable rather than a fictitious loss.

Trade balancing uses user-entered local fair values and cash; market-reference
comparisons remain in USD. Saving a backdated trade queries prices at that trade
date rather than treating today's price as the historical baseline. Changing
basket values or currency invalidates cached advanced analysis.

Default startup does not initialize the database, build research or enroll cards.
The API additionally requires its own explicit opt-in. Public data mode is not a
replacement for authentication: never expose the local-data API or dashboard to
untrusted users with private credentials configured.
