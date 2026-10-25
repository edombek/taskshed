"""Repository tests against in-memory SQLite (no network)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from taskshed.core.models import ScheduleType
from taskshed.storage.db import make_engine
from taskshed.storage.repositories import Store


@pytest.fixture
async def store():
    st = Store(make_engine("sqlite+aiosqlite:///:memory:"))
    await st.create_all()
    return st


async def make_user(store, places=("Дом", "Работа")):
    user, _ = await store.register_account("telegram", "111", language="ru", tz="Europe/Moscow")
    for p in places:
        await store.add_place(user.id, p)
    return user


class TestAccounts:
    async def test_register_and_resolve(self, store):
        user, account = await store.register_account("vk", "222", language="ru")
        found = await store.get_user_by_account("vk", "222")
        assert found and found.id == user.id
        assert await store.get_settings(user.id)  # default settings created
        accts = await store.get_accounts(user.id)
        assert [a.platform for a in accts] == ["vk"]

    async def test_duplicate_place_rejected(self, store):
        user, _ = await store.register_account("telegram", "1")
        assert await store.add_place(user.id, "Дом") is not None
        assert await store.add_place(user.id, "Дом") is None


class TestTasksAndOccurrences:
    async def test_materialize_and_today_filtering(self, store):
        user = await make_user(store)
        places = await store.list_places(user.id)
        work = next(p for p in places if p.name == "Работа")
        task = await store.create_task(
            user.id, work.id, "Отчёт", ScheduleType.EVERY_N_DAYS,
            {"n": 1, "anchor": "2026-10-25"}, time(9, 0),
        )
        n = await store.materialize_occurrences(task, date(2026, 10, 25), 2, "Europe/Moscow")
        assert n == 3
        # idempotent
        again = await store.materialize_occurrences(task, date(2026, 10, 25), 2, "Europe/Moscow")
        assert again == 0

        found = await store.occurrences_today(user.id, date(2026, 10, 26), "Europe/Moscow")
        # today's occurrence + yesterday's still-pending one (overdue keeps showing)
        assert len(found) == 2
        occ, t = found[-1]
        assert t.title == "Отчёт"
        assert occ.due_at.astimezone(ZoneInfo("Europe/Moscow")).date() == date(2026, 10, 26)
        # filtered by place with no tasks there
        home = next(p for p in places if p.name == "Дом")
        assert await store.occurrences_today(user.id, date(2026, 10, 26),
                                             "Europe/Moscow", place_id=home.id) == []

    async def test_done_and_completion(self, store):
        user = await make_user(store)
        work = (await store.list_places(user.id))[1]
        task = await store.create_task(user.id, work.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": "2026-10-25"}, time(9, 0))
        await store.materialize_occurrences(task, date(2026, 10, 25), 0, "Europe/Moscow")
        occ, _ = (await store.occurrences_today(user.id, date(2026, 10, 25), "Europe/Moscow"))[0]
        assert await store.mark_done(occ.id, "telegram") is True
        assert await store.mark_done(occ.id, "telegram") is False  # already done
        assert await store.occurrences_today(user.id, date(2026, 10, 25), "Europe/Moscow") == []

    async def test_overdue_and_snooze(self, store):
        user = await make_user(store)
        work = (await store.list_places(user.id))[1]
        past = datetime.now(UTC) - timedelta(hours=1)
        task = await store.create_task(user.id, work.id, "T", ScheduleType.ONCE, {},
                                       time(9, 0), deadline=past)
        await store.materialize_occurrences(task, past.date(), 0, "UTC")
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        assert occs and occs[0][0].status == "pending"
        assert await store.mark_overdue(datetime.now(UTC)) == 1
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        assert occs[0][0].status == "missed"
        assert await store.snooze(occs[0][0].id, 60) is True
        # snoozed beyond "now" — nothing overdue anymore
        assert await store.mark_overdue(datetime.now(UTC)) == 0


class TestLocation:
    async def test_set_get_location(self, store):
        user = await make_user(store)
        home = (await store.list_places(user.id))[0]
        assert await store.get_location(user.id) is None
        await store.set_location(user.id, home.id)
        place, set_at = await store.get_location(user.id)
        assert place.name == "Дом"
        assert (datetime.now(UTC) - set_at) < timedelta(minutes=1)


class TestLinking:
    async def test_link_code_roundtrip(self, store):
        tg = await make_user(store)
        vk_user, vk_account = await store.register_account("vk", "555")
        code = await store.create_link_code(tg.id, "telegram")
        owner = await store.redeem_link_code(code.lower())
        assert owner == tg.id
        # one-time
        assert await store.redeem_link_code(code) is None
        await store.attach_account(vk_account, tg.id)
        found = await store.get_user_by_account("vk", "555")
        assert found.id == tg.id
        assert len(await store.get_accounts(tg.id)) == 2

    async def test_expired_code(self, store):
        user = await make_user(store)
        code = await store.create_link_code(user.id, "telegram")
        # force expiry
        async with store._sf() as s:
            from taskshed.storage.tables import LinkCode
            row = await s.get(LinkCode, code)
            row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
            await s.commit()
        assert await store.redeem_link_code(code) is None
