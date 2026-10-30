"""HTTP-слой: /webhook (приём событий) и /stats (счётчики)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import queue
from .db import get_db

bp = Blueprint("api", __name__)


def _validate_body(raw) -> tuple[dict | None, str | None]:
    """Разбирает тело запроса: (событие, None) либо (None, сообщение об ошибке)."""
    if raw is None:
        return None, "Body must be valid JSON with Content-Type: application/json"
    if not isinstance(raw, dict):
        return None, "Body must be a JSON object"
    event_id = raw.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return None, "Field 'event_id' must be a non-empty string"
    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return None, "Field 'type' must be a non-empty string"
    if "payload" not in raw:
        return None, "Field 'payload' is required"
    if not isinstance(raw["payload"], dict):
        return None, "Field 'payload' must be a JSON object"
    return {"event_id": event_id, "type": event_type, "payload": raw["payload"]}, None


@bp.post("/webhook")
def webhook():
    event, error = _validate_body(request.get_json(silent=True))
    if error is not None:
        return jsonify(error="invalid_body", message=error), 400

    created = queue.enqueue_event(
        get_db(),
        event_id=event["event_id"],
        event_type=event["type"],
        payload=event["payload"],
    )
    # И создание, и дубликат отвечаем 200; клиент отличает их по полю duplicate.
    return jsonify(event_id=event["event_id"], duplicate=not created), 200


@bp.get("/stats")
def stats():
    return jsonify(queue.stats(get_db()))
