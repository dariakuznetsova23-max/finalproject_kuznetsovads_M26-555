"""Command parsing and presentation; business rules live in usecases."""

import argparse
import shlex
import sys

from prettytable import PrettyTable

from valutatrade_hub.core.currencies import CURRENCIES
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    ValutaTradeError,
)
from valutatrade_hub.core.usecases import WalletService
from valutatrade_hub.logging_config import setup_logging


class CommandParser(argparse.ArgumentParser):
    """Argument parser that does not terminate the interactive shell on errors."""

    def error(self, message: str) -> None:
        raise ValueError(f"Ошибка аргументов: {message}")


def build_parser() -> argparse.ArgumentParser:
    """Build all supported command parsers."""
    parser = CommandParser(prog="valutatrade")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("register", "login"):
        command = commands.add_parser(name)
        command.add_argument("--username", required=True)
        command.add_argument("--password", required=True)

    commands.add_parser("logout")
    commands.add_parser("exit")

    portfolio = commands.add_parser("show-portfolio")
    portfolio.add_argument("--base")

    for name in ("buy", "sell", "deposit"):
        command = commands.add_parser(name)
        command.add_argument("--currency", required=True)
        command.add_argument("--amount", required=True, type=float)

    rate = commands.add_parser("get-rate")
    rate.add_argument("--from", dest="from_code", required=True)
    rate.add_argument("--to", dest="to_code", required=True)

    update = commands.add_parser("update-rates")
    update.add_argument("--source", choices=["coingecko", "exchangerate"])

    rates = commands.add_parser("show-rates")
    rates.add_argument("--currency")
    rates.add_argument("--top", type=int)
    rates.add_argument("--base", default="USD")

    return parser


def format_number(value: float) -> str:
    """Format balances without hiding cryptocurrency precision."""
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def print_portfolio(result: dict) -> None:
    """Display a portfolio table."""
    print(f"Портфель пользователя '{result['username']}' (база: {result['base']}):")

    if not result["rows"]:
        print("Портфель пуст. Пополните USD: deposit --currency USD --amount 10000")
        return

    table = PrettyTable(["Валюта", "Баланс", f"Стоимость, {result['base']}"])
    for row in result["rows"]:
        table.add_row(
            [
                row["currency"],
                format_number(row["balance"]),
                format_number(row["value"]),
            ]
        )

    print(table)
    print(f"ИТОГО: {result['total']:,.2f} {result['base']}")


def print_rates(result: dict) -> None:
    """Display cached rates and freshness markers."""
    print(f"Последнее обновление кеша: {result['last_refresh']}")

    table = PrettyTable(["Пара", "Курс", "Обновлено, UTC", "Источник", "Статус"])
    for row in result["rows"]:
        table.add_row(
            [
                row["pair"],
                format_number(row["rate"]),
                row["updated_at"],
                row["source"],
                "УСТАРЕЛ" if row["stale"] else "свежий",
            ]
        )

    print(table)


def execute(service: WalletService, args: argparse.Namespace) -> None:
    """Dispatch a parsed command and render its result."""
    match args.command:
        case "register":
            user = service.register(args.username, args.password)
            print(
                f"Пользователь '{user.username}' зарегистрирован "
                f"(id={user.user_id}). Выполните login."
            )

        case "login":
            user = service.login(args.username, args.password)
            print(f"Вы вошли как '{user.username}'")

        case "logout":
            service.logout()
            print("Вы вышли из системы")

        case "deposit":
            result = service.deposit(args.currency, args.amount)
            print(
                f"Виртуальный USD-кошелёк пополнен: "
                f"{format_number(result['before'])} → "
                f"{format_number(result['after'])} USD"
            )

        case "buy" | "sell":
            operation = service.buy if args.command == "buy" else service.sell
            result = operation(args.currency, args.amount)
            title = "Покупка" if args.command == "buy" else "Продажа"
            value_title = "Стоимость" if args.command == "buy" else "Выручка"

            print(
                f"{title} выполнена: {format_number(result['amount'])} "
                f"{result['currency_code']} по курсу "
                f"{format_number(result['rate'])} USD/{result['currency_code']}"
            )
            print(
                f"{result['currency_code']}: "
                f"{format_number(result['before'])} → "
                f"{format_number(result['after'])}"
            )
            print(
                f"USD: {format_number(result['usd_before'])} → {format_number(result['usd_after'])}"
            )
            print(f"{value_title}: {result['value']:,.2f} USD")

        case "show-portfolio":
            print_portfolio(service.show_portfolio(args.base))

        case "get-rate":
            result = service.get_rate(args.from_code, args.to_code)
            from_code = args.from_code.strip().upper()
            to_code = args.to_code.strip().upper()

            print(
                f"Курс {from_code}→{to_code}: {result['rate']:.10f} "
                f"(обновлено: {result['updated_at']})"
            )
            print(f"Обратный курс {to_code}→{from_code}: {1 / result['rate']:.10f}")

        case "update-rates":
            print("Запуск обновления курсов...")
            result = service.update_rates(args.source)
            print(
                f"Обновлено пар: {result['count']}. Последнее обновление: {result['last_refresh']}"
            )
            if result["errors"]:
                print("Обновление завершено частично:")
                for error in result["errors"]:
                    print(f"- {error}")
                print("Подробности: logs/parser.log")

        case "show-rates":
            print_rates(service.show_rates(args.currency, args.top, args.base))


def process_tokens(
    parser: argparse.ArgumentParser,
    service: WalletService,
    tokens: list[str],
) -> tuple[bool, int]:
    """Process one command; return whether the shell should continue."""
    if not tokens:
        return True, 0

    if tokens[0] == "help":
        tokens = [*tokens[1:], "--help"]

    try:
        args = parser.parse_args(tokens)

        if args.command == "exit":
            return False, 0

        execute(service, args)
        return True, 0

    except SystemExit as exc:
        return True, int(exc.code or 0)
    except CurrencyNotFoundError as exc:
        print(exc)
        print("Поддерживаемые валюты:", ", ".join(sorted(CURRENCIES)))
        print("Справка: help get-rate")
    except ApiRequestError as exc:
        print(exc)
        print("Проверьте сеть и API-ключи, затем повторите update-rates.")
    except (ValutaTradeError, ValueError, OSError) as exc:
        print(exc)
    except (KeyError, TypeError) as exc:
        print(f"Некорректная структура данных: {exc}. Проверьте JSON в data/.")

    return True, 1


def main(argv: list[str] | None = None) -> int:
    """Start the interactive shell or execute a single stateless command."""
    try:
        setup_logging()
        service = WalletService()
    except (ValutaTradeError, ValueError, OSError) as exc:
        print(f"Ошибка запуска: {exc}")
        return 1

    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv

    if arguments:
        return process_tokens(parser, service, arguments)[1]

    print("ValutaTrade Hub — виртуальный валютный кошелёк")
    print("Справка: help. Выход: exit.")
    print("Суммы вводите с точкой: 0.05. Валюты: USD, EUR, GBP, RUB, BTC, ETH, SOL.")
    print("Используйте только учебные пароли: вводимые команды видны на экране.")

    while True:
        try:
            line = input("valuta> ")
        except (EOFError, KeyboardInterrupt):
            print("\nДо встречи!")
            return 0

        try:
            # These are our command arguments, not operating-system commands.
            # Unix-style quoting works on Windows because no shell is invoked.
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            print(f"Ошибка кавычек: {exc}")
            continue

        try:
            should_continue, _ = process_tokens(parser, service, tokens)
        except KeyboardInterrupt:
            print("\nКоманда прервана")
            continue

        if not should_continue:
            print("До встречи!")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
