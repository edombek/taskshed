"""VK adapter pure-function tests + import smoke tests (no network)."""

import json
from datetime import UTC, datetime

from taskshed.messaging.vk import build_keyboard, parse_longpoll_update


class TestKeyboard:
    def test_rows_and_envelope(self):
        kb = json.loads(build_keyboard([[("✅", "done:1"), ("⏭", "snooze:1")],
                                        [("Дом", "place:2")]]))
        assert kb["inline"] is True and kb["one_time"] is False
        assert len(kb["buttons"]) == 2                     # rows stay rows
        assert kb["buttons"][0][0]["action"]["label"] == "✅"
        assert kb["buttons"][1][0]["color"] == "secondary"
        # VK only takes JSON payloads: our cb travels in an envelope
        assert json.loads(kb["buttons"][0][0]["action"]["payload"]) == {"cb": "done:1"}

    def test_only_documented_fields(self):
        # live error 911 rejected the old {"type": "inline"}/pos shape
        kb = json.loads(build_keyboard([[("x", "rem:toggle")]]))
        btn = kb["buttons"][0][0]
        assert set(kb) == {"one_time", "inline", "buttons"}
        assert set(btn) == {"action", "color"}
        assert set(btn["action"]) == {"type", "label", "payload"}

    def test_payload_roundtrip(self):
        from taskshed.messaging.vk import _unwrap_payload
        kb = json.loads(build_keyboard([[("x", "rem:toggle")]]))
        assert _unwrap_payload(kb["buttons"][0][0]["action"]["payload"]) == "rem:toggle"


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

    def test_button_press_cb_envelope(self):
        # the payload build_keyboard sends comes back wrapped in {"cb": …}
        ev = parse_longpoll_update({"type": "message_new", "object": {
            "message": {"from_id": 7, "text": ""},
            "client_info": {"button_action": {
                "type": "callback", "payload": '{"cb": "fin:"}'}},
        }})
        assert ev and ev.callback == "fin:"

    def test_message_carries_the_button_message_id(self, env=None):
        ev = parse_longpoll_update({"type": "message_new", "object": {
            "message": {"from_id": 7, "text": ""},
            "last_message_id": 111,
            "client_info": {"button_action": {"type": "callback", "payload": "done:3"}},
        }})
        assert ev and ev.message_id == "111"

    def test_ignores_other_updates(self):
        assert parse_longpoll_update({"type": "message_allow"}) is None
        assert parse_longpoll_update({"type": "message_new",
                                      "object": {"message": {"from_id": 1, "text": ""}}}) is None

    def test_list_object_does_not_crash(self):
        assert parse_longpoll_update({"type": "message_new",
                                      "object": [{"from_id": 1}]}) is None

    def test_update_itself_is_a_list(self):
        # a real live payload once carried a bare list where an object was
        # expected — the poll loop must shrug, not die
        assert parse_longpoll_update(["message_new", {}]) is None

    def test_list_client_info_and_button_action_are_tolerated(self):
        ev = parse_longpoll_update({"type": "message_new", "object": {
            "message": {"from_id": 5, "text": "/старт", "id": 3},
            "client_info": [{"button_action": ["type", "callback"]}],
        }})
        assert ev and ev.text == "/старт" and ev.callback is None

    def test_message_field_of_the_wrong_kind_falls_back_to_object(self):
        ev = parse_longpoll_update({"type": "message_new", "object": {
            "message": ["nope"], "from_id": 9, "text": "привет",
        }})
        assert ev and ev.text == "привет" and ev.platform_user_id == "9"

    def test_numeric_user_lp_message(self):
        # the exact shape the live fallback stream carried
        ev = parse_longpoll_update(
            [4, 16, 1, 323871959, 1793179926, " ... ", "/старт"])
        assert ev and ev.text == "/старт"
        assert ev.platform_user_id == "323871959"
        assert ev.message_id == "16"

    def test_numeric_typing_event_is_ignored(self):
        assert parse_longpoll_update([61, 323871959, 1]) is None


class TestUpdateLoop:
    """One broken update must never take the long poll connection down."""

    async def test_handle_update_swallows_everything(self):
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter

        class Boom:
            async def handle(self, ev):
                raise RuntimeError("app exploded")

        ad = VkAdapter(VkCfg(group_token="t"), app=Boom())
        for upd in ([1], {"type": "message_new",
                          "object": {"message": {"from_id": 1, "text": "x"}}},
                    {"type": "message_change", "object": []}):
            await ad._handle_update(upd)     # nothing may escape

    async def test_own_community_lines_are_dropped(self):
        # the user-LP stream also carries what the bot itself sent: answering
        # those would loop the conversation forever
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter

        class Recorder:
            def __init__(self):
                self.got = []

            async def handle(self, ev):
                self.got.append(ev.platform_user_id)

        rec = Recorder()
        ad = VkAdapter(VkCfg(group_token="t"), app=rec)
        ad._group_id = 242594545
        await ad._handle_update(
            [4, 20, 2, 242594545, 1793180000, " ... ", "мой же ответ"])
        await ad._handle_update(
            [4, 21, 1, 323871959, 1793180005, " ... ", "/старт"])
        assert rec.got == ["323871959"]


