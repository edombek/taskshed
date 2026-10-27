"""Static bot-layer constants: command aliases, button presets, text helpers."""

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

COMMANDS = {
    "start": "start", "помощь": "help", "help": "help",
    "гдея": "where", "where": "where",
    "задачи": "tasks", "tasks": "tasks",
    "задача": "task", "task": "task",
    "места": "places", "places": "places",
    "удалить": "delete", "delete": "delete",
    "изменить": "edit", "edit": "edit", "редактировать": "edit",
    "напоминания": "settings", "reminders": "settings",
    "настройки": "settings", "settings": "settings",
    "статистика": "stats", "stats": "stats",
    "привязать": "link", "link": "link",
    "отвязать": "unlink", "unlink": "unlink",
    "lang": "lang", "язык": "lang",
    "отмена": "cancel", "cancel": "cancel",
}

PERIODS = {"неделя": ("week", 7), "week": ("week", 7),
           "месяц": ("month", 30), "month": ("month", 30),
           "год": ("year", 365), "year": ("year", 365),
           "всё": ("all", None), "все": ("all", None), "all": ("all", None)}
WD_KEYS = ["wd_mo", "wd_tu", "wd_we", "wd_th", "wd_fr", "wd_sa", "wd_su"]
# time presets offered as inline buttons (Telegram cannot send an empty
# message, so the 09:00 default reminder gets a ⏎ button instead)
TM_PRESETS = [[("08:00", "tm:08:00"), ("12:00", "tm:12:00"), ("15:00", "tm:15:00")],
              [("18:00", "tm:18:00"), ("21:00", "tm:21:00"), ("23:59", "tm:23:59")]]
REMINDER_TMS = [[("⏎ 09:00", "tm:09:00")],
                [("08:00", "tm:08:00"), ("12:00", "tm:12:00")],
                [("18:00", "tm:18:00"), ("21:00", "tm:21:00")]]


# URLs are defanged in user-supplied strings (task titles, place names):
# a bot that sends clickable links can get flagged and banned by Telegram.
_URL_RE = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|(?<![\w./@-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
    r"(?:/\S*)?"
)


def defang(text: str) -> str:
    """Neutralize links: example.com -> example[.]com (not clickable)."""
    return _URL_RE.sub(lambda m: m.group(0).replace(".", "[.]"), text)


def at(d: date, tval: time, tz: ZoneInfo) -> datetime:
    """Wall-clock moment of `tval` on day `d` in `tz`."""
    return datetime(d.year, d.month, d.day, tval.hour, tval.minute, tzinfo=tz)
