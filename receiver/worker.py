"""Воркер очереди: взятие задач, повторы с нарастающей паузой, защита от зависших pending.

Конфигурация — Mapping, собранный из `Config` (receiver/config.py, python-decouple):
- Flask-обёртка для тестов: cfg = app.config (тесты переопределяют
  FAILURE_RATE/BACKOFF_BASE через create_app(config={...}), см. tests/conftest.py);
- standalone-запуск `python -m receiver.worker`: значения напрямую из Config,
  Flask не нужен вовсе.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from . import handlers, queue
from .db import build_engine_and_factory

log = logging.getLogger(__name__)

# Ключи конфигурации, нужные воркеру; заполняются из Config при standalone-запуске.
WORKER_CONFIG_KEYS = (
    "DATABASE",
    "MAX_ATTEMPTS",
    "BACKOFF_BASE",
    "FAILURE_RATE",
    "LEASE_TIMEOUT",
    "POLL_INTERVAL",
)


def standalone_runtime() -> tuple[Mapping[str, Any], sessionmaker]:
    """Рантайм без Flask: cfg из Config (python-decouple) + фабрика сессий БД."""
    from .config import Config  # локальный импорт

    cfg = {key: getattr(Config, key) for key in WORKER_CONFIG_KEYS}
    _, session_factory = build_engine_and_factory(cfg["DATABASE"])
    return cfg, session_factory


def backoff_delay(attempts: int, base: float) -> float:
    """Пауза после неудачной попытки №attempts (нумерация с 1): base * 2^(n-1).

    base=1.0 -> паузы 1с, 2с, 4с... — нарастающая, экспоненциальная.
    """
    return base * (2 ** (attempts - 1))


def process_next(cfg: Mapping[str, Any], session: Session) -> dict | None:
    """Обрабатывает одну задачу до терминального статуса или до планирования повторов.

    None — готовых задач нет.
    """
    task = queue.claim_next(
        session,
        lease_timeout=cfg["LEASE_TIMEOUT"],
        max_attempts=cfg["MAX_ATTEMPTS"],
    )
    if task is None:
        return None

    event = queue.get_event(session, task.event_id)
    payload = json.loads(event.payload)
    try:
        outcome = handlers.handle_event(
            event.type, payload, failure_rate=cfg["FAILURE_RATE"]
        )
    except Exception as exc:  # сбой заглушки — и любая неожиданная ошибка тоже
        if task.attempts >= cfg["MAX_ATTEMPTS"]:
            # Третья попытка не удалась: больше не берём.
            queue.finish(session, task.id, queue.FAILED, error=str(exc))
            log.error(
                "task=%s event=%s attempt %s/%s -> failed: %s",
                task.id,
                task.event_id,
                task.attempts,
                cfg["MAX_ATTEMPTS"],
                exc,
            )
            status = queue.FAILED
        else:
            delay = backoff_delay(task.attempts, cfg["BACKOFF_BASE"])
            queue.release(session, task.id, retry_at=time.time() + delay)
            log.warning(
                "task=%s event=%s attempt %s/%s -> retry in %.2fs: %s",
                task.id,
                task.event_id,
                task.attempts,
                cfg["MAX_ATTEMPTS"],
                delay,
                exc,
            )
            status = queue.PENDING
    else:
        status = queue.SUCCESS if outcome == "success" else queue.SKIPPED
        queue.finish(session, task.id, status)
        log.info("task=%s event=%s -> %s", task.id, task.event_id, status)

    return {"task_id": task.id, "event_id": task.event_id, "status": status}


def _drain(cfg: Mapping[str, Any], session: Session) -> int:
    """Обрабатывает все задачи, чья пауза уже истекла. Возвращает число взятых задач.

    При BACKOFF_BASE > 0 останавливается на первой задаче в паузе —
    тогда воркер ждет POLL_INTERVAL и проверяет снова.
    """
    processed = 0
    while process_next(cfg, session) is not None:
        processed += 1
    return processed


def run_due(app) -> int:
    """Разовый проход по конфигу Flask-app (используется в тестах).

    Возвращает число взятий задач: одна задача при повторах считается
    по каждой попытке.
    """
    session = app.db_session_factory()
    try:
        return _drain(app.config, session)
    finally:
        session.close()


def run_once(cfg: Mapping[str, Any], session_factory: sessionmaker) -> int:
    """Standalone разовый проход: обработать готовые задачи и завершиться."""
    session = session_factory()
    try:
        return _drain(cfg, session)
    finally:
        session.close()


def run_forever(cfg: Mapping[str, Any], session_factory: sessionmaker) -> None:
    """Основной цикл воркера: обработать готовое, иначе подождать и опросить снова."""
    log.info(
        "worker started: db=%s, max_attempts=%s, backoff_base=%ss, failure_rate=%s, lease=%ss",
        cfg["DATABASE"],
        cfg["MAX_ATTEMPTS"],
        cfg["BACKOFF_BASE"],
        cfg["FAILURE_RATE"],
        cfg["LEASE_TIMEOUT"],
    )
    session = session_factory()
    try:
        while True:
            if _drain(cfg, session) == 0:
                time.sleep(cfg["POLL_INTERVAL"])
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Обработчик очереди событий")
    parser.add_argument(
        "--once",
        action="store_true",
        help="обработать готовые задачи и завершиться (по умолчанию — цикл)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # create_app() не нужен: конфиг — напрямую из Config (python-decouple).
    cfg, session_factory = standalone_runtime()
    try:
        if args.once:
            processed = run_once(cfg, session_factory)
            log.info("processed %s task(s)", processed)
            return 0
        run_forever(cfg, session_factory)
    except KeyboardInterrupt:
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
