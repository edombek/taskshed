"""Scheduler tick tests: morning digest, every-T repeat, silence, quiet hours."""

from datetime import UTC, datetime, time, timedelta

import pytest

from taskshed.bot.app import BotApp
from taskshed.config import BotCfg
from taskshed.core.models import ScheduleType
from taskshed.messaging.base import FakeMessenger, RawEvent
from taskshed.scheduler.jobs import _in_quiet, run_tick
from taskshed.storage.db import make_engine
from taskshed.storage.repositories import Store
from taskshed.storage.tables import CurrentLocation, TaskOccurrence

# a nominal "now": 09:01 UTC on today's date (users here live in UTC)
NOW = datetime.combine(datetime.now(UTC).date(), time(9, 1), tzinfo=UTC)


@pytest.fixture
async def env():
    store = Store(make_engine("sqlite+aiosqlite:///:memory:"))
    await store.create_all()
    fake = FakeMessenger()
    app = BotApp(store, BotCfg(), {"telegram": fake})
    yield app, store, fake
    await store._engine.dispose()


async def setup_user(store, place="Дом", title="Задача дня", lead_min=240,
                     deadline_hours=3, ttl_hours=6, morning="09:00", nag=60):
    """One user, one place, one open repeat: notice opened an hour ago,
    deadline three hours ahead — so both the morning set and the window own it."""
    user, _ = await store.register_account("telegram", "u1", tz="UTC")
    p = await store.add_place(user.id, place)
    if title is None:
        await store.update_settings(user.id, location_ttl_hours=ttl_hours,
                                    morning_time=morning, nag_interval_min=nag,
                                    quiet_start="00:00", quiet_end="00:00")
        return user, p
    deadline = NOW + timedelta(hours=deadline_hours)
    task = await store.create_task(user.id, p.id, title, ScheduleType.ONCE, {},
                                   notify_before_min=lead_min, deadline=deadline)
    await store.materialize_occurrences(task, NOW.date(), 10)
    await store.update_settings(user.id, location_ttl_hours=ttl_hours,
                                morning_time=morning, nag_interval_min=nag,
                                quiet_start="00:00", quiet_end="00:00")
    return user, p


async def stale_location(store, user_id, hours):
    async with store._sf() as s:
        row = await s.get(CurrentLocation, user_id)
        row.set_at = (NOW - timedelta(hours=hours)).replace(tzinfo=None)
        await s.commit()


class TestQuiet:
    def test_windows(self):
        assert _in_quiet(time(23, 30), time(23, 0), time(7, 0))
        assert _in_quiet(time(6, 0), time(23, 0), time(7, 0))
        assert not _in_quiet(time(12, 0), time(23, 0), time(7, 0))
        assert _in_quiet(time(1, 0), time(0, 30), time(1, 30))
        assert not _in_quiet(time(12, 0), time(0, 0), time(0, 0))  # disabled


class TestMorningDigest:
    async def test_silent_without_tasks(self, env):
        app, store, fake = env
        await setup_user(store, title=None)
        await run_tick(app, store, NOW)
        assert fake.sent == []

    async def test_digest_at_morning_moment_with_fresh_location(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert any("Задача дня" in m.text for m in fake.sent)
        # the task message itself carries the buttons
        assert any(any(cb.startswith("done:") for _, cb in row)
                   for m in fake.sent for row in m.buttons)
        st = await store.get_settings(user.id)
        assert st.last_morning_at is not None and st.last_notified_at is not None

    async def test_nothing_before_the_morning_moment(self, env):
        app, store, fake = env
        user, p = await setup_user(store, morning="09:30", lead_min=15)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)          # 09:01, morning is 09:30
        assert fake.sent == []

    async def test_quiet_hours_silence(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.update_settings(user.id, quiet_start="08:00", quiet_end="10:00")
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent == []

    async def test_other_places_still_arrive_when_this_one_is_empty(self, env):
        """No tasks for the current place: the notice is not dropped — the
        other place's tasks come through in brief form."""
        app, store, fake = env
        user, _ = await setup_user(store, place="Работа")
        elsewhere = await store.add_place(user.id, "Дача")
        await store.set_location(user.id, elsewhere.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert any("Дача" in m.text for m in fake.sent)
        assert any("Работа: Задача дня" in m.text for m in fake.sent)

    async def test_morning_digest_once_per_day(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=1))
        assert fake.sent == []   # the T-repeat is not due yet, morning is done

    async def test_other_places_arrive_in_brief_form(self, env):
        app, store, fake = env
        user, p = await setup_user(store, place="Дом")
        work = await store.add_place(user.id, "Работа")
        task = await store.create_task(user.id, work.id, "Отчёт", ScheduleType.ONCE,
                                       {}, notify_before_min=120,
                                       deadline=NOW + timedelta(hours=4))
        await store.materialize_occurrences(task, NOW.date(), 10)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert any("Задача дня" in m.text for m in fake.sent)   # here: its own message
        assert any("Другие места" in m.text and "Отчёт" in m.text for m in fake.sent)
        assert any(cb == "fin:" for row in fake.last_buttons() for _, cb in row)
        # the brief copy carries no buttons of its own
        brief = next(m for m in fake.sent if "Другие места" in m.text)
        assert brief.buttons == []


class TestStaleLocation:
    async def test_asks_once_then_notices_on_the_answer(self, env):
        app, store, fake = env
        user, p = await setup_user(store, ttl_hours=6)
        await store.set_location(user.id, p.id)
        await stale_location(store, user.id, hours=7)
        await run_tick(app, store, NOW)
        assert any("Где вы сейчас?" in m.text for m in fake.sent)
        st = await store.get_settings(user.id)
        assert st.pending_reminder is True
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=1))
        assert fake.sent == []  # not nagging twice
        await app.handle(RawEvent("telegram", "u1", callback=f"place:{p.id}"))
        assert any("Задача дня" in m.text for m in fake.sent)
        assert (await store.get_settings(user.id)).pending_reminder is False

