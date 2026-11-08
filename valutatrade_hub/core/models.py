"""User, wallet and portfolio domain models."""

import hashlib
import hmac
import re
import secrets
from datetime import datetime
from typing import Callable

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import InsufficientFundsError
from valutatrade_hub.core.utils import (
    parse_timestamp,
    utc_now,
    validate_amount,
    validate_number,
)


class User:
    """A registered user with a salted SHA-256 password hash."""

    def __init__(
        self,
        user_id: int,
        username: str,
        hashed_password: str,
        salt: str,
        registration_date: str | datetime,
    ) -> None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id должен быть положительным целым числом")
        if not isinstance(salt, str) or not salt:
            raise ValueError("Соль не может быть пустой")
        if not isinstance(hashed_password, str) or not re.fullmatch(
            r"[0-9a-f]{64}", hashed_password
        ):
            raise ValueError("Некорректный SHA-256 хеш пароля")

        self._user_id = user_id
        self.username = username
        self._salt = salt
        self._hashed_password = hashed_password
        self._registration_date = (
            parse_timestamp(registration_date)
            if isinstance(registration_date, str)
            else parse_timestamp(registration_date.isoformat())
        )

    @classmethod
    def create(cls, user_id: int, username: str, password: str) -> "User":
        """Create a user with a generated salt."""
        cls._validate_password(password)
        salt = secrets.token_hex(16)
        password_hash = cls._hash_password(password, salt)
        return cls(user_id, username, password_hash, salt, utc_now())

    @staticmethod
    def _validate_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < 4:
            raise ValueError("Пароль должен быть не короче 4 символов")

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()

    @property
    def user_id(self) -> int:
        """Return the immutable identifier."""
        return self._user_id

    @property
    def username(self) -> str:
        """Return the username."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        self._username = value.strip()

    @property
    def hashed_password(self) -> str:
        """Return the password hash for persistence."""
        return self._hashed_password

    @property
    def salt(self) -> str:
        """Return the user salt."""
        return self._salt

    @property
    def registration_date(self) -> datetime:
        """Return the registration timestamp."""
        return self._registration_date

    def get_user_info(self) -> dict:
        """Return public user information without password or salt."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": self.registration_date.isoformat(),
        }

    def change_password(self, new_password: str) -> None:
        """Replace the password and rotate its salt."""
        self._validate_password(new_password)
        self._salt = secrets.token_hex(16)
        self._hashed_password = self._hash_password(new_password, self._salt)

    def verify_password(self, password: str) -> bool:
        """Compare a candidate password with the stored hash."""
        if not isinstance(password, str):
            return False
        candidate = self._hash_password(password, self.salt)
        return hmac.compare_digest(candidate, self.hashed_password)

    def to_dict(self) -> dict:
        """Serialize the complete user record for storage."""
        return {
            **self.get_user_info(),
            "hashed_password": self.hashed_password,
            "salt": self.salt,
        }


class Wallet:
    """A balance in one registered currency."""

    def __init__(self, currency_code: str, balance: float = 0.0) -> None:
        self._currency_code = get_currency(currency_code).code
        self.balance = balance

    @property
    def currency_code(self) -> str:
        """Return the currency ticker."""
        return self._currency_code

    @property
    def balance(self) -> float:
        """Return the current balance."""
        return self._balance

    @balance.setter
    def balance(self, value: float) -> None:
        self._balance = validate_number(value, label="Баланс")

    def deposit(self, amount: float) -> None:
        """Increase the balance by a positive finite amount."""
        self.balance = self.balance + validate_amount(amount)

    def withdraw(self, amount: float) -> None:
        """Decrease the balance without allowing overdrafts."""
        amount = validate_amount(amount)
        if amount > self.balance:
            raise InsufficientFundsError(self.balance, amount, self.currency_code)
        self.balance = self.balance - amount

    def get_balance_info(self) -> str:
        """Return a readable balance."""
        return f"{self.currency_code}: {self.balance:.8f}"

    def to_dict(self) -> dict:
        """Serialize the wallet."""
        return {
            "currency_code": self.currency_code,
            "balance": self.balance,
        }


class Portfolio:
    """All wallets belonging to one user."""

    def __init__(
        self,
        user_id: int,
        user: User,
        wallets: dict[str, Wallet] | None = None,
    ) -> None:
        if user_id != user.user_id:
            raise ValueError("Пользователь не соответствует владельцу портфеля")

        self._user_id = user_id
        self._user = user
        self._wallets: dict[str, Wallet] = {}

        for code, wallet in (wallets or {}).items():
            normalized = get_currency(code).code
            if not isinstance(wallet, Wallet) or wallet.currency_code != normalized:
                raise ValueError("Некорректный кошелёк в портфеле")
            self._wallets[normalized] = wallet

    @property
    def user_id(self) -> int:
        """Return the owner identifier."""
        return self._user_id

    @property
    def user(self) -> User:
        """Return the owner without allowing reassignment."""
        return self._user

    @property
    def wallets(self) -> dict[str, Wallet]:
        """Return a shallow copy of the wallet mapping."""
        return self._wallets.copy()

    def add_currency(self, currency_code: str) -> Wallet:
        """Create a wallet if absent and return the existing one otherwise."""
        code = get_currency(currency_code).code
        if code not in self._wallets:
            self._wallets[code] = Wallet(code)
        return self._wallets[code]

    def get_wallet(self, currency_code: str) -> Wallet | None:
        """Return a wallet or None when it has not been created."""
        return self._wallets.get(get_currency(currency_code).code)

    def get_total_value(
        self,
        base_currency: str = "USD",
        rate_getter: Callable | None = None,
    ) -> float:
        """Calculate total value using a supplied or default rate service."""
        base = get_currency(base_currency).code

        if rate_getter is None:
            from valutatrade_hub.core.rates import RateService

            rate_getter = RateService().get_rate

        total = 0.0
        for code, wallet in self._wallets.items():
            if wallet.balance == 0:
                continue
            rate = rate_getter(code, base)["rate"]
            total += wallet.balance * rate

        return validate_number(total, label="Стоимость портфеля")

    def to_dict(self) -> dict:
        """Serialize the portfolio."""
        return {
            "user_id": self.user_id,
            "wallets": {code: wallet.to_dict() for code, wallet in self._wallets.items()},
        }

    @classmethod
    def from_dict(cls, record: dict, user: User) -> "Portfolio":
        """Restore wallets from JSON."""
        wallets = {code: Wallet(code, item["balance"]) for code, item in record["wallets"].items()}
        return cls(record["user_id"], user, wallets)
