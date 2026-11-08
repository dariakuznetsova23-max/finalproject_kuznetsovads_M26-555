"""Rotating file logging configuration."""

import logging
import time
from logging.handlers import RotatingFileHandler

from valutatrade_hub.infra.settings import SettingsLoader


def setup_logging() -> None:
    """Configure separate action and parser log files."""
    settings = SettingsLoader()
    directory = settings.get("logs_dir")
    directory.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        settings.get("log_format"),
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime

    level_name = str(settings.get("log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    for logger_name, filename in (
        ("valutatrade.actions", "actions.log"),
        ("valutatrade.parser", "parser.log"),
    ):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False

        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

        handler = RotatingFileHandler(
            directory / filename,
            maxBytes=int(settings.get("log_max_bytes")),
            backupCount=int(settings.get("log_backup_count")),
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
