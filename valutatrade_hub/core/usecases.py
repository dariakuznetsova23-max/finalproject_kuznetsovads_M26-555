"""Application use cases; no terminal input or output."""

from valutatrade_hub.core.currencies import (
    CURRENCIES,
    CryptoCurrency,
    get_currency,
)
from valutatrade_hub.core.exceptions import ApiRequestError, InsufficientFundsError
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.utils import validate_amount, validate_number
from valutatrade_hub.decorators import log_action
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class WalletService:
    """Manage an in-memory session backed by persistent JSON documents."""

    def __init__(self) -> None:
        self.db = DatabaseManager()
        self.db.ensure_files()
        self.settings = SettingsLoader()
        self.rates = RateService()
        self.current_user: User | None = None

    def _require_user(self) -> User:
        if self.current_user is None:
            raise ValueError("Сначала выполните login")
        return self.current_user

    def _portfolio_from_rows(self, rows: list, user: User) -> Portfolio:
        for record in rows:
            if record["user_id"] == user.user_id:
                return Portfolio.from_dict(record, user)

        # Also repairs an absent empty portfolio after interrupted registration.
        return Portfolio(user.user_id, user)

    @staticmethod
    def _save_portfolio(rows: list, portfolio: Portfolio) -> None:
        record = portfolio.to_dict()
        for index, current in enumerate(rows):
            if current["user_id"] == portfolio.user_id:
                rows[index] = record
                return
        rows.append(record)

    def register(self, username: str, password: str) -> User:
        """Register a unique username and create an empty portfolio."""
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        username = username.strip()

        with self.db.transaction("users", "portfolios") as documents:
            users = documents["users"]
            if any(record["username"] == username for record in users):
                raise ValueError(f"Имя пользователя '{username}' уже занято")

            user_id = max((record["user_id"] for record in users), default=0) + 1
            user = User.create(user_id, username, password)
            users.append(user.to_dict())
            documents["portfolios"].append(Portfolio(user.user_id, user).to_dict())

        return user

    def login(self, username: str, password: str) -> User:
        """Authenticate and replace the current session on success."""
        username = username.strip()
        records = self.db.read("users")

        record = next(
            (record for record in records if record["username"] == username),
            None,
        )
        if record is None:
            raise ValueError(f"Пользователь '{username}' не найден")

        user = User(**record)
        if not user.verify_password(password):
            raise ValueError("Неверный пароль")

        self.current_user = user
        return user

    def logout(self) -> None:
        """Clear the current in-memory session."""
        self.current_user = None

    @log_action("DEPOSIT", verbose=True)
    def deposit(self, currency_code: str, amount: float) -> dict:
        """Top up virtual USD; this is not a real payment operation."""
        user = self._require_user()
        code = get_currency(currency_code).code
        amount = validate_amount(amount)

        if code != "USD":
            raise ValueError("Учебное пополнение доступно только для USD")

        with self.db.transaction("portfolios") as documents:
            rows = documents["portfolios"]
            portfolio = self._portfolio_from_rows(rows, user)
            wallet = portfolio.add_currency("USD")
            before = wallet.balance
            wallet.deposit(amount)
            self._save_portfolio(rows, portfolio)

            result = {
                "currency_code": code,
                "amount": amount,
                "before": before,
                "after": wallet.balance,
            }

        return result

    @log_action("BUY", verbose=True)
    def buy(self, currency_code: str, amount: float) -> dict:
        """Buy units of a currency by debiting the USD wallet."""
        user = self._require_user()
        code = get_currency(currency_code).code
        amount = validate_amount(amount)

        if code == "USD":
            raise ValueError("USD — расчётная валюта. Используйте deposit для пополнения.")

        rate = self.rates.get_rate(code, "USD")["rate"]
        cost = validate_number(amount * rate, positive=True, label="Стоимость")

        with self.db.transaction("portfolios") as documents:
            rows = documents["portfolios"]
            portfolio = self._portfolio_from_rows(rows, user)
            usd_wallet = portfolio.get_wallet("USD")

            available = usd_wallet.balance if usd_wallet else 0.0
            if usd_wallet is None or available < cost:
                raise InsufficientFundsError(available, cost, "USD")

            wallet = portfolio.add_currency(code)
            before = wallet.balance
            usd_before = usd_wallet.balance

            usd_wallet.withdraw(cost)
            wallet.deposit(amount)

            self._save_portfolio(rows, portfolio)
            result = {
                "currency_code": code,
                "amount": amount,
                "rate": rate,
                "value": cost,
                "before": before,
                "after": wallet.balance,
                "usd_before": usd_before,
                "usd_after": usd_wallet.balance,
            }

        return result

    @log_action("SELL", verbose=True)
    def sell(self, currency_code: str, amount: float) -> dict:
        """Sell currency units and credit their USD proceeds."""
        user = self._require_user()
        code = get_currency(currency_code).code
        amount = validate_amount(amount)

        if code == "USD":
            raise ValueError("USD — расчётная валюта. Продавайте другие валюты за USD.")

        with self.db.transaction("portfolios") as documents:
            rows = documents["portfolios"]
            portfolio = self._portfolio_from_rows(rows, user)
            wallet = portfolio.get_wallet(code)

            if wallet is None:
                raise ValueError(
                    f"У вас нет кошелька '{code}'. Он создаётся автоматически при первой покупке."
                )
            if amount > wallet.balance:
                raise InsufficientFundsError(wallet.balance, amount, code)

        # Rate lookup uses storage too, so do not nest storage locks.
        rate = self.rates.get_rate(code, "USD")["rate"]
        revenue = validate_number(amount * rate, positive=True, label="Выручка")

        with self.db.transaction("portfolios") as documents:
            rows = documents["portfolios"]
            portfolio = self._portfolio_from_rows(rows, user)
            wallet = portfolio.get_wallet(code)

            if wallet is None:
                raise ValueError(f"У вас нет кошелька '{code}'")

            usd_wallet = portfolio.add_currency("USD")
            before = wallet.balance
            usd_before = usd_wallet.balance

            # Revalidate funds under the write lock.
            wallet.withdraw(amount)
            usd_wallet.deposit(revenue)

            self._save_portfolio(rows, portfolio)
            result = {
                "currency_code": code,
                "amount": amount,
                "rate": rate,
                "value": revenue,
                "before": before,
                "after": wallet.balance,
                "usd_before": usd_before,
                "usd_after": usd_wallet.balance,
            }

        return result

    def get_rate(self, from_code: str, to_code: str) -> dict:
        """Return a fresh direct or cross rate."""
        return self.rates.get_rate(from_code, to_code)

    def show_portfolio(self, base_currency: str | None = None) -> dict:
        """Return wallet rows and the portfolio total."""
        user = self._require_user()
        base = get_currency(base_currency or self.settings.get("default_base_currency")).code
        portfolio = self._portfolio_from_rows(self.db.read("portfolios"), user)
        rows = []

        for code, wallet in sorted(portfolio.wallets.items()):
            value = (
                wallet.balance * self.rates.get_rate(code, base)["rate"]
                if wallet.balance > 0
                else 0.0
            )
            rows.append(
                {
                    "currency": code,
                    "balance": wallet.balance,
                    "value": value,
                }
            )

        total = validate_number(
            sum(row["value"] for row in rows),
            label="Стоимость портфеля",
        )

        return {
            "username": user.username,
            "base": base,
            "rows": rows,
            "total": total,
        }

    def update_rates(self, source: str | None = None) -> dict:
        """Run the parser on demand."""
        from valutatrade_hub.parser_service.updater import build_updater

        return build_updater(source).run_update()

    def show_rates(
        self,
        currency_code: str | None = None,
        top: int | None = None,
        base_currency: str = "USD",
    ) -> dict:
        """Return cached rates, including explicit stale markers."""
        base = get_currency(base_currency).code

        if currency_code is not None:
            currency_code = get_currency(currency_code).code
        if top is not None and top <= 0:
            raise ValueError("--top должен быть положительным целым числом")

        snapshot = self.db.read("rates")
        if not snapshot["pairs"]:
            raise ValueError(
                "Локальный кеш курсов пуст. Выполните 'update-rates', чтобы загрузить данные."
            )

        codes = set()
        for pair in snapshot["pairs"]:
            left, right = pair.split("_")
            codes.update((left, right))

        codes.discard(base)

        if currency_code:
            codes = {currency_code}

        rows = []
        for code in sorted(codes):
            if top is not None and not isinstance(CURRENCIES.get(code), CryptoCurrency):
                continue

            try:
                quote = self.rates.get_rate(code, base, allow_stale=True)
            except ApiRequestError:
                if currency_code:
                    raise
                continue

            rows.append(
                {
                    "pair": f"{code}_{base}",
                    "rate": quote["rate"],
                    "updated_at": quote["updated_at"],
                    "source": quote["source"],
                    "stale": quote["stale"],
                }
            )

        if top is not None:
            rows.sort(key=lambda row: row["rate"], reverse=True)
            rows = rows[:top]

        if not rows:
            raise ValueError("Для выбранного фильтра нет доступных курсов в кеше")

        return {
            "rows": rows,
            "last_refresh": snapshot["last_refresh"],
        }
