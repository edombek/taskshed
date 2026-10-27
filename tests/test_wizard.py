"""Task-creation wizard and saved cards, end-to-end through BotApp.

Every kind ends with the same two answers: the **deadline** (one moment for a
one-off, a clock on each repeat day for the rest) and **how long before it**
the user wants to be warned.
"""

import re
from datetime import UTC, datetime, time, timedelta

from chatenv import PID, place_id_of, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import RawEvent


async def _home(env):
    """A fresh user with one place «Дом» — returns (user, place_id)."""
    _, store, _ = env
    await send(env, "Дом")
    user = await store.get_user_by_account("telegram", PID)
    return user, await place_id_of(store, user.id, "Дом")


async def _once_start(env, title="Полка"):
    """Up to the one-off deadline question."""
    user, pid = await _home(env)
    await send(env, "/задача")
    await send(env, title)
    await send(env, cb=f"place:{pid}")
    await send(env, cb="sched:once")
    return user


async def _recurring_start(env, kind="every_n_days", title="Отчёт"):
    """Up to the schedule-parameter question of a repeating task."""
    user, pid = await _home(env)
    await send(env, "/задача")
    await send(env, title)
    await send(env, cb=f"place:{pid}")
    await send(env, cb=f"sched:{kind}")
    return user


def _flat(f) -> list[str]:
    return [cb for row in f.last_buttons() for _, cb in row]


class TestTimeInput:
    """One reader for every typed moment, plus a "did I read that right?" card."""

    async def test_garbage_at_the_when_step_is_explained_not_echoed(self, env):
        await _once_start(env)
        f = await send(env, "после обеда где-то")
        assert "Не понял" in f.texts()[0]
        assert "К какому моменту" in f.texts()[0]   # the question repeats as hint
        assert "bad" not in f.texts()[0]            # no raw i18n keys to the chat

    async def test_deadline_in_the_past_is_refused_at_once(self, env):
        await _once_start(env)
        f = await send(env, "2016-10-26 04:05")     # year typo: ten years ago
        assert "уже прошло" in f.texts()[0]
        _, store, _ = env
        assert await store.list_tasks(
            (await store.get_user_by_account("telegram", PID)).id) == []

    async def test_bare_time_is_read_back_and_confirmed(self, env):
        """A deadline is a moment, so "13.00" is a clock, never 13 May."""
        user = await _once_start(env)
        f = await send(env, "23.30")
        assert "понял так" in f.texts()[0] and "23:30" in f.texts()[0]
        assert "cfm:yes" in _flat(f) and "cfm:no" in _flat(f)
        await send(env, cb="cfm:yes")
        f = await send(env, "30")
        assert f.find("добавлена")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        assert task.notify_before_min == 30
        # a bare clock means the next moment it is still ahead of us
        assert task.deadline > datetime.now(UTC) - timedelta(minutes=5)
        assert (task.deadline.hour, task.deadline.minute) == (23, 30)

    async def test_denied_reading_is_asked_again(self, env):
        user = await _once_start(env)
        await send(env, "23.30")
        f = await send(env, cb="cfm:no")
        assert "К какому моменту" in f.texts()[0]   # the same question returns
        f = await send(env, "завтра 09:00")
        assert "понял так" in f.texts()[0]
        await send(env, cb="cfm:yes")
        await send(env, cb="ln:60")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        assert task.deadline.strftime("%d.%m") == f"{tomorrow:%d.%m}"
        assert (task.deadline.hour, task.deadline.minute) == (9, 0)

    async def test_a_day_alone_moves_to_the_clock_question(self, env):
        user = await _once_start(env)
        f = await send(env, "завтра")
        assert "Во сколько?" in f.texts()[0]        # the day needs a clock
        assert "tm:12:00" in _flat(f)
        await send(env, cb="tm:12:00")
        await send(env, cb="ln:60")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        assert task.deadline.strftime(
            "%d.%m %H:%M") == f"{tomorrow:%d.%m} 12:00"


