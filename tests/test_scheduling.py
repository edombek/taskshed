"""Tests for pure scheduling rules (no DB, no platforms)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from taskshed.core.models import OccurrenceStatus, ScheduleType, Task, TaskOccurrence
from taskshed.core.scheduling import (
    occurrence_dates,
    occurrence_datetimes,
    overdue,
    plan_occurrences,
)

MSK = ZoneInfo("Europe/Moscow")


def task(stype=ScheduleType.EVERY_N_DAYS, data=None, when=time(9, 0), deadline=None,
         place_id=1) -> Task:
    return Task(id=7, user_id=1, place_id=place_id, title="t",
                schedule_type=stype, schedule_data=data or {},
                reminder_time=when, deadline=deadline)


class TestOccurrenceDates:
    def test_every_n_days_grid(self):
        t = task(data={"n": 3, "anchor": "2026-10-01"})
        got = occurrence_dates(t, date(2026, 10, 1), date(2026, 10, 15))
        assert got == [date(2026, 10, d) for d in (1, 4, 7, 10, 13)]

    def test_every_n_days_grid_starts_mid(self):
        t = task(data={"n": 3, "anchor": "2026-10-01"})
        got = occurrence_dates(t, date(2026, 10, 5), date(2026, 10, 10))
        assert got == [date(2026, 10, 7), date(2026, 10, 10)]

    def test_weekdays(self):
        t = task(ScheduleType.WEEKDAYS, {"weekdays": [0, 4]})  # Mon, Fri
        got = occurrence_dates(t, date(2026, 10, 19), date(2026, 10, 30))  # Mon..Fri x2
        assert got == [date(2026, 10, d) for d in (19, 23, 26, 30)]

    def test_monthly_day_clamps(self):
        t = task(ScheduleType.MONTHLY_DAY, {"day": 31})
        got = occurrence_dates(t, date(2026, 2, 1), date(2026, 4, 30))
        assert got == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]

    def test_once_in_range(self):
        dl = datetime(2026, 10, 20, 12, tzinfo=MSK)
        t = task(ScheduleType.ONCE, deadline=dl)
        assert occurrence_dates(t, date(2026, 10, 19), date(2026, 10, 21)) == [date(2026, 10, 20)]
        assert occurrence_dates(t, date(2026, 10, 21), date(2026, 10, 25)) == []

    def test_empty_range(self):
        t = task(ScheduleType.WEEKDAYS, {"weekdays": [0]})
        assert occurrence_dates(t, date(2026, 10, 10), date(2026, 10, 1)) == []


class TestDatetimes:
    def test_regular_occurrence_utc_conversion(self):
        t = task(when=time(9, 0))
        due, rem = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        assert rem == datetime(2026, 10, 26, 6, 0, tzinfo=UTC)  # 09:00 MSK
        assert due.hour == 20 and due.minute == 59  # 23:59 MSK

    def test_once_reminder_not_after_deadline(self):
        dl = datetime(2026, 10, 26, 8, 30, tzinfo=MSK)
        t = task(ScheduleType.ONCE, deadline=dl, when=time(9, 0))
        due, rem = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        assert rem <= due
        assert due == dl.astimezone(UTC)


class TestPlan:
    def test_plan_horizon(self):
        t = task(data={"n": 2, "anchor": "2026-10-25"})
        occs = plan_occurrences(t, date(2026, 10, 25), 5, MSK)
        assert [o.due_at.date() for o in occs] == [
            date(2026, 10, d) for d in (25, 27, 29)
        ]
        assert all(o.status == OccurrenceStatus.PENDING for o in occs)
        assert all(o.task_id == 7 for o in occs)

    def test_backfill_stops_at_the_anchor(self):
        # a chart window may reach back, but never past the date in the rule
        t = task(data={"n": 3, "anchor": "2026-10-16"})
        occs = plan_occurrences(t, date(2026, 10, 25), 5, MSK, back_days=30)
        assert [o.due_at.date() for o in occs] == [
            date(2026, 10, d) for d in (16, 19, 22, 25, 28)
        ]

    def test_backfill_invents_no_history_for_dateless_rules(self):
        # weekdays/monthly rules carry no date of their own: they are never
        # planned before the day they started running
        t = task(ScheduleType.WEEKDAYS, data={"weekdays": [0, 2, 4]})
        occs = plan_occurrences(t, date(2026, 10, 25), 5, MSK, back_days=60)
        assert [o.due_at.date() for o in occs] == [
            date(2026, 10, d) for d in (26, 28, 30)
        ]


class TestOverdue:
    def test_overdue(self):
        now = datetime(2026, 10, 26, 12, tzinfo=UTC)
        pending = TaskOccurrence(None, 1, datetime(2026, 10, 26, 11, tzinfo=UTC),
                                 datetime(2026, 10, 26, 9, tzinfo=UTC))
        assert overdue(pending, now)
        done = TaskOccurrence(None, 1, pending.due_at, pending.reminder_at,
                              status=OccurrenceStatus.DONE)
        assert not overdue(done, now)
        future = TaskOccurrence(None, 1, datetime(2026, 10, 27, tzinfo=UTC),
                                datetime(2026, 10, 26, 9, tzinfo=UTC))
        assert not overdue(future, now)
