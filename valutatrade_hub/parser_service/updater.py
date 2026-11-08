"""Coordinate independent rate providers and persistent storage."""

import logging

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import utc_now
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage


class RatesUpdater:
    """Fetch providers independently and preserve successful results."""

    def __init__(
        self,
        clients: list[BaseApiClient],
        storage: RatesStorage,
    ) -> None:
        self.clients = clients
        self.storage = storage
        self.logger = logging.getLogger("valutatrade.parser")

    def run_update(self) -> dict:
        """Update available providers; fail only if none produced data."""
        observations = []
        errors = []
        self.logger.info("Starting rates update")

        for client in self.clients:
            self.logger.info("Fetching from %s", client.source)

            try:
                rates = client.fetch_rates()
            except ApiRequestError as exc:
                errors.append(str(exc))
                self.logger.warning("Provider failed: %s", exc)
                continue

            for pair, rate in rates.items():
                from_code, to_code = pair.split("_")
                metadata = dict(client.last_meta.get(pair, {}))
                timestamp = metadata.pop("timestamp", utc_now())

                observations.append(
                    {
                        "from_currency": from_code,
                        "to_currency": to_code,
                        "rate": rate,
                        "timestamp": timestamp,
                        "source": client.source,
                        "meta": metadata,
                    }
                )

            self.logger.info("%s: received %s rates", client.source, len(rates))

        if not observations:
            reason = "; ".join(errors) or "Источники не вернули курсы"
            self.logger.error("Update failed: no observations")
            raise ApiRequestError(reason)

        report = self.storage.save(observations)
        report["errors"] = errors

        self.logger.info(
            "Update completed: updated=%s errors=%s last_refresh=%s",
            report["count"],
            len(errors),
            report["last_refresh"],
        )
        return report


def build_updater(source: str | None = None) -> RatesUpdater:
    """Create an updater for all sources or a selected source."""
    config = ParserConfig()

    if source is None:
        clients = [CoinGeckoClient(config), ExchangeRateApiClient(config)]
    elif source == "coingecko":
        clients = [CoinGeckoClient(config)]
    elif source == "exchangerate":
        clients = [ExchangeRateApiClient(config)]
    else:
        raise ValueError("Источник должен быть coingecko или exchangerate")

    return RatesUpdater(clients, RatesStorage())
