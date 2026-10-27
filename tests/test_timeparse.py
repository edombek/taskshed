"""The shared date/time reader: one input, one meaning, everywhere."""

from datetime import date, time

import pytest

from taskshed.bot.timeparse import DATE, DATETIME, TIME, as_clock, as_day, parse_when

TODAY = date(2026, 10, 27)


def test_time_only_formats():
    for text, want in (("13:00", time(13, 0)), ("13.00", time(13, 0)),
                       ("13", time(13, 0)), ("08:30", time(8, 30)),
                       ("23.59", time(23, 59)), ("9", time(9, 0))):
        got = parse_when(text, TODAY)
        assert got is not None and got.kind == TIME, text
        assert got.clock == want, text
        assert got.day is None, text


def test_day_only_formats():
    for text, want in (("15.11.2026", date(2026, 11, 15)),
                       ("2026-11-15", date(2026, 11, 15)),
                       ("15/11/26", date(2026, 11, 15)),
                       ("сегодня", TODAY), ("завтра", date(2026, 10, 28)),
                       ("послезавтра", date(2026, 10, 29)),
                       ("через 3 дня", date(2026, 10, 30)),
                       ("in 5 days", date(2026, 11, 1))):
        got = parse_when(text, TODAY)
        assert got is not None and got.kind == DATE, text
        assert got.day == want, text


def test_day_and_time_together():
    for text, d, t in (("15.11 14.30", date(2026, 11, 15), time(14, 30)),
                       ("15.11.2026 09:00", date(2026, 11, 15), time(9, 0)),
                       ("завтра 18", date(2026, 10, 28), time(18, 0)),
                       ("2026-11-01 7.05", date(2026, 11, 1), time(7, 5))):
        got = parse_when(text, TODAY)
        assert got is not None and got.kind == DATETIME, text
        assert (got.day, got.clock) == (d, t), text


def test_bare_dot_date_is_a_date_and_admits_guessing():
    # "15.11" is 15 November (RU habit) — the year is our guess, so the bot
    # must ask the user whether it read that right
    got = parse_when("15.11", TODAY)
    assert got.kind == DATE and got.day == date(2026, 11, 15) and got.guessed


def test_past_bare_date_rolls_to_next_year():
    got = parse_when("15.10", TODAY)
    assert got.kind == DATE and got.day == date(2027, 10, 15) and got.guessed


def test_ambiguous_dot_value_carries_both_readings():
    # "9.5" could be 9 May or 9:05: the parser keeps both and flags the guess,
    # the step that asked picks one, and the user confirms it
    got = parse_when("9.5", TODAY)
    assert got.kind == DATE and got.guessed
    assert as_day(got).day == date(2027, 5, 9)
    assert as_clock(got).clock == time(9, 5)
    # a value that cannot be a month has one reading only
    assert parse_when("13.00", TODAY).kind == TIME
    assert parse_when("11.50", TODAY).kind == TIME
    assert as_day(parse_when("13.00", TODAY)) is None


def test_full_iso_needs_no_confirmation():
    got = parse_when("2026-11-15", TODAY)
    assert got.kind == DATE and not got.guessed


def test_nonsense_is_rejected():
    for text in ("", "   ", "когда-нибудь", "13.00.2026.5", "abc", "1300",
                 "13:99", "2026-13-01"):
        assert parse_when(text, TODAY) is None, text


def test_label_is_what_the_user_sees():
    assert parse_when("13.00", TODAY).label() == "13:00"
    assert parse_when("15.11.2026", TODAY).label() == "15.11.2026"
    assert parse_when("15.11 14.30", TODAY).label() == "15.11.2026 14:30"

@pytest.mark.parametrize(("typed", "want"), [
    ("11:55", time(11, 55)), ("11.55", time(11, 55)), ("11-55", time(11, 55)),
    ("13.00", time(13, 0)),
])
def test_clock_separators(typed, want):
    """Whatever separator is typed, the clock reading is the same."""
    assert as_clock(parse_when(typed, TODAY)).clock == want


@pytest.mark.parametrize(("typed", "want"), [
    ("2026-11-15", date(2026, 11, 15)), ("15.11.2026", date(2026, 11, 15)),
    ("15/11/2026", date(2026, 11, 15)), ("15 11 2026", date(2026, 11, 15)),
    ("15.11.26", date(2026, 11, 15)), ("15.02", date(2027, 2, 15)),
    ("завтра", date(2026, 10, 28)), ("Today", TODAY),
])
def test_day_formats(typed, want):
    assert parse_when(typed, TODAY).day == want


@pytest.mark.parametrize("typed", ["15.13.2026", "32.11.2026", "abc", "2026-11",
                                   "1155", "11:55:55", "25:00", "11:75"])
def test_rejects_garbage_formats(typed):
    assert parse_when(typed, TODAY) is None
