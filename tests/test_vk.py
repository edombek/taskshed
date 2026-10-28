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


class TestLongPollServer:
    """A bad group_id must not kill the loop: fallback + loud failure."""

    def _adapter(self, group_id: int, api_calls: list):
        async def fake_api(method, **params):
            api_calls.append(method)
            if method == "groups.getLongPollServer":
                return {}                      # vk replied an error → {}
            return {"server": "api.gim/1", "key": "k", "ts": 1}
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t", group_id=group_id), app=None)
        ad._api = fake_api
        return ad

    async def test_falls_back_to_messages_endpoint(self):
        calls: list = []
        ad = self._adapter(0, calls)
        assert await ad._server() == ("https://api.gim/1", "k", 1)
        assert calls == ["messages.getLongPollServer"]  # group_id=0: skipped

    async def test_falls_back_when_groups_endpoint_denies(self):
        calls: list = []
        ad = self._adapter(-123, calls)
        assert await ad._server() == ("https://api.gim/1", "k", 1)
        assert calls == ["groups.getLongPollServer", "messages.getLongPollServer"]

    async def test_clear_error_when_both_endpoints_fail(self):
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t", group_id=0), app=None)
        async def dead(method, **params):
            return {}
        ad._api = dead
        import pytest
        with pytest.raises(RuntimeError, match="check group_token/group_id"):
            await ad._server()


def test_adapters_importable():
    from taskshed.entrypoints.common import build  # noqa: F401
    from taskshed.messaging.telegram import TelegramAdapter  # noqa: F401
    from taskshed.messaging.vk import VkAdapter  # noqa: F401


class TestApiAddress:
    """VK moved to the vk.ru domain zone: base is the default, still movable."""

    def test_default_base_is_vk_ru(self):
        from taskshed.config import VkCfg
        from taskshed.messaging.vk import VkAdapter
        ad = VkAdapter(VkCfg(group_token="t", group_id=1), app=None)
        assert ad._api_base == "https://api.vk.ru/method/"

    def test_loader_default_is_the_value_not_a_slot_descriptor(self, tmp_path):
        # a slots dataclass hands back a descriptor for the class attribute,
        # so the loader must not build the URL from VkCfg.api_url
        from taskshed.config import load_config
        p = tmp_path / "config.yaml"
        p.write_text('platforms:\n  vk:\n    enabled: true\n'
                     '    group_token: "t"\n    group_id: 7\n', encoding="utf-8")
        cfg = load_config(p)
        assert cfg.vk.api_url == "https://api.vk.ru/method/"
        assert not cfg.vk.api_url.startswith("<")

    def test_config_and_env_override_the_base(self, tmp_path, monkeypatch):
        from taskshed.config import load_config
        p = tmp_path / "config.yaml"
        p.write_text("platforms:\n  vk:\n    enabled: true\n"
                     '    group_token: "t"\n    group_id: 7\n'
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
