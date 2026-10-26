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


def inline_rows(buttons: ButtonRows) -> list[list]:
    """Convert port ButtonRows to Telethon inline keyboard buttons.

    Telethon's inline buttons are ``Button.inline(text, data=bytes)`` —
    ``Button(label, callback=…)`` does not exist and this drift broke the
    live bot once, hence the unit test in tests/test_telegram_adapter.py.
    """
    return [[Button.inline(label, data=cb.encode("utf-8")) for label, cb in row]
            for row in buttons]


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
        log.info("incoming message chat=%s private=%s text=%r",
                 ev.chat_id, ev.is_private, (ev.raw_text or "")[:80])
        if not ev.is_private:
            return
        await self.app.handle(RawEvent(
            platform=self.platform,
            platform_user_id=str(ev.chat_id),
            text=ev.raw_text,
        ))

    async def _on_callback(self, ev) -> None:
        log.info("incoming callback chat=%s data=%r", ev.chat_id, ev.data)
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

    async def send_text(self, account: Account, text: str) -> str | None:
        msg = await self.client.send_message(int(account.platform_user_id), text,
                                             link_preview=False)
        return str(msg.id)

    async def send_buttons(self, account: Account, text: str,
                           buttons: ButtonRows) -> str | None:
        msg = await self.client.send_message(
            int(account.platform_user_id), text,
            buttons=inline_rows(buttons), parse_mode=None, link_preview=False,
        )
        return str(msg.id)

    async def edit_buttons(self, account: Account, message_id: str, text: str,
                           buttons: ButtonRows) -> bool:
        try:
            # Telethon signature: edit_message(entity, message=<id>, text=…)
            await self.client.edit_message(int(account.platform_user_id),
                                           int(message_id), text=text,
                                           buttons=inline_rows(buttons),
                                           parse_mode=None, link_preview=False)
            return True
        except Exception:  # deleted/stale message: caller re-sends instead
            log.info("edit_message failed (chat=%s id=%s)",
                     account.platform_user_id, message_id)
            return False

    async def send_image(self, account: Account, path: str, caption: str = "") -> None:
        await self.client.send_file(int(account.platform_user_id), path, caption=caption)

    async def send_document(self, account: Account, path: str, name: str,
                            caption: str = "") -> None:
        await self.client.send_file(int(account.platform_user_id), path,
                                    caption=caption, force_document=True)

    # ---------- lifecycle ----------

    async def start(self) -> None:
        # Telethon >= 1.45 dropped client.start_bot(); bot login is start(bot_token=...)
        await self.client.start(bot_token=self.cfg.bot_token)
        log.info("telegram bot started (proxy=%s)", self.cfg.proxy.type or "none")

    async def run_forever(self) -> None:
        await self.start()
        await self.client.run_until_disconnected()
