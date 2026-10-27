"""Chat logic end-to-end through BotApp + FakeMessenger (no network)."""

import re
from datetime import UTC, datetime, time, timedelta

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


def prev_buttons(f) -> list:
    """Buttons of the command's own reply (the menu follow-up is the last)."""
    return f.sent[-2].buttons or []


class TestTimeInput:
    """One parser for typed moments, plus a "did I read that right?" card."""

    async def _to_until_step(self, env):
        _, store, _ = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Полка")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{home}")
        await send(env, cb="sched:once")
        await send(env, cb="dt:1")     # tomorrow, by button
        f = await send(env, "10.00")   # dot instead of a colon
        assert "понял так" in f.texts()[0] and "10:00" in f.texts()[0]
        await send(env, cb="cfm:yes")
        return user

    async def _finish(self, env):
        await send(env, "09:00")       # an exact time: no guessing, no card
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        return (await store.list_tasks(user.id))[0]

    async def test_bare_deadline_time_is_read_back_and_confirmed(self, env):
        # «До какого времени» used to read a bare "13.00" as a date, said
        # "не понял" and left the user typing the same thing again
        await self._to_until_step(env)
        f = await send(env, "13.00")
        assert "понял так" in f.texts()[0] and "13:00" in f.texts()[0]
        cbs = [cb for row in f.last_buttons() for _, cb in row]
        assert "cfm:yes" in cbs and "cfm:no" in cbs
        await send(env, cb="cfm:yes")
        task = await self._finish(env)
        assert task.deadline - task.start_at == timedelta(hours=3)

    async def test_denied_reading_is_asked_again(self, env):
        await self._to_until_step(env)
        await send(env, "13.00")
        f = await send(env, cb="cfm:no")
        assert "закончить" in f.texts()[0]      # the same question returns
        f = await send(env, "15")               # a whole hour this time
        assert "понял так" in f.texts()[0] and "15:00" in f.texts()[0]
        await send(env, cb="cfm:yes")
        task = await self._finish(env)
        assert task.deadline - task.start_at == timedelta(hours=5)

    async def test_deadline_before_the_start_is_refused_at_once(self, env):
        await self._to_until_step(env)
        f = await send(env, "09.00")            # earlier than the 10:00 start
        assert "понял так" not in f.texts()[0]  # no point confirming nonsense
        assert "позже начала" in f.texts()[0]

    async def test_bare_time_at_the_start_step_picks_the_next_moment(self, env):
        _, store, _ = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Позвонить")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{home}")
        await send(env, cb="sched:once")
        soon = (datetime.now(UTC) + timedelta(hours=2)).time()
        f = await send(env, soon.strftime("%H.%M"))   # not a date typed
        assert "понял так" in f.texts()[0]
        await send(env, cb="cfm:yes")
        await send(env, cb="dtl:eod")
        await send(env, "09:00")
        task = (await store.list_tasks(user.id))[0]
        today = datetime.now(UTC).date()
        assert task.start_at > datetime.now(UTC) - timedelta(minutes=1)
        assert task.start_at.hour == soon.hour
        assert today <= task.start_at.date() <= today + timedelta(days=1)

    async def test_no_deadline_keeps_it_a_plain_reminder(self, env):
        # the «✖ Без срока» button: a moment to be reminded at, not an event
        # stretched over the rest of the day
        _, store, fake = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Позвонить в банк")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{home}")
        await send(env, cb="sched:once")
        f = await send(env, cb="now:")
        assert "dtl:none" in [cb for row in f.last_buttons() for _, cb in row]
        await send(env, cb="dtl:none")
        await send(env, "09:00")
        assert fake.find("добавлена")
        task = (await store.list_tasks(user.id))[0]
        assert task.deadline is None and task.start_at is not None
        card = fake.texts()[0]
        assert "🕐" in card and " до " not in card   # one moment, no window
        today = datetime.now(UTC).date()
        occs = await store.occurrences_in_range(
            user.id,
            datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=1),
            datetime(today.year, today.month, today.day, tzinfo=UTC) + timedelta(days=7))
        assert len(occs) == 1

    async def test_now_button_starts_the_task_immediately(self, env):
        _, store, _ = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Разобрать почту")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{home}")
        f = await send(env, cb="sched:once")
        assert "now:" in [cb for row in f.last_buttons() for _, cb in row]
        f = await send(env, cb="now:")          # «▶ Сейчас», no questions asked
        assert "закончить" in f.texts()[0]
        await send(env, cb="dtl:eod")
        await send(env, "09:00")
        task = (await store.list_tasks(user.id))[0]
        assert abs((datetime.now(UTC) - task.start_at).total_seconds()) < 120


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
    async def test_wizard_asks_again_when_its_place_vanished(self, env):
        # a place deleted while the wizard is open must not become a dangling
        # reference: the step is simply asked again
        app, store, fake = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Полка")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{home}")
        await send(env, cb="sched:every_n_days")
        await send(env, "2")
        await send(env, datetime.now(UTC).date().isoformat())
        assert await store.delete_place(user.id, home)
        f = await send(env, "09:00")  # the finish step notices the hole
        assert "местности" in f.texts()[0]
        assert await store.list_tasks(user.id) == []
        f = await send(env, "Дача")   # a brand new name heals it
        assert "добавлена" in fake.texts()[0]
        task = (await store.list_tasks(user.id))[0]
        assert task.place_id == await place_id_of(store, user.id, "Дача")

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

    async def test_typed_new_place_at_the_place_step_is_created(self, env):
        # the place step used to answer only buttons: with no places saved the
        # bot said "add one by text", then swallowed whatever was typed
        app, store, fake = env
        await send(env, "/задача")
        await send(env, "Корм для кота")
        f = await send(env, "Дом")
        cbs = [cb for row in f.last_buttons() for _, cb in row]
        assert "sched:once" in cbs, "the wizard must move on to the schedule"
        user = await store.get_user_by_account("telegram", PID)
        assert await place_id_of(store, user.id, "Дом")
        await send(env, cb="sched:every_n_days")
        await send(env, "2")
        await send(env, datetime.now(UTC).date().isoformat())
        await send(env, "09:00")
        assert "добавлена" in fake.texts()[0]
        task = (await store.list_tasks(user.id))[0]
        assert task.place_id == await place_id_of(store, user.id, "Дом")

    async def test_typed_existing_place_is_reused_not_duplicated(self, env):
        app, store, fake = env
        await send(env, "Дом")
        await send(env, "/задача")
        await send(env, "Полка")
        f = await send(env, "дом")  # same place, different case
        cbs = [cb for row in f.last_buttons() for _, cb in row]
        assert "sched:once" in cbs
        user = await store.get_user_by_account("telegram", PID)
        names = [p.name for p in await store.list_places(user.id)]
        assert names == ["Дом"]

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
        assert any(cb == "menu:back" for row in f.last_buttons() for _, cb in row)
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
        occ_cb = prev_buttons(f)[0][0][1]
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
        docs = [m.document for m in f.sent if m.document]
        assert len(docs) == 1 and docs[0].endswith(".csv")
        f = await send(env, "/статистика месяц график")
        images = [m.image for m in f.sent if m.image]
        assert len(images) == 1 and images[0].endswith(".png")


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
                "cmd:settings", "cmd:stats", "cmd:edit"} == set(flat)
        await send(env, "/help")
        assert any(cb.startswith("cmd:")
                   for _, cb in fake.last_buttons()[0])

    async def test_menu_button_runs_command(self, env):
        app, store, fake = env
        await send(env, "Дом")  # registers user, creates place
        f = await send(env, cb="cmd:places")
        assert "Дом" in f.texts()[0]

    async def test_once_wizard_rejects_past_start(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "Тест")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        f = await send(env, "2016-10-26 04:05")  # year typo: 10 years ago
        assert "уже прошло" in f.texts()[-1]
        assert await store.list_tasks(user.id) == []  # nothing created
        today = datetime.now(UTC).date().isoformat()
        await send(env, f"{today} 12:00")   # when it starts
        await send(env, "")                 # ready-by: Enter = end of that day
        await send(env, "09:00")            # reminder time
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1
        assert tasks[0].start_at.year == datetime.now(UTC).year
        assert (tasks[0].deadline.hour, tasks[0].deadline.minute) == (23, 59)

    async def test_once_until_must_be_after_start(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "Тест")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        today = datetime.now(UTC).date()
        await send(env, f"{today.isoformat()} 18:00")
        f = await send(env, f"{today.isoformat()} 12:00")  # before the start
        assert "позже начала" in f.texts()[-1]
        await send(env, cb="dtl:eod")  # a button does the right thing anyway
        labels = [lbl for row in fake.last_buttons() for lbl, _ in row]
        assert "⏎ 18:00" in labels  # reminder defaults to the start time
        await send(env, "")
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1


