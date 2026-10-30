"""Заглушка обработчика: реакция на тип события + симуляция сбоев."""
from __future__ import annotations

import logging
import random

log = logging.getLogger(__name__)


class HandlerError(Exception):
    """Симулированная ошибка обработки (намеренный сбой заглушки)."""


def _payment_succeeded(payload: dict) -> None:
    log.info("stub: mark invoice paid (amount=%s)", payload.get("amount"))


def _payment_failed(payload: dict) -> None:
    log.info("stub: record failed payment (reason=%s)", payload.get("reason"))


def _order_created(payload: dict) -> None:
    log.info("stub: start order fulfillment (order_id=%s)", payload.get("order_id"))


def _order_shipped(payload: dict) -> None:
    log.info("stub: notify customer (tracking=%s)", payload.get("tracking"))


# Что «делает» сервис по каждому типу события. Типы вне карты — skipped.
_ACTIONS = {
    "payment_succeeded": _payment_succeeded,
    "payment_failed": _payment_failed,
    "order_created": _order_created,
    "order_shipped": _order_shipped,
}


def handle_event(
    event_type: str,
    payload: dict,
    *,
    failure_rate: float,
    rng: random.Random | None = None,
) -> str:
    """Заглушка: по типу события выполняет действие; иногда «падает».

    Возвращает "success" или "skipped"; бросает HandlerError при симуляции сбоя.
    failure_rate = 0 — падений нет (удобно для тестов), 1 — всегда падает.
    """
    action = _ACTIONS.get(event_type)
    if action is None:
        log.info("no handler for type=%r, event will be skipped", event_type)
        return "skipped"

    action(payload)

    generator = rng if rng is not None else random
    if failure_rate > 0 and generator.random() < failure_rate:
        raise HandlerError(f"simulated failure while processing {event_type!r}")
    return "success"
