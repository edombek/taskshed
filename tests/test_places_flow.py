"""The places screen: one live message, in-place delete ask/confirm."""

from chatenv import PID

from taskshed.messaging.base import RawEvent


async def _tap(env, text=None, cb=None, msg=None):
    """Drive an event without the send() helper: it wipes edited messages."""
    app, _, fake = env
    before = len(fake.sent)
    await app.handle(RawEvent("telegram", PID, text=text, callback=cb, message_id=msg))
    return fake.sent[before:]


class TestPlacesScreen:
    async def test_add_by_text_redraws_the_same_message(self, env):
        _, store, fake = env
        await _tap(env, text="/места")
        scr = fake.sent[-1]
        assert [cb for row in scr.buttons for _, cb in row] == ["pl:close"]
        assert not await _tap(env, text="Дом")       # added on the screen
        assert "Дом" in scr.text and "добавлено" in scr.text
        flat = [cb for row in scr.buttons for _, cb in row]
        assert "pl:close" in flat and any(cb.startswith("pdel:") for cb in flat)
        user = await store.get_user_by_account("telegram", PID)
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]

    async def test_delete_asks_and_confirms_on_the_same_message(self, env):
        app, store, fake = env
        await _tap(env, text="/места")
        await _tap(env, text="Дом")
        scr = fake.sent[-1]
        user = await store.get_user_by_account("telegram", PID)
        pid = (await store.list_places(user.id))[0].id
        before = len(fake.sent)
        assert not await _tap(env, cb=f"pdel:{pid}", msg=scr.id)  # the ask
        flat = [cb for row in scr.buttons for _, cb in row]
        assert flat == [f"pdelok:{pid}", f"pdno:{pid}"]
        assert "Удалить место" in scr.text
        assert not await _tap(env, cb=f"pdno:{pid}", msg=scr.id)  # back to list
        flat = [cb for row in scr.buttons for _, cb in row]
        assert f"pdel:{pid}" in flat
        assert not await _tap(env, cb=f"pdel:{pid}", msg=scr.id)
        assert not await _tap(env, cb=f"pdelok:{pid}", msg=scr.id)
        assert len(fake.sent) == before  # the whole flow: zero new messages
        assert "удалено" in scr.text
        assert [cb for row in scr.buttons for _, cb in row] == ["pl:close"]
        assert not await store.list_places(user.id)

    async def test_close_disarms_the_screen(self, env):
        app, store, fake = env
        await _tap(env, text="/места")
        await _tap(env, text="Дом")
        scr = fake.sent[-1]
        assert not await _tap(env, cb="pl:close", msg=scr.id)
        assert scr.buttons == []
        assert not app._pl_msgs and ctx_gone(app)
        before = len(fake.sent)
        await _tap(env, text="Работа")  # the add-by-text mode exited too
        assert not any("Работа" in m.text for m in fake.sent[before:])
        user = await store.get_user_by_account("telegram", PID)
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]

    async def test_close_on_a_stale_screen_still_returns_the_menu(self, env):
        # an old copy of the screen (restart, lost bookkeeping): its ✔️ Готово
        # must still lead somewhere — never silence
        app, _, fake = env
        await _tap(env, text="/места")
        await _tap(env, text="Дом")
        app._pl_msgs.clear()
        msgs = await _tap(env, cb="pl:close")
        assert any(m.buttons and any(cb.startswith("cmd:")
                                     for row in m.buttons for _, cb in row)
                   for m in msgs)


def ctx_gone(app) -> bool:
    return app.wizards.get(1) is None or app.wizards[1].get("kind") != "addplace"
