"""Часовые напоминания привязаны к локальным дате и времени начала."""

from datetime import datetime, timedelta

from .config import HOUR_INTERVALS


def daily_times(task, day):
    """Фиксированные часы с учащением за выбранное число дней до события."""
    if task["recurrence"] == "three_times_daily":
        return ("09:00", "15:00", "21:00")
    if task["recurrence"] == "event":
        event = datetime.fromisoformat(task["event_date"]).date()
        if 0 <= (event - day).days <= task["escalation_days"]:
            return ("09:00", "15:00", "21:00")
        return ("09:00",)
    return ()


def hourly_slot(task, now):
    """Вернуть последний часовой слот сегодня, не догоняя вчерашние сигналы."""
    hours = HOUR_INTERVALS.get(task["recurrence"])
    if not hours:
        return None
    anchor = datetime.fromisoformat(task["start_date"] + "T" + task["time"])
    local = now.replace(tzinfo=None)
    if local < anchor:
        return None
    period = timedelta(hours=hours)
    slot = anchor + ((local - anchor) // period) * period
    return slot if slot.date() == local.date() else None


def notification_due(row, now):
    """Учитывать повтор, доставку, ручное откладывание и пропущенные часы."""
    if row["recurrence"] == "once" and row["date"] != row["start_date"]:
        return False  # Сохранённый прошлый план после переноса не становится будильником.
    end = row["reminder_end_date"] or (row["date"] if row["recurrence"] == "once" else None)
    if end and now.date().isoformat() > end:
        return False
    if row["recurrence"] != "once" and row["date"] < now.date().isoformat():
        return False  # Отложенный сигнал прошлого дня тоже закончил свой срок.
    if row["reminder_end_date"] and now.date().isoformat() > row["reminder_end_date"]:
        return False
    if row["recurrence"] == "event" and now.date().isoformat() > row["event_date"]:
        return False
    if row["snooze_until"]:
        return now >= datetime.fromisoformat(row["snooze_until"])
    times = daily_times(row, now.date())
    if times:
        local = now.replace(tzinfo=None)
        slots = [datetime.fromisoformat(now.date().isoformat() + "T" + value)
                 for value in times]
        past = [slot for slot in slots if slot <= local]
        delivered = row["notified_at"]
        return bool(past) and (not delivered or max(past) >
                              datetime.fromisoformat(delivered).replace(tzinfo=None))
    if row["recurrence"] in HOUR_INTERVALS:
        slot = hourly_slot(row, now)
        if slot is None:
            return False
        delivered = row["notified_at"]
        return not delivered or slot > datetime.fromisoformat(
            delivered).replace(tzinfo=None)
    return row["notified_at"] is None and (
        row["date"] < now.date().isoformat()
        or row["time"] <= now.strftime("%H:%M")
    )