class TestRepeatEveryTMinutes:
    """Tasks between their notice moment and their deadline come back every
    settings.nag_interval_min minutes."""

    async def test_repeats_on_the_interval(self, env):
        app, store, fake = env
        user, p = await setup_user(store, morning="23:00", nag=60)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=30))
        assert fake.sent == []
        await run_tick(app, store, NOW + timedelta(minutes=61))
        assert fake.sent

    async def test_silent_while_the_notice_moment_is_ahead(self, env):
        app, store, fake = env
        # lead 15 min at a deadline 3h away: nothing to repeat about yet
        user, p = await setup_user(store, morning="23:00", lead_min=15)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent == []

    async def test_snooze_holds_the_next_repeat_back(self, env):
        app, store, fake = env
        user, p = await setup_user(store, morning="23:00", nag=30)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent
        occ_id = (await store.rows_in_window(user.id, NOW))[0][0].id
        # push this row's notice moment out (nominal NOW, not the host clock)
        async with store._sf() as s:
            row = await s.get(TaskOccurrence, occ_id)
            row.notify_at = (NOW + timedelta(minutes=120)).replace(tzinfo=None)
            await s.commit()
        await store.update_settings(user.id, last_notified_at=NOW)
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=31))
        assert fake.sent == []      # its notice moment was pushed two hours out
        await run_tick(app, store, NOW + timedelta(minutes=125))
        assert fake.sent

    async def test_closed_tasks_stop_the_repeat(self, env):
        app, store, fake = env
        user, p = await setup_user(store, morning="23:00", nag=30)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent
        for occ, _ in await store.rows_in_window(user.id, NOW):
            assert await store.skip(occ.id)
        await store.update_settings(user.id, last_notified_at=NOW)
        fake.clear()
        await run_tick(app, store, NOW + timedelta(hours=5))
        assert fake.sent == []


class TestPlaceButtonsInNotice:
    async def test_done_button_closes_only_that_task(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        msg = next(m for m in fake.sent if "Задача дня" in m.text and m.buttons)
        occ_id = next(int(cb.split(":")[1]) for row in msg.buttons
                      for _, cb in row if cb.startswith("done:"))
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", "u1", callback=f"done:{occ_id}",
                                  message_id=msg.id))
        assert len(fake.sent) == before          # no chatter: the message re-drew
        assert "✅" in msg.text and msg.buttons == []

    async def test_finish_button_returns_the_main_menu(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        fin = next(m for m in fake.sent
                   if any(cb == "fin:" for row in m.buttons for _, cb in row))
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", "u1", callback="fin:",
                                  message_id=fin.id))
        fresh = fake.sent[before:]
        assert any("ещё" in m.text for m in fresh)
        assert any(any(cb == "cmd:tasks" for row in m.buttons for _, cb in row)
                   for m in fresh)
        # the notice's keyboards are disarmed, including this one
        assert fin.buttons == []
