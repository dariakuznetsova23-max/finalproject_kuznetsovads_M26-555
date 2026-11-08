"""Offline tests for models, trading, rates and parser storage."""

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import requests

from valutatrade_hub.core.currencies import FiatCurrency, get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.models import Portfolio, User, Wallet
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import WalletService
from valutatrade_hub.core.utils import utc_now
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater


class ProjectTests(unittest.TestCase):
    """Run every test with isolated storage and blocked network access."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.temporary = temporary

        # Cleanups run in reverse order, even if a test fails.
        self.addCleanup(temporary.cleanup)
        self.addCleanup(SettingsLoader().reload)

        environment = patch.dict(
            os.environ,
            {
                "VALUTATRADE_DATA_DIR": temporary.name,
                "VALUTATRADE_LOG_DIR": temporary.name,
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

        network = patch(
            "valutatrade_hub.parser_service.api_clients.requests.get",
            side_effect=AssertionError("Настоящие HTTP-запросы в тестах запрещены"),
        )
        self.http_get = network.start()
        self.addCleanup(network.stop)

        SettingsLoader().reload()
        self.db = DatabaseManager()
        self.db.ensure_files()
        self.service = WalletService()

    def seed_rates(self, timestamp: str | None = None) -> None:
        """Create a deterministic local rate snapshot."""
        timestamp = timestamp or utc_now()
        self.db.write(
            "rates",
            {
                "pairs": {
                    "BTC_USD": {
                        "rate": 50000.0,
                        "updated_at": timestamp,
                        "source": "test",
                    },
                    "EUR_USD": {
                        "rate": 1.25,
                        "updated_at": timestamp,
                        "source": "test",
                    },
                },
                "last_refresh": timestamp,
            },
        )

    def create_session(self) -> User:
        """Register and log in a test user."""
        user = self.service.register("alice", "1234")
        self.service.login("alice", "1234")
        return user

    def observation(self, timestamp: str, rate: float = 50000.0) -> dict:
        """Create a parser observation."""
        return {
            "from_currency": "BTC",
            "to_currency": "USD",
            "rate": rate,
            "timestamp": timestamp,
            "source": "test",
            "meta": {"status_code": 200},
        }

    def mock_response(self, payload: dict) -> Mock:
        """Configure a successful HTTP response without accessing the network."""
        response = Mock()
        response.status_code = 200
        response.headers = {"ETag": "test-etag"}
        response.json.return_value = payload

        self.http_get.side_effect = None
        self.http_get.return_value = response
        return response

    def test_user_hash_salt_serialization_and_password_change(self) -> None:
        first = User.create(1, "alice", "1234")
        second = User.create(2, "bob", "1234")

        self.assertNotEqual(first.salt, second.salt)
        self.assertNotEqual(first.hashed_password, second.hashed_password)
        self.assertTrue(first.verify_password("1234"))
        self.assertFalse(first.verify_password("wrong"))
        self.assertNotIn("hashed_password", first.get_user_info())
        self.assertNotIn("salt", first.get_user_info())

        restored = User(**first.to_dict())
        self.assertTrue(restored.verify_password("1234"))

        first.change_password("5678")
        self.assertTrue(first.verify_password("5678"))
        self.assertFalse(first.verify_password("1234"))

    def test_user_validation(self) -> None:
        with self.assertRaises(ValueError):
            User.create(1, "", "1234")

        with self.assertRaises(ValueError):
            User.create(1, "alice", "123")

    def test_currency_registry(self) -> None:
        self.assertIsInstance(get_currency("usd"), FiatCurrency)
        self.assertIn("[CRYPTO]", get_currency("BTC").get_display_info())

        with self.assertRaises(CurrencyNotFoundError):
            get_currency("ABC")

        with self.assertRaises(ValueError):
            get_currency("BTC1")

    def test_wallet_validation_and_withdrawal(self) -> None:
        wallet = Wallet("USD", 100)
        wallet.deposit(50)
        wallet.withdraw(25)

        self.assertEqual(wallet.balance, 125)

        with self.assertRaises(InsufficientFundsError):
            wallet.withdraw(200)

        self.assertEqual(wallet.balance, 125)

        for amount in (0, -1, float("nan"), float("inf"), True, "10"):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    wallet.deposit(amount)

        with self.assertRaises(ValueError):
            wallet.balance = -1

    def test_portfolio_serialization_and_total(self) -> None:
        self.seed_rates()
        user = User.create(1, "alice", "1234")
        portfolio = Portfolio(user.user_id, user)

        portfolio.add_currency("USD").deposit(100)
        portfolio.add_currency("BTC").deposit(0.01)

        self.assertEqual(
            portfolio.get_total_value("USD", RateService().get_rate),
            600,
        )

        restored = Portfolio.from_dict(portfolio.to_dict(), user)
        self.assertEqual(restored.get_wallet("USD").balance, 100)
        self.assertEqual(restored.get_wallet("BTC").balance, 0.01)

        # Removing an entry from the returned mapping must not remove a wallet.
        wallets_copy = portfolio.wallets
        wallets_copy.clear()
        self.assertEqual(len(portfolio.wallets), 2)

    def test_singletons(self) -> None:
        self.assertIs(SettingsLoader(), SettingsLoader())
        self.assertIs(DatabaseManager(), DatabaseManager())

    def test_registration_and_authentication(self) -> None:
        user = self.service.register("alice", "1234")

        self.assertEqual(user.user_id, 1)
        self.assertEqual(self.db.read("portfolios")[0]["wallets"], {})

        with self.assertRaises(ValueError):
            self.service.register("alice", "5678")

        with self.assertRaises(ValueError):
            self.service.login("alice", "wrong")

        with self.assertRaises(ValueError):
            self.service.login("missing", "1234")

        self.service.login("alice", "1234")
        self.assertEqual(self.service.current_user.username, "alice")

        self.service.logout()
        self.assertIsNone(self.service.current_user)

    def test_operations_require_login(self) -> None:
        with self.assertRaisesRegex(ValueError, "login"):
            self.service.deposit("USD", 100)

        with self.assertRaisesRegex(ValueError, "login"):
            self.service.buy("BTC", 0.01)

        with self.assertRaisesRegex(ValueError, "login"):
            self.service.show_portfolio()

    def test_virtual_deposit(self) -> None:
        self.create_session()

        result = self.service.deposit("USD", 1000)
        self.assertEqual(result["before"], 0)
        self.assertEqual(result["after"], 1000)

        with self.assertRaises(ValueError):
            self.service.deposit("EUR", 100)

    def test_buy_and_sell_update_both_wallets(self) -> None:
        self.create_session()
        self.seed_rates()
        self.service.deposit("USD", 1000)

        bought = self.service.buy("BTC", 0.01)
        self.assertEqual(bought["value"], 500)
        self.assertEqual(bought["usd_after"], 500)
        self.assertAlmostEqual(bought["after"], 0.01)

        sold = self.service.sell("BTC", 0.004)
        self.assertEqual(sold["value"], 200)
        self.assertEqual(sold["usd_after"], 700)
        self.assertAlmostEqual(sold["after"], 0.006)

        portfolio = self.service.show_portfolio()
        self.assertAlmostEqual(portfolio["total"], 1000)

    def test_failed_buy_does_not_change_storage(self) -> None:
        self.create_session()
        self.seed_rates()
        self.service.deposit("USD", 100)

        before = self.db.read("portfolios")

        with self.assertRaises(InsufficientFundsError):
            self.service.buy("BTC", 0.01)

        self.assertEqual(self.db.read("portfolios"), before)

    def test_failed_sell_does_not_change_storage(self) -> None:
        self.create_session()
        self.seed_rates()

        with self.assertRaisesRegex(ValueError, "нет кошелька"):
            self.service.sell("BTC", 0.01)

        self.service.deposit("USD", 1000)
        self.service.buy("BTC", 0.01)
        before = self.db.read("portfolios")

        with self.assertRaises(InsufficientFundsError):
            self.service.sell("BTC", 0.02)

        self.assertEqual(self.db.read("portfolios"), before)

    def test_direct_inverse_cross_and_identity_rates(self) -> None:
        self.seed_rates()
        rates = RateService()

        self.assertEqual(rates.get_rate("BTC", "USD")["rate"], 50000)
        self.assertAlmostEqual(rates.get_rate("USD", "BTC")["rate"], 0.00002)
        self.assertEqual(rates.get_rate("BTC", "EUR")["rate"], 40000)
        self.assertEqual(rates.get_rate("USD", "USD")["rate"], 1)

    def test_inverse_only_snapshot(self) -> None:
        timestamp = utc_now()
        self.db.write(
            "rates",
            {
                "pairs": {
                    "USD_EUR": {
                        "rate": 0.8,
                        "updated_at": timestamp,
                        "source": "test",
                    }
                },
                "last_refresh": timestamp,
            },
        )

        self.assertEqual(RateService().get_rate("EUR", "USD")["rate"], 1.25)

    def test_stale_rate_is_rejected_but_can_be_displayed(self) -> None:
        ttl = SettingsLoader().get("rates_ttl_seconds")
        old_timestamp = (datetime.now(UTC) - timedelta(seconds=ttl + 60)).isoformat()
        self.seed_rates(old_timestamp)

        with self.assertRaises(ApiRequestError):
            RateService().get_rate("BTC", "USD")

        quote = RateService().get_rate("BTC", "USD", allow_stale=True)
        self.assertTrue(quote["stale"])

        displayed = self.service.show_rates(currency_code="BTC")
        self.assertTrue(displayed["rows"][0]["stale"])

    def test_missing_rate(self) -> None:
        with self.assertRaises(ApiRequestError):
            RateService().get_rate("BTC", "USD")

    def test_rate_cache_refreshes_after_file_replacement(self) -> None:
        self.seed_rates()
        rates = RateService()
        self.assertEqual(rates.get_rate("BTC", "USD")["rate"], 50000)

        snapshot = self.db.read("rates")
        snapshot["pairs"]["BTC_USD"]["rate"] = 60000
        self.db.write("rates", snapshot)

        self.assertEqual(rates.get_rate("BTC", "USD")["rate"], 60000)

    def test_transaction_rolls_back_on_exception(self) -> None:
        before = self.db.read("users")

        with self.assertRaises(RuntimeError):
            with self.db.transaction("users") as documents:
                documents["users"].append({"user_id": 999})
                raise RuntimeError("Test rollback")

        self.assertEqual(self.db.read("users"), before)

    def test_corrupted_json_is_not_overwritten(self) -> None:
        path = self.db.path("users")
        path.write_text("{broken-json", encoding="utf-8")

        with self.assertRaises(StorageError):
            self.db.read("users")

        self.db.ensure_files()
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken-json")

    def test_history_deduplication_and_newest_snapshot(self) -> None:
        storage = RatesStorage()
        timestamp = utc_now()
        record = self.observation(timestamp)

        first = storage.save([record])
        second = storage.save([record])

        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 0)
        self.assertEqual(len(self.db.read("history")), 1)

        older = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        storage.save([self.observation(older, 10000)])

        self.assertEqual(len(self.db.read("history")), 2)
        self.assertEqual(
            self.db.read("rates")["pairs"]["BTC_USD"]["rate"],
            50000,
        )

    def test_coingecko_client_parses_mocked_response(self) -> None:
        self.mock_response(
            {
                "bitcoin": {"usd": 50000, "last_updated_at": 1700000000},
                "ethereum": {"usd": 2500},
                "solana": {"usd": 100},
            }
        )

        client = CoinGeckoClient(ParserConfig(COINGECKO_API_KEY="test-key"))
        rates = client.fetch_rates()

        self.assertEqual(rates["BTC_USD"], 50000)
        self.assertEqual(rates["ETH_USD"], 2500)
        self.assertEqual(rates["SOL_USD"], 100)
        self.assertEqual(client.last_meta["BTC_USD"]["raw_id"], "bitcoin")

        self.http_get.assert_called_once()
        self.assertEqual(
            self.http_get.call_args.kwargs["timeout"],
            client.config.REQUEST_TIMEOUT,
        )

    def test_exchangerate_client_inverts_usd_quotes(self) -> None:
        self.mock_response(
            {
                "result": "success",
                "base_code": "USD",
                "conversion_rates": {
                    "EUR": 0.8,
                    "GBP": 0.5,
                    "RUB": 100,
                },
            }
        )

        client = ExchangeRateApiClient(ParserConfig(EXCHANGERATE_API_KEY="test-key"))
        rates = client.fetch_rates()

        self.assertEqual(rates["EUR_USD"], 1.25)
        self.assertEqual(rates["GBP_USD"], 2)
        self.assertEqual(rates["RUB_USD"], 0.01)

    def test_missing_api_key_does_not_send_request(self) -> None:
        client = ExchangeRateApiClient(ParserConfig(EXCHANGERATE_API_KEY=""))

        with self.assertRaises(ApiRequestError):
            client.fetch_rates()

        self.http_get.assert_not_called()

    def test_api_timeout(self) -> None:
        self.http_get.side_effect = requests.exceptions.Timeout()
        client = CoinGeckoClient(ParserConfig())

        with self.assertRaisesRegex(ApiRequestError, "время ожидания"):
            client.fetch_rates()

    def test_api_http_error(self) -> None:
        response = self.mock_response({})
        response.status_code = 429
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaisesRegex(ApiRequestError, "429"):
            CoinGeckoClient(ParserConfig()).fetch_rates()

    def test_api_invalid_json(self) -> None:
        response = self.mock_response({})
        response.json.side_effect = ValueError("Invalid JSON")

        with self.assertRaises(ApiRequestError):
            CoinGeckoClient(ParserConfig()).fetch_rates()

    def test_api_invalid_rate(self) -> None:
        self.mock_response(
            {
                "bitcoin": {"usd": -1},
                "ethereum": {"usd": 2500},
                "solana": {"usd": 100},
            }
        )

        with self.assertRaises(ApiRequestError):
            CoinGeckoClient(ParserConfig()).fetch_rates()

    def test_updater_preserves_success_when_another_source_fails(self) -> None:
        successful = Mock(spec=BaseApiClient)
        successful.source = "MockSuccess"
        successful.fetch_rates.return_value = {"BTC_USD": 50000}
        successful.last_meta = {
            "BTC_USD": {
                "timestamp": utc_now(),
                "status_code": 200,
            }
        }

        failed = Mock(spec=BaseApiClient)
        failed.source = "MockFailure"
        failed.fetch_rates.side_effect = ApiRequestError("Test failure")

        report = RatesUpdater(
            [failed, successful],
            RatesStorage(),
        ).run_update()

        self.assertEqual(report["count"], 1)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(
            self.db.read("rates")["pairs"]["BTC_USD"]["source"],
            "MockSuccess",
        )

    def test_updater_all_sources_failed(self) -> None:
        failed = Mock(spec=BaseApiClient)
        failed.source = "MockFailure"
        failed.fetch_rates.side_effect = ApiRequestError("Test failure")

        before = self.db.read("rates")

        with self.assertRaises(ApiRequestError):
            RatesUpdater([failed], RatesStorage()).run_update()

        self.assertEqual(self.db.read("rates"), before)


if __name__ == "__main__":
    unittest.main()
