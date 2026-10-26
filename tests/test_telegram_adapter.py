"""Adapter-level tests that don't need a live Telegram connection."""

from telethon.tl.types import KeyboardInlineButton, ReplyKeyboardHide

from taskshed.messaging.telegram import edit_markup, inline_rows


def test_inline_rows_build_real_telethon_buttons():
    # Regression: Button(label, callback=...) was a non-existent API and the
    # live bot only failed at runtime. inline_rows must produce real
    # KeyboardInlineButton objects carrying the callback data as bytes.
    rows = inline_rows([
        [("✅ Готово", "done:42"), ("⏭ Отложить", "snooze:42")],
        [("Работа", "place:1")],
    ])
    assert len(rows) == 2 and len(rows[0]) == 2
    btn = rows[0][0]
    assert isinstance(btn, KeyboardInlineButton)
    assert btn.text == "✅ Готово"
    assert btn.type.data == b"done:42"
    assert rows[1][0].type.data == b"place:1"


def test_empty_markup_clears_the_keyboard():
    """Stale screens are disarmed.

    Live API check: an empty ReplyInlineMarkup raises ReplyMarkupInvalidError,
    ReplyKeyboardHide is what leaves the message with buttons=None.
    """
    assert isinstance(edit_markup([]), ReplyKeyboardHide)
    cleared = edit_markup([[("Работа", "place:1")]])
    assert isinstance(cleared[0][0], KeyboardInlineButton)
