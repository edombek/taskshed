"""Task-creation wizard and saved cards, end-to-end through BotApp."""

import re
from datetime import UTC, datetime, time, timedelta

from chatenv import PID, place_id_of, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import RawEvent


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
        soon = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=30).time()
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