class TestWizardKeyboards:
    async def test_once_start_and_until_via_buttons(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "Тест")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        rows = fake.last_buttons()
        cbs = [cb for row in rows for _, cb in row]
        assert "now:" in cbs                     # «▶ Сейчас» — start this minute
        assert ("📅 Завтра", "dt:1") in rows[1]
        await send(env, cb="dt:1")
        times = [cb for row in fake.last_buttons() for _, cb in row]
        assert "tm:18:00" in times
        await send(env, cb="tm:18:00")
        until = [cb for row in fake.last_buttons() for _, cb in row]
        assert "dtl:eod" in until and "dtl:next18" in until
        await send(env, cb="dtl:next18")
        await send(env, "")  # reminder defaults to the start time
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1
        day1 = datetime.now(UTC).date() + timedelta(days=1)
        assert tasks[0].start_at == datetime(
            day1.year, day1.month, day1.day, 18, 0, tzinfo=UTC)
        day2 = day1 + timedelta(days=1)
        assert tasks[0].deadline == datetime(
            day2.year, day2.month, day2.day, 18, 0, tzinfo=UTC)
        assert tasks[0].reminder_time == time(18, 0)

    async def test_whentime_accepts_typed_hhmm(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "Тест")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        await send(env, cb="dt:0")
        f = await send(env, "7:45")
        assert "понял так" in f.texts()[0]   # the day came from the ⏎ button
        await send(env, cb="cfm:yes")
        await send(env, "")  # ready-by: end of that day
        await send(env, "")  # default reminder = when it starts
        tasks = await store.list_tasks(user.id)
        assert tasks and tasks[0].start_at.hour == 7 and tasks[0].start_at.minute == 45
        assert (tasks[0].deadline.hour, tasks[0].deadline.minute) == (23, 59)
        assert tasks[0].reminder_time == time(7, 45)

    async def test_urls_defanged_in_titles_and_places(self, env):
        app, store, fake = env
        await send(env, "мой сайт https://evil.example.com/spam")  # first-run place
        places = await store.list_places(
            (await store.get_user_by_account("telegram", PID)).id)
        assert "evil[.]example[.]com" in places[0].name
        assert "evil.example.com" not in places[0].name  # not clickable anymore
        await send(env, "/задача")
        await send(env, "прочитать www.banned.org/x и позвонить")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, places[0].name)
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:weekdays")
        await send(env, cb="wd:0")
        await send(env, cb="wdsubmit")
        await send(env, "09:00")
        tasks = await store.list_tasks(user.id)
        assert tasks and "www[.]banned[.]org" in tasks[0].title
        assert "www." not in tasks[0].title


