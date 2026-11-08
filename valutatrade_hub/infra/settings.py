"""Singleton configuration loaded from pyproject.toml and environment."""

import copy
import os
import threading
import tomllib
from pathlib import Path
from typing import Any


class SettingsLoader:
    """A single shared configuration instance."""

    _instance = None
    _guard = threading.Lock()

    def __new__(cls):
        # __new__ keeps Singleton creation explicit and easy to read.
        with cls._guard:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance.reload()
                cls._instance = instance
        return cls._instance

    def reload(self) -> None:
        """Reload settings relative to the current working directory."""
        root = Path.cwd()
        settings = {
            "data_dir": "data",
            "logs_dir": "logs",
            "rates_ttl_seconds": 300,
            "default_base_currency": "USD",
            "log_level": "INFO",
            "log_format": "%(levelname)s %(asctime)s %(message)s",
            "log_max_bytes": 1_048_576,
            "log_backup_count": 3,
            "storage_lock_timeout": 10,
        }

        project_file = root / "pyproject.toml"
        if project_file.exists():
            with project_file.open("rb") as file:
                project = tomllib.load(file)
            settings.update(project.get("tool", {}).get("valutatrade", {}))

        settings["data_dir"] = (
            Path(os.getenv("VALUTATRADE_DATA_DIR", str(settings["data_dir"])))
            .expanduser()
            .resolve()
        )

        settings["logs_dir"] = (
            Path(os.getenv("VALUTATRADE_LOG_DIR", str(settings["logs_dir"]))).expanduser().resolve()
        )

        settings["rates_ttl_seconds"] = int(settings["rates_ttl_seconds"])
        if settings["rates_ttl_seconds"] <= 0:
            raise ValueError("TTL должен быть положительным")

        self._settings = settings

    def get(self, key: str, default: Any = None) -> Any:
        """Return a defensive copy of a setting."""
        return copy.deepcopy(self._settings.get(key, default))
