"""Часовые напоминания привязаны к локальным дате и времени начала."""

from datetime import datetime, timedelta

from .config import HOUR_INTERVALS


def deadline(row):
    """Время сигнала не является дедлайном: срок заканчивается в конце дня."""
    day = row["date"]
    if row["recurrence"] == "once" and day == row["start_date"]:
        day = row["reminder_end_date"] or day
    return datetime.fromisoformat(day) + timedelta(days=1)


def grace_slot(row, now):
    local = now.replace(tzinfo=None)
    end = deadline(row)
    slots = [end + timedelta(hours=1), end + timedelta(hours=2)]
    # Короткое окно доставки последнего сигнала учитывает задержку фонового цикла.
    if local < slots[0] or local >= slots[1] + timedelta(minutes=5):
        return None
    return slots[1] if local >= slots[1] else slots[0]


def grace_due(row, now):
    if row["dismissed_at"]:
        return False
    if row["recurrence"] == "once" and row["date"] != row["start_date"]:
        return False
    if row["reminder_end_date"] and row["date"] > row["reminder_end_date"]:
        return False
    slot = grace_slot(row, now)
    delivered = row["grace_notified_at"]
    return slot is not None and (not delivered or slot > datetime.fromisoformat(delivered).replace(tzinfo=None))


def occurrence_state(row, now):
    result = dict(row)
    end = deadline(row)
    until = end + timedelta(hours=2)
    local = now.replace(tzinfo=None)
    result.update(deadline_at=end.replace(tzinfo=now.tzinfo).isoformat(),
                  grace_until=until.replace(tzinfo=now.tzinfo).isoformat(),
                  in_grace=row["status"] == "pending" and not row["dismissed_at"] and end <= local < until,
                  can_dismiss=row["status"] == "pending" and not row["dismissed_at"] and local >= until)
    return result


def missed_deadline(row):
    if row["status"] == "pending":
        return True
    return row["status"] == "done" and datetime.fromisoformat(row["completed_at"]).replace(tzinfo=None) > deadline(row) + timedelta(hours=2)


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
