"""Currency hierarchy and currency registry."""

from abc import ABC, abstractmethod

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.core.utils import normalize_code, validate_number


class Currency(ABC):
    """Abstract currency with a common display interface."""

    def __init__(self, name: str, code: str) -> None:
        self.name = name
        self.code = code

    @property
    def name(self) -> str:
        """Return the human-readable name."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Название валюты не может быть пустым")
        self._name = value.strip()

    @property
    def code(self) -> str:
        """Return the normalized ticker."""
        return self._code

    @code.setter
    def code(self, value: str) -> None:
        self._code = normalize_code(value)

    @abstractmethod
    def get_display_info(self) -> str:
        """Return a display string for a concrete currency."""


class FiatCurrency(Currency):
    """A government-issued currency."""

    def __init__(self, name: str, code: str, issuing_country: str) -> None:
        super().__init__(name, code)
        if not isinstance(issuing_country, str) or not issuing_country.strip():
            raise ValueError("Страна или зона эмиссии не может быть пустой")
        self.issuing_country = issuing_country.strip()

    def get_display_info(self) -> str:
        """Return fiat currency details."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """A cryptocurrency with algorithm and capitalization metadata."""

    def __init__(
        self,
        name: str,
        code: str,
        algorithm: str,
        market_cap: float,
    ) -> None:
        super().__init__(name, code)
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise ValueError("Алгоритм не может быть пустым")
        self.algorithm = algorithm.strip()
        self.market_cap = validate_number(market_cap, label="Капитализация")

    def get_display_info(self) -> str:
        """Return cryptocurrency details."""
        capitalization = f"{self.market_cap:.2e}"
        return (
            f"[CRYPTO] {self.code} — {self.name} (Algo: {self.algorithm}, MCAP: {capitalization})"
        )


CURRENCIES: dict[str, Currency] = {
    "USD": FiatCurrency("US Dollar", "USD", "United States"),
    "EUR": FiatCurrency("Euro", "EUR", "Eurozone"),
    "GBP": FiatCurrency("Pound Sterling", "GBP", "United Kingdom"),
    "RUB": FiatCurrency("Russian Ruble", "RUB", "Russia"),
    "BTC": CryptoCurrency("Bitcoin", "BTC", "SHA-256", 1.12e12),
    "ETH": CryptoCurrency("Ethereum", "ETH", "Proof of Stake", 4.0e11),
    "SOL": CryptoCurrency("Solana", "SOL", "Proof of Stake", 6.0e10),
}


def get_currency(code: str) -> Currency:
    """Return a registered currency or raise CurrencyNotFoundError."""
    normalized = normalize_code(code)
    try:
        return CURRENCIES[normalized]
    except KeyError as exc:
        raise CurrencyNotFoundError(normalized) from exc
