from __future__ import annotations

import time
from datetime import date

import requests

from pokemon_eras import (
    BLACK_WHITE_SET_NAMES,
    LEGENDARY_COLLECTION_SET_NAMES,
    E_CARD_SET_NAMES,
    EX_SERIES_SET_NAMES,
    DIAMOND_PEARL_SET_NAMES,
    HGSS_SET_NAMES,
    PLATINUM_SET_NAMES,
)


BASE_URL = "https://tcgcsv.com"
POKEMON_CATEGORY_ID = 3

USER_AGENT = (
    "TCGCardTracker/0.2 "
    "(personal university research project)"
)


class TCGCSVClient:
    def __init__(
        self,
        min_interval: float = 0.15,
        timeout: float = 30.0,
    ):
        self.min_interval = min_interval
        self.timeout = timeout
        self.last_request_at = 0.0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
            }
        )

    def _pace(self) -> None:
        elapsed = time.monotonic() - self.last_request_at
        wait = self.min_interval - elapsed

        if wait > 0:
            time.sleep(wait)

    def _get_json(self, url: str):
        last_error = None

        for attempt in range(5):
            self._pace()
            self.last_request_at = time.monotonic()

            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                )

                if response.status_code == 429:
                    wait = min(
                        60,
                        5 * (attempt + 1),
                    )

                    print(
                        f"TCGCSV rate limited. "
                        f"Waiting {wait}s..."
                    )

                    time.sleep(wait)
                    continue

                response.raise_for_status()

                data = response.json()

                if not data.get("success", True):
                    raise RuntimeError(
                        f"TCGCSV returned errors: "
                        f"{data.get('errors')}"
                    )

                return data

            except (
                requests.RequestException,
                ValueError,
                RuntimeError,
            ) as error:
                last_error = error

                if attempt == 4:
                    break

                wait = 2 ** attempt

                print(
                    f"TCGCSV request failed: {error}. "
                    f"Retrying in {wait}s..."
                )

                time.sleep(wait)

        raise RuntimeError(
            f"TCGCSV request failed after retries: "
            f"{last_error}"
        )

    def get_groups(self) -> list[dict]:
        url = (
            f"{BASE_URL}/tcgplayer/"
            f"{POKEMON_CATEGORY_ID}/groups"
        )

        return self._get_json(url)["results"]

    def get_products(
        self,
        group_id: int,
    ) -> list[dict]:
        url = (
            f"{BASE_URL}/tcgplayer/"
            f"{POKEMON_CATEGORY_ID}/"
            f"{group_id}/products"
        )

        return self._get_json(url)["results"]


def extended_fields(product: dict) -> dict[str, str]:
    result = {}

    for item in product.get("extendedData") or []:
        name = item.get("name")
        value = item.get("value")

        if name and value is not None:
            result[name] = value

    return result


def is_card_product(product: dict) -> bool:
    """
    TCGCSV recommends Number/Rarity as indicators
    that a product is an individual card.
    """
    fields = extended_fields(product)

    name = (
        product.get("name")
        or ""
    ).lower()

    if "code card" in name:
        return False

    return bool(
        fields.get("Number")
        or fields.get("Rarity")
    )


def is_target_group(group: dict) -> bool:
    """
    Return True for the English Pokemon eras used by
    the TCGPlayer research database.

    XY and Sun & Moon both contain special releases whose
    TCGPlayer group names do not begin with the normal era prefix,
    so those groups are included explicitly by ID.
    """
    name = str(
        group.get("name") or ""
    ).strip()

    try:
        group_id = int(
            group.get("groupId") or 0
        )
    except (TypeError, ValueError):
        group_id = 0

    xy_special_group_ids = {
        1522,  # Kalos Starter Set
        1525,  # Double Crisis
        1692,  # McDonald's Promos 2014
        1694,  # McDonald's Promos 2015
        1728,  # Generations
        1729,  # Generations: Radiant Collection
        # McDonald's Promos 2016 is also caught by name below because
        # its group ID is not required for reliable inclusion.
    }

    xy_special_names = {
        "McDonald's Promos 2016",
    }

    sun_moon_special_group_ids = {
        2054,  # Shining Legends
        2148,  # McDonald's Promos 2017
        2295,  # Dragon Majesty
        2364,  # McDonald's Promos 2018
        2409,  # Detective Pikachu
        2480,  # Hidden Fates
        2594,  # Hidden Fates: Shiny Vault
        2555,  # McDonald's Promos 2019
    }

    return (
        name in LEGENDARY_COLLECTION_SET_NAMES
        or name in E_CARD_SET_NAMES
        or name in EX_SERIES_SET_NAMES
        or name in DIAMOND_PEARL_SET_NAMES
        or name in PLATINUM_SET_NAMES
        or name in HGSS_SET_NAMES
        or name in BLACK_WHITE_SET_NAMES
        or name.startswith(
            (
                "XY",
                "SM",
                "SWSH",
                "SV",
                "ME",
            )
        )
        or group_id in xy_special_group_ids
        or name in xy_special_names
        or group_id in sun_moon_special_group_ids
    )

def parse_published_date(
    value: str | None,
) -> date | None:
    if not value:
        return None

    try:
        return date.fromisoformat(
            value[:10]
        )
    except ValueError:
        return None


def product_to_row(
    group: dict,
    product: dict,
) -> dict:
    fields = extended_fields(product)

    product_id = int(
        product["productId"]
    )

    return {
        "product_id": product_id,
        "group_id": int(
            product["groupId"]
        ),
        "name": product["name"],
        "set_name": group["name"],
        "card_number": fields.get(
            "Number"
        ),
        "rarity": fields.get(
            "Rarity"
        ),
        "artist": fields.get(
            "Artist"
        ),
        "image_url": product.get(
            "imageUrl"
        ),
        "tcgplayer_url": product.get(
            "url"
        ),
        "tickermint_url": (
            "https://tickermint.cards/"
            f"card/{product_id}"
        ),
        "set_published_on": (
            parse_published_date(
                group.get("publishedOn")
            )
        ),
    }
