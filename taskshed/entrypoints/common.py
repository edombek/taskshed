"""Shared wiring: config → store → adapters → BotApp (+ scheduler loop)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from taskshed.bot.app import BotApp
from taskshed.config import BotCfg, load_config
from taskshed.messaging.base import IMessenger
from taskshed.scheduler.jobs import run_forever as scheduler_forever
from taskshed.storage.db import make_engine
from taskshed.storage.repositories import Store

log = logging.getLogger(__name__)


@dataclass
class Wiring:
    cfg: BotCfg
    store: Store
    app: BotApp
    tg: IMessenger | None = None
    vk: IMessenger | None = None


def build(config_path: str = "config.yaml", only: str | None = None) -> Wiring:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(config_path)
    if only == "telegram":
        cfg.vk.enabled = False
    elif only == "vk":
        cfg.telegram.enabled = False
    store = Store(make_engine(cfg.database_url))
    messengers: dict[str, IMessenger] = {}
    w = Wiring(cfg=cfg, store=store, app=BotApp(store, cfg, messengers))
    if cfg.telegram.enabled:
        from taskshed.messaging.telegram import TelegramAdapter
        w.tg = TelegramAdapter(cfg.telegram, w.app)
        messengers["telegram"] = w.tg
    if cfg.vk.enabled:
        from taskshed.messaging.vk import VkAdapter
        w.vk = VkAdapter(cfg.vk, w.app)
        messengers["vk"] = w.vk
    if not messengers:
        raise SystemExit("enable at least one platform in config.yaml / env "
                         "(see config.example.yaml)")
    return w


async def run(w: Wiring) -> None:
    sched = logging.getLogger("taskshed.scheduler")
    sched.info("starting; platforms=%s", sorted(
        {m.platform for m in (w.tg, w.vk) if m}))
    tasks = []
    if w.tg:
        await w.tg.start()  # type: ignore[attr-defined]
        tasks.append(w.tg.client.run_until_disconnected())  # type: ignore[attr-defined]
    if w.vk:
        tasks.append(w.vk.run_forever())  # type: ignore[attr-defined]
    tasks.append(scheduler_forever(w.app, w.store))
    import asyncio
    await asyncio.gather(*tasks)