class TestDeadlineClock:
    """Repeating kinds ask for the clock their deadline falls at."""

    async def _to_dtime(self, env, kind="every_n_days"):
        """Up to the deadline-clock question, whatever the kind."""
        await _recurring_start(env, kind)
        if kind == "every_n_days":
            await send(env, "3")
            return await send(env, cb="dt:0")      # first deadline: today
        if kind == "monthly_day":
            return await send(env, "15")           # → the clock question
        await send(env, cb="wd:2")                 # Wednesdays
        return await send(env, cb="wdsubmit")      # → the clock question

    async def test_clock_buttons_offer_a_default(self, env):
        f = await self._to_dtime(env)
        assert "Во сколько по сроку" in f.texts()[0]
        assert "tm:18:00" in _flat(f)               # ⏎ default gets a button
        assert "ln:" not in _flat(f)                # the lead comes after

    async def test_exact_clock_needs_no_confirm_the_lead_follows(self, env):
        await self._to_dtime(env)
        f = await send(env, "7.45")
        assert "За сколько до срока" in f.texts()[0]
        f = await send(env, cb="ln:120")
        assert f.find("добавлена")
        user = await store_user(env)
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        assert task.deadline_time == time(7, 45)
        assert task.notify_before_min == 120

    async def test_an_ambiguous_clock_is_read_back_as_time(self, env):
        """At this step «15.11» can only be 15:11 — but the bot asks."""
        await self._to_dtime(env)
        f = await send(env, "15.11")
        assert "понял так" in f.texts()[0] and "15:11" in f.texts()[0]
        await send(env, cb="cfm:yes")
        await send(env, cb="ln:60")
        user = await store_user(env)
        _, store, _ = env
        assert (await store.list_tasks(user.id))[0].deadline_time == time(15, 11)

    async def test_empty_answer_takes_the_offered_clock(self, env):
        await self._to_dtime(env)
        await send(env, "")                        # Enter = the ⏎ 18:00
        await send(env, cb="ln:60")
        user = await store_user(env)
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        assert task.deadline_time == time(18, 0)

    async def test_garbage_at_the_clock_step_is_explained(self, env):
        await self._to_dtime(env)
        f = await send(env, "как получится")
        assert "Не понял" in f.texts()[0] and "по сроку" in f.texts()[0]


async def store_user(env):
    return await (env[1]).get_user_by_account("telegram", PID)


class TestWarningLead:
    """The last question of every kind: how long before the deadline."""

    async def _to_lead(self, env):
        await _recurring_start(env, "every_n_days")
        await send(env, "3")
        await send(env, cb="dt:0")
        return await send(env, cb="tm:09:00")

    async def test_buttons_span_minutes_to_a_week(self, env):
        f = await self._to_lead(env)
        flat = _flat(f)
        assert "ln:15" in flat and "ln:1440" in flat and "ln:10080" in flat
        await send(env, cb="ln:1440")
        user = await store_user(env)
        _, store, _ = env
        assert (await store.list_tasks(user.id))[0].notify_before_min == 1440

    async def test_typed_leads_are_understood(self, env):
        user = await _recurring_start(env, "every_n_days")
        await send(env, "2")
        await send(env, cb="dt:0")
        await send(env, cb="tm:12:00")
        await send(env, "2 часа")
        _, store, _ = env
        assert (await store.list_tasks(user.id))[0].notify_before_min == 120
        await _recurring_start(env, "every_n_days", title="Второй")
        _, store, _ = env
        await send(env, "2")
        await send(env, cb="dt:0")
        await send(env, cb="tm:12:00")
        await send(env, "за 3 дня")
        tasks = await store.list_tasks(user.id)
        assert tasks[-1].notify_before_min == 3 * 1440

    async def test_nonsense_lead_repeats_the_question(self, env):
        await self._to_lead(env)
        f = await send(env, "побольше")
        assert "Не понял" in f.texts()[0] and "За сколько до срока" in f.texts()[0]
        _, store, _ = env
        assert await store.list_tasks(
            (await store_user(env)).id) == []


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
        f = await send(env, cb="dt:0")             # first deadline today
        assert "по сроку" in f.texts()[0]
        await send(env, cb="tm:08:30")
        await send(env, cb="ln:30")
        assert "добавлена" in fake.texts()[0]
        tasks = await store.list_tasks(user.id)
        assert len(tasks) == 1
        assert tasks[0].schedule_type == ScheduleType.EVERY_N_DAYS
        assert tasks[0].schedule_data["n"] == 3
        assert tasks[0].deadline_time == time(8, 30)
        assert tasks[0].notify_before_min == 30

    async def test_monthly_day_flow(self, env):
        user = await _recurring_start(env, "monthly_day", title="Коммуналка")
        await send(env, "15")
        await send(env, cb="tm:12:00")
        await send(env, cb="ln:1440")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        assert task.schedule_type == ScheduleType.MONTHLY_DAY
        assert task.schedule_data == {"day": 15}
        assert task.deadline_time == time(12, 0)
        assert task.notify_before_min == 1440

    async def test_weekdays_flow(self, env):
        user = await _recurring_start(env, "weekdays", title="Уборка")
        await send(env, cb="wd:0")                 # toggle Mon on
        await send(env, cb="wd:0")                 # toggle off
        await send(env, cb="wd:2")                 # Wed
        f = await send(env, cb="wdsubmit")
        assert "по сроку" in f.texts()[0]          # the deadline clock follows
        await send(env, "")                        # Enter = the ⏎ 18:00
        await send(env, cb="ln:120")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        assert task.schedule_data == {"weekdays": [2]}
        assert task.deadline_time == time(18, 0)   # the ⏎ default

    async def test_typed_new_place_at_the_place_step_is_created(self, env):
        # the place step used to answer only buttons: with no places saved the
        # bot said "add one by text", then swallowed whatever was typed
        await send(env, "/задача")
        await send(env, "Корм для кота")
        f = await send(env, "Дом")
        assert "sched:once" in _flat(f), "the wizard must move on to the schedule"
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        assert await place_id_of(store, user.id, "Дом")
        await send(env, cb="sched:every_n_days")
        await send(env, "2")
        await send(env, cb="dt:0")
        await send(env, cb="tm:09:00")
        await send(env, cb="ln:60")
        task = (await store.list_tasks(user.id))[0]
        assert task.place_id == await place_id_of(store, user.id, "Дом")

    async def test_typed_existing_place_is_reused_not_duplicated(self, env):
        await _home(env)
        await send(env, "/задача")
        await send(env, "Полка")
        f = await send(env, "дом")                 # same place, different case
        assert "sched:once" in _flat(f)
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]

    async def test_wizard_asks_again_when_its_place_vanished(self, env):
        # a place deleted while the wizard is open must not become a dangling
        # reference: the step is simply asked again
        _, store, fake = env
        user, home = await _home(env)
        await send(env, "/задача")
        await send(env, "Полка")
        await send(env, cb=f"place:{home}")
        await send(env, cb="sched:every_n_days")
        await send(env, "2")
        await send(env, cb="dt:0")
        await send(env, cb="tm:09:00")
        assert await store.delete_place(user.id, home)
        f = await send(env, cb="ln:60")            # the finish notices the hole
        assert "местности" in f.texts()[0]
        assert await store.list_tasks(user.id) == []
        await send(env, "Дача")                    # a brand new name heals it
        assert "добавлена" in fake.texts()[0]
        task = (await store.list_tasks(user.id))[0]
        assert task.place_id == await place_id_of(store, user.id, "Дача")


