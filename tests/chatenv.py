"""Shared test bed for the chat-flow tests: in-memory store + fake messengers."""


import pytest

from taskshed.bot.app import BotApp
from taskshed.config import BotCfg
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


async def send(env, text=None, cb=None, msg=None, pid=PID, platform="telegram"):
    app, store, fake = env
    messenger = app.messengers[platform]
    fake.clear()
    app.messengers["vk"].clear()
    await app.handle(RawEvent(platform, pid, text=text, callback=cb, message_id=msg))
    return messenger


async def place_id_of(store, pid_user, name):
    for p in await store.list_places(pid_user):
        if p.name == name:
            return p.id
    raise AssertionError(f"place {name} not found")


def prev_buttons(f) -> list:
    """Buttons of the command's own reply (the menu follow-up is the last)."""
    return f.sent[-2].buttons or []
