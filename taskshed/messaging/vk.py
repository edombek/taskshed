"""VK community bot adapter — Bot Long Poll API over aiohttp.

Deliberately framework-free: VK Bot Long Poll is small enough to own, which
keeps the dependency surface identical to the Telegram side and the port
contract intact. Supports inline keyboards, photos and documents.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import aiohttp

from taskshed.bot.app import BotApp
from taskshed.config import VkCfg
from taskshed.core.models import Account
from taskshed.messaging.base import ButtonRows, RawEvent

log = logging.getLogger(__name__)

API = "https://api.vk.com/method/"
API_VERSION = "5.199"


def build_keyboard(buttons: ButtonRows) -> str:
    """Flatten our rows into a VK inline keyboard JSON.

    VK inline keyboards flow by `pos` (0..3); a button at pos 3 ends the row.
    Short rows therefore place their last button at pos 3.
    """
    flat, row_no = [], 0
    colors = ["positive", "secondary", "primary", "negative"]
    for row in buttons:
        n = len(row)
        for j, (label, cb) in enumerate(row):
            if n >= 4:
                pos = j % 4
            elif j == n - 1:
                pos = 3
            else:
                pos = j
            flat.append({
                "color": colors[row_no % len(colors)],
                "action": {"type": "callback", "label": label[:40], "payload": cb},
                "pos": pos,
            })
        row_no += 1
    return json.dumps({"type": "inline", "buttons": flat}, ensure_ascii=False)


def parse_longpoll_update(update: dict) -> RawEvent | None:
    if update.get("type") != "message_new":
        return None
    obj = update.get("object", {})
    msg = obj.get("message", obj)
    peer = msg.get("from_id") or msg.get("peer_id")
    if not peer:
        return None
    client = (obj.get("client_info") or msg.get("client_info") or {})
    action = client.get("button_action") or {}
    text = (msg.get("text") or "").strip()
    callback = action.get("payload") if action.get("type") == "callback" else None
    if not text and not callback:
        return None
    return RawEvent(platform="vk", platform_user_id=str(peer),
                    text=text or None, callback=callback or None)


class VkAdapter:
    platform = "vk"

    def __init__(self, cfg: VkCfg, app: BotApp):
        self.cfg = cfg
        self.app = app
        self._session: aiohttp.ClientSession | None = None
        self._token = cfg.group_token

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            proxy = self.cfg.proxy.url if self.cfg.proxy.enabled else None
            self._session = aiohttp.ClientSession(proxy=proxy)
        return self._session

    async def _api(self, method: str, **params) -> dict:
        session = await self._http()
        params.update(access_token=self._token, v=API_VERSION)
        async with session.post(API + method, data=params) as resp:
            data = await resp.json()
        if "error" in data:
            log.warning("vk api %s error: %s", method, data["error"])
            return {}
        return data.get("response", {})

    # ---------- long poll loop ----------

    async def run_forever(self) -> None:
        server, key, ts = await self._server()
        log.info("vk bot long poll started (group %s, proxy=%s)",
                 self.cfg.group_id, self.cfg.proxy.type or "none")
        while True:
            try:
                session = await self._http()
                async with session.get(server, params={
                        "act": "a_check", "key": key, "ts": ts, "wait": 25}) as resp:
                    payload = await resp.json()
                if "failed" in payload:
                    server, key, ts = await self._server()
                    continue
                ts = payload.get("ts", ts)
                for upd in payload.get("updates", []):
                    ev = parse_longpoll_update(upd)
                    if ev:
                        await self.app.handle(ev)
            except (TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(3)
                server, key, ts = await self._server()

    async def _server(self):
        d = await self._api("groups.getLongPollServer", group_id=self.cfg.group_id)
        return d["server"], d["key"], d["ts"]

    # ---------- IMessenger ----------

    async def send_text(self, account: Account, text: str) -> None:
        await self._api("messages.send", peer_id=account.platform_user_id,
                        message=text, random_id=random.randint(1, 2**31))

    async def send_buttons(self, account: Account, text: str, buttons: ButtonRows) -> None:
        await self._api("messages.send", peer_id=account.platform_user_id,
                        message=text, keyboard=build_keyboard(buttons),
                        random_id=random.randint(1, 2**31))

    async def send_image(self, account: Account, path: str, caption: str = "") -> None:
        try:
            up = await self._api("messages.getConversationPhotoUploadServer",
                                 peer_id=account.platform_user_id)
            session = await self._http()
            form = aiohttp.FormData()
            form.add_field("file", open(path, "rb"), content_type="image/png")
            async with session.post(up["upload_url"], data=form) as resp:
                res = await resp.json()
            att = f"photo{res.get('owner_id')}_{res.get('pid') or res.get('photo')}"
            await self._api("messages.send", peer_id=account.platform_user_id,
                            message=caption, attachment=att,
                            random_id=random.randint(1, 2**31))
        except Exception:
            log.exception("vk photo upload failed; falling back to text")
            if caption:
                await self.send_text(account, caption)

    async def send_document(self, account: Account, path: str, name: str,
                            caption: str = "") -> None:
        try:
            up = await self._api("docs.getMessagesUploadServer",
                                 peer_id=account.platform_user_id, type="doc")
            session = await self._http()
            form = aiohttp.FormData()
            form.add_field("file", open(path, "rb"), filename=name)
            async with session.post(up["upload_url"], data=form) as resp:
                res = await resp.json()
            saved = await self._api("docs.save", file=res["file"])
            doc = saved.get("doc", {})
            att = f"doc{doc.get('owner_id')}_{doc.get('id')}"
            await self._api("messages.send", peer_id=account.platform_user_id,
                            message=caption, attachment=att,
                            random_id=random.randint(1, 2**31))
        except Exception:
            log.exception("vk document upload failed; falling back to text")
            if caption:
                await self.send_text(account, caption)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
