#!/usr/bin/env python3
"""Offline smoke of the full chat flow: no network, real code path.

Drives the same BotApp the entrypoints wire up, through a FakeMessenger
and a throwaway SQLite file: onboarding → location → task wizard (deadline +
warning lead) → notice screen with per-task buttons → stats (text + CSV) →
the silence rule. Exits non-zero if any step goes wrong. Safe to run anytime;
it touches no live config or tokens.
"""

import asyncio
import os
import sys
import tempfile
from datetime import UTC, datetime

from taskshed.bot.app import BotApp
from taskshed.config import BotCfg
from taskshed.core.models import ScheduleType
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

    # the wizard: a one-off with a deadline and a warning lead
    await ev("/задача")
    await ev("Полить цветы")
    await ev("Дом")
    await ev(cb="sched:once")
    await ev(cb="dt:0")      # deadline today
    await ev(cb="tm:23:59")
    await ev(cb="ln:60")     # warn an hour before
    msgs = await ev("/задачи")
    assert any("Полить цветы" in m.text for m in msgs), "the notice lists the task"
    assert any(any(cb == "fin:" for _, cb in row) for m in msgs for row in m.buttons), \
        "the notice closes with «Готово»"
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
    # the only repeat of today is done: nothing to announce, so the bot is silent
    rows = await store.rows_to_announce(1)
    assert not rows, "silence rule: no open repeats for today"
    assert not await app.send_notification(1, settings, loc[0], rows), "silence rule"

    # a daily repeat whose warning lead is a day long sits in the notice window:
    # the same screen comes back on its own, with buttons and «Готово»
    today = datetime.now(UTC).date()
    task = await store.create_task(
        1, loc[0].id, "Зарядка", ScheduleType.EVERY_N_DAYS,
        {"n": 1, "anchor": today.isoformat()},
        deadline_time=datetime.now(UTC).time().replace(second=0, microsecond=0),
        notify_before_min=1440)
    await store.materialize_occurrences(task, today, 1)
    assert await store.rows_in_window(1, datetime.now(UTC)), "the window is open"
    assert await app.send_notification(1, settings, loc[0],
                                       await store.rows_to_announce(1)), "notice sent"
    assert any(any(cb == "fin:" for _, cb in row) for m in fake.sent
               for row in m.buttons), "the notice closes with «Готово»"
    print("✅ smoke ok: onboarding, wizard, notice, done, stats csv, silence rule")
    await store._engine.dispose()
    os.unlink(db)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
