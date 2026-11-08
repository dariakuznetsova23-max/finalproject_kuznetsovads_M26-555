"""Centralized parser configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from valutatrade_hub.infra.settings import SettingsLoader


@dataclass
class ParserConfig:
    """API configuration; secrets are read when an instance is created."""

    EXCHANGERATE_API_KEY: str = field(default_factory=lambda: os.getenv("EXCHANGERATE_API_KEY", ""))
    COINGECKO_API_KEY: str = field(default_factory=lambda: os.getenv("COINGECKO_API_KEY", ""))

    COINGECKO_URL: str = "https://api.coingecko.com/api/v3/simple/price"
    EXCHANGERATE_API_URL: str = "https://v6.exchangerate-api.com/v6"

    BASE_CURRENCY: str = "USD"
    FIAT_CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "RUB")
    CRYPTO_CURRENCIES: tuple[str, ...] = ("BTC", "ETH", "SOL")

    CRYPTO_ID_MAP: dict[str, str] = field(
        default_factory=lambda: {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
        }
    )

    REQUEST_TIMEOUT: int = 10

    RATES_FILE_PATH: Path = field(
        default_factory=lambda: SettingsLoader().get("data_dir") / "rates.json"
    )
    HISTORY_FILE_PATH: Path = field(
        default_factory=lambda: SettingsLoader().get("data_dir") / "exchange_rates.json"
    )
