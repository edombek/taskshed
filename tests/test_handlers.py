"""Chat logic end-to-end through BotApp + FakeMessenger (no network)."""

import re
from datetime import UTC, date, datetime, time, timedelta

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
        assert prev_buttons(f)[0][0][1] == f"place:{pid_home}"
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
        assert prev_buttons(f)[0][0][1] == f"place:{pid_home}"
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


class TestWizardKeyboards:
    async def test_once_deadline_via_buttons(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await send(env, "/задача")
        await send(env, "Тест")
        pid = await place_id_of(store, user.id, "Дом")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        rows = fake.last_buttons()
        assert ("📅 Завтра", "dt:1") in rows[0]
        await send(env, cb="dt:1")
        times = [cb for row in fake.last_buttons() for _, cb in row]
        assert "tm:18:00" in times
        await send(env, cb="tm:18:00")
        await send(env, "09:00")
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        assert tasks[0].deadline == datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, 18, 0, tzinfo=UTC)

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
        await send(env, "7:45")
        await send(env, "")  # default reminder
        tasks = await store.list_tasks(user.id)
        assert tasks and tasks[0].deadline.hour == 7 and tasks[0].deadline.minute == 45

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


class TestTimeInputFormats:
    @pytest.mark.parametrize(("typed", "want"), [
        ("11:55", time(11, 55)),
        ("11 55", time(11, 55)),
        ("11.55", time(11, 55)),
        ("11-55", time(11, 55)),
        ("9.5", time(9, 5)),
    ])
    def test_flexible_separators(self, typed, want):
        from taskshed.bot.app import _parse_hhmm
        assert _parse_hhmm(typed) == want

    @pytest.mark.parametrize("typed", ["1155", "11:55:55", "25:00", "11:75", ""])
    def test_rejects_garbage(self, typed):
        from taskshed.bot.app import _parse_hhmm
        assert _parse_hhmm(typed) is None


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


class TestFlexibleDates:
    @pytest.mark.parametrize(("typed", "want"), [
        ("2026-11-15", date(2026, 11, 15)),
        ("15.11.2026", date(2026, 11, 15)),
        ("15/11/2026", date(2026, 11, 15)),
        ("15 11 2026", date(2026, 11, 15)),
        ("15.11.26", date(2026, 11, 15)),
    ])
    def test_full_dates(self, typed, want):
        from taskshed.bot.app import _parse_date
        assert _parse_date(typed, date(2026, 10, 26)) == want

    def test_short_rolls_forward(self):
        from taskshed.bot.app import _parse_date
        assert _parse_date("15.11", date(2026, 10, 26)) == date(2026, 11, 15)
        assert _parse_date("15.02", date(2026, 10, 26)) == date(2027, 2, 15)

    def test_words(self):
        from taskshed.bot.app import _parse_date
        assert _parse_date("завтра", date(2026, 10, 26)) == date(2026, 10, 27)
        assert _parse_date("Today", date(2026, 10, 26)) == date(2026, 10, 26)

    @pytest.mark.parametrize("typed", ["15.13.2026", "32.11.2026", "abc", "2026-11"])
    def test_rejects_garbage(self, typed):
        from taskshed.bot.app import _parse_date
        assert _parse_date(typed, date(2026, 10, 26)) is None


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
        await send(env, "завтра")  # anchor as a word
        f = await send(env, cb="tm:09:00")
        assert fake.find("добавлена")
        tasks = await store.list_tasks(user.id)
        assert tasks[0].title == "второе название"
        assert tasks[0].schedule_data["n"] == 3
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        assert tasks[0].schedule_data["anchor"] == tomorrow
