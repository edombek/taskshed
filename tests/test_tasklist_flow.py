"""The task notice and its buttons: done, skip, в работе, «уведомить позже»."""

from datetime import UTC, datetime, time, timedelta

from chatenv import PID, place_id_of, prev_buttons, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import FakeMessenger, RawEvent


async def _daily_task(env, title="T", clock=time(9, 0), lead=60):
    """One place «Дом», current, with an every-day repeat due today."""
    _, store, _ = env
    await send(env, "Дом")
    user = await store.get_user_by_account("telegram", PID)
    p = (await store.list_places(user.id))[0]
    await store.set_location(user.id, p.id)
    anchor = datetime.now(UTC).date().isoformat()
    task = await store.create_task(user.id, p.id, title, ScheduleType.EVERY_N_DAYS,
                                   {"n": 1, "anchor": anchor}, clock,
                                   notify_before_min=lead)
    await store.materialize_occurrences(task, datetime.now(UTC).date(), 1)
    occs = await store.occurrences_today(user.id, datetime.now(UTC).date())
    return store, occs[0][0].id


class TestSnoozePicker:
    """💤 «Уведомить позже» must ask *how long*, not pick a fixed hour."""

    async def test_picker_offers_durations(self, env):
        _, occ_id = await _daily_task(env)
        f = await send(env, cb=f"snooze:{occ_id}")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert f"snz:{occ_id}:15" in flat
        assert f"snz:{occ_id}:1440" in flat
        # the picker itself is the interactive reply: no menu spam below it
        assert len(f.sent) == 1

    async def test_choice_moves_the_notice_moment(self, env):
        store, occ_id = await _daily_task(env)
        f = await send(env, cb=f"snz:{occ_id}:15")
        assert any("15 мин" in t for t in f.texts())
        after = (await store.get_occurrence(occ_id))[0].notify_at
        # 💤 sets the notice moment to now+15 min, whatever it was before — so
        # the check is against the clock, not against a value that depends on
        # what hour of the day the test happens to run at
        target = datetime.now(UTC) + timedelta(minutes=15)
        assert abs((after - target).total_seconds()) < 90
        # after a *completed* action the menu follow-up returns
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat


class TestEditlessPlatform:
    """A platform whose edits always fail (hypothetical — VK and Telegram
    both edit now): the tap answers as a fresh message, and since that reply
    cannot disarm the footer, the round ends right there — confirmation plus
    the main menu, one exit, never a menu waiting behind a dead «Готово»."""

    async def test_done_tap_falls_back_to_reply_plus_menu(self, env):
        app, store, fake = env
        _, occ_id = await _daily_task(env)
        noedit = FakeMessenger("telegram")

        async def no_edit(*a, **k):
            return False
        noedit.edit_buttons = no_edit
        app.messengers["telegram"] = noedit

        await send(env, "/задачи")                     # notice with «Готово»
        n = len(noedit.sent)
        await send(env, cb=f"done:{occ_id}")
        added = noedit.sent[n:]
        assert len(added) == 2                         # confirmation + menu
        assert not added[0].buttons                    # the confirmation
        assert any(cb == "cmd:tasks" for _, cb in added[1].buttons[0])

    async def test_stale_tap_outside_a_notice_still_gets_the_menu(self, env):
        app, store, fake = env
        store_, occ_id = await _daily_task(env)
        noedit = FakeMessenger("telegram")

        async def no_edit(*a, **k):
            return False
        noedit.edit_buttons = no_edit
        app.messengers["telegram"] = noedit

        # tap straight away: no notice open → the menu follow-up is the exit
        f = await send(env, cb=f"done:{occ_id}")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat


class TestSkipOccurrence:
    async def test_skip_closes_occurrence_without_missed(self, env):
        store, occ_id = await _daily_task(env)
        f = await send(env, "/задачи")
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert f"skip:{occ_id}" in flat
        f = await send(env, cb=f"skip:{occ_id}")
        assert f.find("Пропущено")
        occ, _ = await store.get_occurrence(occ_id)
        assert occ.status == "skipped"
        # gone from today's list, and the background overdue pass ignores it
        user = await store.get_user_by_account("telegram", PID)
        assert not await store.occurrences_today(user.id, datetime.now(UTC).date())
        await store.mark_overdue(datetime.now(UTC) + timedelta(days=1))
        assert (await store.get_occurrence(occ_id))[0].status == "skipped"

    async def test_skipped_shows_in_stats_not_as_missed(self, env):
        store, occ_id = await _daily_task(env)
        await send(env, cb=f"skip:{occ_id}")
        f = await send(env, "/статистика всё")
        assert "пропущено 1" in f.texts()[-1]
        assert "просрочено 0" in f.texts()[-1]


class TestWorkInProgress:
    """A one-off with a work window offers «▶ В работе» and stays open."""

    async def _stretched_task(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        task = await store.create_task(
            user.id, pid, "Полка", ScheduleType.ONCE, {}, time(9, 0),
            deadline=datetime.now(UTC) - timedelta(hours=1),
            start_at=datetime.now(UTC) - timedelta(hours=3),
        )
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 3)
        await store.set_location(user.id, pid)
        return app, store, task

    async def test_list_offers_wip_and_shows_the_window(self, env):
        await self._stretched_task(env)
        f = await send(env, "/задачи")
        assert any("с " in m and " до " in m for m in f.texts())
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert any(cb.startswith("wip:") for cb in flat)
        assert any(cb.startswith("done:") for cb in flat)

    async def test_wip_keeps_the_row_open_and_drops_itself(self, env):
        _, store, _ = await self._stretched_task(env)
        f = await send(env, "/задачи")
        occ_id = next(int(cb.split(":")[1]) for row in prev_buttons(f)
                      for _, cb in row if cb.startswith("wip:"))
        f = await send(env, cb=f"wip:{occ_id}")
        assert "В работе: «Полка»" in f.texts()[0]
        # the overdue pass does not turn started work into missed
        assert await store.mark_overdue(datetime.now(UTC)) == 0
        f = await send(env, "/задачи")
        assert any("▶в работе" in m for m in f.texts())
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert not any(cb.startswith("wip:") for cb in flat)
        assert not any(cb.startswith("snooze:") for cb in flat)
        assert f"done:{occ_id}" in flat and f"skip:{occ_id}" in flat

    async def test_notice_offers_wip_too(self, env):
        app, store, _task = await self._stretched_task(env)
        fake = env[2]
        user = await store.get_user_by_account("telegram", PID)
        settings = await store.get_settings(user.id)
        place = (await store.get_location(user.id))[0]
        rows = await store.rows_to_announce(user.id)
        assert rows and await app.send_notification(user.id, settings, place, rows)
        assert any(any(cb.startswith("wip:") for _, cb in row)
                   for m in fake.sent for row in m.buttons)