class TestMenuAfterCommand:
    async def _menu_on_last(self, env):
        _, _, fake = env
        rows = fake.last_buttons()
        assert rows and any(cb.startswith("cmd:") for r in rows for _, cb in r)

    async def test_done_callback_gets_menu(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        await send(env, cb=f"done:{occs[0][0].id}")
        assert fake.find("Готово")
        await self._menu_on_last(env)

    async def test_wizard_save_gets_menu(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "T")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:monthly_day")
        await send(env, "15")
        await send(env, "09:00")
        await self._menu_on_last(env)


class TestStatsIncludePlanned:
    async def test_future_task_appears_in_stats(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        future = datetime.now(UTC) + timedelta(hours=20)  # inside the stats window
        task = await store.create_task(user.id, p.id, "запланировано", ScheduleType.ONCE,
                                       {}, time(9, 0), deadline=future)
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 8, "UTC")
        f = await send(env, "/статистика всё")
        assert "запланировано" in f.texts()[-1]


class TestMenuFollowUp:
    async def test_plain_reply_gets_menu_by_default(self, env):
        app, store, fake = env
        await send(env, "Дом")
        f = await send(env, "/отмена")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat

    async def test_task_list_has_menu_after_task_buttons(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
        f = await send(env, "/задачи")
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert any(cb.startswith("done:") for cb in flat)
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:task" in flat


class TestSnoozePicker:
    """⏭ Отложить must ask *how long* instead of picking a fixed hour."""

    async def _occ(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(0, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        return store, occs[0][0].id

    async def test_picker_offers_durations(self, env):
        _, occ_id = await self._occ(env)
        f = await send(env, cb=f"snooze:{occ_id}")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert f"snz:{occ_id}:15" in flat
        assert f"snz:{occ_id}:1440" in flat
        # the picker itself is the interactive reply: no menu spam below it
        assert len(f.sent) == 1

    async def test_choice_snoozes_by_requested_minutes(self, env):
        store, occ_id = await self._occ(env)
        before = (await store.get_occurrence(occ_id))[0].reminder_at
        f = await send(env, cb=f"snz:{occ_id}:15")
        assert any("15 мин" in t for t in f.texts())
        after = (await store.get_occurrence(occ_id))[0].reminder_at
        assert after > before + timedelta(minutes=14)
        # after a *completed* action the menu follow-up returns
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat


class TestInteractiveStats:
    async def _seed(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        task = await store.create_task(user.id, p.id, "дело", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1,
                                        "anchor": datetime.now(UTC).date().isoformat()},
                                       time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 3, "UTC")

    async def test_period_asked_via_buttons(self, env):
        await self._seed(env)
        f = await send(env, "/статистика")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["per:week", "per:month", "per:year", "per:all", "menu:back"]
        assert len(f.sent) == 1  # picker replaces the text, no double reply

    async def test_period_button_shows_text_with_format_buttons(self, env):
        await self._seed(env)
        f = await send(env, cb="per:week")
        assert "дело" in f.texts()[-1]
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:chart:week" in flat and "fmt:csv:week" in flat
        assert len(f.sent) == 1

    async def test_chart_sends_only_the_activity_heatmap(self, env):
        await self._seed(env)
        f = await send(env, cb="fmt:chart:week")
        images = [m for m in f.sent if m.image]
        assert len(images) == 1  # the retired daily-bar chart must not return
        assert images[0].image.endswith(".png")
        # the chart is the end of the answer: the menu follow-up must come
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat

    async def test_csv_button_sends_document(self, env):
        await self._seed(env)
        f = await send(env, cb="fmt:csv:month")
        docs = [m for m in f.sent if m.document]
        assert len(docs) == 1 and docs[0].document.endswith(".csv")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat


class TestFollowUpSuppressed:
    async def test_start_has_single_menu_message(self, env):
        _, _, fake = env
        await send(env, "/start")
        assert len(fake.sent) == 1

    async def test_reminders_status_exposes_location_frequency_button(self, env):
        await send(env, "Дом")
        f = await send(env, "/напоминания")
        labels = [label for row in f.last_buttons() for label, _ in row]
        assert any("спрашивать о месте" in label for label in labels)


class TestSubmenuBack:
    async def test_reminders_status_is_a_menu_with_back(self, env):
        await send(env, "Дом")
        f = await send(env, "/напоминания")
        assert len(f.sent) == 1  # no "anything else?" under a submenu screen
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "menu:back" in flat
        f = await send(env, cb="menu:back")
        assert len(f.sent) == 1  # the main menu answer does not repeat itself
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat and "menu:back" not in flat

    async def test_back_cancels_pending_addplace(self, env):
        app, store, fake = env
        await send(env, "Дом")
        await send(env, "/места")
        f = await send(env, cb="menu:back")
        assert "cmd:tasks" in [cb for row in f.last_buttons() for _, cb in row]
        assert not app.wizards  # the addplace wizard was dropped
        await send(env, "Какая-то фраза")
        user = await store.get_user_by_account("telegram", PID)
        assert "Какая-то" not in [p.name for p in await store.list_places(user.id)]


class TestWizardReminderButtons:
    async def test_reminder_time_via_buttons(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, "/задача")
        await send(env, "полить")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:every_n_days")
        await send(env, "3")
        f = await send(env, cb="dt:0")  # anchor today
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "tm:09:00" in flat  # the default gets a button, not an Enter
        await send(env, cb="tm:12:30")
        assert fake.find("добавлена")
        tasks = await store.list_tasks(user.id)
        assert tasks and tasks[0].reminder_time == time(12, 30)


class TestSkipOccurrence:
    async def _occ(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        return store, occs[0][0].id

    async def test_skip_closes_occurrence_without_missed(self, env):
        store, occ_id = await self._occ(env)
        f = await send(env, "/задачи")
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert f"skip:{occ_id}" in flat
        f = await send(env, cb=f"skip:{occ_id}")
        assert f.find("Пропущено")
        occ, _ = await store.get_occurrence(occ_id)
        assert occ.status == "skipped"
        # gone from today's list, and the background overdue pass ignores it
        assert not await store.occurrences_today(
            (await store.get_user_by_account("telegram", PID)).id,
            datetime.now(UTC).date(), "UTC")
        await store.mark_overdue(datetime.now(UTC) + timedelta(days=1))
        assert (await store.get_occurrence(occ_id))[0].status == "skipped"

    async def test_skipped_shows_in_stats_not_as_missed(self, env):
        store, occ_id = await self._occ(env)
        await send(env, cb=f"skip:{occ_id}")
        f = await send(env, "/статистика всё")
        assert "пропущено 1" in f.texts()[-1]
        assert "просрочено 0" in f.texts()[-1]


class TestWizardBackAndFlexDates:
    async def _start(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, "/задача")
        await send(env, "первое название")
        return app, store, fake, user, pid

    async def test_back_redoes_previous_question(self, env):
        _, store, fake, user, pid = await self._start(env)
        f = await send(env, cb="wz:back")  # from place back to title
        assert "Название задачи" in f.texts()[-1]
        f = await send(env, "второе название")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert f"place:{pid}" in flat and "wz:back" in flat
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:every_n_days")
        await send(env, "3")
        f = await send(env, "завтра")  # anchor as a word → read back first
        assert "понял так" in f.texts()[0]
        await send(env, cb="cfm:yes")
        f = await send(env, cb="tm:09:00")
        assert fake.find("добавлена")
        tasks = await store.list_tasks(user.id)
        assert tasks[0].title == "второе название"
        assert tasks[0].schedule_data["n"] == 3
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        assert tasks[0].schedule_data["anchor"] == tomorrow


class TestWizardDaysToggle:
    async def test_days_marked_and_message_edited(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, "/задача")
        await send(env, "тренировка")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:weekdays")
        assert len(fake.sent) == 1  # the days keyboard, nothing else yet
        # taps arrive without clearing the fake: edits must not add messages
        for cb in ("wd:4", "wd:5"):
            await app.handle(RawEvent("telegram", PID, callback=cb))
        assert len(fake.sent) == 1  # edited in place, not re-sent
        labels = [lbl for row in fake.last_buttons() for lbl, _ in row]
        assert sum(1 for lbl in labels if lbl.startswith("✅")) == 2
        # deselect drops the check too
        await app.handle(RawEvent("telegram", PID, callback="wd:4"))
        labels = [lbl for row in fake.last_buttons() for lbl, _ in row]
        assert sum(1 for lbl in labels if lbl.startswith("✅")) == 1


class TestWizardCancelButton:
    async def test_title_step_has_cancel_button(self, env):
        app, store, fake = env
        f = await send(env, "/задача")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "wz:cancel" in flat
        await send(env, cb="wz:cancel")
        assert fake.find("Отменено")
        assert not app.wizards


class TestReminderSettingsFlow:
    async def _open_settings(self, env):
        await send(env, "Дом")
        app, store, fake = env
        fake.clear()
        await app.handle(RawEvent("telegram", PID, text="/напоминания"))
        return app, store, fake

    async def _cb(self, app, cb):
        await app.handle(RawEvent("telegram", PID, callback=cb))

    async def _text(self, app, text):
        await app.handle(RawEvent("telegram", PID, text=text))

    async def test_buttons_prompt_and_status_edits_in_place(self, env):
        app, store, fake = await self._open_settings(env)
        assert len(fake.sent) == 1 and "Напоминания:" in fake.texts()[0]
        await self._cb(app, "rem:times")
        assert "Во сколько напоминать" in fake.texts()[-1]  # prompt appears
        await self._text(app, "09:00 18:30")
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1  # the screen was edited, not duplicated
        assert "по временам (09:00 18:30)" in statuses[0].text
        st = await store.get_settings((await store.get_user_by_account(
            "telegram", PID)).id)
        assert st.remind_times == ["09:00", "18:30"]

    async def test_toggle_edits_same_message(self, env):
        app, store, fake = await self._open_settings(env)
        await self._cb(app, "rem:toggle")
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1 and "выключены" in statuses[0].text

    async def test_bad_value_reprompts_without_touching_status(self, env):
        app, store, fake = await self._open_settings(env)
        await self._cb(app, "rem:ttl")
        assert "1–168" in fake.texts()[-1]
        await self._text(app, "999")
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1
        assert fake.find("Не понял")


class TestBackOnEveryPrompt:
    async def test_rem_prompt_has_back_and_cancels_input(self, env):
        app, store, fake = env
        await send(env, "Дом")
        f = await send(env, "/настройки")
        assert "⚙️ Настройки" in f.texts()[0]
        f = await send(env, cb="rem:ttl")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["rem:back"]  # back returns to the settings screen
        assert app._pending_inputs  # awaiting a value now
        f = await send(env, cb="rem:back")
        assert not app._pending_inputs
        assert "Напоминания:" in f.texts()[0]  # the settings screen again
        # a later number is no longer consumed as the ttl value
        await send(env, "999")
        user = await store.get_user_by_account("telegram", PID)
        assert (await store.get_settings(user.id)).location_ttl_hours == 6

    async def test_places_without_any_still_offers_back(self, env):
        app, store, fake = env
        await send(env, "/start")  # no places yet
        f = await send(env, "/места")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["menu:back"]


class TestRemindersWhenFlow:
    async def _open(self, env):
        await send(env, "Дом")
        app, store, fake = env
        fake.clear()
        await app.handle(RawEvent("telegram", PID, text="/напоминания"))
        return app, store, fake

    async def _cb(self, app, cb):
        await app.handle(RawEvent("telegram", PID, callback=cb))

    async def test_single_when_button_with_mode_picker(self, env):
        app, store, fake = await self._open(env)
        flat = [cb for row in fake.last_buttons() for _, cb in row]
        assert "rem:when" in flat and "rem:times" not in flat
        await self._cb(app, "rem:when")
        flat = [cb for row in fake.last_buttons() for _, cb in row]
        assert flat == ["rem:times", "rem:interval", "rem:back"]
        assert "Сейчас: по временам" in fake.texts()[-1]
        # Назад in the picker returns to the settings screen in place
        await self._cb(app, "rem:back")
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1

    async def test_interval_via_picker(self, env):
        app, store, fake = await self._open(env)
        await self._cb(app, "rem:when")
        await self._cb(app, "rem:interval")
        assert "Каждые сколько минут" in fake.texts()[-1]
        await app.handle(RawEvent("telegram", PID, text="90"))
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1
        assert "каждые 90 мин" in statuses[0].text


class TestStaleKeyboardCleanup:
    """Only the newest screen of a chat keeps its buttons."""

    async def test_previous_keyboards_are_disarmed(self, env):
        app, store, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, text="/гдея"))
        question = fake.sent[-1]
        assert question.buttons
        await app.handle(RawEvent("telegram", PID, text="/статистика"))
        assert question.buttons == []      # older keyboard is dead now
        assert fake.sent[-1].buttons       # the new screen is armed

    async def test_followup_menu_is_disarmed_too(self, env):
        app, store, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, text="/задачи"))
        menu = fake.sent[-1]
        assert menu.buttons  # the "anything else?" menu
        await app.handle(RawEvent("telegram", PID, text="/гдея"))
        assert menu.buttons == []

    async def test_inplace_screen_stays_armed(self, env):
        app, store, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, text="/напоминания"))
        screen = fake.sent[-1]
        assert screen.buttons
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", PID, callback="rem:toggle"))
        assert len(fake.sent) == before  # edited in place...
        assert screen.buttons            # ...and still clickable


