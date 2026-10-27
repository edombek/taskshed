"""BotBase: transient dialog state + stale-keyboard bookkeeping."""

import logging

from taskshed.config import BotCfg
from taskshed.core.models import Account
from taskshed.messaging.base import IMessenger
from taskshed.storage.repositories import Store

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
        # live task-list/digest messages per (user, account): {message_id:
        # (place_name, [occurrence ids])}. Taps on them re-render that very
        # message, so one reminder can be worked through without chat spam
        self._tl_msgs: dict[tuple[int, int], dict[str, tuple[str, list[int]]]] = {}
        # last tab of the task editor per user: where ⬅️ Назад goes back to
        self._edit_tab: dict[int, str] = {}
        # a "where are you?" that interrupted something else: the interrupted
        # dialog lives here and is replayed once the place is known
        self._await_loc: dict[int, dict] = {}

    def _track_kb(self, user_id: int, account: Account, mid: str | None,
                  text: str) -> None:
        """Remember an interactive message so its keyboard can be stripped."""
        if mid:
            self._kb_msgs.setdefault((user_id, account.id), {})[mid] = text

    def _track_tl(self, user_id: int, account: Account, mid: str | None,
                  place_name: str, occ_ids: list[int]) -> None:
        """Remember which occurrences a task-list message shows."""
        if mid:
            self._tl_msgs.setdefault((user_id, account.id), {})[mid] = (
                place_name, occ_ids)

    async def _strip_keyboards(self, messenger: IMessenger, account: Account,
                               user_id: int, keep: str = "") -> None:
        """Disarm every keyboard that is no longer the newest screen.

        `keep` is the id a handler is about to re-render in place: it stays
        armed (the edit that follows replaces its buttons anyway).
        """
        live = self._kb_msgs.pop((user_id, account.id), {})
        live_tl = self._tl_msgs.pop((user_id, account.id), {})
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
