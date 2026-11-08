"""Validation, UTC timestamps and closure-based JSON caching."""

import copy
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


def normalize_code(code: str) -> str:
    """Normalize and validate a currency ticker."""
    if not isinstance(code, str):
        raise ValueError("Код валюты должен быть строкой")

    normalized = code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}", normalized):
        raise ValueError("Код валюты: от 2 до 5 латинских букв без пробелов")

    return normalized


def validate_number(
    value: Any,
    *,
    positive: bool = False,
    label: str = "Значение",
) -> float:
    """Validate finite numeric values and reject booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} должно быть числом")

    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} должно быть конечным числом") from exc

    if not math.isfinite(number):
        raise ValueError(f"{label} должно быть конечным числом")

    if positive and number <= 0:
        raise ValueError(f"{label} должно быть положительным числом")

    if not positive and number < 0:
        raise ValueError(f"{label} не может быть отрицательным")

    return number


def validate_amount(value: Any) -> float:
    """Validate a transaction amount."""
    try:
        return validate_number(value, positive=True, label="'amount'")
    except ValueError as exc:
        raise ValueError("'amount' должен быть положительным конечным числом") from exc


def utc_now() -> str:
    """Return the current UTC timestamp in ISO format."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    """Parse ISO timestamps; treat legacy naive timestamps as UTC."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def make_json_cache() -> Callable[[Path], Any]:
    """Create a reader cached by file identity, size and modification time."""
    cache: dict[Path, tuple[tuple[int, int, int, int], Any]] = {}

    def read_cached(path: Path) -> Any:
        resolved = path.resolve()
        stat = resolved.stat()
        fingerprint = (
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            stat.st_size,
            stat.st_ino,
        )
        cached = cache.get(resolved)

        if cached is None or cached[0] != fingerprint:
            with resolved.open("r", encoding="utf-8-sig") as file:
                value = json.load(file)
            cache[resolved] = (fingerprint, value)

        return copy.deepcopy(cache[resolved][1])

    return read_cached
