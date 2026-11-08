"""Decorators for structured domain-operation logging."""

import inspect
import json
import logging
from functools import wraps

from valutatrade_hub.core.utils import utc_now


def log_action(action: str, verbose: bool = False):
    """Log an operation without swallowing exceptions or logging passwords."""

    def decorator(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapper(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            service = bound.arguments.get("self")
            user = getattr(service, "current_user", None)

            payload = {
                "timestamp": utc_now(),
                "action": action,
                "username": getattr(user, "username", None),
                "currency_code": bound.arguments.get("currency_code"),
                "amount": bound.arguments.get("amount"),
                "rate": None,
                "base": "USD",
            }
            logger = logging.getLogger("valutatrade.actions")

            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                payload.update(
                    result="ERROR",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                logger.info(json.dumps(payload, ensure_ascii=False, default=str))
                raise

            payload["result"] = "OK"
            if isinstance(result, dict):
                payload["rate"] = result.get("rate")
                payload["currency_code"] = result.get("currency_code", payload["currency_code"])
                if verbose:
                    payload["context"] = {
                        key: result.get(key)
                        for key in ("before", "after", "usd_before", "usd_after")
                    }

            logger.info(json.dumps(payload, ensure_ascii=False, default=str))
            return result

        return wrapper

    return decorator
