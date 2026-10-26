"""Scheduler tick tests: silence rules, stale-location ask, digest, quiet hours."""

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

NOW = datetime.combine(datetime.now(UTC).date(), time(9, 1), tzinfo=UTC)


@pytest.fixture
async def env():
    store = Store(make_engine("sqlite+aiosqlite:///:memory:"))
    await store.create_all()
    fake = FakeMessenger()
    app = BotApp(store, BotCfg(), {"telegram": fake})
    yield app, store, fake
    await store._engine.dispose()


async def setup_user(store, anchor_today=True, place="Дом", with_task=True, ttl_hours=6,
                     rt=time(8, 0)):
    user, _ = await store.register_account("telegram", "u1", tz="UTC")
    p = await store.add_place(user.id, place)
    if with_task:
        anchor = datetime.now(UTC).date().isoformat() if anchor_today else "2020-01-01"
        task = await store.create_task(user.id, p.id, "Задача дня",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, rt)
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
    await store.update_settings(user.id, location_ttl_hours=ttl_hours,
                                remind_times=["09:00"], quiet_start="00:00", quiet_end="00:00")
    return user, p


async def stale_location(store, user_id, place_id, hours):
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


class TestTick:
    async def test_silent_without_tasks(self, env):
        app, store, fake = env
        await setup_user(store, with_task=False)
        await run_tick(app, store, NOW)
        assert fake.sent == []

    async def test_digest_when_fresh_location(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert any("Задача дня" in m.text for m in fake.sent)
        assert any("done:" in cb for row in fake.last_buttons() for _, cb in row)
        st = await store.get_settings(user.id)
        assert st.last_reminded_at is not None

    async def test_silent_when_no_tasks_for_place(self, env):
        app, store, fake = env
        user, p = await setup_user(store, place="Работа")
        other = await store.add_place(user.id, "Дача")
        await store.set_location(user.id, other.id, set_at=NOW)  # fresh, but no tasks there
        await run_tick(app, store, NOW)
        assert fake.sent == []

    async def test_stale_location_asks_once_then_digest_on_answer(self, env):
        app, store, fake = env
        user, p = await setup_user(store, ttl_hours=6)
        await store.set_location(user.id, p.id)
        await stale_location(store, user.id, p.id, hours=7)
        await run_tick(app, store, NOW)
        assert any("Где вы сейчас?" in m.text for m in fake.sent)
        st = await store.get_settings(user.id)
        assert st.pending_reminder is True
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=1))
        assert fake.sent == []  # not nagging twice
        # user taps the place button → digest follows
        await app.handle(RawEvent("telegram", "u1", callback=f"place:{p.id}"))
        assert any("Задача дня" in m.text for m in fake.sent)
        st = await store.get_settings(user.id)
        assert st.pending_reminder is False

    async def test_quiet_hours_silence(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.update_settings(user.id, quiet_start="08:00", quiet_end="10:00")
        await store.set_location(user.id, p.id, set_at=NOW)
        await run_tick(app, store, NOW)
        assert fake.sent == []

    async def test_not_yet_trigger_time(self, env):
        app, store, fake = env
        user, p = await setup_user(store, rt=time(20, 0))
        await store.set_location(user.id, p.id, set_at=NOW)
        early = datetime.combine(datetime.now(UTC).date(), time(8, 30), tzinfo=UTC)
        await run_tick(app, store, early)
        assert fake.sent == []

    async def test_interval_mode(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        st = await store.update_settings(user.id, remind_mode="interval",
                                         remind_interval_min=60)
        assert st.last_reminded_at is None
        await run_tick(app, store, NOW)
        assert fake.sent  # fired
        fake.clear()
        recent = await store.get_settings(user.id)
        assert recent.last_reminded_at >= NOW
        await run_tick(app, store, NOW + timedelta(minutes=30))
        assert fake.sent == []
        fake.clear()
        await run_tick(app, store, NOW + timedelta(hours=3))
        assert fake.sent


class TestPerTaskReminder:
    async def test_task_reminder_time_fires_before_global_moment(self, env):
        """A task's own reminder_at triggers the digest even if the global
        settings moment (23:00) is far away — this is what users expect."""
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await store.update_settings(user.id, remind_times=["23:00"])
        # occurrence reminder_at 08:00 < NOW (09:01) and last_reminded_at None
        await run_tick(app, store, NOW)
        assert fake.sent, "digest should fire at the task's reminder time"
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=1))
        assert fake.sent == []  # not nagging again

    async def test_snooze_pushes_per_task_reminder(self, env):
        app, store, fake = env
        user, p = await setup_user(store)
        await store.set_location(user.id, p.id, set_at=NOW)
        await store.update_settings(user.id, remind_times=["23:00"])
        await run_tick(app, store, NOW)
        assert fake.sent
        occs = await store.occurrences_today(user.id, NOW.date(), "UTC")
        # deterministic: reminder_at := NOW+60min (store.snooze uses wall clock,
        # which the nominal NOW may not match)
        async with store._sf() as s:
            row = await s.get(TaskOccurrence, occs[0][0].id)
            row.reminder_at = (NOW + timedelta(minutes=60)).replace(tzinfo=None)
            await s.commit()
        await store.update_settings(user.id, last_reminded_at=None)
        fake.clear()
        await run_tick(app, store, NOW + timedelta(minutes=2))
        assert fake.sent == []  # snoozed reminder is an hour ahead
        await run_tick(app, store, NOW + timedelta(minutes=62))
        assert fake.sent
