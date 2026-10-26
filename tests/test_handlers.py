"""Chat logic end-to-end through BotApp + FakeMessenger (no network)."""

import re
from datetime import UTC, datetime

import pytest

from taskshed.bot.app import BotApp
from taskshed.config import BotCfg
from taskshed.core.models import ScheduleType
from taskshed.messaging.base import FakeMessenger, RawEvent
from taskshed.storage.db import make_engine
from taskshed.storage.repositories import Store

PID = "1000"


@pytest.fixture
async def env():
    store = Store(make_engine("sqlite+aiosqlite:///:memory:"))
    await store.create_all()
    fake = FakeMessenger()
    vk_fake = FakeMessenger("vk")
    app = BotApp(store, BotCfg(), {"telegram": fake, "vk": vk_fake})
    yield app, store, fake
    await store._engine.dispose()


async def send(env, text=None, cb=None, pid=PID, platform="telegram"):
    app, store, fake = env
    messenger = app.messengers[platform]
    fake.clear()
    app.messengers["vk"].clear()
    await app.handle(RawEvent(platform, pid, text=text, callback=cb))
    return messenger


async def place_id_of(store, pid_user, name):
    for p in await store.list_places(pid_user):
        if p.name == name:
            return p.id
    raise AssertionError(f"place {name} not found")


class TestOnboarding:
    async def test_start_and_first_place(self, env):
        app, store, fake = env
        f = await send(env, "/start")
        assert "Привет" in f.texts()[0]
        user = await store.get_user_by_account("telegram", PID)
        f = await send(env, "Дом")
        assert "добавлено" in f.texts()[0]
        assert len(await store.list_places(user.id)) == 1

    async def test_add_second_place_via_places_command(self, env):
        await send(env, "Дом")
        await send(env, "/места")
        f = await send(env, "Работа")
        assert "добавлено" in f.texts()[0]

    async def test_language_switch(self, env):
        f = await send(env, "/lang")
        assert f.last_buttons()[0][1][1] == "lang:en"
        f = await send(env, cb="lang:en")
        assert f.texts()[0] == "Language: English"
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        assert user.language == "en"


class TestTaskWizard:
    async def test_every_n_days_flow(self, env):
        app, store, fake = env
        await send(env, "Работа")
        await send(env, "/задача")
        assert "Название" in fake.texts()[0]
        await send(env, "Отчёт")
        user = await store.get_user_by_account("telegram", PID)
        pid_work = await place_id_of(store, user.id, "Работа")
        f = await send(env, cb=f"place:{pid_work}")
        labels = [label for row in f.last_buttons() for label, _ in row]
        assert any("Каждые" in label for label in labels)
        await send(env, cb="sched:every_n_days")
        assert "сколько дней" in fake.texts()[0]
        await send(env, "3")
        today = datetime.now(UTC).date().isoformat()
        await send(env, today)
        await send(env, "08:30")
        assert "добавлена" in fake.texts()[0]
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1
        assert tasks[0].schedule_type == ScheduleType.EVERY_N_DAYS
        assert tasks[0].schedule_data["n"] == 3

    async def test_weekdays_flow(self, env):
        _, store, _ = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Уборка")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid_home}")
        await send(env, cb="sched:weekdays")
        await send(env, cb="wd:0")   # toggle Mon on
        await send(env, cb="wd:0")   # toggle off
        await send(env, cb="wd:2")   # Wed
        await send(env, cb="wdsubmit")
        await send(env, "")
        tasks = await store.list_tasks(user.id)
        assert tasks[0].schedule_data == {"weekdays": [2]}


