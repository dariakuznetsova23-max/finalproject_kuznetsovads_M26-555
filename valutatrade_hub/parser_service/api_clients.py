"""HTTP clients for CoinGecko and ExchangeRate-API."""

import time
from abc import ABC, abstractmethod

import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import utc_now, validate_number
from valutatrade_hub.parser_service.config import ParserConfig


class BaseApiClient(ABC):
    """Common API client interface."""

    source = "unknown"

    def __init__(self, config: ParserConfig | None = None) -> None:
        self.config = config or ParserConfig()
        self.last_meta: dict[str, dict] = {}

    def _request(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[dict, dict]:
        started = time.perf_counter()
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "ValutaTradeHub/0.1",
        }
        request_headers.update(headers or {})

        try:
            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                timeout=self.config.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise ApiRequestError(f"{self.source}: превышено время ожидания") from exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            explanation = {
                401: "неверный или отсутствующий API-ключ",
                403: "доступ запрещён; проверьте ключ и тариф",
                429: "превышен лимит запросов; повторите позже",
            }.get(status, "ошибка HTTP")
            raise ApiRequestError(f"{self.source}: HTTP {status}, {explanation}") from exc
        except requests.exceptions.RequestException as exc:
            # Do not include the URL: it may contain an API key.
            raise ApiRequestError(f"{self.source}: сетевая ошибка ({type(exc).__name__})") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiRequestError(f"{self.source}: ответ не является JSON") from exc

        if not isinstance(payload, dict):
            raise ApiRequestError(f"{self.source}: ожидался JSON-объект")

        metadata = {
            "timestamp": utc_now(),
            "request_ms": round((time.perf_counter() - started) * 1000, 2),
            "status_code": response.status_code,
            "etag": response.headers.get("ETag"),
        }
        return payload, metadata

    @abstractmethod
    def fetch_rates(self) -> dict[str, float]:
        """Fetch rates normalized to CODE_USD."""


class CoinGeckoClient(BaseApiClient):
    """Fetch cryptocurrency prices in USD."""

    source = "CoinGecko"

    def fetch_rates(self) -> dict[str, float]:
        """Request all configured cryptocurrencies in one HTTP call."""
        if self.config.BASE_CURRENCY != "USD":
            raise ApiRequestError("CoinGecko: в этом проекте базой должен быть USD")

        try:
            ids = [self.config.CRYPTO_ID_MAP[code] for code in self.config.CRYPTO_CURRENCIES]
        except KeyError as exc:
            raise ApiRequestError("CoinGecko: отсутствует ID валюты в конфигурации") from exc

        headers = {}
        if self.config.COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = self.config.COINGECKO_API_KEY

        payload, metadata = self._request(
            self.config.COINGECKO_URL,
            params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
            headers=headers,
        )

        rates = {}
        self.last_meta = {}

        try:
            for code in self.config.CRYPTO_CURRENCIES:
                raw_id = self.config.CRYPTO_ID_MAP[code]
                item = payload[raw_id]
                pair = f"{code}_USD"
                rates[pair] = validate_number(item["usd"], positive=True, label="Курс")
                self.last_meta[pair] = {
                    **metadata,
                    "raw_id": raw_id,
                    "provider_updated_at": item.get("last_updated_at"),
                }
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiRequestError("CoinGecko: некорректные данные курса") from exc

        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Fetch fiat rates and invert USD-based quotes."""

    source = "ExchangeRate-API"

    def fetch_rates(self) -> dict[str, float]:
        """Return fiat-to-USD rates rather than USD-to-fiat rates."""
        if not self.config.EXCHANGERATE_API_KEY:
            raise ApiRequestError("ExchangeRate-API: задайте переменную EXCHANGERATE_API_KEY")
        if self.config.BASE_CURRENCY != "USD":
            raise ApiRequestError("ExchangeRate-API: в этом проекте базой должен быть USD")

        url = f"{self.config.EXCHANGERATE_API_URL}/{self.config.EXCHANGERATE_API_KEY}/latest/USD"
        payload, metadata = self._request(url)

        if payload.get("result") != "success":
            raise ApiRequestError(
                "ExchangeRate-API: запрос отклонён; проверьте ключ, тариф и лимит"
            )

        if payload.get("base_code") != "USD":
            raise ApiRequestError("ExchangeRate-API: неожиданная базовая валюта")

        # Actual v6 responses use conversion_rates.
        # The fallback supports the simplified example from the assignment.
        raw_rates = payload.get("conversion_rates", payload.get("rates"))
        if not isinstance(raw_rates, dict):
            raise ApiRequestError("ExchangeRate-API: отсутствуют conversion_rates")

        rates = {}
        self.last_meta = {}

        try:
            for code in self.config.FIAT_CURRENCIES:
                usd_to_currency = validate_number(raw_rates[code], positive=True, label="Курс")
                pair = f"{code}_USD"
                rates[pair] = validate_number(1.0 / usd_to_currency, positive=True, label="Курс")
                self.last_meta[pair] = {
                    **metadata,
                    "raw_id": code,
                    "provider_updated_at": payload.get("time_last_update_unix"),
                }
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ApiRequestError("ExchangeRate-API: некорректные данные курса") from exc

        return rates
