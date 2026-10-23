from __future__ import annotations

import csv
import io
import time

import requests


BASE_URL = "https://api.tickermint.cards"

USER_AGENT = (
    "TCGCardTracker/0.2 "
    "(personal university research project)"
)


class TickerMintClient:
    def __init__(
        self,
        min_interval: float = 0.65,
        timeout: float = 30.0,
    ):
        self.min_interval = min_interval
        self.timeout = timeout
        self.last_request_at = 0.0
        self.request_count = 0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )

    def _pace(self) -> None:
        elapsed = (
            time.monotonic()
            - self.last_request_at
        )

        wait = (
            self.min_interval
            - elapsed
        )

        if wait > 0:
            time.sleep(wait)

    def get_price_history(
        self,
        product_id: int,
    ) -> dict:
        url = (
            f"{BASE_URL}/products/"
            f"{product_id}/prices"
        )

        last_error = None

        for attempt in range(5):
            self._pace()

            self.last_request_at = (
                time.monotonic()
            )

            self.request_count += 1

            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                )

                if response.status_code == 404:
                    return {}

                if response.status_code == 429:
                    retry_after = (
                        response.headers.get(
                            "Retry-After"
                        )
                    )

                    try:
                        wait = float(
                            retry_after
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        wait = min(
                            30,
                            5 * (attempt + 1),
                        )

                    print(
                        f"TickerMint 429. "
                        f"Waiting {wait:.1f}s..."
                    )

                    time.sleep(wait)
                    continue

                if response.status_code >= 500:
                    wait = min(
                        30,
                        2 ** (attempt + 1),
                    )

                    print(
                        f"TickerMint "
                        f"{response.status_code}. "
                        f"Waiting {wait}s..."
                    )

                    time.sleep(wait)
                    continue

                response.raise_for_status()

                data = response.json()

                if not isinstance(
                    data,
                    dict,
                ):
                    raise RuntimeError(
                        "Unexpected TickerMint "
                        "history response."
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

                wait = min(
                    30,
                    2 ** (attempt + 1),
                )

                print(
                    f"TickerMint request failed: "
                    f"{error}. "
                    f"Retrying in {wait}s..."
                )

                time.sleep(wait)

        raise RuntimeError(
            f"TickerMint request failed "
            f"after retries: {last_error}"
        )

    def get_set_prices(
        self,
        group_id: int,
    ) -> list[dict]:
        """Return the latest daily TCGPlayer prices for one set."""
        url = (
            f"{BASE_URL}/sets/"
            f"{group_id}.csv"
        )

        last_error = None

        for attempt in range(5):
            self._pace()

            self.last_request_at = (
                time.monotonic()
            )

            self.request_count += 1

            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    headers={
                        "Accept": "text/csv",
                    },
                )

                if response.status_code == 404:
                    return []

                if response.status_code == 429:
                    retry_after = (
                        response.headers.get(
                            "Retry-After"
                        )
                    )

                    try:
                        wait = float(
                            retry_after
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        wait = min(
                            30,
                            5 * (attempt + 1),
                        )

                    print(
                        f"TickerMint 429. "
                        f"Waiting {wait:.1f}s..."
                    )

                    time.sleep(wait)
                    continue

                if response.status_code >= 500:
                    wait = min(
                        30,
                        2 ** (attempt + 1),
                    )

                    print(
                        f"TickerMint "
                        f"{response.status_code}. "
                        f"Waiting {wait}s..."
                    )

                    time.sleep(wait)
                    continue

                response.raise_for_status()

                reader = csv.DictReader(
                    io.StringIO(
                        response.text
                    )
                )

                required = {
                    "date",
                    "product_id",
                    "printing",
                    "market_price_usd",
                }

                fields = set(
                    reader.fieldnames or []
                )

                if not required.issubset(fields):
                    raise RuntimeError(
                        "Unexpected TickerMint "
                        "set CSV columns."
                    )

                return [
                    dict(row)
                    for row in reader
                    if any(
                        value not in (
                            None,
                            "",
                        )
                        for value in row.values()
                    )
                ]

            except (
                requests.RequestException,
                ValueError,
                RuntimeError,
            ) as error:
                last_error = error

                if attempt == 4:
                    break

                wait = min(
                    30,
                    2 ** (attempt + 1),
                )

                print(
                    f"TickerMint request failed: "
                    f"{error}. "
                    f"Retrying in {wait}s..."
                )

                time.sleep(wait)

        raise RuntimeError(
            f"TickerMint request failed "
            f"after retries: {last_error}"
        )
