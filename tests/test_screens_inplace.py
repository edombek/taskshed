"""Menu sub-screens re-render in place: tapping an option rewrites the
screen's own message instead of stacking a fresh copy in the chat."""

from datetime import timedelta

from chatenv import PID, send

from taskshed.messaging.base import RawEvent


def screens_of(fake, needle: str) -> list:
    return [m for m in fake.sent if needle in m.text]


class TestWhereScreen:
    """📡 «Куда слать»: the ✓ moves on the same message."""

    async def test_mode_pick_edits_the_screen(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        vk_user, vk_acc = await store.register_account("vk", "999")
        await store.attach_account(vk_acc, user.id)
        await store.merge_user(vk_user.id, user.id)
        await app.handle(RawEvent("telegram", PID, callback="rem:where"))
        screens = screens_of(fake, "Куда присылать")
        assert len(screens) == 1
        scr = screens[0]
        labels = {cb: lbl for row in scr.buttons for lbl, cb in row}
        assert labels["rem:where:primary"].startswith("✓")
        await app.handle(RawEvent("telegram", PID, callback="rem:where:vk"))
        assert len(screens_of(fake, "Куда присылать")) == 1   # no fresh copy
        assert screens_of(fake, "Куда присылать")[0].id == scr.id
        labels = {cb: lbl for row in scr.buttons for lbl, cb in row}
        assert labels["rem:where:vk"].startswith("✓")
        assert not labels["rem:where:primary"].startswith("✓")
        assert (await store.get_settings(user.id)).notify_platforms == "vk"

    async def test_language_submenu_edits_in_place(self, env):
        app, _, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, callback="rem:lang"))
        screens = screens_of(fake, "Выберите язык")
        assert len(screens) == 1
        # re-opening the submenu must not pile up copies either
        await app.handle(RawEvent("telegram", PID, callback="rem:lang"))
        assert len(screens_of(fake, "Выберите язык")) == 1
        assert screens_of(fake, "Выберите язык")[0].id == screens[0].id


class TestStatsScreens:
    async def test_period_answer_becomes_the_report_in_place(self, env):
        app, _, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, text="/статистика"))
        ask = screens_of(fake, "За какой период")
        assert len(ask) == 1
        await app.handle(RawEvent("telegram", PID, callback="per:week"))
        # the question itself turns into the stats text + format buttons
        assert not screens_of(fake, "За какой период")
        scr = ask[0]
        cbs = {cb for row in scr.buttons for _, cb in row}
        assert {"fmt:chart:week", "fmt:csv:week", "fmt:xlsx:week",
                "fmt:hours:week", "hrs:7"} <= cbs
        assert "срабатываний не было" in scr.text

    async def test_hour_map_pager_reuses_its_screen(self, env):
        app, _, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, callback="hrs:0"))
        nav = [m for m in fake.sent
               if m.buttons and any(cb.startswith("hrs:")
                                    for _, cb in
                                    [b for row in m.buttons for b in row])]
        assert len(nav) == 1
        scr, text = nav[0], nav[0].text
        await app.handle(RawEvent("telegram", PID, callback="hrs:7"))
        # paging moved that same message to the next week, no second pager
        nav2 = [m for m in fake.sent
                if m.buttons and any(cb.startswith("hrs:")
                                     for _, cb in
                                     [b for row in m.buttons for b in row])]
        assert len(nav2) == 1 and nav2[0].id == scr.id
        assert scr.text != text


class TestEditorPicker:
    async def test_tab_switch_redraws_the_picker(self, env):
        app, _, fake = env
        await send(env, "Дом")
        await app.handle(RawEvent("telegram", PID, text="/изменить"))
        pick = screens_of(fake, "выберите задачу")
        assert len(pick) == 1
        await app.handle(RawEvent("telegram", PID, callback="etab:all"))
        again = screens_of(fake, "выберите задачу")
        assert len(again) == 1 and again[0].id == pick[0].id

    async def test_saved_field_edit_redraws_the_card(self, env):
        from datetime import UTC, datetime, time

        from taskshed.core.models import ScheduleType

        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        task = await store.create_task(user.id, (await store.list_places(user.id))[0].id,
                                       "Полка", ScheduleType.ONCE, {}, time(9, 0),
                                       deadline=datetime.now(UTC) + timedelta(days=2))
        await app.handle(RawEvent("telegram", PID, callback=f"edt:future:{task.id}"))
        card = screens_of(fake, "✏️ Полка")
        assert len(card) == 1
        await app.handle(RawEvent("telegram", PID, callback=f"edf:{task.id}:title"))
        await app.handle(RawEvent("telegram", PID, text="повесить полку"))
        # the saved card rewrites itself: no second card for the same task
        cards = [m for m in fake.sent if "Изменено" in m.text]
        assert len(cards) == 1 and cards[0].id == card[0].id