class TestLongPollServer:
    """The community id comes from the token, not the config; a refused
    endpoint falls back, a fully dead token fails loudly."""

    def _adapter(self, responses: dict, calls: list):
        async def fake_api(method, **params):
            calls.append((method, params))
            return responses.get(method, {})
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t"), app=None)
        ad._api = fake_api
        return ad

    async def test_group_detected_then_groups_endpoint(self):
        calls: list = []
        ad = self._adapter({
            "groups.getById": {"groups": [{"id": 42, "name": "x"}]},
            "groups.getLongPollServer": {"server": "lp/1", "key": "k", "ts": 7},
        }, calls)
        assert await ad._server() == ("https://lp/1", "k", 7)
        assert ad._group_id == 42
        assert [m for m, _ in calls] == ["groups.getById", "groups.getLongPollServer"]
        assert calls[1][1] == {"group_id": 42}

    async def test_falls_back_to_messages_endpoint(self):
        calls: list = []
        ad = self._adapter({
            "groups.getById": {"groups": [{"id": 42}]},
            "messages.getLongPollServer": {"server": "api.gim/1", "key": "k", "ts": 1},
        }, calls)
        assert await ad._server() == ("https://api.gim/1", "k", 1)
        assert [m for m, _ in calls] == ["groups.getById", "groups.getLongPollServer",
                                         "messages.getLongPollServer"]

    async def test_groups_endpoint_works_without_id_when_detect_fails(self):
        calls: list = []
        ad = self._adapter({
            "groups.getLongPollServer": {"server": "lp/2", "key": "k", "ts": 3},
        }, calls)
        assert await ad._server() == ("https://lp/2", "k", 3)
        assert ad._group_id is None
        assert calls[1] == ("groups.getLongPollServer", {})

    async def test_clear_error_when_both_endpoints_fail(self):
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t"), app=None)
        async def dead(method, **params):
            return {"error": {"error_code": 5}}
        ad._api = dead
        import pytest
        with pytest.raises(RuntimeError, match="check group_token"):
            await ad._server()


class TestSendFallback:
    async def test_912_community_without_bot_status_sends_text(self):
        # VK refuses keyboards for communities lacking the chat bot status
        # (912); the conversation must continue as plain text
        from taskshed.config import VkCfg
        from taskshed.core.models import Account
        from taskshed.messaging.vk import VkAdapter
        sent: list = []

        async def fake_api(method, **params):
            if method == "messages.send" and "keyboard" in params:
                return {"error": {"error_code": 912}}
            sent.append(params.get("message"))
            return {"response_id": 1}
        ad = VkAdapter(VkCfg(group_token="t"), app=None)
        ad._api = fake_api
        acc = Account(id=1, user_id=1, platform="vk", platform_user_id="9",
                      linked_at=datetime.now(UTC))
        await ad.send_buttons(acc, "текст", [[("A", "a")]])
        assert sent == ["текст"]


def test_adapters_importable():
    from taskshed.entrypoints.common import build  # noqa: F401
    from taskshed.messaging.telegram import TelegramAdapter  # noqa: F401
    from taskshed.messaging.vk import VkAdapter  # noqa: F401


class TestApiAddress:
    """VK moved to the vk.ru domain zone: base is the default, still movable."""

    def test_default_base_is_vk_ru(self):
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t"), app=None)
        assert ad._api_base == "https://api.vk.ru/method/"

    def test_loader_default_is_the_value_not_a_slot_descriptor(self, tmp_path):
        # a slots dataclass hands back a descriptor for the class attribute,
        # so the loader must not build the URL from VkCfg.api_url
        from taskshed.config import load_config
        p = tmp_path / "config.yaml"
        p.write_text('platforms:\n  vk:\n    enabled: true\n'
                     '    group_token: "t"\n', encoding="utf-8")
        cfg = load_config(p)
        assert cfg.vk.api_url == "https://api.vk.ru/method/"
        assert not cfg.vk.api_url.startswith("<")

    def test_config_and_env_override_the_base(self, tmp_path, monkeypatch):
        from taskshed.config import load_config
        p = tmp_path / "config.yaml"
        p.write_text("platforms:\n  vk:\n    enabled: true\n"
                     '    group_token: "t"\n'
                     '    api_url: "https://api.vk.com/method/"\n',
                     encoding="utf-8")
        cfg = load_config(p)
        assert cfg.vk.api_url == "https://api.vk.com/method/"
        monkeypatch.setenv("TASKSHED_VK_API_URL", "https://api.vk.ru/method/")
        assert load_config(p).vk.api_url == "https://api.vk.ru/method/"

    def test_base_is_slash_normalised(self):
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t", api_url="https://api.vk.ru/method"),
                       app=None)
        assert ad._api_base == "https://api.vk.ru/method/"