class TestTaskEditor:
    """✏️ Editor: change any field of a task, reach past and recurring ones."""

    async def _seed(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await store.add_place(user.id, "Работа")
        places = await store.list_places(user.id)
        home, work = places[0].id, places[1].id
        today = datetime.now(UTC).date()
        future = await store.create_task(
            user.id, home, "Полка", ScheduleType.ONCE, {}, time(9, 0),
            deadline=datetime.now(UTC) + timedelta(days=2),
            start_at=datetime.now(UTC) + timedelta(days=1))
        every = await store.create_task(
            user.id, home, "Полить", ScheduleType.EVERY_N_DAYS,
            {"n": 1, "anchor": today.isoformat()}, time(9, 0))
        await store.materialize_occurrences(every, today, 3, "UTC")
        weekly = await store.create_task(
            user.id, home, "Стирка", ScheduleType.WEEKDAYS,
            {"weekdays": [2]}, time(9, 0))
        back = await store.create_task(
            user.id, home, "Прошлое", ScheduleType.EVERY_N_DAYS,
            {"n": 5, "anchor": (today - timedelta(days=30)).isoformat()}, time(9, 0))
        await store.materialize_occurrences(back, today - timedelta(days=30), 25, "UTC")
        await store.mark_overdue(datetime.now(UTC))
        return store, user, future.id, every.id, weekly.id, back.id, work

    def _cbs(self, messenger) -> list[str]:
        return [cb for row in messenger.last_buttons() for _, cb in row]

    async def test_picker_offers_tabs_and_upcoming_tasks(self, env):
        store, user, future, every, _, back, _ = await self._seed(env)
        f = await send(env, "/изменить")
        flat = self._cbs(f)
        assert f"edt:future:{future}" in flat and f"edt:future:{every}" in flat
        assert "etab:past" in flat and "etab:recurring" in flat and "etab:all" in flat
        assert "Будущие" in f.texts()[0]
        # past and recurring live behind their own tabs
        f = await send(env, cb="etab:past")
        flat = self._cbs(f)
        assert f"edt:past:{back}" in flat and f"edt:past:{every}" not in flat
        assert f"edt:past:{future}" not in flat
        f = await send(env, cb="etab:recurring")
        assert f"edt:recurring:{every}" in self._cbs(f)

    async def test_rename_keeps_the_rest_of_the_task(self, env):
        store, user, future, *_ = await self._seed(env)
        await send(env, "/изменить")
        f = await send(env, cb=f"edf:{future}:title")
        assert "Название" in f.texts()[0] or "название" in f.texts()[0]
        f = await send(env, "повесить полку")
        assert "повесить полку" in f.texts()[0] and "Изменено" in f.texts()[0]
        task = await store.get_task(user.id, future)
        assert task.title == "повесить полку"
        assert task.start_at and task.reminder_time == time(9, 0)

    async def test_move_to_another_place(self, env):
        store, user, future, *_, work = await self._seed(env)
        await send(env, cb=f"edf:{future}:place")
        await send(env, cb=f"place:{work}")
        assert (await store.get_task(user.id, future)).place_id == work

    async def test_change_reminder_moment(self, env):
        store, user, future, *_ = await self._seed(env)
        await send(env, cb=f"edf:{future}:reminder")
        f = await send(env, cb="tm:07:15")
        assert "07:15" in f.texts()[0]
        assert (await store.get_task(user.id, future)).reminder_time == time(7, 15)

    async def test_change_weekdays_only(self, env):
        store, user, _, _, weekly, *_ = await self._seed(env)
        await send(env, cb=f"edf:{weekly}:weekdays")
        await send(env, cb="wd:4")
        await send(env, cb="wdsubmit")
        task = await store.get_task(user.id, weekly)
        assert sorted(task.schedule_data["weekdays"]) == [2, 4]
        assert task.reminder_time == time(9, 0)

    async def test_moving_the_window_rebuilds_open_repeats(self, env):
        store, user, future, *_ = await self._seed(env)
        await send(env, cb=f"edf:{future}:when")
        await send(env, cb="dt:6")        # start six days ahead
        await send(env, cb="tm:11:00")
        await send(env, cb="dtl:eod")     # ready by the end of that day
        f = await send(env, cb="tm:11:00")
        assert "Изменено" in f.texts()[0]
        task = await store.get_task(user.id, future)
        assert task.start_at.date() == (datetime.now(UTC) + timedelta(days=6)).date()
        # the old repeat is gone, the new one exists
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC), datetime.now(UTC) + timedelta(days=9))
        mine = [o for o, t in occs if t.id == future]
        assert len(mine) == 1 and mine[0].start_at.date() == task.start_at.date()

    async def test_delete_asks_then_removes_history_too(self, env):
        store, user, _, _, _, back, _ = await self._seed(env)
        await send(env, cb="etab:past")
        f = await send(env, cb=f"eddel:past:{back}")
        assert "edyes" in " ".join(self._cbs(f)) and "Прошлое" in f.texts()[0]
        f = await send(env, cb=f"edyes:past:{back}")
        assert "удалена" in f.texts()[0]
        assert await store.get_task(user.id, back) is None
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=120), datetime.now(UTC))
        assert not [o for o, t in occs if t.id == back]  # cascade, no orphans


    async def test_menu_returns_after_the_editor_and_a_skip(self, env):
        # browsing the editor must not leave a dialog open: skip/done replies
        # still end with the main menu, otherwise nothing is clickable anymore
        app, store, _ = env
        store, user, _, every, *_ = await self._seed(env)
        home = next(p.id for p in await store.list_places(user.id) if p.name == "Дом")
        await store.set_location(user.id, home)
        await send(env, "/изменить")
        await send(env, cb="etab:all")
        await send(env, cb=f"edt:all:{every}")
        assert not app.wizards, "a card is not a dialog: no wizard may stay open"
        f = await send(env, "/задачи")
        skip = next(cb for row in prev_buttons(f) for _, cb in row
                    if cb.startswith("skip:"))
        f = await send(env, cb=skip)
        assert "Пропущено" in f.texts()[0]
        assert "Хотите сделать" in f.texts()[-1]
        assert "cmd:tasks" in [cb for row in f.sent[-1].buttons for _, cb in row]

    async def test_place_delete_asks_before_wiping_its_tasks(self, env):
        store, user, future, every, weekly, back, _ = await self._seed(env)
        home = next(p.id for p in await store.list_places(user.id) if p.name == "Дом")
        f = await send(env, cb=f"pdel:{home}")
        assert "pdelok" in " ".join(self._cbs(f))
        assert await store.get_place(user.id, home) is not None
        f = await send(env, cb=f"pdelok:{home}")
        assert "удалено задач: 4" in f.texts()[0]
        assert await store.get_place(user.id, home) is None
        assert await store.list_tasks(user.id, active_only=False) == []
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=90), datetime.now(UTC))
        assert occs == []  # history went with it, and the user said yes to that

    async def test_places_come_back_after_a_full_wipe(self, env):
        # the owner deleted both places: the first-run UX (a bare word is a
        # place name) must pick up again, and near-duplicates are refused
        app, store, _ = env
        store, user, *_ = await self._seed(env)
        for p in await store.list_places(user.id):
            f = await send(env, cb=f"pdel:{p.id}")
            ok = [cb for row in (f.last_buttons() or []) for _, cb in row
                  if cb.startswith("pdelok:")]
            if ok:
                await send(env, cb=ok[0])
        assert await store.list_places(user.id) == []
        assert await store.list_tasks(user.id, active_only=False) == []
        f = await send(env, "Дом")
        assert "добавлено" in f.texts()[0]
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]
        await send(env, "/места")
        f = await send(env, "дом")
        assert "уже есть" in f.texts()[0]
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]


