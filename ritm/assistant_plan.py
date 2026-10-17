"""Проверка плана из ChatGPT; импорт только после просмотра пользователем."""

import json
import uuid

from .store import ValidationError, clean_task


def preview_plan(store, body):
    """Проверить весь план до первой записи. Произвольные команды запрещены."""
    tasks = body.get("tasks")
    if set(body) - {"tasks", "request_id"} or not isinstance(tasks, list) or not 1 <= len(tasks) <= 30:
        raise ValidationError('Нужен объект {"tasks": [...]} с 1–30 задачами.')
    return [clean_task(item, store.clock().date()) for item in tasks]


def import_plan(store, body):
    """Атомарно импортировать проверенный план с защитой от повторного запроса."""
    tasks = preview_plan(store, body)
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not request_id.startswith("plan-"):
        raise ValidationError("Не указан идентификатор импорта.")
    now = store.clock()
    with store.connection(write=True) as db:
        cached, fingerprint = store._request(db, request_id, {"tasks": tasks})
        if cached is not None:
            return cached
        for values in tasks:
            task = dict(values, id=str(uuid.uuid4()), created_at=now.isoformat(), archived_date=None)
            db.execute("INSERT INTO tasks (" + ",".join(task) + ") VALUES ("
                       + ",".join("?" for _ in task) + ")", tuple(task.values()))
            store._materialize(db, now.date())
            from .store import parse_day
            store._materialize(db, parse_day(task["start_date"]))
        result = {"created": len(tasks)}
        db.execute("INSERT INTO requests VALUES (?,?,?)", (request_id, fingerprint, json.dumps(result)))
    return result
