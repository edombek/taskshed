"""Command menu follow-ups, submenu back buttons, stale keyboard cleanup."""

from datetime import UTC, datetime, time

from chatenv import PID, place_id_of, prev_buttons, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import RawEvent


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
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1)
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date())
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
        await send(env, cb="tm:09:00")             # the deadline clock
        await send(env, cb="ln:60")                # and the warning lead
        await self._menu_on_last(env)


class TestMenuFollowUp:
    async def test_plain_reply_gets_menu_by_default(self, env):
        app, store, fake = env
        await send(env, "Дом")
        f = await send(env, "/отмена")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat

    async def test_notice_ends_with_its_own_done_button(self, env):
        """A task notice owns the chat: it closes with «Готово», not a menu."""
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1)
        f = await send(env, "/задачи")
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert any(cb.startswith("done:") for cb in flat)
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["fin:", "tas:all"]  # close the round, or see everything
        # «Готово» is what hands the menu back
        f = await send(env, cb="fin:")
        assert "cmd:task" in [cb for row in f.last_buttons() for _, cb in row]


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
        # the last level of the walk says «Готово», not «Назад»
        labels = [lbl for row in f.last_buttons() for lbl, cb in row
                  if cb == "menu:back"]
        assert labels == ["✅ Готово"]
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