class TestSavedCard:
    """Saving a task answers with what was created: window + reminder + next."""

    async def test_stretched_task_card_shows_window_and_reminder(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, "/задача")
        await send(env, "собрать полку")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        await send(env, cb="dt:1")        # start: tomorrow
        f = await send(env, cb="tm:10:00")
        assert "dtl:eod" in [cb for row in f.last_buttons() for _, cb in row]
        await send(env, cb="dtl:eod")     # ready by the end of that day
        f = await send(env, cb="tm:10:00")
        text = f.texts()[0]
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        assert "добавлена" in text
        assert f"{tomorrow:%d.%m} 10:00" in text   # начало
        assert f"{tomorrow:%d.%m} 23:59" in text   # конец
        assert "напоминание в 10:00" in text
        assert "ближайшее срабатывание" in text

    async def test_recurring_card_shows_rule_and_first_date(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, "/задача")
        await send(env, "полить")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:every_n_days")
        await send(env, "3")
        await send(env, cb="dt:0")
        f = await send(env, cb="tm:09:00")
        text = f.texts()[0]
        assert "каждые 3 дн." in text and "напоминание в 09:00" in text
        assert re.search(r"ближайшее срабатывание: \d{2}\.\d{2} 09:00", text)


class TestWorkInProgress:
    """One-off tasks have a work window and a «▶ В работе» button."""

    async def _stretched_task(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        task = await store.create_task(
            user.id, pid, "Полка", ScheduleType.ONCE, {}, time(9, 0),
            deadline=datetime.now(UTC) - timedelta(hours=1),
            start_at=datetime.now(UTC) - timedelta(hours=3),
        )
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 3, "UTC")
        await store.set_location(user.id, pid)
        return app, store, task

    async def test_list_offers_wip_and_shows_the_window(self, env):
        app, store, task = await self._stretched_task(env)
        f = await send(env, "/задачи")
        assert "с " in f.texts()[0] and " до " in f.texts()[0]
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert any(cb.startswith("wip:") for cb in flat)
        assert any(cb.startswith("done:") for cb in flat)

    async def test_wip_keeps_the_row_open_and_drops_itself(self, env):
        app, store, _ = await self._stretched_task(env)
        f = await send(env, "/задачи")
        occ_id = next(int(cb.split(":")[1]) for row in prev_buttons(f)
                      for _, cb in row if cb.startswith("wip:"))
        f = await send(env, cb=f"wip:{occ_id}")
        assert "В работе: «Полка»" in f.texts()[0]
        # the overdue pass does not turn started work into missed
        assert await store.mark_overdue(datetime.now(UTC)) == 0
        f = await send(env, "/задачи")
        assert "▶в работе" in f.texts()[0]
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert not any(cb.startswith("wip:") for cb in flat)
        assert not any(cb.startswith("snooze:") for cb in flat)
        assert f"done:{occ_id}" in flat and f"skip:{occ_id}" in flat

    async def test_digest_offers_wip_too(self, env):
        app, store, _ = await self._stretched_task(env)
        await store.set_location((await store.get_user_by_account("telegram", PID)).id,
                                 await place_id_of(store, (await store.get_user_by_account(
                                     "telegram", PID)).id, "Дом"))
        settings = await store.get_settings((await store.get_user_by_account(
            "telegram", PID)).id)
        place = (await store.get_location((await store.get_user_by_account(
            "telegram", PID)).id))[0]
        digest = await app.build_digest(
            (await store.get_user_by_account("telegram", PID)).id, settings, place)
        assert digest and any(cb.startswith("wip:") for row in digest[1] for _, cb in row)


