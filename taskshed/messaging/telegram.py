"""Telegram adapter — Telethon in bot mode with optional MTProto proxy."""

from __future__ import annotations

import logging

from telethon import Button, TelegramClient, events
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

from taskshed.bot.app import BotApp
from taskshed.config import TelegramCfg
from taskshed.core.models import Account
from taskshed.messaging.base import ButtonRows, RawEvent

log = logging.getLogger(__name__)

MT_PROXY_TYPES = ("mtproxy", "securedmtproxy")


class TelegramAdapter:
    platform = "telegram"

    def __init__(self, cfg: TelegramCfg, app: BotApp, session_file: str = "taskshed_tg"):
        self.cfg = cfg
        self.app = app
        kwargs: dict = {}
        if cfg.proxy.enabled and cfg.proxy.type in MT_PROXY_TYPES:
            kwargs["connection"] = ConnectionTcpMTProxyRandomizedIntermediate
            kwargs["proxy"] = (cfg.proxy.ip, int(cfg.proxy.port), cfg.proxy.secret)
        self.client = TelegramClient(session_file, cfg.api_id, cfg.api_hash, **kwargs)
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._on_callback, events.CallbackQuery)

    # ---------- inbound ----------

    async def _on_message(self, ev) -> None:
        if not ev.is_private:
            return
        await self.app.handle(RawEvent(
            platform=self.platform,
            platform_user_id=str(ev.chat_id),
            text=ev.raw_text,
        ))

    async def _on_callback(self, ev) -> None:
        if not ev.is_private:
            return
        try:
            await ev.answer()
        except Exception:  # bot API complains about stale buttons; harmless
            pass
        data = ev.data.decode("utf-8") if ev.data else None
        if data:
            await self.app.handle(RawEvent(
                platform=self.platform,
                platform_user_id=str(ev.chat_id),
                callback=data,
            ))

    # ---------- IMessenger ----------

    async def send_text(self, account: Account, text: str) -> None:
        await self.client.send_message(int(account.platform_user_id), text)

    async def send_buttons(self, account: Account, text: str, buttons: ButtonRows) -> None:
        rows = [[Button(label, callback=cb.encode("utf-8")) for label, cb in row]
                for row in buttons]
        await self.client.send_message(
            int(account.platform_user_id), text,
            buttons=rows, parse_mode=None, link_preview=False,
        )

    async def send_image(self, account: Account, path: str, caption: str = "") -> None:
        await self.client.send_file(int(account.platform_user_id), path, caption=caption)

    async def send_document(self, account: Account, path: str, name: str,
                            caption: str = "") -> None:
        await self.client.send_file(int(account.platform_user_id), path,
                                    caption=caption, force_document=True)

    # ---------- lifecycle ----------

    async def start(self) -> None:
        await self.client.start_bot(self.cfg.bot_token)
        log.info("telegram bot started (proxy=%s)", self.cfg.proxy.type or "none")

    async def run_forever(self) -> None:
        await self.start()
        await self.client.run_until_disconnected()
