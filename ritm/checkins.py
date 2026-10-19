"""Отдельные выполнения внутри дня; пропуски выводятся из расписания и часов."""
import json
from datetime import datetime, timedelta

from .config import HOUR_INTERVALS


def plan(task):
    hours = HOUR_INTERVALS.get(task["recurrence"])
    if not task.get("checkins_enabled", False) or not hours or not task["time"]:
        return None
    start = task["time"]
    end = task.get("repeat_until") or max(start, "21:00")
    cursor = datetime.fromisoformat("2000-01-01T" + start)
    finish = datetime.fromisoformat("2000-01-01T" + end)
    times = []
    while cursor <= finish:
        times.append(cursor.strftime("%H:%M"))
        cursor += timedelta(hours=hours)
    if times and times[-1] != end:
        times.append(end)
    return times


def unpack(row):
    result = dict(row)
    for field, fallback in (("slots", None), ("checks", {})):
        value = result.get(field)
        result[field] = json.loads(value) if isinstance(value, str) else value or fallback
    return result


def decorate(row, now):
    result = unpack(row)
    times = result["slots"] or []
    local = now.replace(tzinfo=None)
    result["checkins"] = []
    for i, value in enumerate(times):
        start = datetime.fromisoformat(result["date"] + "T" + value)
        end = (datetime.fromisoformat(result["date"] + "T" + times[i+1]) if i+1 < len(times)
               else min(start+timedelta(hours=HOUR_INTERVALS.get(result.get("recurrence"),24)),datetime.fromisoformat(result["date"]) + timedelta(days=1)))
        checked = result["checks"].get(value)
        status = "done" if checked else "future" if local < start else "missed" if local >= end else "pending"
        result["checkins"].append(dict(time=value, status=status, completed_at=checked,
                                      available=local >= start and not result.get("dismissed_at")))
    return result


def events(rows):
    result = []
    for source in rows:
        row = unpack(source)
        if row["status"] == "cancelled" and not row["slots"]:
            continue
        if row["slots"]:
            for slot, completed_at in row["checks"].items():
                if slot in row["slots"]:
                    result.append(dict(row, slot_time=slot, completed_at=completed_at, status="done"))
        elif row["status"] == "done":
            result.append(dict(row, slot_time=None))
    return sorted(result, key=lambda event:event["completed_at"], reverse=True)
