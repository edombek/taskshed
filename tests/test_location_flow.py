"""Onboarding, /гдея and the modal place question, end-to-end."""

from datetime import UTC, datetime

from chatenv import PID, place_id_of, prev_buttons, send

from taskshed.core.models import ScheduleType


class TestOnboarding:
    async def test_start_and_first_place(self, env):
        app, store, fake = env
        f = await send(env, "/start")
        assert "Привет" in f.texts()[0]
        user = await store.get_user_by_account("telegram", PID)
        f = await send(env, "Дом")
        assert "добавлено" in f.texts()[0]
        assert len(await store.list_places(user.id)) == 1

    async def test_cyrillic_start_alias(self, env):
        f = await send(env, "/старт")
        assert "Привет" in f.texts()[0]

    async def test_where_question_with_no_places_honors_typed_name(self, env):
        # the question says "add one by text": typing it must not loop
        _, store, _ = env
        f = await send(env, "/гдея")
        assert "Где вы сейчас?" in f.texts()[0]
        f = await send(env, "Дом")
        assert "Текущее место: Дом" in f.texts()[0]
        user = await store.get_user_by_account("telegram", PID)
        loc = await store.get_location(user.id)
        assert loc and loc[0].name == "Дом"
        # the answer ends with the menu follow-up: buttons must stay reachable
        flat = {cb for m in f.sent for row in (m.buttons or []) for _, cb in row}
        assert "cmd:task" in flat

    async def test_unknown_name_at_question_re_asks_without_menu(self, env):
        await send(env, "Дом")
        await send(env, "/гдея")
        f = await send(env, "Луна")
        assert "Где вы сейчас?" in f.texts()[0]
        assert not any("Хотите сделать" in m for m in f.texts())
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        assert len(await store.list_places(user.id)) == 1  # «Луна» not created

    async def test_not_now_from_a_plain_where_offers_the_menu(self, env):
        # ⏭ with nothing parked behind the question must not leave a
        # buttonless dead end
        await send(env, "Дом")
        await send(env, "/гдея")
        f = await send(env, cb="loc:later")
        assert {"cmd:where", "cmd:tasks"} <= {cb for row in f.last_buttons()
                                              for _, cb in row}

    async def test_stale_not_now_copy_still_returns_the_menu(self, env):
        # a fanned-out question lives in several chats; after one chat answered,
        # the other chat's ⏭ must still land somewhere, not into silence
        await send(env, "Дом")
        await send(env, "/гдея")
        f = await send(env, cb="loc:later")
        assert "ещё" in f.texts()[0]
        f = await send(env, cb="loc:later")  # the copy in the other chat
        assert "ещё" in f.texts()[0]

    async def test_tap_of_a_deleted_place_button_says_so(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await store.delete_place(user.id, home)
        f = await send(env, cb=f"place:{home}")
        assert any("удалено" in m for m in f.texts())

    async def test_add_second_place_via_places_command(self, env):
        await send(env, "Дом")
        await send(env, "/места")
        f = await send(env, "Работа")
        assert "добавлено" in f.texts()[0]

    async def test_language_switch(self, env):
        f = await send(env, "/lang")
        assert f.last_buttons()[0][1][1] == "lang:en"
        f = await send(env, cb="lang:en")
        assert f.texts()[0] == "Language: English"
        _, store, _ = env
        user = await store.get_user_by_account("telegram", PID)
        assert user.language == "en"


class TestWhereAndTasks:
    async def test_where_sets_location(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        f = await send(env, "/гдея")
        assert f.last_buttons()[0][0][1] == f"place:{pid_home}"
        assert any(cb == "menu:back" for row in f.last_buttons() for _, cb in row)
        f = await send(env, cb=f"place:{pid_home}")
        assert "Текущее место: Дом" in f.texts()[0]

    async def test_task_list_with_done_button(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(user.id, pid_home, "Полить цветы",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": today.isoformat()},
                                       datetime.now(UTC).timetz().replace(second=0,
                                                                          microsecond=0))
        await store.materialize_occurrences(task, today, 1, "UTC")
        await store.set_location(user.id, pid_home)
        f = await send(env, "/задачи")
        assert any("Полить цветы" in m for m in f.texts())
        occ_cb = prev_buttons(f)[0][0][1]
        assert occ_cb.startswith("done:")
        occ_id = int(occ_cb.split(":")[1])
        f = await send(env, cb=occ_cb)
        assert "Готово: «Полить цветы»" in f.texts()[0]
        occ, _ = await store.get_occurrence(occ_id)
        assert occ.status == "done"

    async def test_tasks_asks_where_when_no_location(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        f = await send(env, "/задачи")
        assert f.last_buttons()[0][0][1] == f"place:{pid_home}"
        f = await send(env, cb=f"place:{pid_home}")
        # the tasks list followed the location answer (the menu follow-up after)
        joined = " | ".join(f.texts())
        assert "нет" in joined.lower() or "Задачи" in joined


class TestLocationQuestionIsModal:
    """«Где вы сейчас?» waits: the interrupted dialog is replayed after it."""

    async def _wizard_awaiting_place(self, env):
        app, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await send(env, "/задача")
        await send(env, "Полка")           # the wizard sits on its place step
        app.park_for_location(user.id)     # exactly what the scheduler does
        return app, store, user, home

    async def test_tapping_a_place_resumes_the_wizard(self, env):
        app, store, user, home = await self._wizard_awaiting_place(env)
        f = await send(env, cb=f"place:{home}")
        assert "Текущее место" in f.texts()[0]
        assert app.wizards[user.id]["step"] == "place", "the dialog came back"
        await send(env, cb=f"place:{home}")   # this one belongs to the wizard
        f = await send(env, cb="sched:every_n_days")
        assert "сколько дней" in f.texts()[0]

    async def test_typing_a_place_name_answers_it(self, env):
        app, store, user, home = await self._wizard_awaiting_place(env)
        f = await send(env, "дом")           # any case
        assert "Текущее место" in f.texts()[0]
        assert (await store.get_location(user.id))[0].name == "Дом"
        assert app.wizards[user.id]["step"] == "place"

    async def test_nonsense_does_not_leak_into_the_wizard(self, env):
        app, store, user, home = await self._wizard_awaiting_place(env)
        f = await send(env, "ну-ка")
        assert "Где вы сейчас?" in f.texts()[0]      # asked again, not eaten
        assert await store.get_location(user.id) is None
        # the dialog is still parked, untouched, behind the question
        assert app._await_loc[user.id]["wizard"]["step"] == "place"

    async def test_not_now_goes_straight_back_to_the_dialog(self, env):
        app, store, user, home = await self._wizard_awaiting_place(env)
        f = await send(env, cb="loc:later")
        assert "местности" in f.texts()[0]           # the wizard's own question
        assert await store.get_location(user.id) is None
        await send(env, cb=f"place:{home}")
        f = await send(env, cb="sched:every_n_days")
        assert "сколько дней" in f.texts()[0]
