#!/usr/bin/env python3
"""Offline smoke of the full chat flow: no network, real code path.

Drives the same BotApp the entrypoints wire up, through a FakeMessenger
and a throwaway SQLite file: onboarding → location → task wizard →
digest rule → ✅ done → stats (text + CSV). Exits non-zero if any step
goes wrong. Safe to run anytime; it touches no live config or tokens.
"""

import asyncio
import os
import sys
import tempfile

from taskshed.bot.app import BotApp
from taskshed.config import BotCfg
from taskshed.messaging.base import FakeMessenger, RawEvent
from taskshed.storage.db import make_engine
from taskshed.storage.repositories import Store


async def main() -> int:
    db = tempfile.mktemp(suffix=".db")
    store = Store(make_engine(f"sqlite+aiosqlite:///{db}"))
    await store.create_all()
    fake = FakeMessenger()
    app = BotApp(store, BotCfg(), {"telegram": fake})

    async def ev(text: str | None = None, cb: str | None = None) -> list:
        fake.clear()
        await app.handle(RawEvent("telegram", "u1", text=text, callback=cb))
        first = fake.sent[0] if fake.sent else None
        shown = first.text.split("\n")[0] if first else "(no answer)"
        btns = "; ".join(",".join(lbl for lbl, _ in r)
                         for r in first.buttons) if first and first.buttons else ""
        print(f"👤 {text or f'[{cb}]':22} 🤖 {shown} {'| ' + btns if btns else ''}")
        return fake.sent

    msgs = await ev("/старт")
    assert any("Привет" in m.text for m in msgs), "greeting"
    await ev("/гдея")
    msgs = await ev("Дом")   # typed name at the question: creates + selects
    assert any("Текущее место: Дом" in m.text for m in msgs), "location set"
    await ev("/задача")
    await ev("Полить цветы")
    await ev("Дом")
    await ev(cb="sched:once")
    await ev(cb="now:")      # ▶ Сейчас: starts right now
    await ev(cb="dtl:eod")   # ready by end of day
    await ev(cb="tm:09:00")
    msgs = await ev("/задачи")
    done = next(cb for m in msgs for row in m.buttons or []
                for _, cb in row if cb.startswith("done:"))
    msgs = await ev(cb=done)
    assert any("Готово" in m.text for m in msgs), "completion"
    await ev("/статистика")
    await ev(cb="per:week")
    msgs = await ev(cb="fmt:csv:week")
    assert any(getattr(m, "document", None) for m in msgs), "csv file sent"

    settings = await store.get_settings(1)
    loc = await store.get_location(1)
    # task is done: the digest rule must keep the bot silent
    assert await app.build_digest(1, settings, loc[0]) is None, "silence rule"
    print("✅ smoke ok: onboarding, wizard, list, done, stats csv, silence rule")
    await store._engine.dispose()
    os.unlink(db)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