class TestMenuAndDeadline:
    async def test_start_and_help_have_menu_buttons(self, env):
        _, _, fake = env
        await send(env, "/start")
        flat = _flat(fake)
        assert {"cmd:where", "cmd:tasks", "cmd:task", "cmd:places",
                "cmd:settings", "cmd:stats", "cmd:edit"} == set(flat)
        await send(env, "/help")
        assert any(cb.startswith("cmd:")
                   for _, cb in fake.last_buttons()[0])

    async def test_menu_button_runs_command(self, env):
        await _home(env)
        f = await send(env, cb="cmd:places")
        assert "Дом" in f.texts()[0]


class TestWizardKeyboards:
    async def test_once_deadline_via_buttons(self, env):
        user, pid = await _home(env)
        await send(env, "/задача")
        await send(env, "Тест")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        f = await send(env, cb="dt:1")             # «📅 Завтра»
        assert "tm:18:00" in _flat(f)
        await send(env, cb="tm:18:00")
        f = await send(env, cb="ln:120")
        assert f.find("добавлена")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        assert task.deadline == datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, 18, 0, tzinfo=UTC)
        assert task.notify_before_min == 120

    async def test_whentime_accepts_typed_hhmm(self, env):
        user, pid = await _home(env)
        await send(env, "/задача")
        await send(env, "Тест")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        await send(env, cb="dt:1")
        f = await send(env, "7:45")
        assert "понял так" in f.texts()[0]         # the day came from the button
        await send(env, cb="cfm:yes")
        await send(env, cb="ln:60")
        _, store, _ = env
        task = (await store.list_tasks(user.id))[0]
        assert (task.deadline.hour, task.deadline.minute) == (7, 45)

    async def test_urls_defanged_in_titles_and_places(self, env):
        await send(env, "мой сайт https://evil.example.com/spam")  # first place
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        places = await store.list_places(user.id)
        assert "evil[.]example[.]com" in places[0].name
        assert "evil.example.com" not in places[0].name
        pid = await place_id_of(store, user.id, places[0].name)
        await send(env, "/задача")
        await send(env, "прочитать www.banned.org/x и позвонить")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:weekdays")
        await send(env, cb="wd:0")
        await send(env, cb="wdsubmit")
        await send(env, cb="tm:09:00")
        await send(env, cb="ln:60")
        tasks = await store.list_tasks(user.id)
        assert tasks and "www[.]banned[.]org" in tasks[0].title
        assert "www." not in tasks[0].title


