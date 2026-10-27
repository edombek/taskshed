"""Tests for pure scheduling rules (no DB, no platforms)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from taskshed.core.models import (
    OccurrenceStatus,
    ScheduleType,
    Task,
    TaskOccurrence,
)
from taskshed.core.scheduling import (
    due_for_the_day,
    in_notice_window,
    lead_parts,
    occurrence_dates,
    occurrence_datetimes,
    overdue,
    plan_occurrences,
)

MSK = ZoneInfo("Europe/Moscow")
LONDON = ZoneInfo("Europe/London")


def task(stype=ScheduleType.EVERY_N_DAYS, data=None, clock=time(18, 0), lead=60,
         deadline=None, start=None, place_id=1) -> Task:
    return Task(id=7, user_id=1, place_id=place_id, title="t",
                schedule_type=stype, schedule_data=data or {},
                deadline_time=clock, notify_before_min=lead, deadline=deadline,
                start_at=start)


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
        got = occurrence_dates(t, date(2026, 10, 19), date(2026, 10, 30))
        assert got == [date(2026, 10, d) for d in (19, 23, 26, 30)]

    def test_monthly_day_clamps(self):
        t = task(ScheduleType.MONTHLY_DAY, {"day": 31})
        got = occurrence_dates(t, date(2026, 2, 1), date(2026, 4, 30))
        assert got == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]

    def test_once_in_range(self):
        dl = datetime(2026, 10, 20, 12, tzinfo=MSK)
        t = task(ScheduleType.ONCE, deadline=dl)
        assert occurrence_dates(t, date(2026, 10, 19), date(2026, 10, 21)) == [
            date(2026, 10, 20)]
        assert occurrence_dates(t, date(2026, 10, 21), date(2026, 10, 25)) == []

    def test_empty_range(self):
        t = task(ScheduleType.WEEKDAYS, {"weekdays": [0]})
        assert occurrence_dates(t, date(2026, 10, 10), date(2026, 10, 1)) == []


class TestDeadlineAndNotice:
    def test_recurring_deadline_is_the_rules_clock(self):
        t = task(clock=time(18, 0), lead=60)
        due, notify = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        assert due == datetime(2026, 10, 26, 15, 0, tzinfo=UTC)     # 18:00 MSK
        assert notify == datetime(2026, 10, 26, 14, 0, tzinfo=UTC)  # an hour earlier

    def test_end_of_day_when_no_clock_chosen(self):
        t = task(clock=time(23, 59), lead=0)
        due, notify = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        assert (due.hour, due.minute) == (20, 59)   # 23:59 MSK in UTC
        assert notify == due

    def test_lead_may_open_days_before_the_deadline(self):
        t = task(clock=time(12, 0), lead=3 * 1440)
        due, notify = occurrence_datetimes(t, date(2026, 10, 30), MSK)
        assert notify == due - timedelta(days=3)

    def test_once_deadline_is_its_own_moment(self):
        dl = datetime(2026, 10, 26, 8, 30, tzinfo=MSK)
        t = task(ScheduleType.ONCE, deadline=dl, lead=90)
        due, notify = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        assert due == dl.astimezone(UTC)
        assert notify == due - timedelta(minutes=90)

    def test_start_without_a_deadline_is_due_that_evening(self):
        t = task(ScheduleType.ONCE, start=datetime(2026, 10, 26, 9, tzinfo=MSK),
                 lead=30)
        due, notify = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        assert (due.astimezone(MSK).hour, due.astimezone(MSK).minute) == (23, 59)
        assert notify == due - timedelta(minutes=30)

    def test_wall_clock_survives_a_timezone_change(self):
        """The plan is the user's clock: 18:00 is 18:00 in either zone."""
        t = task(clock=time(18, 0), lead=60)
        msk_due, msk_notify = occurrence_datetimes(t, date(2026, 10, 26), MSK)
        lon_due, lon_notify = occurrence_datetimes(t, date(2026, 10, 26), LONDON)
        assert msk_due.astimezone(MSK).time() == lon_due.astimezone(LONDON).time()
        assert msk_due != lon_due            # a different real moment
        assert (msk_due - msk_notify) == (lon_due - lon_notify) == timedelta(hours=1)


