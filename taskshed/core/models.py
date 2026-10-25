"""Domain model — plain dataclasses, no storage/platform dependencies.

All datetimes crossing this module's boundaries are timezone-aware.
Storage keeps them as UTC; display-side code converts to the user timezone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum


class ScheduleType(StrEnum):
    ONCE = "once"
    EVERY_N_DAYS = "every_n_days"
    WEEKDAYS = "weekdays"
    MONTHLY_DAY = "monthly_day"


class OccurrenceStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    MISSED = "missed"


class Platform(StrEnum):
    TELEGRAM = "telegram"
    VK = "vk"


@dataclass(slots=True)
class User:
    id: int
    created_at: datetime
    language: str = "ru"


@dataclass(slots=True)
class Account:
    id: int
    user_id: int
    platform: Platform
    platform_user_id: str
    linked_at: datetime
    last_seen: datetime | None = None


@dataclass(slots=True)
class Place:
    id: int
    user_id: int
    name: str


@dataclass(slots=True)
class Task:
    id: int | None
    user_id: int
    place_id: int
    title: str
    note: str = ""
    deadline: datetime | None = None  # only meaningful for ScheduleType.ONCE
    schedule_type: ScheduleType = ScheduleType.EVERY_N_DAYS
    schedule_data: dict = field(default_factory=dict)
    #  "every_n_days": {"n": 3, "anchor": "2026-10-25"}
    #  "weekdays":     {"weekdays": [0, 2, 4]}        # Mon=0 .. Sun=6
    #  "monthly_day":  {"day": 15}
    reminder_time: time = time(9, 0)
    is_active: bool = True


@dataclass(slots=True)
class TaskOccurrence:
    id: int | None
    task_id: int
    due_at: datetime       # deadline of this repeat
    reminder_at: datetime  # when the reminder may fire
    status: OccurrenceStatus = OccurrenceStatus.PENDING


@dataclass(slots=True)
class UserSettings:
    user_id: int
    reminders_on: bool = True
    remind_mode: str = "times"          # "times" | "interval"
    remind_times: list[str] = field(default_factory=lambda: ["09:00"])  # HH:MM, user tz
    remind_interval_min: int = 120
    quiet_start: time = time(23, 0)     # user tz; quiet if start > end spans midnight
    quiet_end: time = time(7, 0)
    location_ttl_hours: int = 6
    tz: str = "UTC"
    notify_platforms: str = "all"       # "all" | "primary"
    stats_format: str = "both"          # "text" | "image" | "both"
    last_reminded_at: datetime | None = None
    pending_reminder: bool = False      # digest awaiting location answer
