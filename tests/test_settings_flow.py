"""The settings screen and every value it awaits by text."""



from chatenv import PID, send

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

    async def test_bad_value_re_asks_the_same_prompt_in_place(self, env):
        app, store, fake = await self._open(env)
        await self._cb(app, "rem:interval")
        fake.clear()
        await app.handle(RawEvent("telegram", PID, text="не-число"))
        assert "Каждые сколько минут" in fake.texts()[-1]  # prompt is back
        assert app._pending_inputs.get(1) == "reminput:interval"  # still armed
        await app.handle(RawEvent("telegram", PID, text="90"))    # fixed in place
        assert (await store.get_settings(1)).remind_interval_min == 90


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
