"""Конфигурация приложения: python-decouple вместо ручного os.environ.

`config = AutoConfig()` ищет `.env`/`settings.ini` от каталога текущего
модуля вверх до корня — находит `.env` в корне проекта. Приоритет значений:
os.environ → .env → аргумент default.
"""
from __future__ import annotations

from pathlib import Path

from decouple import config


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Config:
    """Настройки по умолчанию; переопределяются переменными окружения
    RECEIVER_* и аргументом create_app({...})."""

    DATABASE = config("RECEIVER_DB", default=str(PROJECT_ROOT / "receiver.db"))
    MAX_ATTEMPTS = max(1, config("RECEIVER_MAX_ATTEMPTS", default=3, cast=int))
    BACKOFF_BASE = max(0.0, config("RECEIVER_BACKOFF_BASE", default=1.0, cast=float))   # с; паузы 1с, 2с, 4с...
    FAILURE_RATE = min(1.0, max(0.0, config("RECEIVER_FAILURE_RATE", default=0.2, cast=float)))  # доля сбоев заглушки
    LEASE_TIMEOUT = max(1.0, config("RECEIVER_LEASE_TIMEOUT", default=60.0, cast=float))  # с; потом блокировка снимается
    POLL_INTERVAL = max(0.05, config("RECEIVER_POLL_INTERVAL", default=1.0, cast=float))  # с; период опроса очереди

    # HTTP-сервер (python -m receiver)
    HOST = config("RECEIVER_HOST", default="127.0.0.1")
    PORT = config("RECEIVER_PORT", default=5000, cast=int)
