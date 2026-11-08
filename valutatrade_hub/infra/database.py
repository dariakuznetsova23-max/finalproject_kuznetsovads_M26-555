"""Singleton JSON storage with atomic writes and process locking."""

import copy
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.utils import make_json_cache
from valutatrade_hub.infra.settings import SettingsLoader

DEFAULT_DOCUMENTS = {
    "users": [],
    "portfolios": [],
    "rates": {"pairs": {}, "last_refresh": None},
    "history": [],
}

FILE_NAMES = {
    "users": "users.json",
    "portfolios": "portfolios.json",
    "rates": "rates.json",
    "history": "exchange_rates.json",
}


class DatabaseManager:
    """A single JSON repository shared inside the application."""

    _instance = None
    _guard = threading.Lock()

    def __new__(cls):
        with cls._guard:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._thread_lock = threading.RLock()
                instance._cached_reader = make_json_cache()
                cls._instance = instance
        return cls._instance

    def path(self, name: str) -> Path:
        """Return the configured path of a known document."""
        if name not in FILE_NAMES:
            raise ValueError(f"Неизвестное хранилище: {name}")
        return SettingsLoader().get("data_dir") / FILE_NAMES[name]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Acquire thread and cross-process locks for the data directory."""
        with self._thread_lock:
            directory = SettingsLoader().get("data_dir")
            directory.mkdir(parents=True, exist_ok=True)
            lock_path = directory / ".storage.lock"
            timeout = float(SettingsLoader().get("storage_lock_timeout", 10))

            with lock_path.open("a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()

                deadline = time.monotonic() + timeout

                while True:
                    try:
                        lock_file.seek(0)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(
                                lock_file.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise StorageError("Не удалось получить блокировку хранилища") from exc
                        time.sleep(0.05)

                try:
                    yield
                finally:
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _load(self, name: str):
        path = self.path(name)
        if not path.exists():
            return copy.deepcopy(DEFAULT_DOCUMENTS[name])

        try:
            if name == "rates":
                result = self._cached_reader(path)
            else:
                with path.open("r", encoding="utf-8-sig") as file:
                    result = json.load(file)
        except (OSError, ValueError) as exc:
            raise StorageError(
                f"Не удалось прочитать {path.name}. Проверьте JSON; исходный файл не перезаписан."
            ) from exc

        expected_type = dict if name == "rates" else list
        if not isinstance(result, expected_type):
            raise StorageError(f"Неверный формат файла {path.name}")

        if name == "rates" and not isinstance(result.get("pairs"), dict):
            raise StorageError("В rates.json отсутствует объект 'pairs'")

        return result

    def _atomic_write(self, name: str, value) -> None:
        path = self.path(name)
        temporary_name = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(value, temporary, ensure_ascii=False, indent=2, allow_nan=False)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())

            os.replace(temporary_name, path)
        except (OSError, TypeError, ValueError) as exc:
            raise StorageError(f"Не удалось записать {path.name}") from exc
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def ensure_files(self) -> None:
        """Create missing documents without overwriting existing ones."""
        with self._locked():
            for name, default in DEFAULT_DOCUMENTS.items():
                if not self.path(name).exists():
                    self._atomic_write(name, copy.deepcopy(default))

    def read(self, name: str):
        """Read a document safely."""
        with self._locked():
            return self._load(name)

    def write(self, name: str, value) -> None:
        """Replace a document atomically."""
        with self._locked():
            self._atomic_write(name, value)

    @contextmanager
    def transaction(self, *names: str) -> Iterator[dict]:
        """Lock read-modify-write; each changed document is replaced atomically."""
        with self._locked():
            documents = {name: self._load(name) for name in names}
            original = copy.deepcopy(documents)

            yield documents

            for name in names:
                if documents[name] != original[name]:
                    self._atomic_write(name, documents[name])
