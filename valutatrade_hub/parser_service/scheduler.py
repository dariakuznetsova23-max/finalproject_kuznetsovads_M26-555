"""Standalone periodic parser process."""

import argparse
import logging
import time

from valutatrade_hub.core.exceptions import ValutaTradeError
from valutatrade_hub.logging_config import setup_logging
from valutatrade_hub.parser_service.updater import build_updater


def main() -> int:
    """Run updates periodically until interrupted."""
    parser = argparse.ArgumentParser(description="Periodic currency rate updater")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--source", choices=["coingecko", "exchangerate"])
    args = parser.parse_args()

    if args.interval < 10:
        parser.error("--interval должен быть не меньше 10 секунд")

    setup_logging()
    logger = logging.getLogger("valutatrade.parser")
    updater = build_updater(args.source)

    print(f"Парсер запущен. Интервал: {args.interval} с. Остановка: Ctrl+C.")

    try:
        while True:
            started = time.monotonic()

            try:
                report = updater.run_update()
                print(f"Обновлено: {report['count']}; ошибок источников: {len(report['errors'])}")
            except (ValutaTradeError, ValueError, OSError) as exc:
                logger.error("Scheduled update failed: %s", exc)
                print(f"Ошибка обновления: {exc}")

            elapsed = time.monotonic() - started
            time.sleep(max(0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\nПарсер остановлен.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