class TestOneMessagePerTask:
    """One task = one message. A button tap re-renders that very message:
    closed task loses its keyboard, no new messages, no menu follow-up."""

    async def _list_with_two(self, env):
        _, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        for title in ("A", "B"):
            task = await store.create_task(user.id, p.id, title,
                                           ScheduleType.EVERY_N_DAYS,
                                           {"n": 1, "anchor": anchor}, time(9, 0))
            await store.materialize_occurrences(task, datetime.now(UTC).date(), 1)
        await send(env, "/задачи")
        assert fake.sent[0].buttons == []  # header: a plain text message
        return fake.sent[1], fake.sent[2]  # the two task messages

    async def _tap(self, env, cb, msg_id):
        """Tap without the send() helper: it would wipe the edited message."""
        app, _, fake = env
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", PID, callback=cb, message_id=msg_id))
        return fake.sent[before:]

    async def test_done_disarms_only_its_own_message(self, env):
        a, b = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("done:"))
        assert not await self._tap(env, f"done:{d1}", a.id)  # nothing new sent
        assert "✅" in a.text and a.buttons == []
        # the neighbour task keeps its keyboard untouched
        assert any(cb.startswith("done:") for row in b.buttons for _, cb in row)

    async def test_wip_swaps_the_keyboard_in_place(self, env):
        a, _ = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("wip:"))
        assert not await self._tap(env, f"wip:{d1}", a.id)
        assert "▶в работе" in a.text
        flat = [cb for row in a.buttons for _, cb in row]
        assert f"done:{d1}" in flat and f"skip:{d1}" in flat
        assert not any(cb.startswith("wip:") for cb in flat)

    async def test_snooze_picker_lives_in_the_same_message(self, env):
        a, _ = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("snooze:"))
        assert not await self._tap(env, f"snooze:{d1}", a.id)
        flat = [cb for row in a.buttons for _, cb in row]
        assert f"snz:{d1}:15" in flat and f"snzb:{d1}" in flat
        assert not await self._tap(env, f"snz:{d1}:15", a.id)
        assert "💤" in a.text  # the note landed on the task's own message
        flat = [cb for row in a.buttons for _, cb in row]
        assert f"done:{d1}" in flat  # still open, buttons are back

    async def test_unknown_message_falls_back_to_the_old_reply(self, env):
        # unknown message id (forwarded copy, stale keyboard after restart):
        # the fallback is a plain reply — and since that reply disarms the
        # «Готово» footer on the way, the main menu follows as the exit
        a, _ = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("done:"))
        f = await send(env, cb=f"done:{d1}", msg="telegram-999")
        assert any("Готово" in t for t in f.texts())
        menu = f.sent[-1]
        assert menu.buttons and any(cb == "cmd:tasks" for _, cb in menu.buttons[0])


class TestNoticeScreen:
    """The notice: header, one message per task, and a «Готово» to end the round."""

    async def _two_tasks(self, env, titles=("A", "B")):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await store.set_location(user.id, pid)
        anchor = datetime.now(UTC).date().isoformat()
        for title in titles:
            task = await store.create_task(user.id, pid, title,
                                           ScheduleType.EVERY_N_DAYS,
                                           {"n": 1, "anchor": anchor}, time(9, 0),
                                           notify_before_min=120)
            await store.materialize_occurrences(task, datetime.now(UTC).date(), 1)
        return app, store, fake

    async def test_notice_is_a_header_plus_task_messages_plus_done(self, env):
        app, store, fake = await self._two_tasks(env)
        user = await store.get_user_by_account("telegram", PID)
        settings = await store.get_settings(user.id)
        place = (await store.get_location(user.id))[0]
        rows = await store.rows_to_announce(settings.user_id)
        base = len(fake.sent)
        assert await app.send_notification(settings.user_id, settings, place, rows)
        sent = fake.sent[base:]
        assert sent[0].buttons == [] and "«Дом»" in sent[0].text
        assert len(sent) == 4            # header + two tasks + «Готово»
        assert any(cb == "fin:" for _, cb in sent[-1].buttons[-1])
        msg = sent[1]
        occ_id = next(int(cb.split(":")[1]) for row in msg.buttons
                      for _, cb in row if cb.startswith("done:"))
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", PID, callback=f"done:{occ_id}",
                                  message_id=msg.id))
        assert len(fake.sent) == before  # the task message absorbed the tap
        assert "✅" in msg.text and msg.buttons == []

    async def test_finish_ends_the_round_with_no_extra_taps(self, env):
        app, store, fake = await self._two_tasks(env)
        user = await store.get_user_by_account("telegram", PID)
        settings = await store.get_settings(user.id)
        place = (await store.get_location(user.id))[0]
        rows = await store.rows_to_announce(user.id)
        assert await app.send_notification(user.id, settings, place, rows)
        fin = next(m for m in fake.sent
                   if any(cb == "fin:" for row in m.buttons for _, cb in row))
        f = await send(env, cb="fin:", msg=fin.id)
        assert any(any(cb.startswith("cmd:") for row in m.buttons for _, cb in row)
                   for m in f.sent)
        assert not app._fin_msgs         # the screen is closed


