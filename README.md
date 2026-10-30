# Приёмник событий с очередью обработки

Небольшой сервис: принимает события по webhook, сохраняет их в SQLite и обрабатывает
их вручную написанной очередью (на таблице — без Celery/RQ/Huey).

**Стек:** Python 3.11+, Flask 3.x, SQLite (стандартная библиотека `sqlite3`), pytest.
**Управление зависимостями:** [uv](https://docs.astral.sh/uv/) — `pyproject.toml` + `uv.lock`.

## Структура

```
pyproject.toml    # проект, зависимости, dev-группа (pytest), tool.uv
uv.lock           # закреплённые версии — коммитится
receiver/
  __init__.py     # create_app() и сборка приложения
  config.py       # Config: переменные окружения через python-decouple
  __main__.py     # python -m receiver — HTTP-сервер
  db.py           # соединение, схема (события + задачи), get_db()
  queue.py        # очередь: постановка, взятие, завершение, повторы, статистика
  handlers.py     # заглушка обработчика: реакция по типу + симуляция сбоев
  worker.py       # воркер: взятие задач, backoff, защита от зависших pending
  routes.py       # POST /webhook, GET /stats
tests/            # pytest
```

## Запуск

```bash
uv sync               # создать .venv и установить зависимости (+ dev-группа)

# терминал 1 — HTTP-сервер (http://127.0.0.1:5000)
uv run python -m receiver

# терминал 2 — воркер очереди (бесконечный цикл опроса)
uv run python -m receiver.worker

# либо разовый проход: обработать готовые задачи и выйти
uv run python -m receiver.worker --once
```

Альтернатива серверу: `uv run flask --app receiver run`.

База по умолчанию — `receiver.db` рядом с проектом (переопределяется `RECEIVER_DB`).
Файлы `*.db` в `.gitignore`, их можно удалять без последствий.

### Примеры

```bash
# принять событие
curl -s -X POST localhost:5000/webhook -H 'Content-Type: application/json' \
  -d '{"event_id": "abc-123", "type": "payment_succeeded", "payload": {"amount": 1500}}'
# {"duplicate": false, "event_id": "abc-123"}

# повтор с тем же event_id — дубликат не создаётся, тоже 200
curl -s -X POST localhost:5000/webhook -H 'Content-Type: application/json' \
  -d '{"event_id": "abc-123", "type": "payment_succeeded", "payload": {"amount": 1}}'
# {"duplicate": true, "event_id": "abc-123"}

# некорректное тело — 400 с понятным сообщением
curl -s -X POST localhost:5000/webhook -H 'Content-Type: application/json' -d '{"event_id": ""}'
# {"error": "invalid_body", "message": "Field 'event_id' must be a non-empty string"}

# счётчики
curl -s localhost:5000/stats
# {"events_by_type": {"payment_succeeded": 1},
#  "tasks_by_status": {"pending": 1, "success": 0, "failed": 0, "skipped": 0}}
```

## Тесты

```bash
uv run pytest          # или: uv run python -m pytest
```

22 теста, в их числе:

| Тест                                                                                         | Что проверяет                                                                                                          |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `test_repeat_webhook_same_event_id_does_not_create_duplicate`                                | повторный webhook с тем же `event_id` не создаёт дубль (1 событие, 1 задача), отвечает 200, данные не перезаписываются |
| `test_invalid_body_returns_400_and_saves_nothing` (+ ещё два 400-теста)                      | битый JSON, не-объект, отсутствующие/пустые/неправильного типа поля → 400 и ничего не сохранено                        |
| `test_failing_task_goes_failed_after_three_attempts`                                         | ровно 3 попытки → `failed`, дальше не берётся                                                                          |
| `test_pause_between_attempts_grows`                                                          | паузы нарастают (1с после 1-й попытки, 2с после 2-й)                                                                   |
| `test_stale_lock_is_reclaimed_and_task_finishes`                                             | задача с протухшей блокировкой (воркер умер) не остаётся в `pending` — её дорабатывает следующий проход                |
| `test_task_crashed_on_last_attempt_becomes_failed`                                           | воркер умер на исчерпанной попытке → `failed`, а не вечный `pending`                                                   |
| `test_successful_processing_marks_task_success`, `test_unknown_event_type_is_marked_skipped` | успешная обработка и `skipped` для неизвестного типа                                                                   |
| `test_stats_*`                                                                               | `/stats`: счётчики по типам событий и статусам задач                                                                   |

Тесты быстрые (<1 с): в них `FAILURE_RATE`/`BACKOFF_BASE` задаются через конфиг,
реальные паузы и сбои не ждём.

## Как устроено

**Таблицы** (`db.py`, создаются идемпотентно при старте, миграций нет):

- `events`: `event_id` (PK), `type`, `payload` (JSON-строка), `received_at` (UTC ISO).
- `tasks`: `event_id` (UNIQUE → не больше одной задачи на событие), `status`
  (`pending/success/failed/skipped`, CHECK), `attempts`, `next_run_at`, `locked_at`,
  `last_error`, времена.

**Приём (`routes.py` → `queue.enqueue_event`):**
валидация каркаса тела (JSON-объект, непустые строки `event_id`/`type`, `payload` — объект)
→ вставка события и задачи одной транзакцией `BEGIN IMMEDIATE`.
Дубль ловится первичным ключом: `IntegrityError` → откат → ответ 200 c `duplicate: true`.

**Обработка (`worker.py`):**

1. Взятие задачи (`queue.claim_next`): одна write-транзакция — сначала возвращаются
   в работу протухшие блокировки, затем выбирается задача с `next_run_at <= now`
   без блокировки, `attempts += 1`, `locked_at = now`.
2. Заглушка (`handlers.handle_event`) по типу события «что-то делает» и с вероятностью
   `FAILURE_RATE` падает. Неизвестный тип → `skipped` (терминальный статус, повторов нет).
3. Ошибка → `attempts < 3`: задача возвращается в `pending` с паузой
   `BACKOFF_BASE * 2^(attempts-1)` (1с, 2с при значении по умолчанию).
   `attempts = 3` → `failed`, больше не берётся.
4. Успех → `success`.

**Почему задача не может зависнуть в `pending`:**

| Сценарий                         | Механизм                                                                |
| -------------------------------- | ----------------------------------------------------------------------- |
| обычная ошибка                   | до 3 попыток, затем `failed`                                            |
| воркер упал, удерживая задачу    | через `LEASE_TIMEOUT` (60 с) блокировка снимается, задача берётся снова |
| воркер упал на последней попытке | при снятии такой блокировки задача сразу помечается `failed`            |
| неизвестный тип события          | сразу `skipped`                                                         |
| неожиданная ошибка в обработчике | считается обычной попыткой → те же 3 попытки → `failed`                 |

Единственный «вечный pending» возможен, если воркер вообще не запущен — это
операционная ситуация, о ней сказано честно: поднимать `uv run python -m receiver.worker`.

**`GET /stats`:** `events_by_type` — GROUP BY по таблице событий;
`tasks_by_status` — все четыре статуса присутствуют всегда (0, если пусто).

## Настройки (переменные окружения)

| Переменная                        | По умолчанию           | Смысл                                                  |
| --------------------------------- | ---------------------- | ------------------------------------------------------ |
| `RECEIVER_DB`                     | `<корень>/receiver.db` | путь к SQLite                                          |
| `RECEIVER_MAX_ATTEMPTS`           | `3`                    | всего попыток на задачу                                |
| `RECEIVER_BACKOFF_BASE`           | `1.0`                  | база паузы, с (паузы 1с, 2с, 4с…)                      |
| `RECEIVER_FAILURE_RATE`           | `0.2`                  | доля сбоев заглушки (0…1)                              |
| `RECEIVER_LEASE_TIMEOUT`          | `60`                   | с, после которых блокировка мёртвого воркера снимается |
| `RECEIVER_POLL_INTERVAL`          | `1`                    | с, период опроса очереди воркером                      |
| `RECEIVER_HOST` / `RECEIVER_PORT` | `127.0.0.1` / `5000`   | адрес сервера (`python -m receiver`)                   |
