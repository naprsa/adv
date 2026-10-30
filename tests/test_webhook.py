"""Приём событий: POST /webhook — валидация, сохранение, идемпотентность."""
from __future__ import annotations

import pytest

from receiver import create_app
from receiver.db import Event, Task


def test_valid_webhook_stores_event_and_pending_task(client, db, sample_event):
    resp = client.post("/webhook", json=sample_event())

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["event_id"] == "abc-123"
    assert body["duplicate"] is False

    event = db.query(Event).first()
    assert event is not None
    assert event.type == "payment_succeeded"
    assert event.payload == '{"amount": 100}'

    task = db.query(Task).first()
    assert task is not None
    assert task.event_id == "abc-123"
    assert task.status == "pending"
    assert task.attempts == 0


def test_repeat_webhook_same_event_id_does_not_create_duplicate(client, db, sample_event):
    first = client.post("/webhook", json=sample_event())
    # тот же event_id, но с другим payload — данные не должны перезаписаться
    second = client.post("/webhook", json=sample_event(payload={"amount": 999}))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True

    assert db.query(Event).count() == 1
    assert db.query(Task).count() == 1
    assert db.query(Event).first().payload == '{"amount": 100}'


def test_malformed_json_returns_400(client, db):
    resp = client.post(
        "/webhook", data='{"event_id": ', content_type="application/json"
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_body"
    assert resp.get_json()["message"]
    assert db.query(Event).count() == 0


def test_non_json_body_returns_400(client, db):
    resp = client.post(
        "/webhook", data="event_id=abc", content_type="application/x-www-form-urlencoded"
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_body"


@pytest.mark.parametrize(
    "body",
    [
        [],                                                        # не объект
        {},                                                        # нет полей
        {"event_id": "", "type": "x", "payload": {}},              # пустой event_id
        {"event_id": "  ", "type": "x", "payload": {}},            # пробелы вместо id
        {"event_id": "abc", "type": "", "payload": {}},            # пустой type
        {"event_id": "abc", "type": "x"},                          # нет payload
        {"event_id": "abc", "type": "x", "payload": [1, 2]},       # payload не объект
        {"event_id": 42, "type": "x", "payload": {}},              # event_id не строка
        {"event_id": "abc", "type": 7, "payload": {}},             # type не строка
    ],
)
def test_invalid_body_returns_400_and_saves_nothing(client, db, body):
    resp = client.post("/webhook", json=body)

    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["error"] == "invalid_body"
    assert payload["message"]
    assert db.query(Event).count() == 0
    assert db.query(Task).count() == 0