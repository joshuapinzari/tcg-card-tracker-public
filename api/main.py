"""Read-only TCG Market API.

Safe public default: no PostgreSQL reads and no provider calls.
A local owner with their *own* market data may explicitly enable private
access with TCG_DATA_MODE=local and TCG_API_DATA_ACCESS=1.

This replaces the old API prototype which referenced a retired function.
"""

from __future__ import annotations

import os
import math
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from dashboard.logic import number


MarketType = Literal["RAW", "PSA_9", "PSA_10", "ACE_10"]

app = FastAPI(
    title="TCG Market API",
    description="Read-only API; defaults to no data and no external requests.",
    version="0.2.0",
)


def private_data_access_enabled() -> bool:
    return (
        os.getenv("TCG_DATA_MODE", "empty").strip().lower() == "local"
        and os.getenv("TCG_API_DATA_ACCESS", "0").strip() == "1"
    )


class PricePoint(BaseModel):
    market_type: MarketType
    printing: str | None = None
    price: float
    currency: str = "USD"
    source: str
    recorded_at: date


class CardHistoryResponse(BaseModel):
    product_id: int
    observations: list[PricePoint] = Field(default_factory=list)
    data_available: bool = False


@app.get("/")
def root() -> dict:
    return {"name": "TCG Market API", "docs": "/docs", "data_access": private_data_access_enabled()}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "data_access": private_data_access_enabled()}


@app.get("/v1/cards")
def search_cards(q: str = Query(min_length=2, max_length=120), limit: int = Query(default=25, ge=1, le=100)) -> list[dict]:
    if not private_data_access_enabled():
        return []
    from database.card_intelligence import search_cards as search_database_cards
    return search_database_cards(q, limit=limit)


def _own_history(product_id: int, market: MarketType | None, printing: str | None, days: int | None) -> list[PricePoint]:
    """Query source-separated history, never substituting a different RAW finish."""
    from database.canonical_cards import get_canonical_raw_history_rows
    from database.provider_history import get_product_provider_history

    earliest = date.today() - timedelta(days=days) if days is not None else None
    observations: list[PricePoint] = []
    if market is None or market == "RAW":
        for observed_on, raw_printing, price in get_canonical_raw_history_rows(product_id, printing):
            price = number(price)
            if price is None or price <= 0:
                continue
            observed_date = observed_on.date() if isinstance(observed_on, datetime) else observed_on
            if earliest is not None and observed_date < earliest:
                continue
            observations.append(PricePoint(
                market_type="RAW", printing=str(raw_printing), price=float(price),
                source="TickerMint/TCGPlayer", recorded_at=observed_date,
            ))
    if market is None or market in {"PSA_9", "PSA_10", "ACE_10"}:
        for observed_on, tier, price, _sales, _volume, _label in get_product_provider_history(product_id):
            if tier not in {"PSA_9", "PSA_10", "ACE_10"} or (market is not None and tier != market):
                continue
            price = number(price)
            if price is None or price <= 0:
                continue
            observed_date = observed_on.date() if isinstance(observed_on, datetime) else observed_on
            if earliest is not None and observed_date < earliest:
                continue
            observations.append(PricePoint(
                market_type=tier, price=float(price), source="PokemonPriceTracker",
                recorded_at=observed_date,
            ))
    return sorted(observations, key=lambda row: (row.recorded_at, row.market_type, row.printing or ""))


@app.get("/v1/cards/{product_id}/history", response_model=CardHistoryResponse)
@app.get("/cards/{product_id}/history", response_model=CardHistoryResponse, include_in_schema=False)
def card_history(
    product_id: int = Path(ge=1),
    market: MarketType | None = None,
    printing: str | None = Query(default=None, min_length=1, max_length=100),
    days: int | None = Query(default=None, ge=1, le=3650),
) -> CardHistoryResponse:
    if not private_data_access_enabled():
        return CardHistoryResponse(product_id=product_id)
    if market in {None, 'RAW'} and (printing is None or not printing.strip()):
        raise HTTPException(status_code=422, detail='Select an exact RAW printing; no automatic Normal fallback.')
    rows = _own_history(product_id, market=market, printing=printing, days=days)
    return CardHistoryResponse(product_id=product_id, observations=rows, data_available=bool(rows))


@app.get("/v1/opportunities")
def opportunities(limit: int = Query(default=25, ge=1, le=250)) -> dict:
    if not private_data_access_enabled():
        return {"status": "no_data", "count": 0, "cards": []}
    from services.analysis.opportunity_snapshot import load_snapshot
    snapshot = load_snapshot()
    cards = []
    for item in snapshot.frame.head(limit).to_dict("records"):
        score = item.get("preferred_ranking_score")
        score = float(score) if score is not None else None
        if score is not None and not math.isfinite(score):
            score = None
        cards.append({
            "product_id": int(item["product_id"]),
            "name": str(item.get("name") or ""),
            "set_name": str(item.get("set_name") or ""),
            "printing": str(item.get("printing") or ""),
            "preferred_model": str(item.get("preferred_model") or ""),
            "preferred_ranking_score": score,
        })
    return {"status": snapshot.status, "count": len(cards), "cards": cards}
