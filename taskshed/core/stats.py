"""Pure statistics aggregations over (occurrence, task) pairs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Task, TaskOccurrence


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
        d = occ.due_at.astimezone(tzinfo).date()
        days[d][occ.status] = days[d].get(occ.status, 0) + 1
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


def completions_by_hour(done_at: list[datetime], tz: str) -> dict[tuple[date, int], int]:
    """(user-local date, hour of day) -> tasks finished in that hour.

    Short reporting periods are drawn as an hour-level grid, so the daily
    view is not the only resolution available.
    """
    tzinfo = ZoneInfo(tz)
    grid: dict[tuple[date, int], int] = defaultdict(int)
    for stamp in done_at:
        local = stamp.astimezone(tzinfo)
        grid[(local.date(), local.hour)] += 1
    return dict(grid)
