"""Очередь: обработка, повторы с нарастающей паузой, невозможность вечного pending."""
from __future__ import annotations

import time

from receiver import queue, worker
from receiver.db import Event, Task


def test_backoff_delay_grows_exponentially():
    delays = [worker.backoff_delay(attempt, base=1.0) for attempt in (1, 2, 3)]
    assert delays == [1.0, 2.0, 4.0]
    assert delays == sorted(delays)


def test_failing_task_goes_failed_after_three_attempts(make_app, db, sample_event):
    app = make_app(FAILURE_RATE=1.0)  # заглушка всегда падает
    app.test_client().post("/webhook", json=sample_event())

    # BACKOFF_BASE=0 -> пауза нулевая, все три попытки за один проход
    assert worker.run_due(app) == 3

    task = db.query(Task).first()
    assert task is not None
    assert task.status == "failed"
    assert task.attempts == 3
    assert task.last_error
    assert task.locked_at is None
    # после третьей попытки задача больше не берётся
    assert worker.run_due(app) == 0


def test_pause_between_attempts_grows(make_app, db, sample_event):
    app = make_app(FAILURE_RATE=1.0, BACKOFF_BASE=1.0)
    app.test_client().post("/webhook", json=sample_event())

    started = time.time()
    assert worker.run_due(app) == 1  # попытка 1 падает, ждём ~1с

    task = db.query(Task).first()
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 1
    first_pause = task.next_run_at - started
    assert first_pause >= 1.0

    # помечаем паузу истёкшей
    from sqlalchemy import update as sa_update
    db.execute(
        sa_update(Task).where(Task.id == task.id).values(next_run_at=time.time())
    )
    db.commit()

    second_started = time.time()
    assert worker.run_due(app) == 1  # попытка 2 падает, ждём ~2с

    task = db.query(Task).first()
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 2
    second_pause = task.next_run_at - second_started
    assert second_pause >= 2.0
    assert second_pause > first_pause


def test_stale_lock_is_reclaimed_and_task_finishes(make_app, db, sample_event):
    app = make_app()  # обработчик не падает
    app.test_client().post("/webhook", json=sample_event())

    # «мёртвый воркер» взял задачу и не снял блокировку
    claimed = queue.claim_next(db, lease_timeout=60.0, max_attempts=3)
    assert claimed is not None
    assert claimed.attempts == 1
    # в worker.py claim_next теперь использует session, а не conn
    # Но для теста directly манипулируем БД через SQLAlchemy
    from sqlalchemy import update as sa_update
    db.execute(
        sa_update(Task)
        .where(Task.event_id == "abc-123")
        .values(locked_at=time.time() - 61, status="pending")
    )
    db.commit()

    assert worker.run_due(app) == 1

    task = db.query(Task).first()
    assert task is not None
    assert task.status == "success"  # не зависло в pending
    assert task.attempts == 2        # упавшая (брошенная) попытка + успешная


def test_task_crashed_on_last_attempt_becomes_failed(make_app, db, sample_event):
    app = make_app()
    app.test_client().post("/webhook", json=sample_event())

    # попытки исчерпаны, воркер умер на последней и не снял блокировку
    from sqlalchemy import update as sa_update
    db.execute(
        sa_update(Task).where(Task.id == 1).values(attempts=3, locked_at=time.time() - 61)
    )
    db.commit()

    assert worker.run_due(app) == 0  # брать нечего: реклейм уже перевёл в failed

    task = db.query(Task).first()
    assert task is not None
    assert task.status == "failed"
    assert "lease" in task.last_error
    assert worker.run_due(app) == 0