class TestWhereAndTasks:
    async def test_where_sets_location(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        f = await send(env, "/гдея")
        assert f.last_buttons()[0][0][1] == f"place:{pid_home}"
        f = await send(env, cb=f"place:{pid_home}")
        assert "Текущее место: Дом" in f.texts()[0]

    async def test_task_list_with_done_button(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(user.id, pid_home, "Полить цветы",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": today.isoformat()},
                                       datetime.now(UTC).timetz().replace(second=0,
                                                                          microsecond=0))
        await store.materialize_occurrences(task, today, 1, "UTC")
        await store.set_location(user.id, pid_home)
        f = await send(env, "/задачи")
        assert "Полить цветы" in f.texts()[0]
        occ_cb = f.last_buttons()[0][0][1]
        assert occ_cb.startswith("done:")
        occ_id = int(occ_cb.split(":")[1])
        f = await send(env, cb=occ_cb)
        assert "Готово: «Полить цветы»" in f.texts()[0]
        occ, _ = await store.get_occurrence(occ_id)
        assert occ.status == "done"

    async def test_tasks_asks_where_when_no_location(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        f = await send(env, "/задачи")
        assert f.last_buttons()[0][0][1] == f"place:{pid_home}"
        f = await send(env, cb=f"place:{pid_home}")
        # tasks list followed the location answer
        assert "нет" in f.texts()[0].lower() or "Задачи" in f.texts()[0]


class TestStatsCommands:
    async def test_text_csv_chart(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(user.id, pid_home, "Отчёт",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": today.isoformat()},
                                       datetime.now(UTC).timetz().replace(second=0,
                                                                          microsecond=0))
        await store.materialize_occurrences(task, today, 0, "UTC")
        occ, _ = (await store.occurrences_today(user.id, today, "UTC"))[0]
        await store.mark_done(occ.id, "telegram")
        f = await send(env, "/статистика неделя")
        assert "Отчёт" in f.texts()[0] and "100%" in f.texts()[0]
        f = await send(env, "/статистика всё csv")
        assert f.sent[-1].document and f.sent[-1].document.endswith(".csv")
        f = await send(env, "/статистика месяц график")
        assert f.sent[-1].image and f.sent[-1].image.endswith(".png")


class TestFanout:
    async def test_all_vs_primary(self, env):
        app, store, fake = env
        user, _ = await store.register_account("telegram", PID)
        vk_user, vk_acc = await store.register_account("vk", "999")
        await store.attach_account(vk_acc, user.id)
        await store.merge_user(vk_user.id, user.id)
        text, buttons = "📋 digest", [[("✅", "done:1")]]
        await app.notify_user(user.id, text, buttons)  # notify_platforms=all default
        assert any(m.text == text for m in fake.sent)
        assert any(m.text == text for m in app.messengers["vk"].sent)
        app.messengers["vk"].clear()
        fake.clear()
        await store.update_settings(user.id, notify_platforms="primary")
        await app.handle(RawEvent("vk", "999", text="/start"))  # vk becomes most recent
        await app.notify_user(user.id, text, buttons)
        assert app.messengers["vk"].sent and not fake.sent


class TestLinking:
    async def test_link_code_flow_across_platforms(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        # register a VK account (own empty user first)
        vk_user, _ = await store.register_account("vk", "777")
        f = await send(env, "/привязать")
        m = re.search(r"/link (\w{8})", f.texts()[0])
        assert m
        code = m.group(1)
        f = await send(env, f"/link {code}", pid="777", platform="vk")
        assert "связаны" in f.texts()[0].lower() or "связаны" in f.texts()[-1].lower()
        same = await store.get_user_by_account("vk", "777")
        assert same.id == user.id
        assert len(await store.get_accounts(user.id)) == 2


class TestMenuAndDeadline:
    async def test_start_and_help_have_menu_buttons(self, env):
        app, store, fake = env
        await send(env, "/start")
        flat = [cb for row in fake.last_buttons() for _, cb in row]
        assert {"cmd:where", "cmd:tasks", "cmd:task", "cmd:places",
                "cmd:reminders", "cmd:stats"} == set(flat)
        await send(env, "/help")
        assert any(cb.startswith("cmd:")
                   for _, cb in fake.last_buttons()[0])

    async def test_menu_button_runs_command(self, env):
        app, store, fake = env
        await send(env, "Дом")  # registers user, creates place
        f = await send(env, cb="cmd:places")
        assert "Дом" in f.texts()[0]

    async def test_once_wizard_rejects_past_deadline(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "Тест")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        f = await send(env, "2016-10-26 04:05")  # year typo: 10 years ago
        assert "прошёл" in f.texts()[-1]
        assert await store.list_tasks(user.id) == []  # nothing created
        today = datetime.now(UTC).date().isoformat()
        await send(env, f"{today} 12:00")
        await send(env, "09:00")
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1 and tasks[0].deadline.year == datetime.now(UTC).year
