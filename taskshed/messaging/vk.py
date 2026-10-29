"""VK community bot adapter — Bot Long Poll API over aiohttp.

Deliberately framework-free: VK Bot Long Poll is small enough to own, which
keeps the dependency surface identical to the Telegram side and the port
contract intact. Supports inline keyboards, photos and documents.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random

import aiohttp

from taskshed.bot.app import BotApp
from taskshed.config import VkCfg
from taskshed.core.models import Account
from taskshed.messaging.base import ButtonRows, RawEvent

log = logging.getLogger(__name__)

API_VERSION = "5.199"


def build_keyboard(buttons: ButtonRows) -> str:
    """Render our rows as VK's native keyboard JSON (inline callback keys).

    VK wants a list of rows, each button `{action, color}`; the payload must
    be a JSON string ≤256 chars, so our internal `cmd:…` payloads travel in a
    `cb` envelope that the long poll parser unwraps again. The old flat
    `{"type": "inline"}` shape was rejected live with 911 "unknown fields",
    and long screens hit 911 "too much rows" — callers chunk via VK_MAX_ROWS.
    """
    colors = ["positive", "secondary", "primary", "negative"]
    rows = [[{"action": {"type": "callback", "label": label[:40],
                         "payload": json.dumps({"cb": cb}, ensure_ascii=False)},
              "color": colors[i % len(colors)]}
             for label, cb in row]
            for i, row in enumerate(buttons)]
    return json.dumps({"one_time": False, "inline": True, "buttons": rows},
                      ensure_ascii=False)


# VK inline keyboards are capped live (error 911): ≤6 rows, ≤10 buttons total,
# ≤5 in one row. Long screens are packed into several messages; only the first
# carries the text, the rest are a "…" continuation.
VK_MAX_ROWS = 6
VK_MAX_BUTTONS = 10


def pack_keyboards(buttons: ButtonRows) -> list[ButtonRows]:
    """Split rows into VK-sized chunks (greedy, whole rows stay together)."""
    chunks: list[ButtonRows] = []
    cur: ButtonRows = []
    n_btn = 0
    for row in buttons:
        size = max(len(row), 1)
        if cur and (len(cur) + 1 > VK_MAX_ROWS or n_btn + size > VK_MAX_BUTTONS):
            chunks.append(cur)
            cur, n_btn = [], 0
        cur.append(row)
        n_btn += size
    if cur:
        chunks.append(cur)
    return chunks or [[]]


def _unwrap_payload(raw) -> str | None:
    """Take a button payload back to the app's internal callback string.

    VK hands `button_action.payload` back verbatim: JSON from build_keyboard,
    anything else (someone crafted it by hand) is passed through as text.
    """
    if isinstance(raw, dict):  # message_event payloads arrive already decoded
        cb = raw.get("cb")
        return cb if isinstance(cb, str) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(data, dict):
        cb = data.get("cb")
        return cb if isinstance(cb, str) else None
    return data if isinstance(data, str) else None


def _parse_user_lp_message(ev: list) -> RawEvent | None:
    """Numeric user-LP `message_new` (the fallback stream's shape).

    Observed live: `[4, msg_id, flags, author_id, ts, title, text]`, and
    typing arrives as `[61, uid, chat]`. The community's own outgoing
    messages ride the same stream, so events authored by our group are
    dropped — otherwise the bot would answer itself forever.
    """
    if ev[0] != 4 or len(ev) < 5:
        return None
    author = next((x for x in ev[1:] if isinstance(x, int) and 1_000_000 <= x < 2_000_000_000),
                  None)
    text = next((x for x in reversed(ev) if isinstance(x, str) and x.strip()
                 and x.strip() != " ... "), None)
    if not author or not text:
        return None
    mid = ev[1] if isinstance(ev[1], int) else None
    return RawEvent(platform="vk", platform_user_id=str(author),
                    text=text.strip(), callback=None,
                    message_id=str(mid) if mid else None)


def parse_longpoll_update(update: dict) -> RawEvent | None:
    """Turn one long poll update into a RawEvent, tolerating odd payloads.

    VK hands back shapes that differ between clients and update kinds: the
    object may be a list, `client_info`/`button_action` may be lists or
    missing. None of that must ever take the poll loop down — an unknown
    update is dropped, a message is read as well as it can be.
    """
    if isinstance(update, (list, tuple)):
        # user long poll (the token without the longpoll right): numeric events
        return _parse_user_lp_message(list(update))
    if not isinstance(update, dict):
        log.debug("vk update is not an object: %.200r", update)
        return None
    if update.get("type") == "message_event":
        return _parse_message_event(update.get("object"))
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
        # the VK bot catalog's «Начать» arrives as a command, not as text;
        # make it a real "/start" so it routes as a command and never lands
        # as free text (a pending "where are you?" would eat it as a name)
        text = "/start"
    elif text.lower() in ("начать", "start"):
        # …and the community's «кнопка старт» delivers the very same «Начать»
        # as an ordinary text message (live: text='Начать', client_info=null)
        text = "/start"
    callback = (_unwrap_payload(action.get("payload"))
                if action.get("type") == "callback" else None)
    if not text and not callback:
        return None
    mid = obj.get("last_message_id") or msg.get("id")
    return RawEvent(platform="vk", platform_user_id=str(peer),
                    text=text or None, callback=callback or None,
                    message_id=str(mid) if mid else None)


def _parse_message_event(obj) -> RawEvent | None:
    """VK callback taps are their own update kind, not messages.

    Live shape: object {user_id, peer_id, event_id, payload: {cb: …}} — the
    payload arrives already decoded. Without handling these, button taps
    vanish and VK shows the user a forever-spinning button.
    """
    if not isinstance(obj, dict):
        return None
    user = obj.get("user_id") or obj.get("peer_id")
    cb = _unwrap_payload(obj.get("payload"))
    if not user or not cb:
        return None
    mid = obj.get("conversation_message_id")
    return RawEvent(platform="vk", platform_user_id=str(user),
                    text=None, callback=cb,
                    message_id=str(mid) if mid else None)


class VkAdapter:
    platform = "vk"

    def __init__(self, cfg: VkCfg, app: BotApp):
        self.cfg = cfg
        self.app = app
        self._session: aiohttp.ClientSession | None = None
        self._token = cfg.group_token
        self._lp_method = ""  # long poll endpoint that worked last time
        self._group_id: int | None = None  # discovered via groups.getById
        self._can_ack_events = True  # until vk.ru says error 27, see below
        # head message id -> continuation ids of a split screen (edit reshapes it)
        self._cont_ids: dict[str, list] = {}
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
            return {"error": data["error"]}
        resp = data.get("response", {})
        # some methods answer with a bare value (messages.send → message id);
        # callers may always assume a mapping
        return resp if isinstance(resp, dict) else {"_value": resp}

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
            if (isinstance(upd, dict) and upd.get("type") == "message_event"
                    and self._can_ack_events):
                # try to stop the button spinner; live vk.ru refuses this with
                # error 27 for community tokens — then we stop asking
                obj = upd.get("object") or {}
                resp = await self._api("messages.sendMessageEvent",
                                       peer_id=obj.get("peer_id"),
                                       event_id=obj.get("event_id"))
                if (resp.get("error") or {}).get("error_code") == 27:
                    self._can_ack_events = False
                    log.info("vk: sendMessageEvent is refused with a community "
                             "token (error 27) — taps work, spinners time out")
            ev = parse_longpoll_update(upd)
            if ev and ev.platform_user_id == str(self._group_id):
                return  # our own outgoing line on the user-LP stream
            if ev:
                await self.app.handle(ev)
        except Exception:
            log.exception("vk update failed: %.500r", upd)

    async def _detect_group(self) -> None:
        """Learn which community the token belongs to.

        `groups.getById` without arguments answers with the very community
        of a group token, so the config never repeats (or misspells) it.
        """
        d = await self._api("groups.getById")
        groups = d.get("groups") or []
        if groups and groups[0].get("id"):
            self._group_id = int(groups[0]["id"])
            log.info("vk community: id=%s «%s»",
                     self._group_id, groups[0].get("name", "?"))
        else:
            log.warning("vk: groups.getById answered nothing — is the "
                        "group_token valid?")

    async def _server(self):
        # which community the token belongs to is discovered, not configured;
        # when the community BLP endpoint is refused (token without the
        # longpoll right), the very same server is reachable via
        # messages.getLongPollServer under a group token.
        # Remember which one worked so key refreshes stop probing both.
        if self._group_id is None:
            await self._detect_group()
        order: list[tuple[str, dict]] = []
        if self._lp_method != "messages":
            order.append(("groups.getLongPollServer",
                          {"group_id": self._group_id}
                          if self._group_id else {}))
        order.append(("messages.getLongPollServer", {}))
        for name, params in order:
            d = await self._api(name, **params)
            if "server" in d:
                self._lp_method = name.split(".")[0]
                server = d["server"]
                if not server.startswith(("http://", "https://")):
                    server = "https://" + server  # VK hands back a bare host/path
                if name == "messages.getLongPollServer":
                    log.warning(
                        "vk: groups.getLongPollServer is refused (the community "
                        "token lacks the longpoll right) — falling back to the "
                        "user long poll, which carries no button payloads; "
                        "re-issue the token with «longpoll» for full support")
                log.info("vk long poll via %s: server=%s ts=%s",
                         name, server[:60], d.get("ts"))
                return server, d["key"], d["ts"]
        raise RuntimeError("no vk long poll server: check group_token")

    # ---------- IMessenger ----------

    async def send_text(self, account: Account, text: str) -> str | None:
        resp = await self._api("messages.send", peer_id=account.platform_user_id,
                               message=text, random_id=random.randint(1, 2**31))
        v = resp.get("_value")
        return str(v) if v else None

    async def send_buttons(self, account: Account, text: str,
                           buttons: ButtonRows) -> str | None:
        # VK caps inline keyboards (911), so long screens arrive as several
        # messages; only the first carries the text. The head id is returned
        # and continuations remembered, so editing that screen rewrites the
        # head and re-shapes the tail instead of stacking fresh copies.
        head: str | None = None
        conts: list = []
        for i, chunk in enumerate(pack_keyboards(buttons)):
            resp = await self._api("messages.send", peer_id=account.platform_user_id,
                                   message=text if i == 0 else "…",
                                   keyboard=build_keyboard(chunk),
                                   random_id=random.randint(1, 2**31))
            err = (resp.get("error") or {}).get("error_code")
            if err == 912 and i == 0:
                # VK only lets communities with the «чат-бот» status attach
                # keyboards; without it the conversation must still go on as text
                log.warning("vk: keyboard refused — enable the chat bot status "
                            "in the community settings; sending text only")
                await self.send_text(account, text)
                return None
            v = resp.get("_value")
            if i == 0:
                head = str(v) if v else None
            elif v:
                conts.append(v)
        if head and conts:
            self._cont_ids[head] = conts
        return head

    async def edit_buttons(self, account: Account, message_id: str, text: str,
                           buttons: ButtonRows) -> bool:
        # messages.edit rewrites text and keyboard of a community message in
        # place (verified live; an empty keyboard clears the buttons); a
        # screen that was split keeps its head and gets a fresh tail
        chunks = pack_keyboards(buttons)
        resp = await self._api("messages.edit", peer_id=account.platform_user_id,
                               message_id=message_id, message=text,
                               keyboard=build_keyboard(chunks[0]))
        if "error" in resp:
            return False
        for cid in self._cont_ids.pop(str(message_id), []):
            await self._api("messages.delete", message_id=cid)
        conts: list = []
        for chunk in chunks[1:]:
            r = await self._api("messages.send", peer_id=account.platform_user_id,
                                message="…", keyboard=build_keyboard(chunk),
                                random_id=random.randint(1, 2**31))
            if v := r.get("_value"):
                conts.append(v)
        if conts:
            self._cont_ids[str(message_id)] = conts
        return True

    async def _upload(self, url: str, path: str, filename: str,
                      content_type: str) -> dict:
        """POST a file to one of VK's temporary upload servers."""
        session = await self._http()
        form = aiohttp.FormData()
        with open(path, "rb") as fh:
            form.add_field("file", fh.read(), filename=filename,
                           content_type=content_type)
            async with session.post(url, data=form) as resp:
                return await resp.json()

    async def _send_attach(self, account: Account, caption: str,
                           attachment: str) -> None:
        resp = await self._api("messages.send", peer_id=account.platform_user_id,
                               message=caption, attachment=attachment,
                               random_id=random.randint(1, 2**31))
        if "error" in resp:
            raise RuntimeError(f"messages.send {attachment}: {resp['error']}")

    async def send_image(self, account: Account, path: str, caption: str = "") -> None:
        # live-verified path for community tokens: the modern
        # photos.saveMessagePhoto is user-only (error 3), but the classic trio
        # photos.getMessagesUploadServer → upload → photos.saveMessagesPhoto
        # works, and photo{owner}_{id}_{access_key} attaches to messages.send
        try:
            up = await self._api("photos.getMessagesUploadServer",
                                 peer_id=account.platform_user_id)
            url = up.get("upload_url")
            if not url:
                raise RuntimeError(f"no upload_url: {up}")
            res = await self._upload(url, path, os.path.basename(path), "image/png")
            saved = await self._api("photos.saveMessagesPhoto", photo=res.get("photo"),
                                    server=res.get("server"), hash=res.get("hash"))
            ph = saved.get("_value") or saved.get("photo") or {}
            # saveMessagesPhoto answers with a list of one photo
            ph = ph[0] if isinstance(ph, list) else ph
            if not ph.get("id"):
                raise RuntimeError(f"saveMessagesPhoto: {saved}")
            att = f"photo{ph['owner_id']}_{ph['id']}_{ph.get('access_key', '')}"
            await self._send_attach(account, caption, att)
        except Exception:
            log.exception("vk photo upload failed; falling back to text")
            if caption:
                await self.send_text(account, caption)

    async def send_document(self, account: Account, path: str, name: str,
                            caption: str = "") -> None:
        try:
            up = await self._api("docs.getMessagesUploadServer",
                                 peer_id=account.platform_user_id, type="doc")
            url = up.get("upload_url")
            if not url:
                raise RuntimeError(f"no upload_url: {up}")
            res = await self._upload(url, path, name, "application/octet-stream")
            saved = await self._api("docs.save", file=res.get("file"))
            doc = saved.get("doc") or {}
            if not doc.get("id"):
                raise RuntimeError(f"docs.save: {saved}")
            # the access_hash is part of the doc attachment: without it
            # messages.send refuses the reference ("attachment not found")
            att = f"doc{doc['owner_id']}_{doc['id']}_{doc.get('access_hash', '')}"
            await self._send_attach(account, caption, att)
        except Exception:
            log.exception("vk document upload failed; falling back to text")
            if caption:
                await self.send_text(account, caption)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
