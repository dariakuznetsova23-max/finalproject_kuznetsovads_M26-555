"""Persistence of rate history and the latest rate snapshot."""

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.utils import parse_timestamp, utc_now, validate_number
from valutatrade_hub.infra.database import DatabaseManager


class RatesStorage:
    """Store deduplicated observations and update only newer snapshots."""

    def __init__(self) -> None:
        self.db = DatabaseManager()
        self.db.ensure_files()

    def save(self, observations: list[dict]) -> dict:
        """Validate and save observations; return snapshot update statistics."""
        validated = []

        for observation in observations:
            record = dict(observation)
            from_code = get_currency(record["from_currency"]).code
            to_code = get_currency(record["to_currency"]).code
            timestamp = parse_timestamp(record["timestamp"]).isoformat().replace("+00:00", "Z")

            record.update(
                id=f"{from_code}_{to_code}_{timestamp}",
                from_currency=from_code,
                to_currency=to_code,
                timestamp=timestamp,
                rate=validate_number(record["rate"], positive=True, label="Курс"),
            )
            if not isinstance(record.get("source"), str) or not record["source"]:
                raise ValueError("Источник курса не может быть пустым")
            if not isinstance(record.get("meta", {}), dict):
                raise ValueError("Метаданные курса должны быть объектом")

            validated.append(record)

        updated_pairs = set()

        with self.db.transaction("history", "rates") as documents:
            history = documents["history"]
            snapshot = documents["rates"]
            existing_ids = {record["id"] for record in history}

            for record in validated:
                if record["id"] not in existing_ids:
                    history.append(record)
                    existing_ids.add(record["id"])

                pair = f"{record['from_currency']}_{record['to_currency']}"
                current = snapshot["pairs"].get(pair)

                if current is None or parse_timestamp(record["timestamp"]) > parse_timestamp(
                    current["updated_at"]
                ):
                    snapshot["pairs"][pair] = {
                        "rate": record["rate"],
                        "updated_at": record["timestamp"],
                        "source": record["source"],
                    }
                    updated_pairs.add(pair)

            if updated_pairs:
                snapshot["last_refresh"] = utc_now()

            last_refresh = snapshot["last_refresh"]

        return {
            "count": len(updated_pairs),
            "last_refresh": last_refresh,
        }
