"""Platform port: platform-independent events, outbound actions, adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from taskshed.core.models import Account


@dataclass(slots=True)
class RawEvent:
    """What every adapter must produce from a message or button press."""

    platform: str
    platform_user_id: str
    text: str | None = None          # cleaned command/message text
    callback: str | None = None      # button payload, e.g. "done:123"
    message_id: str | None = None    # message the button was pressed on


# Outbound button row: list of rows; each row: list of (label, callback_data)
ButtonRows = list[list[tuple[str, str]]]


class IMessenger(Protocol):
    """Sending side of the port. One implementation per platform."""

    platform: str

    async def send_text(self, account: Account, text: str) -> str | None: ...

    async def send_buttons(self, account: Account, text: str,
                           buttons: ButtonRows) -> str | None: ...

    async def send_image(self, account: Account, path: str, caption: str = "") -> None: ...

    async def send_document(self, account: Account, path: str, name: str,
                            caption: str = "") -> None: ...

    async def edit_buttons(self, account: Account, message_id: str, text: str,
                           buttons: ButtonRows) -> bool:
        """Update an interactive message in place; False when unsupported.

        An empty `buttons` list strips the keyboard: only the newest screen
        of a conversation should stay clickable.
        """
        ...


@dataclass(slots=True)
class SentMessage:
    """Record kept by FakeMessenger."""

    account: Account
    text: str
    buttons: ButtonRows = field(default_factory=list)
    image: str | None = None
    document: str | None = None
    id: str = ""


class FakeMessenger:
    """In-memory IMessenger for tests: records everything, no network."""

    def __init__(self, platform: str = "telegram") -> None:
        self.platform = platform
        self.sent: list[SentMessage] = []
        self._seq = 0

    def _mid(self) -> str:
        self._seq += 1
        return f"{self.platform}-{self._seq}"

    async def send_text(self, account: Account, text: str) -> str | None:
        msg = SentMessage(account, text, id=self._mid())
        self.sent.append(msg)
        return msg.id

    async def send_buttons(self, account: Account, text: str, buttons: ButtonRows) -> str | None:
        msg = SentMessage(account, text, buttons, id=self._mid())
        self.sent.append(msg)
        return msg.id

    async def edit_buttons(self, account: Account, message_id: str, text: str,
                           buttons: ButtonRows) -> bool:
        for m in self.sent:
            if m.id == message_id:
                m.text, m.buttons = text, buttons
                return True
        return False

    async def send_image(self, account: Account, path: str, caption: str = "") -> None:
        self.sent.append(SentMessage(account, caption, image=path))

    async def send_document(self, account: Account, path: str, name: str,
                            caption: str = "") -> None:
        self.sent.append(SentMessage(account, caption, document=path))

    # test helpers

    def texts(self) -> list[str]:
        return [m.text for m in self.sent]

    def find(self, needle: str) -> bool:
        return any(needle in m.text for m in self.sent)

    def last_buttons(self) -> ButtonRows:
        return self.sent[-1].buttons

    def clear(self) -> None:
        self.sent.clear()
