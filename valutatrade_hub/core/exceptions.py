"""Domain and infrastructure exceptions."""


class ValutaTradeError(Exception):
    """Base application exception."""


class InsufficientFundsError(ValutaTradeError):
    """Raised when a wallet has insufficient funds."""

    def __init__(self, available: float, required: float, code: str) -> None:
        self.available = available
        self.required = required
        self.code = code
        super().__init__(
            f"Недостаточно средств: доступно {available:.8f} {code}, "
            f"требуется {required:.8f} {code}"
        )


class CurrencyNotFoundError(ValutaTradeError):
    """Raised when a currency is not registered."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Неизвестная валюта '{code}'")


class ApiRequestError(ValutaTradeError):
    """Raised when a rate provider or fresh rate is unavailable."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")


class StorageError(ValutaTradeError):
    """Raised when persistent storage cannot be read or written."""