class TestPlan:
    def test_plan_horizon(self):
        t = task(data={"n": 2, "anchor": "2026-10-25"})
        occs = plan_occurrences(t, date(2026, 10, 25), 5, MSK)
        assert [o.due_at.date() for o in occs] == [
            date(2026, 10, d) for d in (25, 27, 29)
        ]
        assert all(o.status == OccurrenceStatus.PENDING for o in occs)
        assert all(o.task_id == 7 for o in occs)
        assert all(o.notify_at == o.due_at - timedelta(minutes=60) for o in occs)

    def test_backfill_stops_at_the_anchor(self):
        t = task(data={"n": 3, "anchor": "2026-10-16"})
        occs = plan_occurrences(t, date(2026, 10, 25), 5, MSK, back_days=30)
        assert [o.due_at.date() for o in occs] == [
            date(2026, 10, d) for d in (16, 19, 22, 25, 28)
        ]

    def test_backfill_invents_no_history_for_dateless_rules(self):
        t = task(ScheduleType.WEEKDAYS, data={"weekdays": [0, 2, 4]})
        occs = plan_occurrences(t, date(2026, 10, 25), 5, MSK, back_days=60)
        assert [o.due_at.date() for o in occs] == [
            date(2026, 10, d) for d in (26, 28, 30)
        ]


class TestNoticeWindows:
    def _occ(self, due, notify, status=OccurrenceStatus.PENDING):
        return TaskOccurrence(id=1, task_id=7, due_at=due, notify_at=notify,
                              status=status)

    def test_morning_set_takes_notice_passed_deadline_ahead(self):
        day_start = datetime(2026, 10, 26, tzinfo=UTC)
        day_end = datetime(2026, 10, 26, 23, 59, 59, tzinfo=UTC)
        # notice went out two days ago, deadline is later today
        assert due_for_the_day(self._occ(day_end - timedelta(hours=1),
                                         day_start - timedelta(days=2)),
                               day_start, day_end)
        # deadline tomorrow, notice already open: still today's news
        assert due_for_the_day(self._occ(day_end + timedelta(hours=5),
                                         day_start - timedelta(hours=1)),
                               day_start, day_end)
        # notice only opens next week: not today
        assert not due_for_the_day(self._occ(day_end + timedelta(days=9),
                                             day_end + timedelta(days=8)),
                                   day_start, day_end)
        # deadline already behind us: the overdue pass owns it, not the digest
        assert not due_for_the_day(self._occ(day_start - timedelta(hours=3),
                                             day_start - timedelta(hours=6)),
                                   day_start, day_end)
        # a finished repeat is never announced again
        assert not due_for_the_day(self._occ(day_end - timedelta(hours=1),
                                             day_start, status=OccurrenceStatus.DONE),
                                   day_start, day_end)

    def test_window_is_between_notice_and_deadline(self):
        now = datetime(2026, 10, 26, 12, tzinfo=UTC)
        assert in_notice_window(self._occ(now + timedelta(hours=2),
                                          now - timedelta(minutes=5)), now)
        assert not in_notice_window(self._occ(now + timedelta(hours=2),
                                              now + timedelta(minutes=5)), now)
        assert not in_notice_window(self._occ(now - timedelta(hours=1),
                                              now - timedelta(hours=3)), now)
        assert in_notice_window(self._occ(now + timedelta(hours=2),
                                          now - timedelta(hours=9),
                                          status=OccurrenceStatus.IN_PROGRESS), now)
        assert not in_notice_window(self._occ(now + timedelta(hours=2),
                                              now - timedelta(hours=9),
                                              status=OccurrenceStatus.SKIPPED), now)


class TestLeadParts:
    def test_biggest_whole_unit(self):
        assert lead_parts(15) == (15, "lead_min")
        assert lead_parts(90) == (90, "lead_min")   # not a whole number of hours
        assert lead_parts(120) == (2, "lead_hour")
        assert lead_parts(2880) == (2, "lead_day")
        assert lead_parts(0) == (0, "lead_min")


class TestOverdue:
    def test_overdue(self):
        now = datetime(2026, 10, 26, 12, tzinfo=UTC)
        pending = TaskOccurrence(None, 1, datetime(2026, 10, 26, 11, tzinfo=UTC),
                                 datetime(2026, 10, 26, 9, tzinfo=UTC))
        assert overdue(pending, now)
        done = TaskOccurrence(None, 1, pending.due_at, pending.notify_at,
                              status=OccurrenceStatus.DONE)
        assert not overdue(done, now)
        future = TaskOccurrence(None, 1, datetime(2026, 10, 27, tzinfo=UTC),
                                datetime(2026, 10, 26, 9, tzinfo=UTC))
        assert not overdue(future, now)
