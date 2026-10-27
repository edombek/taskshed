"""Pure statistics aggregations over (occurrence, task) pairs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Task, TaskOccurrence


def _status_of(occ: TaskOccurrence) -> str:
    """Map the five statuses onto the four the charts draw."""
    return "pending" if occ.status in ("pending", "in_progress") else occ.status


def per_task(occs: list[tuple[TaskOccurrence, Task]]) -> list[dict]:
    agg: dict[int, dict] = defaultdict(
        lambda: {"title": "", "place_id": 0, "done": 0, "missed": 0, "skipped": 0,
                 "pending": 0, "total": 0}
    )
    for occ, task in occs:
        a = agg[task.id]
        a["title"], a["place_id"] = task.title, task.place_id
        a["total"] += 1
        a[occ.status] = a.get(occ.status, 0) + 1
    out = []
    for a in agg.values():
        # percent ignores intentional skips: they are neither done nor missed
        base = a["total"] - a["skipped"]
        a["percent"] = round(100 * a["done"] / base) if base else 0
        out.append(a)
    out.sort(key=lambda a: (-a["total"], a["title"]))
    return out


def per_day(occs: list[tuple[TaskOccurrence, Task]], tz: str) -> dict[date, dict]:
    tzinfo = ZoneInfo(tz)
    days: dict[date, dict] = defaultdict(lambda: {"done": 0, "missed": 0,
                                                  "skipped": 0, "pending": 0})
    for occ, _ in occs:
        status = _status_of(occ)
        # work stretched over days marks the day it starts as well as the day
        # it has to be ready
        marked = {occ.due_at.astimezone(tzinfo).date()}
        if occ.start_at is not None:
            marked.add(occ.start_at.astimezone(tzinfo).date())
        for d in marked:
            days[d][status] += 1
    return dict(days)


def per_place(occs: list[tuple[TaskOccurrence, Task]],
              place_names: dict[int, str]) -> list[dict]:
    agg: dict[str, dict] = defaultdict(
        lambda: {"done": 0, "missed": 0, "skipped": 0, "pending": 0, "total": 0}
    )
    for occ, task in occs:
        name = place_names.get(task.place_id, "?")
        a = agg[name]
        a["total"] += 1
        a[occ.status] = a.get(occ.status, 0) + 1
    rows = [{"place": name, **a,
             "percent": (round(100 * a["done"] / (a["total"] - a["skipped"]))
                         if a["total"] - a["skipped"] else 0)}
            for name, a in agg.items()]
    rows.sort(key=lambda r: -r["total"])
    return rows


def trend(occs: list[tuple[TaskOccurrence, Task]], tz: str) -> list[tuple[date, int, int]]:
    """(week_start, done, total) chronological for a trend line chart."""
    tzinfo = ZoneInfo(tz)
    buckets: dict[date, dict] = defaultdict(lambda: {"done": 0, "total": 0})
    for occ, _ in occs:
        d = occ.due_at.astimezone(tzinfo).date()
        start = d - timedelta(days=d.weekday())  # week starts Monday
        buckets[start]["total"] += 1
        if occ.status == "done":
            buckets[start]["done"] += 1
    return [(k, v["done"], v["total"]) for k, v in sorted(buckets.items())]


def occurrence_moment(occ: TaskOccurrence) -> datetime:
    """The hour a repeat really belongs to: start of work, else its deadline."""
    return occ.start_at or occ.due_at


# a work window longer than this is drawn only by its boundaries, so a task
# with a deadline a week ahead does not paint a whole week of the grid
HOUR_BLOCK_HOURS = 24


def occurrence_hours(occ: TaskOccurrence, task: Task | None = None) -> list[datetime]:
    """Hours an occurrence occupies: one moment, or the whole work window.

    A task stretched over time («с 10:00 до 14:00») keeps every hour between
    its start and its ready-by moment busy, which is how the owner reads the
    hour grid; very long windows only mark their boundaries. A one-off task
    without a ready-by moment is a plain reminder: it occupies its own hour,
    never a band up to midnight.
    """
    start = occurrence_moment(occ)
    if occ.start_at is None or occ.due_at <= start:
        return [start]
    if task is not None and task.schedule_type == "once" and task.deadline is None:
        return [start]
    if occ.due_at - start > timedelta(hours=HOUR_BLOCK_HOURS):
        return [start, occ.due_at]
    first = start.replace(minute=0, second=0, microsecond=0)
    stamps = []
    cur = first
    while cur <= occ.due_at:
        stamps.append(cur)
        cur += timedelta(hours=1)
    return stamps


def activity_by_hour(occs: list[tuple[TaskOccurrence, Task]],
                     tz: str) -> dict[tuple[date, int], dict]:
    """(user-local date, hour of day) -> status counts.

    Same statuses as the daily map, so a short period can be read hour by
    hour: what was done, missed, skipped and what is still planned.
    """
    tzinfo = ZoneInfo(tz)
    hours: dict[tuple[date, int], dict] = defaultdict(
        lambda: {"done": 0, "missed": 0, "skipped": 0, "pending": 0}
    )
    for occ, task in occs:
        cell_status = _status_of(occ)
        for stamp in occurrence_hours(occ, task):
            local = stamp.astimezone(tzinfo)
            hours[(local.date(), local.hour)][cell_status] += 1
    return dict(hours)
