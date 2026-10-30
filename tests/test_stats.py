"""GET /stats — счётчики событий по типам и задач по статусам."""
from __future__ import annotations

from receiver import worker


def test_stats_counts_events_by_type_and_tasks_by_status(app, client, sample_event):
    client.post("/webhook", json=sample_event())
    client.post("/webhook", json=sample_event(event_id="abc-456"))
    client.post("/webhook", json=sample_event(event_id="abc-789", type="mystery_event"))

    assert worker.run_due(app) == 3  # два success + один skipped
    client.post("/webhook", json=sample_event(event_id="def-000"))  # остаётся pending

    resp = client.get("/stats")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "events_by_type": {"payment_succeeded": 3, "mystery_event": 1},
        "tasks_by_status": {"pending": 1, "success": 2, "failed": 0, "skipped": 1},
    }


def test_stats_on_empty_database(client):
    resp = client.get("/stats")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "events_by_type": {},
        "tasks_by_status": {"pending": 0, "success": 0, "failed": 0, "skipped": 0},
    }