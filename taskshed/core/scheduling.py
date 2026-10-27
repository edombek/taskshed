"""Schedule rules: recurrence expansion, occurrence instants, notice windows.

Pure functions over domain dataclasses — timezone-aware inputs only.

The wall-clock rule lives here on purpose: a recurring task stores a plain
calendar rule plus a `deadline_time` clock, and expansion always happens in
the *user's* zone. Change that zone and every future occurrence is recomputed
from the same plan — 18:00 stays 18:00 on the user's clock, never on the
host's. Only `TaskOccurrence` rows are real instants, and they are rebuilt
when the timezone changes.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import OccurrenceStatus, ScheduleType, Task, TaskOccurrence

END_OF_DAY = time(23, 59)
#: statuses a repeat can still be worked through with
OPEN = (OccurrenceStatus.PENDING, OccurrenceStatus.IN_PROGRESS)


def lead_parts(minutes: int) -> tuple[int, str]:
    """(value, unit key): the biggest whole unit of a warning lead."""
    mins = max(0, int(minutes or 0))
    if mins and mins % 1440 == 0:
        return mins // 1440, "lead_day"
    if mins and mins % 60 == 0:
        return mins // 60, "lead_hour"
    return mins, "lead_min"


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def occurrence_dates(task: Task, start: date, end: date,
                     tz: ZoneInfo | None = None) -> list[date]:
    """Local dates (in user timezone) this task repeats on, within [start, end]."""
    if start > end:
        return []
    data = task.schedule_data
    days: list[date] = []

    if task.schedule_type == ScheduleType.ONCE:
        anchor = task.deadline or task.start_at
        if anchor is not None:
            # stored plans come back wall-clock in the user's zone; the day
            # the user meant is the day on their own calendar
            d = anchor.astimezone(tz).date() if tz else anchor.date()
            if start <= d <= end:
                days.append(d)

    elif task.schedule_type == ScheduleType.EVERY_N_DAYS:
        n = max(1, int(data.get("n", 1)))
        anchor = _parse_date(data.get("anchor")) or start
        d = anchor
        if d < start:
            d = anchor + timedelta(days=((start - anchor).days // n) * n)
            if d < start:
                d += timedelta(days=n)
        while d <= end:
            days.append(d)
            d += timedelta(days=n)

    elif task.schedule_type == ScheduleType.WEEKDAYS:
        allowed = {int(w) % 7 for w in data.get("weekdays", [])}
        d = start
        while d <= end:
            if d.weekday() in allowed:
                days.append(d)
            d += timedelta(days=1)

    elif task.schedule_type == ScheduleType.MONTHLY_DAY:
        wanted = min(max(1, int(data.get("day", 1))), 31)
        y, m = start.year, start.month
        while True:
            last = calendar.monthrange(y, m)[1]
            dd = min(wanted, last)  # 31st in a 30-day month -> last day
            cur = date(y, m, dd)
            if cur > end:
                break
            if cur >= start:
                days.append(cur)
            m += 1
            if m == 13:
                y, m = y + 1, 1
    return days


def deadline_of(task: Task, when: date, tz: ZoneInfo) -> datetime:
    """The ready-by instant of one repeat day: the rule's clock on that day."""
    if task.schedule_type == ScheduleType.ONCE:
        # a one-off carries its own moment; only a start without a ready-by
        # falls back to the end of that day
        if task.deadline is not None:
            return task.deadline.astimezone(UTC)
        if task.start_at is not None:
            return datetime.combine(when, END_OF_DAY, tzinfo=tz).astimezone(UTC)
    return datetime.combine(when, task.deadline_time or END_OF_DAY,
                            tzinfo=tz).astimezone(UTC)


def occurrence_datetimes(
    task: Task, when: date, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """(due_at, notify_at) for one local date — real instants, both in UTC.

    The notice moment is the deadline minus the task's warning lead, whatever
    the schedule kind: `notify_at` may land days before the deadline day.
    """
    due = deadline_of(task, when, tz)
    lead = timedelta(minutes=max(0, int(task.notify_before_min or 0)))
    return due, due - lead


def plan_occurrences(
    task: Task, today: date, horizon_days: int, tz: ZoneInfo,
    back_days: int = 0,
) -> list[TaskOccurrence]:
    """Occurrences from `back_days` before today up to the horizon.

    Nothing is ever planned before a schedule exists. An every-N-days rule
    starts at the anchor date written in it (`occurrence_dates` honours it), a
    one-off at its own moment, and weekday/monthly rules are not reached back
    into at all — they carry no date of their own, so their history is only
    what was already materialized while they were running.
    """
    start = today
    if back_days and task.schedule_type in (ScheduleType.EVERY_N_DAYS,
                                            ScheduleType.ONCE):
        start = today - timedelta(days=back_days)
    end = today + timedelta(days=horizon_days)
    out = []
    for d in occurrence_dates(task, start, end, tz):
        due_at, notify_at = occurrence_datetimes(task, d, tz)
        started = task.start_at.astimezone(UTC) if (
            task.schedule_type == ScheduleType.ONCE and task.start_at
        ) else None
        out.append(TaskOccurrence(id=None, task_id=task.id or 0,
                                  due_at=due_at, notify_at=notify_at,
                                  start_at=started))
    return out


def due_for_the_day(occ: TaskOccurrence, day_start: datetime,
                    day_end: datetime) -> bool:
    """The morning set: open, its notice moment is today or earlier, and its
    deadline is today or still ahead ("уведомление уже было, дедлайн позже")."""
    return (occ.status in OPEN and occ.due_at >= day_start
            and occ.notify_at <= day_end)


def in_notice_window(occ: TaskOccurrence, now: datetime) -> bool:
    """Between the notice moment and the deadline: what the every-T-minutes
    repeat is about."""
    return occ.status in OPEN and occ.notify_at <= now < occ.due_at


def overdue(occ: TaskOccurrence, now: datetime) -> bool:
    return occ.status == OccurrenceStatus.PENDING and occ.due_at < now
