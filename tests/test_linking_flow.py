"""Account linking across platforms and reminder fan-out."""

import re

from chatenv import PID, send

from taskshed.messaging.base import RawEvent


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


class TestUnlinkAlone:
    async def test_single_account_unlink_explains(self, env):
        # /отвязать when there is nothing to unlink must still answer
        await send(env, "Дом")
        f = await send(env, "/отвязать")
        assert "отвязывать нечего" in f.texts()[0]
