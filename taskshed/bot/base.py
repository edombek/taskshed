"""BotBase: transient dialog state + stale-keyboard bookkeeping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from taskshed.config import BotCfg
from taskshed.core.models import Account
from taskshed.messaging.base import ButtonRows, IMessenger
from taskshed.storage.repositories import Store

if TYPE_CHECKING:
    from taskshed.bot.context import Ctx  # circular at runtime: Ctx knows BotBase

log = logging.getLogger(__name__)


class BotBase:

    def __init__(self, store: Store, cfg: BotCfg, messengers: dict[str, IMessenger]):
        self.store = store
        self.cfg = cfg
        self.messengers = messengers
        # in-memory transient dialogs: wizard / one-shot text inputs
        self.wizards: dict[int, dict] = {}
        self._pending_inputs: dict[int, str] = {}
        # last reminder-settings message per (user, account): value changes
        # edit it in place instead of stacking new copies in the chat
        self._rem_msgs: dict[tuple[int, int], str] = {}
        # live keyboards per (user, account): {message_id: text}. A newer
        # answer strips these, so stale buttons cannot be tapped twice
        self._kb_msgs: dict[tuple[int, int], dict[str, str]] = {}
        # live task messages per (user, account): {message_id: occurrence id}.
        # One task per message: a tap re-renders that very message, and the
        # keyboard dies with the task (done/skipped) — no chat spam
        self._tl_msgs: dict[tuple[int, int], dict[str, int]] = {}
        # the «Готово» button closing the last notice of this chat: pressing it
        # ends the status-editing round and hands the main menu back
        self._fin_msgs: dict[tuple[int, int], str] = {}
        # last tab of the task editor per user: where ⬅️ Назад goes back to
        self._edit_tab: dict[int, str] = {}
        # a "where are you?" that interrupted something else: the interrupted
        # dialog lives here and is replayed once the place is known
        self._await_loc: dict[int, dict] = {}
        # live places screen per (user, account): its one message id; all
        # place management (delete ask/confirm, list) redraws that message
        self._pl_msgs: dict[tuple[int, int], str] = {}
        # menu sub-screens (where-to-send, language, stats period, hour-map
        # pager, editor picker): screen name -> (user, account) -> message id;
        # every re-render edits that message instead of stacking a new copy
        self._screens: dict[str, dict[tuple[int, int], str]] = {}

    def _track_kb(self, user_id: int, account: Account, mid: str | None,
                  text: str) -> None:
        """Remember an interactive message so its keyboard can be stripped."""
        if mid:
            self._kb_msgs.setdefault((user_id, account.id), {})[mid] = text

    def _track_tl(self, user_id: int, account: Account, mid: str | None,
                  occ_id: int) -> None:
        """Remember which occurrence a live task message owns."""
        if mid:
            self._tl_msgs.setdefault((user_id, account.id), {})[mid] = occ_id

    async def _screen(self, ctx: Ctx, name: str, text: str,
                      rows: ButtonRows) -> None:
        """Render a menu screen, reusing its message when we own it.

        Menu walks re-render on every tap (option pick, tab switch, paging);
        each re-render edits the previous copy in place instead of stacking a
        fresh one. The first copy lands via ctx.reply — which disarms the
        other screens of this chat — and its id is remembered per chat.
        """
        store = self._screens.setdefault(name, {})
        key = (ctx.user.id, ctx.account.id)
        mid = store.get(key)
        ctx.suppress_menu = True
        if mid:
            # other keyboards of this chat go stale; this one is re-rendered below
            await self._strip_keyboards(ctx.messenger, ctx.account, ctx.user.id,
                                        keep=mid)
            if await ctx.messenger.edit_buttons(ctx.account, mid, text, rows):
                self._track_kb(ctx.user.id, ctx.account, mid, text)
                ctx.replied = True
                return
        new_id = await ctx.reply(text, rows)
        if new_id:
            store[key] = new_id

    async def _strip_keyboards(self, messenger: IMessenger, account: Account,
                               user_id: int, keep: str = "") -> None:
        """Disarm every keyboard that is no longer the newest screen.

        `keep` is the id a handler is about to re-render in place: it stays
        armed (the edit that follows replaces its buttons anyway).
        """
        live = self._kb_msgs.pop((user_id, account.id), {})
        live_tl = self._tl_msgs.pop((user_id, account.id), {})
        self._pl_msgs.pop((user_id, account.id), None)
        self._fin_msgs.pop((user_id, account.id), None)
        for mid, text in live.items():
            if mid == keep:
                continue
            try:
                await messenger.edit_buttons(account, mid, text, [])
            except Exception:  # deleted/aged-out message: nothing to disarm
                log.debug("keyboard strip failed for message %s", mid)
        if keep and keep in live:
            self._kb_msgs[(user_id, account.id)] = {keep: live[keep]}
            if keep in live_tl:
                self._tl_msgs[(user_id, account.id)] = {keep: live_tl[keep]}