class TestAllTasksSummary:
    """🌍 «Все задачи»: открытые повторы изо всех местным одним экраном."""

    async def _two_places(self, env, n=1):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        work = await store.add_place(user.id, "Работа")
        await store.set_location(user.id, home)
        anchor = datetime.now(UTC).date().isoformat()
        for pid, title in ((home, "Полить"), (work.id, "Отчёт")):
            task = await store.create_task(
                user.id, pid, title, ScheduleType.EVERY_N_DAYS,
                # a late clock: today's repeat is open whatever the hour
                {"n": n, "anchor": anchor}, time(23, 0), notify_before_min=120)
            await store.materialize_occurrences(task,
                                                datetime.now(UTC).date(), 1)
        return app, store, fake, user

    async def _footer_of(self, f):
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["fin:", "tas:all"]

    async def test_notice_footer_offers_the_all_places_view(self, env):
        await self._two_places(env)
        f = await send(env, "/задачи")
        await self._footer_of(f)
        f = await send(env, cb="tas:all")
        text = f.texts()[0]
        assert "Полить" in text and "Отчёт" in text   # both places on one screen
        assert "Дом" in text and "Работа" in text     # grouped under place heads
        await self._footer_of(f)                      # and it exits the same way
        f = await send(env, cb="fin:")
        assert any(cb.startswith("cmd:")
                   for row in f.last_buttons() for _, cb in row)

    async def test_summary_lists_only_open_rows(self, env):
        # every-10-days keeps each task to one row inside the horizon
        _, store, _, user = await self._two_places(env, n=10)
        today = datetime.now(UTC).date()
        rows = await store.occurrences_today(user.id, today)
        done = [o for o, tk in rows if tk.title == "Полить"][0]
        await store.mark_done(done.id, "telegram")
        f = await send(env, cb="tas:all")
        assert "Полить" not in f.texts()[0]   # the finished row is gone for good
        assert "Отчёт" in f.texts()[0]

    async def test_notice_without_tappable_tasks_still_closes(self, env):
        # the current place has nothing today: the notice is header + brief
        # lines, and it must still carry «Готово» — without it the user has
        # no way out of the screen
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        work = await store.add_place(user.id, "Работа")
        await store.set_location(user.id, work.id)
        task = await store.create_task(
            user.id, home, "Полить", ScheduleType.EVERY_N_DAYS,
            {"n": 1, "anchor": datetime.now(UTC).date().isoformat()},
            time(23, 0), notify_before_min=120)
        await store.materialize_occurrences(task,
                                            datetime.now(UTC).date(), 1)
        f = await send(env, "/задачи")
        await self._footer_of(f)
        texts = "\n".join(m.text for m in f.sent)
        assert "Полить" in texts and "Работа" in texts  # current place is «Работа»

    async def test_empty_today_offers_the_all_button_and_the_menu(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await store.set_location(user.id,
                                 (await store.list_places(user.id))[0].id)
        f = await send(env, "/задачи")
        assert "tas:all" in [cb for row in prev_buttons(f) for _, cb in row]
        assert any(cb.startswith("cmd:")
                   for row in f.last_buttons() for _, cb in row)


class TestNoticeExitAfterLostMessage:
    """A tap on a task whose message the bot no longer owns (restart, VK id
    mismatch): the fallback reply disarms the «Готово» footer on the way —
    so the round must end and the main menu must follow, never silence."""

    async def test_done_fallback_brings_the_menu_back(self, env):
        app, store, fake = env
        _, occ = await _daily_task(env)
        await send(env, "/задачи")  # notice: task msg + «Готово» footer
        assert (1, 1) in app._fin_msgs
        app._tl_msgs.clear()  # pretend the live task message is gone
        await app.handle(RawEvent("telegram", PID, callback=f"done:{occ}",
                                  message_id="4242"))
        assert (await store.get_occurrence(occ))[0].status == "done"
        assert not app._fin_msgs  # the dead footer is forgotten
        menu = fake.sent[-1]
        assert menu.buttons and any(cb == "cmd:tasks"
                                    for _, cb in menu.buttons[0])
