"""Планировщик, независимый от открытого окна интерфейса."""

import logging
import threading


LOGGER = logging.getLogger(__name__)


class Scheduler(threading.Thread):
    """Проверять расписание в фоновом потоке процесса."""

    def __init__(self, store, notifier, stop_event, interval=5):
        """Связать хранилище и системное уведомление."""
        super().__init__(name="ritm-reminders", daemon=True)
        self.store = store
        self.notifier = notifier
        self.stop_event = stop_event
        self.interval = interval

    def tick(self):
        """Выполнить один цикл, объединив массовые просроченные события."""
        items = self.store.due_notifications()
        if len(items) == 1:
            self.notifier(items[0]["title"], items[0]["body"])
        elif items:
            titles = ", ".join(item["title"] for item in items[:3])
            self.notifier(f"Ритм · задач к напоминанию: {len(items)}", titles)
        return items

    def run(self):
        """Продолжать проверку после закрытия окна и пробуждения ПК."""
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                LOGGER.exception("Не удалось проверить напоминания")
            self.stop_event.wait(self.interval)
