"""Очередь на таблицах Task/Event через SQLAlchemy ORM.

Здесь только работа с таблицей; бизнес-логика обработки — в worker/handlers.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import db as db_module
from . import handlers
from .db import Event, Task, get_session

PENDING = "pending"
SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"

STATUSES = (PENDING, SUCCESS, FAILED, SKIPPED)

LOST_WORKER_ERROR = "worker lost: lease expired before attempt finished"


def _now() -> float:
    return time.time()


@contextmanager
def _tx(session: Session):
    """Явная write-транзакция для сессии SQLAlchemy."""
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def enqueue_event(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    payload: dict,
    now: float | None = None,
) -> bool:
    """Сохраняет событие и ставит задачу в очередь одной транзакцией.

    Возвращает False, если event_id уже встречался (дубликат): ничего
    не перезаписывается и новая задача не создаётся.
    """
    now = _now() if now is None else now
    received_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    payload_json = json.dumps(payload, ensure_ascii=False)

    try:
        with _tx(session):
            event = Event(
                event_id=event_id,
                type=event_type,
                payload=payload_json,
                received_at=received_at,
            )
            session.add(event)

            task = Task(
                event_id=event_id,
                status=PENDING,
                attempts=0,
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
    except Exception:
        # event_id — первичный ключ (unique): значит, событие уже принято.
        return False
    return True


def _reclaim_stale(
    session: Session,
    *,
    now: float,
    lease_timeout: float,
    max_attempts: int,
) -> None:
    """Возвращает в работу задачи, чей воркер умер, не сняв блокировку.

    Попытки уже учтены при взятии (attempts += 1), поэтому:
    - если лимит исчерпан — задача сразу помечается failed;
    - иначе блокировка снимается и задача будет взята повторно.
    """
    deadline = now - lease_timeout
    session.execute(
        text("""
        UPDATE tasks
        SET status = :status, locked_at = NULL, last_error = :last_error, updated_at = :updated_at
        WHERE status = :status_and_attempts AND attempts >= :max_attempts
          AND locked_at IS NOT NULL AND locked_at < :deadline
        """),
        {
            "status": FAILED,
            "last_error": "worker lost: lease expired before attempt finished",
            "updated_at": now,
            "status_and_attempts": PENDING,
            "max_attempts": max_attempts,
            "deadline": deadline,
        },
    )
    session.execute(
        text("""
        UPDATE tasks SET locked_at = NULL, updated_at = :updated_at
        WHERE status = :status AND locked_at IS NOT NULL AND locked_at < :deadline
        """),
        {
            "updated_at": now,
            "status": PENDING,
            "deadline": deadline,
        },
    )


def claim_next(
    session: Session,
    *,
    lease_timeout: float,
    max_attempts: int,
    now: float | None = None,
) -> Task | None:
    """Берёт ближайшую готовую задачу: attempts += 1 и блокировка на время аренды.

    Порок: сначала освобождаются протухшие блокировки, потом выбирается
    задача без активной блокировки, у которой next_run_at уже наступил.
    """
    now = _now() if now is None else now
    with _tx(session):
        _reclaim_stale(
            session, now=now, lease_timeout=lease_timeout, max_attempts=max_attempts
        )
        row = session.execute(
            select(Task)
            .where(
                Task.status == PENDING,
                Task.next_run_at <= now,
                Task.locked_at.is_(None),
            )
            .order_by(Task.next_run_at, Task.id)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.attempts += 1
        row.locked_at = now
        row.updated_at = now
        # session.flush() чтобы записать изменения в БД
        session.flush()
        # Возвращаем refreshed объект или делаем новый select
        return session.get(Task, row.id)


def finish(
    session: Session,
    task_id: int,
    status: str,
    *,
    error: str | None = None,
    now: float | None = None,
) -> None:
    """Переводит задачу в терминальный статус (блокировка снимается)."""
    if status not in (SUCCESS, FAILED, SKIPPED):
        raise ValueError(f"terminal status expected, got {status!r}")
    now = _now() if now is None else now
    session.execute(
        text("""
        UPDATE tasks SET status = :status, last_error = :last_error, locked_at = NULL, updated_at = :updated_at
        WHERE id = :task_id AND status = :pending_status
        """),
        {
            "task_id": task_id,
            "pending_status": PENDING,
            "status": status,
            "last_error": error,
            "updated_at": now,
        },
    )


def release(
    session: Session,
    task_id: int,
    *,
    retry_at: float,
    now: float | None = None,
) -> None:
    """Возвращает задачу в pending с новым временем попытки (блокировка снимается)."""
    now = _now() if now is None else now
    session.execute(
        text("""
        UPDATE tasks SET locked_at = NULL, next_run_at = :retry_at, updated_at = :updated_at
        WHERE id = :task_id AND status = :pending_status
        """),
        {
            "task_id": task_id,
            "pending_status": PENDING,
            "retry_at": retry_at,
            "updated_at": now,
        },
    )


def get_event(session: Session, event_id: str) -> Event:
    row = session.get(Event, event_id)
    if row is None:
        raise LookupError(f"event {event_id!r} not found for its task")
    return row


def stats(session: Session) -> dict:
    """Счётчики: события по типам и задачи по статусам (все статусы всегда на месте)."""
    from sqlalchemy import func, select

    events_by_type = session.execute(
        select(Event.type, func.count(Event.event_id).label("n")).group_by(Event.type)
    ).all()
    events_by_type_dict = {row.type: row.n for row in events_by_type}

    tasks_by_status = {status: 0 for status in STATUSES}
    tasks_rows = session.execute(
        select(Task.status, func.count(Task.id).label("n")).group_by(Task.status)
    ).all()
    for row in tasks_rows:
        tasks_by_status[row.status] = row.n

    return {"events_by_type": events_by_type_dict, "tasks_by_status": tasks_by_status}
