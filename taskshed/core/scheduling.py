"""Schedule rules: recurrence expansion, occurrence datetimes, overdue logic.

Pure functions over domain dataclasses — timezone-aware inputs only.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import OccurrenceStatus, ScheduleType, Task, TaskOccurrence

END_OF_DAY = time(23, 59)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def occurrence_dates(task: Task, start: date, end: date) -> list[date]:
    """Local dates (in user timezone) this task repeats on, within [start, end]."""
    if start > end:
        return []
    data = task.schedule_data
    days: list[date] = []

    if task.schedule_type == ScheduleType.ONCE:
        if task.deadline is not None:
            d = task.deadline.date()
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


def occurrence_datetimes(
    task: Task, when: date, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """(due_at, reminder_at) for one local date, both tz-aware."""
    if task.schedule_type == ScheduleType.ONCE and task.deadline is not None:
        due = task.deadline
        reminder = min(
            datetime.combine(when, task.reminder_time, tzinfo=tz), due
        )
        return due.astimezone(UTC), reminder.astimezone(UTC)
    due = datetime.combine(when, END_OF_DAY, tzinfo=tz)
    reminder = datetime.combine(when, task.reminder_time, tzinfo=tz)
    return due.astimezone(UTC), reminder.astimezone(UTC)


def plan_occurrences(
    task: Task, today: date, horizon_days: int, tz: ZoneInfo
) -> list[TaskOccurrence]:
    """Occurrences from today (inclusive) up to horizon — for materialization."""
    end = today + timedelta(days=horizon_days)
    out = []
    for d in occurrence_dates(task, today, end):
        due_at, reminder_at = occurrence_datetimes(task, d, tz)
        out.append(TaskOccurrence(id=None, task_id=task.id or 0,
                                  due_at=due_at, reminder_at=reminder_at))
    return out


def overdue(occ: TaskOccurrence, now: datetime) -> bool:
    return occ.status == OccurrenceStatus.PENDING and occ.due_at < now
