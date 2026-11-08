"""Cached exchange-rate lookup and freshness checks."""

from datetime import UTC, datetime

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import ApiRequestError, StorageError
from valutatrade_hub.core.utils import parse_timestamp, utc_now, validate_number
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class RateService:
    """Resolve direct, inverse and cross rates using USD as a pivot."""

    def __init__(self) -> None:
        self.db = DatabaseManager()
        self.settings = SettingsLoader()

    def _to_usd(self, code: str, pairs: dict) -> tuple[float, str, str]:
        if code == "USD":
            return 1.0, utc_now(), "identity"

        direct = pairs.get(f"{code}_USD")
        inverse = pairs.get(f"USD_{code}")

        if direct is None and inverse is None:
            raise ApiRequestError(f"Курс {code}→USD недоступен. Выполните update-rates.")

        entry = direct if direct is not None else inverse

        try:
            rate = validate_number(entry["rate"], positive=True, label="Курс")
            updated_at = entry["updated_at"]
            parse_timestamp(updated_at)
            source = str(entry.get("source", "unknown"))
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise StorageError(f"Некорректная запись курса для {code}") from exc

        if direct is None:
            rate = validate_number(1.0 / rate, positive=True, label="Курс")

        return rate, updated_at, source

    def get_rate(
        self,
        from_code: str,
        to_code: str,
        *,
        allow_stale: bool = False,
    ) -> dict:
        """Return a rate, timestamp and freshness metadata."""
        from_code = get_currency(from_code).code
        to_code = get_currency(to_code).code

        if from_code == to_code:
            return {
                "rate": 1.0,
                "updated_at": utc_now(),
                "source": "identity",
                "stale": False,
            }

        pairs = self.db.read("rates")["pairs"]
        left_rate, left_time, left_source = self._to_usd(from_code, pairs)
        right_rate, right_time, right_source = self._to_usd(to_code, pairs)

        timestamps = []
        if from_code != "USD":
            timestamps.append(parse_timestamp(left_time))
        if to_code != "USD":
            timestamps.append(parse_timestamp(right_time))

        updated = min(timestamps)
        age = (datetime.now(UTC) - updated).total_seconds()
        ttl = self.settings.get("rates_ttl_seconds")
        stale = age > ttl

        if age < -60:
            raise StorageError("Время курса находится в будущем. Проверьте системные часы.")

        if stale and not allow_stale:
            raise ApiRequestError(
                f"Кеш {from_code}→{to_code} устарел (TTL={ttl} с). Выполните update-rates."
            )

        rate = validate_number(left_rate / right_rate, positive=True, label="Курс")
        sources = sorted({left_source, right_source} - {"identity"})

        return {
            "rate": rate,
            "updated_at": updated.isoformat().replace("+00:00", "Z"),
            "source": ", ".join(sources),
            "stale": stale,
        }
