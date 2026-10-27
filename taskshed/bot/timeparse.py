"""One reader for anything the user types about dates and times.

Every question that expects a moment (start, ready-by, first repeat, reminder
time) goes through parse_when, so the same sloppy text means the same thing
everywhere: "13.00" is 13:00, "15.11" is 15 November, "завтра 9" is tomorrow at
nine. It reports *which kind* of value it read (time / day / both) and whether
it had to guess; the bot repeats that reading back and asks to confirm it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time, timedelta

TIME = "time"
DATE = "date"
DATETIME = "datetime"

# 13, 13.00, 13:00, 9.5, 11-55 — a clock time (never a 4-digit blob)
_CLOCK_RE = re.compile(r"^(\d{1,2})(?:[.:\-](\d{1,2}))?$")
# 2026-11-15, 15.11.2026, 15/11/26, 15.11, "15 11"
_DAY_RE = re.compile(r"^(\d{1,4})[ ./-]+(\d{1,2})(?:[ ./-]+(\d{2,4}))?$")
_DAY_WORDS_RE = re.compile(
    r"^(сегодня|today|завтра|tomorrow|послезавтра|day after tomorrow)(?=\s|$)")
# "через 3 дня", "in 5 days"
_IN_DAYS_RE = re.compile(r"^(?:через|in)\s+(\d{1,3})\s*(?:дн\w*|да\w*|days?|day)(?=\s|$)")
_WORD_OFFSETS = {"сегодня": 0, "today": 0, "завтра": 1, "tomorrow": 1,
                 "послезавтра": 2, "day after tomorrow": 2}


def hhmm(tval: time) -> str:
    return f"{tval.hour:02d}:{tval.minute:02d}"


def day_ymd(d: date) -> str:
    return d.strftime("%d.%m.%Y")


@dataclass(frozen=True)
class When:
    """What was read: a clock time, a day, or both, plus how sure we are.

    `alt` is the other reading of the same text when it was genuinely
    ambiguous ("15.11" = 15 November *or* 15:11): the step that asked the
    question picks the one it needs, and the user confirms it either way.
    """

    kind: str
    day: date | None = None
    clock: time | None = None
    #: the parser filled a hole (missing year, ambiguous "15.11", a word)
    guessed: bool = False
    alt: When | None = None

    def label(self) -> str:
        if self.kind == TIME and self.clock:
            return hhmm(self.clock)
        when = day_ymd(self.day) if self.day else "?"
        return f"{when} {hhmm(self.clock)}" if self.clock else when


def read_clock(token: str) -> time | None:
    """A clock time on its own: 13, 13.00, 13:00, 9.5."""
    m = _CLOCK_RE.match(token.strip())
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2)) if m.group(2) is not None else 0
    return time(h, mi) if h < 24 and mi < 60 else None


def read_day(token: str, today: date) -> tuple[date, bool] | None:
    """A calendar day, or (day, guessed) when the year was filled in."""
    token = token.strip()
    if token in _WORD_OFFSETS:
        return today + timedelta(days=_WORD_OFFSETS[token]), True
    m = _IN_DAYS_RE.match(token)
    if m:
        return today + timedelta(days=int(m.group(1))), True
    m = _DAY_RE.match(token)
    if not m:
        return None
    a, b, c = (int(g) if g is not None else None for g in m.groups())
    short = c is None
    if short:                       # 15.11 / "15 11" → DD.MM of some year
        if not 1 <= b <= 12:
            return None
        d, mo, y = a, b, today.year
    elif a > 31:                    # 2026-11-15
        y, mo, d = a, b, c
    else:                           # 15.11.2026 / 15.11.26
        d, mo, y = a, b, 2000 + c if c < 100 else c
        short = c < 100
    try:
        got = date(y, mo, d)
    except ValueError:
        return None
    if short and got < today:       # a bare date in the past means next year
        try:
            return date(y + 1, mo, d), True
        except ValueError:
            return None
    return got, short


_LEAD_RE = re.compile(r"^(?:за\s+)?(\d{1,4})\s*([а-яa-z.]{0,7})?$")
# how a bare number or a word reads: minutes unless a unit is spelled out
_LEAD_UNITS = {
    "": 1, "мин": 1, "минута": 1, "минуты": 1, "минут": 1, "m": 1, "min": 1,
    "mins": 1,
    "ч": 60, "час": 60, "часа": 60, "часов": 60, "h": 60, "hr": 60,
    "hour": 60, "hours": 60,
    "д": 1440, "дн": 1440, "день": 1440, "дня": 1440, "дней": 1440,
    "сут": 1440, "суток": 1440, "d": 1440, "day": 1440, "days": 1440,
}


def parse_lead(text: str) -> int | None:
    """«За сколько до срока предупредить»: 30, 30 мин, 2 часа, 3 дня → минуты."""
    m = _LEAD_RE.match(" ".join(text.strip().lower().split()))
    if not m:
        return None
    unit = (m.group(2) or "").rstrip(".")
    if unit and unit not in _LEAD_UNITS:
        return None                     # "5 недель" is not a unit we speak
    mins = int(m.group(1)) * _LEAD_UNITS.get(unit, 1)
    return mins if 0 <= mins <= 60 * 24 * 365 else None


def as_clock(got: When) -> When | None:
    """The reading that gives a clock time (the ambiguous one if needed)."""
    if got.clock:
        return got
    if got.alt and got.alt.clock:
        return When(TIME, None, got.alt.clock, True)
    return None


def as_day(got: When) -> When | None:
    """The reading that gives a calendar day (the ambiguous one if needed)."""
    if got.day:
        return got
    if got.alt and got.alt.day:
        return When(DATE, got.alt.day, None, True)
    return None


def parse_when(text: str, today: date) -> When | None:
    """Read a date and/or a time out of free text; None if nothing fit."""
    s = " ".join(text.strip().lower().split())
    if not s:
        return None
    head: When | None = None
    for pat in (_DAY_WORDS_RE, _IN_DAYS_RE):     # words first, then digits
        m = pat.match(s)
        if m:
            got = read_day(m.group(0), today)
            if got:
                head = When(DATE, got[0], None, True)
            s = s[m.end():].strip()
            break
    rest: When | None = None
    if s:
        parts = s.split(" ", 1)
        pair_day = read_day(parts[0], today) if len(parts) == 2 else None
        pair_clock = read_clock(parts[1]) if len(parts) == 2 else None
        whole = read_day(s, today)       # "15 11 2026": one day, typed spaced
        if pair_day and pair_clock:      # "15.11 14.30", "завтра"-less forms
            rest = When(DATETIME, pair_day[0], pair_clock, pair_day[1])
        elif len(parts) == 1:
            got_day = read_day(parts[0], today)
            got_clock = read_clock(parts[0])
            if got_day and got_clock:
                # "15.11": RU habit says 15 November, the user may have meant
                # 15:11 — both readings travel to the step that asked
                rest = When(DATE, got_day[0], None, True,
                            When(TIME, None, got_clock, True))
            elif got_day:
                rest = When(DATE, got_day[0], None, got_day[1])
            elif got_clock:
                rest = When(TIME, None, got_clock)
        elif whole:
            rest = When(DATE, whole[0], None, whole[1])
        else:
            return head                  # "DATE TIME" that fits neither
    if head and rest:
        if rest.kind == DATE:
            return rest           # two days typed: the explicit one wins
        return When(DATETIME, head.day, rest.clock, True)
    return rest or head