class TestWizardBackAndFlexDates:
    async def _start(self, env):
        user, pid = await _home(env)
        await send(env, "/задача")
        await send(env, "первое название")
        return user, pid

    async def test_back_redoes_previous_question(self, env):
        user, pid = await self._start(env)
        f = await send(env, cb="wz:back")          # from place back to title
        assert "Название задачи" in f.texts()[-1]
        f = await send(env, "второе название")
        assert f"place:{pid}" in _flat(f) and "wz:back" in _flat(f)
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:every_n_days")
        await send(env, "3")
        f = await send(env, "завтра")              # anchor as a word → read back
        assert "понял так" in f.texts()[0]
        await send(env, cb="cfm:yes")
        f = await send(env, cb="tm:09:00")
        assert "За сколько до срока" in f.texts()[0]
        f = await send(env, cb="wz:back")          # ⬅️ from the lead back to it
        assert "по сроку" in f.texts()[0]
        await send(env, cb="tm:09:00")
        await send(env, cb="ln:60")
        _, store, _ = env
        tasks = await store.list_tasks(user.id)
        assert tasks[0].title == "второе название"
        assert tasks[0].schedule_data["n"] == 3
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        assert tasks[0].schedule_data["anchor"] == tomorrow

    async def test_anchor_typed_as_iso_needs_no_confirm(self, env):
        user = await _recurring_start(env, "every_n_days")
        await send(env, "4")
        today = datetime.now(UTC).date()
        f = await send(env, today.isoformat())     # an exact day: no card
        assert "по сроку" in f.texts()[0]
        await send(env, cb="tm:09:00")
        await send(env, cb="ln:60")
        _, store, _ = env
        assert (await store.list_tasks(user.id))[0].schedule_data["anchor"] == (
            today.isoformat())


class TestWizardDaysToggle:
    async def test_days_marked_and_message_edited(self, env):
        app, store, fake = env
        _, pid = await _home(env)
        await send(env, "/задача")
        await send(env, "тренировка")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:weekdays")
        assert len(fake.sent) == 1                 # the days keyboard only
        # taps arrive without clearing the fake: edits must not add messages
        for cb in ("wd:4", "wd:5"):
            await app.handle(RawEvent("telegram", PID, callback=cb))
        assert len(fake.sent) == 1                 # edited in place, not re-sent
        labels = [lbl for row in fake.last_buttons() for lbl, _ in row]
        assert sum(1 for lbl in labels if lbl.startswith("✅")) == 2
        await app.handle(RawEvent("telegram", PID, callback="wd:4"))  # deselect
        labels = [lbl for row in fake.last_buttons() for lbl, _ in row]
        assert sum(1 for lbl in labels if lbl.startswith("✅")) == 1


class TestWizardCancelButton:
    async def test_title_step_has_cancel_button(self, env):
        app, _, fake = env
        f = await send(env, "/задача")
        assert "wz:cancel" in _flat(f)
        await send(env, cb="wz:cancel")
        assert fake.find("Отменено")
        assert not app.wizards


class TestSavedCard:
    """Saving answers with what was created: the rule, its clock, the lead."""

    async def test_recurring_card_shows_clock_lead_and_first_date(self, env):
        user, pid = await _home(env)
        await send(env, "/задача")
        await send(env, "полить")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:every_n_days")
        await send(env, "3")
        await send(env, cb="dt:0")
        await send(env, cb="tm:09:00")
        f = await send(env, cb="ln:120")
        text = f.texts()[0]
        assert "каждые 3 дн." in text
        assert "срок — 09:00" in text
        assert "предупреждать за 2 ч" in text
        assert re.search(r"ближайшее срабатывание: \d{2}\.\d{2} 09:00", text)
        _, store, _ = env
        assert (await store.list_tasks(user.id))[0].deadline_time == time(9, 0)

    async def test_once_card_shows_the_moment_and_the_lead(self, env):
        user, pid = await _home(env)
        await send(env, "/задача")
        await send(env, "собрать полку")
        await send(env, cb=f"place:{pid}")
        await send(env, cb="sched:once")
        await send(env, cb="dt:1")
        await send(env, cb="tm:10:00")
        f = await send(env, cb="ln:30")
        text = f.texts()[0]
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        assert "добавлена" in text
        assert f"до {tomorrow:%d.%m} 10:00" in text
        assert "предупреждать за 30 мин" in text
        assert f"ближайшее срабатывание: {tomorrow:%d.%m} 10:00" in text
        _, store, _ = env
        assert (await store.list_tasks(user.id))[0].deadline_time == time(10, 0)
