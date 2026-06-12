from __future__ import annotations

import random
import tkinter as tk
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional


Reminder = dict[str, object]
BUILTIN_REMINDER_IDS = {"builtin-lunch", "builtin-dinner", "builtin-sleep"}
BUILTIN_REMINDERS: tuple[Reminder, ...] = (
    {
        "id": "builtin-lunch",
        "enabled": True,
        "builtin": True,
        "mode": "time",
        "minutes": 60,
        "time": "12:00",
        "text": "该吃午饭啦，补充一点能量。",
    },
    {
        "id": "builtin-dinner",
        "enabled": True,
        "builtin": True,
        "mode": "time",
        "minutes": 60,
        "time": "18:00",
        "text": "该吃晚饭啦，慢慢吃，别饿着。",
    },
    {
        "id": "builtin-sleep",
        "enabled": True,
        "builtin": True,
        "mode": "time",
        "minutes": 60,
        "time": "00:00",
        "text": "很晚啦，早点睡觉，明天继续精神满满。",
    },
)


def merge_builtin_reminders(items: list[Reminder]) -> list[Reminder]:
    by_id = {str(item.get("id", "")): item for item in items}
    merged: list[Reminder] = []
    for builtin in BUILTIN_REMINDERS:
        stored = by_id.get(str(builtin["id"]), {})
        item = dict(builtin)
        if "enabled" in stored:
            item["enabled"] = bool(stored.get("enabled"))
        merged.append(item)
    merged.extend(item for item in items if str(item.get("id", "")) not in BUILTIN_REMINDER_IDS)
    return merged


def is_builtin_reminder(item: Reminder) -> bool:
    return bool(item.get("builtin")) or str(item.get("id", "")) in BUILTIN_REMINDER_IDS


def startup_greeting_message(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    if 5 <= hour < 9:
        prefix = "早上好"
        choices = (
            "元气满满的一天开始啦。",
            "今天也要闪闪发光。",
            "先伸个懒腰，再慢慢出发。",
        )
    elif 9 <= hour < 12:
        prefix = "上午好"
        choices = (
            "状态不错，慢慢推进就好。",
            "今天的节奏看起来很稳。",
            "先把最重要的一件事拿下吧。",
        )
    elif 12 <= hour < 14:
        prefix = "中午好"
        choices = (
            "先吃饭，灵感也需要补给。",
            "午间暂停一下，下午会更顺。",
            "补充能量，再继续漂亮推进。",
        )
    elif 14 <= hour < 18:
        prefix = "下午好"
        choices = (
            "再撑一会儿，漂亮地收尾。",
            "下午也稳稳来，别忘了喝水。",
            "把节奏放平，事情会一点点完成。",
        )
    else:
        prefix = "晚上好"
        choices = (
            "辛苦啦，记得放松一下。",
            "今天已经做了不少，慢慢收尾吧。",
            "夜晚适合轻一点，也适合早点休息。",
        )
    return f"{prefix}，{random.choice(choices)}"


def next_reminder_times(
    item: Reminder,
    count: int = 2,
    now: Optional[datetime] = None,
    start_time: Optional[datetime] = None,
) -> list[datetime]:
    now = now or datetime.now()
    mode = str(item.get("mode", "interval"))
    if mode == "time":
        first = now + timedelta(milliseconds=ReminderScheduler.time_delay_ms(str(item.get("time", "09:00")), now))
        return [first + timedelta(days=index) for index in range(count)]
    minutes = max(1, ReminderScheduler.safe_int(item.get("minutes"), 60))
    first = ReminderScheduler.next_interval_time(minutes, start_time or now, now)
    return [first + timedelta(minutes=minutes * index) for index in range(count)]


class ReminderScheduler:
    def __init__(self, root: tk.Tk, on_reminder: Callable[[Reminder], None], start_time: Optional[datetime] = None):
        self.root = root
        self.on_reminder = on_reminder
        self.start_time = start_time or datetime.now()
        self.after_ids: dict[str, str] = {}
        self.reminders: list[Reminder] = []

    def schedule(self, reminders: list[Reminder]) -> None:
        self.cancel()
        self.reminders = reminders
        for item in reminders:
            if bool(item.get("enabled", True)):
                self._schedule_item(item)

    def cancel(self) -> None:
        for after_id in self.after_ids.values():
            self.root.after_cancel(after_id)
        self.after_ids = {}

    def _schedule_item(self, item: Reminder) -> None:
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            return
        delay_ms = self._delay_ms(item)
        self.after_ids[item_id] = self.root.after(delay_ms, lambda value=item: self._fire(value))

    def _fire(self, item: Reminder) -> None:
        item_id = str(item.get("id", "")).strip()
        if item_id:
            self.after_ids.pop(item_id, None)
        if not bool(item.get("enabled", True)):
            return
        self.on_reminder(item)
        self._schedule_item(item)

    def _delay_ms(self, item: Reminder) -> int:
        mode = str(item.get("mode", "interval"))
        if mode == "time":
            return self.time_delay_ms(str(item.get("time", "09:00")))
        minutes = self.safe_int(item.get("minutes"), 60)
        now = datetime.now()
        target = self.next_interval_time(max(1, minutes), self.start_time, now)
        return max(1000, round((target - now).total_seconds() * 1000))

    @staticmethod
    def time_delay_ms(raw_time: str, now: Optional[datetime] = None) -> int:
        try:
            hour_text, minute_text = raw_time.strip().split(":", 1)
            hour = max(0, min(23, int(hour_text)))
            minute = max(0, min(59, int(minute_text)))
        except (TypeError, ValueError):
            hour, minute = 9, 0
        now = now or datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(1000, round((target - now).total_seconds() * 1000))

    @staticmethod
    def safe_int(value: object, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def next_interval_time(minutes: int, start_time: datetime, now: datetime) -> datetime:
        interval = timedelta(minutes=max(1, minutes))
        if now < start_time:
            return start_time + interval
        elapsed = now - start_time
        periods = int(elapsed.total_seconds() // interval.total_seconds()) + 1
        return start_time + interval * periods
