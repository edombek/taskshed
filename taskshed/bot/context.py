"""Ctx: the per-event context every handler receives."""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from taskshed.core.models import Account, User, UserSettings
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows, IMessenger, RawEvent

if TYPE_CHECKING:
    from taskshed.bot.base import BotBase


class Ctx:
    """Per-event context passed to all handlers."""

    def __init__(self, app: BotBase, ev: RawEvent, user: User, account: Account,
                 settings: UserSettings, messenger: IMessenger):
        self.app, self.ev, self.user, self.account = app, ev, user, account
        self.settings = settings
        self.messenger = messenger
        self.lang = user.language
        self.tz = ZoneInfo(settings.tz)
        self.replied = False       # handler answered something
        self.suppress_menu = False # answer already has interactive buttons (menu would clutter)

    async def reply(self, text: str, buttons: ButtonRows | None = None) -> None:
        # every previous keyboard in this chat becomes stale right here
        await self.app._strip_keyboards(self.messenger, self.account, self.user.id)
        self.replied = True
        if buttons:
            mid = await self.messenger.send_buttons(self.account, text, buttons)
            self.app._track_kb(self.user.id, self.account, mid, text)
        else:
            await self.messenger.send_text(self.account, text)

    async def place_buttons(self, question_key: str,
                            extra_rows: ButtonRows | None = None) -> None:
        places = await self.app.store.list_places(self.user.id)
        if not places:
            # keep the question on screen: the wizard needs to be answerable
            # by typing a brand new place name
            await self.reply(f"{t(question_key, self.lang)}\n{t('no_places', self.lang)}",
                             extra_rows or None)
            return
        rows = [[(p.name, f"place:{p.id}")] for p in places] + (extra_rows or [])
        await self.reply(t(question_key, self.lang), rows)
