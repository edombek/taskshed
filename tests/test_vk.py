"""VK adapter pure-function tests + import smoke tests (no network)."""

import json

from taskshed.messaging.vk import build_keyboard, parse_longpoll_update


class TestKeyboard:
    def test_inline_flatten_rows(self):
        kb = json.loads(build_keyboard([[("✅", "done:1"), ("⏭", "snooze:1")],
                                        [("Дом", "place:2")]]))
        assert kb["type"] == "inline"
        b = kb["buttons"]
        assert b[0]["pos"] == 0 and b[1]["pos"] == 3   # 1st row ends at pos 3
        assert b[2]["action"]["payload"] == "place:2"

    def test_payload_roundtrip(self):
        kb = json.loads(build_keyboard([[("x", "rem:toggle")]]))
        assert json.loads(json.dumps(kb))  # valid JSON
        assert kb["buttons"][0]["action"]["payload"] == "rem:toggle"


class TestParseUpdate:
    def test_plain_message(self):
        ev = parse_longpoll_update({"type": "message_new", "object": {
            "message": {"from_id": 42, "text": "/задачи"},
        }})
        assert ev and ev.platform_user_id == "42" and ev.text == "/задачи"

    def test_button_press(self):
        ev = parse_longpoll_update({"type": "message_new", "object": {
            "message": {"from_id": 7, "text": ""},
            "client_info": {"button_action": {"type": "callback", "payload": "done:3"}},
        }})
        assert ev and ev.callback == "done:3" and ev.text is None

    def test_ignores_other_updates(self):
        assert parse_longpoll_update({"type": "message_allow"}) is None
        assert parse_longpoll_update({"type": "message_new",
                                      "object": {"message": {"from_id": 1, "text": ""}}}) is None


def test_adapters_importable():
    from taskshed.entrypoints.common import build  # noqa: F401
    from taskshed.messaging.telegram import TelegramAdapter  # noqa: F401
    from taskshed.messaging.vk import VkAdapter  # noqa: F401
