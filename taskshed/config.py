"""Configuration: config.yaml + TASKSHED_* environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(slots=True)
class ProxyCfg:
    type: str = ""          # mtproxy|securedmtproxy (tg) / http|socks5 (vk)
    ip: str = ""
    port: int = 0
    secret: str = ""
    url: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.type)


@dataclass(slots=True)
class TelegramCfg:
    enabled: bool = False
    api_id: int = 0
    api_hash: str = ""
    bot_token: str = ""
    proxy: ProxyCfg = field(default_factory=ProxyCfg)


DEFAULT_VK_API = "https://api.vk.ru/method/"


@dataclass(slots=True)
class VkCfg:
    enabled: bool = False
    group_token: str = ""
    # VK moved its domain zone: api.vk.com now also answers as api.vk.ru,
    # and vk.ru is the address VK points at. Kept configurable so an old
    # deployment or a mirror keeps working without a code change.
    api_url: str = DEFAULT_VK_API
    proxy: ProxyCfg = field(default_factory=ProxyCfg)


@dataclass(slots=True)
class BotCfg:
    default_language: str = "ru"
    occurrence_horizon_days: int = 8
    database_url: str = "sqlite+aiosqlite:///taskshed.db"
    telegram: TelegramCfg = field(default_factory=TelegramCfg)
    vk: VkCfg = field(default_factory=VkCfg)


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def load_config(path: str | Path = "config.yaml") -> BotCfg:
    cfg = BotCfg()
    data: dict = {}
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        data = raw.get("bot", {})
        db = raw.get("database", {})
        if db.get("url"):
            cfg.database_url = db["url"]
        tg = raw.get("platforms", {}).get("telegram", {})
        cfg.telegram = TelegramCfg(
            enabled=tg.get("enabled", False),
            api_id=int(tg.get("api_id") or 0),
            api_hash=str(tg.get("api_hash") or ""),
            bot_token=str(tg.get("bot_token") or ""),
            proxy=ProxyCfg(**{k: v for k, v in (tg.get("proxy") or {}).items()
                              if k in ("type", "ip", "port", "secret")}),
        )
        vk = raw.get("platforms", {}).get("vk", {})
        cfg.vk = VkCfg(
            enabled=vk.get("enabled", False),
            group_token=str(vk.get("group_token") or ""),
            api_url=str(vk.get("api_url") or DEFAULT_VK_API),
            proxy=ProxyCfg(**{k: v for k, v in (vk.get("proxy") or {}).items()
                              if k in ("type", "url")}),
        )
    if data.get("default_language"):
        cfg.default_language = data["default_language"]
    if data.get("occurrence_horizon_days"):
        cfg.occurrence_horizon_days = int(data["occurrence_horizon_days"])

    # env overrides
    cfg.database_url = _env("TASKSHED_DATABASE_URL") or cfg.database_url
    cfg.default_language = _env("TASKSHED_DEFAULT_LANGUAGE") or cfg.default_language
    if v := _env("TASKSHED_TELEGRAM_API_ID"):
        cfg.telegram.enabled, cfg.telegram.api_id = True, int(v)
    if v := _env("TASKSHED_TELEGRAM_API_HASH"):
        cfg.telegram.api_hash = v
    if v := _env("TASKSHED_TELEGRAM_BOT_TOKEN"):
        cfg.telegram.enabled, cfg.telegram.bot_token = True, v
    if all(v := [os.environ.get(n) for n in
                 ("TASKSHED_TELEGRAM_PROXY_IP", "TASKSHED_TELEGRAM_PROXY_PORT",
                  "TASKSHED_TELEGRAM_PROXY_SECRET")]):
        cfg.telegram.proxy = ProxyCfg(type="mtproxy", ip=v[0], port=int(v[1]), secret=v[2])
    if v := _env("TASKSHED_VK_GROUP_TOKEN"):
        cfg.vk.enabled, cfg.vk.group_token = True, v
    if v := _env("TASKSHED_VK_API_URL"):
        cfg.vk.api_url = v
    if v := _env("TASKSHED_VK_PROXY_URL"):
        kind = "socks5" if v.startswith("socks") else "http"
        cfg.vk.proxy = ProxyCfg(type=kind, url=v)
    return cfg
