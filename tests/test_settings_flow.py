"""The settings screen and every value it awaits by text."""



from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from chatenv import PID, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import RawEvent


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
        await self._cb(app, "rem:morning")
        assert "Во сколько утром" in fake.texts()[-1]     # prompt appears
        await self._text(app, "08:30")
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1                 # the screen was edited, not copied
        assert "Утро: 08:30" in statuses[0].text
        st = await store.get_settings((await store.get_user_by_account(
            "telegram", PID)).id)
        assert st.morning_time == time(8, 30)

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

    async def test_places_without_any_still_offers_close(self, env):
        app, store, fake = env
        await send(env, "/start")  # no places yet
        f = await send(env, "/места")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["pl:close"]  # type a name to add, ✔️ to leave


class TestMorningAndRepeatFlow:
    """The two rhythm settings: the morning moment and the repeat interval."""

    async def _open(self, env):
        await send(env, "Дом")
        app, store, fake = env
        fake.clear()
        await app.handle(RawEvent("telegram", PID, text="/напоминания"))
        return app, store, fake

    async def _cb(self, app, cb):
        await app.handle(RawEvent("telegram", PID, callback=cb))

    async def test_the_screen_offers_both_moments_directly(self, env):
        app, store, fake = await self._open(env)
        flat = [cb for row in fake.last_buttons() for _, cb in row]
        assert "rem:morning" in flat and "rem:nag" in flat
        assert "rem:when" not in flat          # no mode picker any more
        await self._cb(app, "rem:morning")
        assert flat[:0] == []
        assert "Во сколько утром" in fake.texts()[-1]
        await self._cb(app, "rem:back")
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1

    async def test_repeat_interval_is_set_by_text(self, env):
        app, store, fake = await self._open(env)
        await self._cb(app, "rem:nag")
        assert "Каждые сколько минут" in fake.texts()[-1]
        await app.handle(RawEvent("telegram", PID, text="90"))
        statuses = [m for m in fake.sent if "Напоминания:" in m.text]
        assert len(statuses) == 1
        assert "Повтор: каждые 90 мин" in statuses[0].text
        assert (await store.get_settings(1)).nag_interval_min == 90

    async def test_bad_value_re_asks_the_same_prompt_in_place(self, env):
        app, store, fake = await self._open(env)
        await self._cb(app, "rem:nag")
        fake.clear()
        await app.handle(RawEvent("telegram", PID, text="не-число"))
        assert "Каждые сколько минут" in fake.texts()[-1]  # prompt is back
        assert app._pending_inputs.get(1) == "reminput:nag"  # still armed
        await app.handle(RawEvent("telegram", PID, text="90"))  # fixed in place
        assert (await store.get_settings(1)).nag_interval_min == 90

    async def test_morning_moment_accepts_a_loose_clock(self, env):
        app, store, fake = await self._open(env)
        await self._cb(app, "rem:morning")
        await app.handle(RawEvent("telegram", PID, text="8.30"))
        assert (await store.get_settings(1)).morning_time == time(8, 30)


class TestTimezoneReplansTasks:
    """Task deadlines are the user's wall-clock: changing the zone moves the
    real moment but keeps the clock they read."""

    async def test_changing_the_zone_moves_open_repeats(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = (await store.list_places(user.id))[0]
        task = await store.create_task(
            user.id, home.id, "Отчёт", ScheduleType.WEEKDAYS, {"weekdays": [3]},
            deadline_time=time(18, 0), notify_before_min=120)
        today = datetime.now(UTC).date()
        await store.materialize_occurrences(task, today, 14)

        async def first_due():
            return (await store.occurrences_in_range(
                user.id, datetime.now(UTC),
                datetime.now(UTC) + timedelta(days=14)))[0][0]

        msk = ZoneInfo("Europe/Moscow")
        london = ZoneInfo("Europe/London")
        # this user lives in UTC until they say otherwise: 18:00 means 18:00 UTC
        before = await first_due()
        assert before.due_at.astimezone(UTC).strftime("%H:%M") == "18:00"
        await app.handle(RawEvent("telegram", PID, text="/настройки"))
        await app.handle(RawEvent("telegram", PID, callback="rem:tz"))
        await app.handle(RawEvent("telegram", PID, text="Europe/Moscow"))
        moved = await first_due()
        assert moved.due_at.astimezone(msk).strftime("%H:%M") == "18:00"
        assert moved.due_at == before.due_at + timedelta(hours=-3)
        assert moved.notify_at.astimezone(msk).strftime("%H:%M") == "16:00"
        await app.handle(RawEvent("telegram", PID, callback="rem:tz"))
        await app.handle(RawEvent("telegram", PID, text="Europe/London"))
        after = await first_due()
        assert after.due_at.astimezone(london).strftime("%H:%M") == "18:00"
        assert after.due_at != moved.due_at          # another real moment
        assert after.notify_at.astimezone(london).strftime("%H:%M") == "16:00"


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
        assert {"rem:toggle", "rem:morning", "rem:nag", "rem:quiet", "rem:ttl",
                "rem:tz", "rem:horizon", "rem:lang", "rem:link", "rem:where",
                "menu:back"} <= set(flat)
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


class TestNotifyWhere:
    """📡 «Куда слать»: all / last-talked / pinned to one platform."""

    async def _linked(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        vk_user, vk_acc = await store.register_account("vk", "999")
        await store.attach_account(vk_acc, user.id)
        await store.merge_user(vk_user.id, user.id)
        return user

    async def test_screen_offers_modes_with_current_marked(self, env):
        app, store, fake = env
        user = await self._linked(env)
        f = await send(env, cb="rem:where")
        labels = {cb: lbl for row in f.last_buttons() for lbl, cb in row}
        assert {"rem:where:all", "rem:where:primary",
                "rem:where:telegram", "rem:where:vk"} <= set(labels)
        assert labels["rem:where:primary"].startswith("✓")   # the default
        f = await send(env, cb="rem:where:vk")
        assert (await store.get_settings(user.id)).notify_platforms == "vk"
        labels = {cb: lbl for row in f.last_buttons() for lbl, cb in row}
        assert labels["rem:where:vk"].startswith("✓")

    async def test_status_line_shows_the_choice(self, env):
        app, store, fake = env
        await self._linked(env)
        f = await send(env, "/настройки")
        assert "Рассылка: 🕒 Где вы последний раз общались" in f.texts()[0]
        await send(env, cb="rem:where:all")
        f = await send(env, "/настройки")
        assert "Рассылка: 🌍 Все платформы" in f.texts()[0]
