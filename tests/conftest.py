"""Общие фикстуры: приложение с тестовым конфигом, клиент, сессия SQLAlchemy."""
from __future__ import annotations

import pytest

from receiver import create_app


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def make_app(db_path):
    """Фабрика приложения: тесты переопределяют поведение очереди (сбои, паузы)."""

    def _make(**overrides):
        config = {
            "TESTING": True,
            "DATABASE": db_path,
            "FAILURE_RATE": 0.0,  # по умолчанию в тестах заглушка не падает
            "BACKOFF_BASE": 0.0,  # ...и паузы между попытками не длятся
            "MAX_ATTEMPTS": 3,
            "LEASE_TIMEOUT": 60.0,
        }
        config.update(overrides)
        app = create_app(config)
        return app

    return _make


@pytest.fixture()
def app(make_app):
    return make_app()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    """Сессия SQLAlchemy для тестов — берется из инициализированного приложения."""
    session = app.db_session_factory()
    yield session
    session.close()


@pytest.fixture()
def sample_event():
    def _make(**overrides):
        event = {
            "event_id": "abc-123",
            "type": "payment_succeeded",
            "payload": {"amount": 100},
        }
        event.update(overrides)
        return event

    return _make