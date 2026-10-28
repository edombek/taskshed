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
    """Turn one long poll update into a RawEvent, tolerating odd payloads.

    VK hands back shapes that differ between clients and update kinds: the
    object may be a list, `client_info`/`button_action` may be lists or
    missing. None of that must ever take the poll loop down — an unknown
    update is dropped, a message is read as well as it can be.
    """
    if not isinstance(update, dict):
        log.debug("vk update is not an object: %.200r", update)
        return None
    if update.get("type") != "message_new":
        log.debug("vk update ignored: %s", update.get("type"))
        return None
    obj = update.get("object") or {}
    if not isinstance(obj, dict):  # some update shapes ship a list here
        log.debug("vk message_new with unexpected object: %.200r", obj)
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        msg = obj
    peer = msg.get("from_id") or msg.get("peer_id")
    if not peer:
        return None
    client = obj.get("client_info") or msg.get("client_info") or {}
    if not isinstance(client, dict):
        client = {}
    action = client.get("button_action") or {}
    if not isinstance(action, dict):
        action = {}
    text = (msg.get("text") or "").strip()
    if not text and isinstance(client.get("command"), str):
        # the VK bot catalog's «Начать» arrives as a command, not as text
        text = client["command"].strip()
    callback = action.get("payload") if action.get("type") == "callback" else None
    if not text and not callback:
        return None
    mid = obj.get("last_message_id") or msg.get("id")
    return RawEvent(platform="vk", platform_user_id=str(peer),
                    text=text or None, callback=callback or None,
                    message_id=str(mid) if mid else None)


class VkAdapter:
    platform = "vk"

    def __init__(self, cfg: VkCfg, app: BotApp):
        self.cfg = cfg
        self.app = app
        self._session: aiohttp.ClientSession | None = None
        self._token = cfg.group_token
        self._lp_method = ""  # long poll endpoint that worked last time
        # every api.vk.com deployment moved to vk.ru: the base is configurable,
        # the long poll host itself VK hands back in the getLongPollServer reply
        self._api_base = (cfg.api_url or "").rstrip("/") + "/"

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            proxy = self.cfg.proxy.url if self.cfg.proxy.enabled else None
            self._session = aiohttp.ClientSession(proxy=proxy)
        return self._session

    async def _api(self, method: str, **params) -> dict:
        session = await self._http()
        params.update(access_token=self._token, v=API_VERSION)
        async with session.post(self._api_base + method, data=params) as resp:
            data = await resp.json()
        if not isinstance(data, dict):
            # VK (or whatever sits in front of it) occasionally hands back a
            # bare list — it must not walk into the caller's .get
            log.warning("vk api %s: unexpected payload %.200r", method, data)
            return {}
        if "error" in data:
            log.warning("vk api %s error: %s", method, data["error"])
            return {}
        return data.get("response", {})

    # ---------- long poll loop ----------

    async def run_forever(self) -> None:
        # never let a VK hiccup take the whole process down: log, wait, retry
        while True:
            try:
                server, key, ts = await self._server()
                log.info("vk bot long poll started (proxy=%s)",
                         self.cfg.proxy.type or "none")
                while True:
                    try:
                        session = await self._http()
                        async with session.get(server, params={
                                "act": "a_check", "key": key, "ts": ts,
                                "wait": 25}) as resp:
                            payload = await resp.json()
                        if not isinstance(payload, dict):
                            log.warning("vk long poll: odd payload: %.200r", payload)
                            continue
                        if "failed" in payload:
                            log.info("vk long poll answered %r: refreshing key",
                                     payload)
                            server, key, ts = await self._server()
                            continue
                        ts = payload.get("ts", ts)
                        for upd in payload.get("updates", []):
                            # one broken update must never kill the connection
                            await self._handle_update(upd)
                    except (TimeoutError, aiohttp.ClientError, ValueError):
                        # ValueError: aiohttp's JSONDecodeError on empty replies
                        await asyncio.sleep(3)
                        server, key, ts = await self._server()
            except Exception as exc:
                log.error("vk long poll unavailable: %s (retry in 30s)", exc)
                await asyncio.sleep(30)

    async def _handle_update(self, upd) -> None:
        """Parse and dispatch one update; never let it break the loop.

        VK shapes vary by client, and an update that parses to nothing is
        invisible otherwise — so every arrival is logged raw first. When
        anything goes wrong the trace lands right under that raw shape and
        the connection stays up.
        """
        log.info("vk update: %.400r", upd)
        try:
            ev = parse_longpoll_update(upd)
            if ev:
                await self.app.handle(ev)
        except Exception:
            log.exception("vk update failed: %.500r", upd)

    async def _server(self):
        # The community BLP endpoint wants group_id in config; when it is
        # missing (0) or the token scopes reject it, the very same server is
        # reachable via messages.getLongPollServer under a group token.
        # Remember which one worked so key refreshes stop probing both.
        order: list[tuple[str, dict]] = []
        if self.cfg.group_id and self._lp_method != "messages":
            order.append(("groups.getLongPollServer",
                          {"group_id": self.cfg.group_id}))
        order.append(("messages.getLongPollServer", {}))
        for name, params in order:
            d = await self._api(name, **params)
            if "server" in d:
                self._lp_method = name.split(".")[0]
                server = d["server"]
                if not server.startswith(("http://", "https://")):
                    server = "https://" + server  # VK hands back a bare host/path
                log.info("vk long poll via %s: server=%s ts=%s",
                         name, server[:60], d.get("ts"))
                return server, d["key"], d["ts"]
        raise RuntimeError("no vk long poll server: check group_token/group_id")

    # ---------- IMessenger ----------

    async def send_text(self, account: Account, text: str) -> None:
        await self._api("messages.send", peer_id=account.platform_user_id,
                        message=text, random_id=random.randint(1, 2**31))

    async def send_buttons(self, account: Account, text: str, buttons: ButtonRows) -> None:
        await self._api("messages.send", peer_id=account.platform_user_id,
                        message=text, keyboard=build_keyboard(buttons),
                        random_id=random.randint(1, 2**31))

    async def edit_buttons(self, account: Account, message_id: str, text: str,
                           buttons: ButtonRows) -> bool:
        # Bot Long Poll sends do not expose message ids, so VK keeps
        # appending fresh keyboard messages instead of editing in place
        return False

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
