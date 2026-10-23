"""Read-only client and normalizers for PokemonPriceTracker API v2."""

from __future__ import annotations

import os
import re

import requests


BASE_URL = "https://www.pokemonpricetracker.com/api/v2"
DEFAULT_TIMEOUT = 30.0
_GRADE_KEY = re.compile(r"^([a-z]+)(\d+(?:[_\.]\d+)?)$", re.IGNORECASE)


class PokemonPriceTrackerError(RuntimeError):
    """Raised when PokemonPriceTracker returns an unsuccessful response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class PokemonPriceTrackerClient:
    """Client for exact TCGPlayer-ID lookups and graded sold data."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or os.getenv("POKEMON_PRICE_TRACKER_API_KEY")
        if not self.api_key:
            raise ValueError("POKEMON_PRICE_TRACKER_API_KEY is not set.")

        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "TCGCardTracker/0.4 (personal research project)",
            }
        )

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        response = self.session.get(
            f"{BASE_URL}{path}",
            params=params,
            timeout=self.timeout,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if not response.ok:
            detail = payload.get("error") or payload.get("message") or response.text[:300]
            retry_after = None
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after not in (None, ""):
                try:
                    retry_after = float(raw_retry_after)
                except (TypeError, ValueError):
                    retry_after = None
            raise PokemonPriceTrackerError(
                f"PokemonPriceTracker {response.status_code}: {detail}",
                status_code=response.status_code,
                retry_after=retry_after,
            )

        if not isinstance(payload, dict):
            raise PokemonPriceTrackerError(
                "PokemonPriceTracker returned a non-object JSON response."
            )

        return payload

    def get_card(
        self,
        product_id: int,
        *,
        include_ebay: bool = True,
        include_history: bool = False,
        days: int = 90,
        max_data_points: int | None = None,
    ) -> tuple[dict | None, dict]:
        """Return one exact TCGPlayer card plus API metadata.

        ``include_history`` requests the provider's dated raw-history payload.
        ``include_ebay`` exposes the dated graded-history payload and current
        graded aggregates. ``max_data_points`` is passed through when supplied
        so paid plans can return daily history across the requested window.
        """
        params: dict[str, object] = {"tcgPlayerId": int(product_id)}
        if include_ebay:
            params["includeEbay"] = "true"
        if include_history:
            params["includeHistory"] = "true"
        if include_ebay or include_history:
            params["days"] = int(days)
        if max_data_points is not None:
            params["maxDataPoints"] = int(max_data_points)

        payload = self._get("/cards", params=params)
        data = payload.get("data")

        if isinstance(data, list):
            card = data[0] if data else None
        elif isinstance(data, dict):
            card = data
        else:
            card = None

        return (card if isinstance(card, dict) else None), payload.get("metadata") or {}


def normalize_grade_key(value: str) -> str | None:
    """Convert API keys such as psa10 or cgc9_5 to PSA_10 / CGC_9_5."""
    cleaned = str(value).strip().replace(" ", "").replace("-", "_")
    match = _GRADE_KEY.match(cleaned)
    if not match:
        return None

    company, grade = match.groups()
    grade = grade.replace(".", "_")
    return f"{company.upper()}_{grade}"


def tcgplayer_id_from_card(card: dict) -> int | None:
    value = card.get("tcgPlayerId") or card.get("tcgplayerId")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ebay_section(card: dict) -> dict:
    value = card.get("ebay")
    return value if isinstance(value, dict) else {}


def graded_sales_by_tier(card: dict) -> dict[str, dict]:
    """Return normalized current graded aggregates keyed as COMPANY_GRADE."""
    ebay = _ebay_section(card)
    sales = ebay.get("salesByGrade")
    if not isinstance(sales, dict):
        sales = {
            key: value
            for key, value in ebay.items()
            if normalize_grade_key(key) is not None and isinstance(value, dict)
        }

    result: dict[str, dict] = {}
    for key, value in sales.items():
        tier = normalize_grade_key(key)
        if tier and isinstance(value, dict):
            result[tier] = value
    return result


def graded_price_history_by_tier(card: dict) -> dict[str, dict]:
    """Return dated graded price-history maps keyed as COMPANY_GRADE."""
    history = _ebay_section(card).get("priceHistory")
    if not isinstance(history, dict):
        return {}

    result: dict[str, dict] = {}
    for key, value in history.items():
        tier = normalize_grade_key(key)
        if tier and isinstance(value, dict):
            result[tier] = value
    return result


def sold_listings_by_tier(card: dict) -> dict[str, list[dict]]:
    """Return dated sold listings grouped by normalized grade tier."""
    sold = _ebay_section(card).get("soldListings")
    if not isinstance(sold, dict):
        return {}

    result: dict[str, list[dict]] = {}
    for key, value in sold.items():
        tier = normalize_grade_key(key)
        if not tier or not isinstance(value, list):
            continue
        result[tier] = [row for row in value if isinstance(row, dict)]
    return result


def _normalise_printing(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def raw_price_history(
    card: dict,
    *,
    preferred_printing: str | None = None,
    condition: str = "Near Mint",
) -> tuple[str | None, list[dict]]:
    """Return one representative raw history series.

    Preference order:
    1. The provider variant matching the canonical TCGPlayer primary printing.
    2. The provider's top-level condition series (its default variant).
    3. A sole available variant when there is no explicit default series.

    The returned label is persisted so the chosen raw series remains auditable.
    """
    price_history = card.get("priceHistory")
    if not isinstance(price_history, dict):
        return None, []

    variants = price_history.get("variants")
    if isinstance(variants, dict) and preferred_printing:
        preferred_key = _normalise_printing(preferred_printing)
        for variant_name, variant_data in variants.items():
            if _normalise_printing(variant_name) != preferred_key:
                continue
            if not isinstance(variant_data, dict):
                continue
            condition_data = variant_data.get(condition)
            if not isinstance(condition_data, dict):
                continue
            history = condition_data.get("history")
            if isinstance(history, list) and history:
                return f"{variant_name} | {condition}", [
                    row for row in history if isinstance(row, dict)
                ]

    conditions = price_history.get("conditions")
    if isinstance(conditions, dict):
        condition_data = conditions.get(condition)
        if isinstance(condition_data, dict):
            history = condition_data.get("history")
            if isinstance(history, list) and history:
                return condition, [row for row in history if isinstance(row, dict)]

    if isinstance(variants, dict) and len(variants) == 1:
        variant_name, variant_data = next(iter(variants.items()))
        if isinstance(variant_data, dict):
            condition_data = variant_data.get(condition)
            if isinstance(condition_data, dict):
                history = condition_data.get("history")
                if isinstance(history, list) and history:
                    return f"{variant_name} | {condition}", [
                        row for row in history if isinstance(row, dict)
                    ]

    return None, []
