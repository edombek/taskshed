"""Domain model — plain dataclasses, no storage/platform dependencies.

Two kinds of moments live here, and keeping them apart is the whole point:

* **Wall-clock plans** — a task's ready-by moment (`Task.deadline`) and its
  recurring deadline clock (`Task.deadline_time`). They are stored in the
  *user's* calendar, never in host/server time, and the storage layer reads
  them back through that user's current timezone — so switching tz re-places
  every plan (18:00 stays 18:00 for the user, whatever zone they pick).
* **Materialized instants** — `TaskOccurrence.due_at/notify_at`: real,
  timezone-aware UTC moments computed from the plan at materialization time.
  They are recomputed from the wall-clock plan whenever the timezone changes.

Everything crossing this module's boundaries is timezone-aware.
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
    IN_PROGRESS = "in_progress"  # started, not finished yet ("в работе")
    DONE = "done"
    MISSED = "missed"
    SKIPPED = "skipped"


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
    """Four schedule kinds; every kind carries a deadline and a warning lead."""

    id: int | None
    user_id: int
    place_id: int
    title: str
    note: str = ""
    # once tasks: the ready-by moment, kept as a wall-clock plan (user zone)
    deadline: datetime | None = None
    # when a one-off task begins: work has a duration, not a single instant
    start_at: datetime | None = None
    schedule_type: ScheduleType = ScheduleType.EVERY_N_DAYS
    schedule_data: dict = field(default_factory=dict)
    #  "every_n_days": {"n": 3, "anchor": "2026-10-25"}
    #  "weekdays":     {"weekdays": [0, 2, 4]}        # Mon=0 .. Sun=6
    #  "monthly_day":  {"day": 15}
    #: clock time of the deadline on each repeat day (recurring kinds)
    deadline_time: time = time(23, 59)
    #: how long before the deadline the user wants to be warned, in minutes
    notify_before_min: int = 60
    is_active: bool = True


@dataclass(slots=True)
class TaskOccurrence:
    id: int | None
    task_id: int
    due_at: datetime        # deadline of this repeat (a real UTC instant)
    notify_at: datetime     # due_at minus the task's warning lead
    start_at: datetime | None = None  # set for once tasks: work may begin here
    status: OccurrenceStatus = OccurrenceStatus.PENDING


@dataclass(slots=True)
class UserSettings:
    user_id: int
    reminders_on: bool = True
    #: the morning digest moment, wall-clock in the user's zone
    morning_time: time = time(9, 0)
    #: how often (minutes) to repeat about tasks between notice and deadline
    nag_interval_min: int = 60
    quiet_start: time = time(23, 0)     # user tz; quiet if start > end spans midnight
    quiet_end: time = time(7, 0)
    location_ttl_hours: int = 6
    tz: str = "UTC"
    notify_platforms: str = "primary"   # "all" | "primary" (last talked) | "telegram" | "vk"
    stats_format: str = "both"          # "text" | "image" | "both"
    # how many days into the future activity charts reach (1 week by default)
    stats_horizon_days: int = 7
    last_morning_at: datetime | None = None   # last morning digest (UTC)
    last_notified_at: datetime | None = None  # last notice of any kind (UTC)
    pending_reminder: bool = False     # digest awaiting location answer