class TestSettingsScreen:
    """One «⚙️ Настройки» screen for everything a user can configure."""

    async def test_screen_shows_platforms_and_chart_horizon(self, env):
        app, store, fake = env
        await send(env, "Дом")
        f = await send(env, "/настройки")  # /напоминания stays as an alias
        text = f.texts()[0]
        assert "⚙️ Настройки" in text
        assert "Telegram ✓" in text and "VK ✗" in text
        assert "График вперёд: 7 дн." in text
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert {"rem:toggle", "rem:when", "rem:quiet", "rem:ttl", "rem:tz",
                "rem:horizon", "rem:lang", "rem:link", "menu:back"} <= set(flat)
        assert not any(cb.startswith("rem:unlink") for cb in flat)

    async def test_chart_horizon_edits_the_screen_in_place(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await app.handle(RawEvent("telegram", PID, text="/настройки"))
        await app.handle(RawEvent("telegram", PID, callback="rem:horizon"))
        await app.handle(RawEvent("telegram", PID, text="14"))
        screens = [m for m in fake.sent if "⚙️ Настройки" in m.text]
        assert len(screens) == 1 and "График вперёд: 14 дн." in screens[0].text
        assert (await store.get_settings(user.id)).stats_horizon_days == 14

    async def test_language_switch_renders_settings_anew(self, env):
        app, store, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, text="/настройки"))
        screen = fake.sent[-1]
        await app.handle(RawEvent("telegram", PID, callback="rem:lang"))
        assert {"lang:ru", "lang:en"} <= {cb for row in fake.last_buttons()
                                          for _, cb in row}
        await app.handle(RawEvent("telegram", PID, callback="lang:en"))
        assert "⚙️ Settings" in screen.text and "Reminders:" in screen.text

    async def test_linked_platform_can_be_unlinked_from_settings(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        vk_user, vk_acc = await store.register_account("vk", "777")
        await store.attach_account(vk_acc, user.id)
        await store.merge_user(vk_user.id, user.id)
        await app.handle(RawEvent("telegram", PID, text="/настройки"))
        screen = fake.sent[-1]
        assert "Telegram ✓, VK ✓" in screen.text
        await app.handle(RawEvent("telegram", PID, callback=f"rem:unlink:{vk_acc.id}"))
        assert fake.find("отвязан")
        assert "VK ✗" in screen.text
        assert [a.platform for a in await store.get_accounts(user.id)] == ["telegram"]


class TestChartHorizonAndHours:
    """Charts look ahead (setting, default 1 week) and short periods go hourly."""

    async def _done_week(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(
            user.id, pid, "Отчёт", ScheduleType.EVERY_N_DAYS,
            {"n": 1, "anchor": today.isoformat()}, time(9, 0))
        await store.materialize_occurrences(task, today, 1, "UTC")
        for occ, _ in await store.occurrences_today(user.id, today, "UTC"):
            await store.mark_done(occ.id, "telegram")
        return app, store, fake, user

    async def test_week_period_offers_the_hourly_chart(self, env):
        app, store, fake, _ = await self._done_week(env)
        await send(env, "/статистика")
        f = await send(env, cb="per:week")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:hours:week" in flat and "fmt:chart:week" in flat
        app._pending_inputs.clear()
        await app.handle(RawEvent("telegram", PID, callback="fmt:hours:week"))
        assert any(m.image for m in fake.sent)

    async def test_longer_periods_do_not_offer_hourly(self, env):
        app, store, fake, _ = await self._done_week(env)
        await send(env, "/статистика")
        f = await send(env, cb="per:month")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:chart:month" in flat and "fmt:hours:month" not in flat

    async def test_hourly_map_covers_missed_and_skipped(self, env):
        # the hour grid is not only about finished tasks: a week where nothing
        # was completed still has to be drawn, with missed/skipped marked
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(user.id, pid, "Уборка",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": today.isoformat()},
                                       time(9, 0))
        await store.materialize_occurrences(task, today, 1, "UTC")
        occs = await store.occurrences_today(user.id, today, "UTC")
        await store.skip(occs[0][0].id)
        app._pending_inputs.clear()
        await app.handle(RawEvent("telegram", PID, callback="fmt:hours:week"))
        assert any(m.image for m in fake.sent)

    async def test_planned_task_shows_up_within_the_horizon(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        when = datetime.now(UTC) + timedelta(days=5)
        await store.create_task(user.id, pid, "Полка", ScheduleType.ONCE, {},
                                time(9, 0), deadline=when, start_at=when)
        # 5 days ahead: outside the old +1 day window, inside the week horizon
        await app.handle(RawEvent("telegram", PID, text="/статистика неделя график"))
        assert any(m.image for m in fake.sent)

    async def test_horizon_setting_widens_the_window(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        when = datetime.now(UTC) + timedelta(days=20)
        await store.create_task(user.id, pid, "Заказ", ScheduleType.ONCE, {},
                                time(9, 0), deadline=when, start_at=when)
        await app.handle(RawEvent("telegram", PID, text="/статистика месяц график"))
        assert not any(m.image for m in fake.sent)  # beyond the default 7 days
        assert fake.find("срабатываний не было")
        await store.update_settings(user.id, stats_horizon_days=30)
        await app.handle(RawEvent("telegram", PID, text="/статистика месяц график"))
        assert any(m.image for m in fake.sent)
