# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- (Stage 0) Repository bootstrap: bilingual README, docs skeleton, Mermaid
  diagrams, CI workflow (ruff + pytest), example config and env files.
- (Stage 1) Core domain (schedule rules, occurrence materialization, overdue
  handling), SQLAlchemy async storage with `Store` repository facade, Alembic
  initial migration, unit tests for scheduling and repositories.
- (Stage 2) Messenger port + `FakeMessenger`, Telegram adapter (Telethon bot
  mode, MTProxy), i18n ru/en with parity test, `BotApp` chat dispatcher
  (commands, task wizard, buttons, `/link`), config loader (YAML + env).
- (Stage 3) Reminder scheduler tick with silence rules, stale-location ask,
  digest fan-out to linked accounts, per-user reminder settings.
- (Stage 4) Statistics: aggregations, text report, CSV/XLSX documents,
  matplotlib PNG charts (daily bars, calendar heatmap, top tasks, trend).
- (Stage 5) VK community bot adapter (Bot Long Poll, inline keyboards,
  photo/document upload, http/socks5 proxy); account linking with
  `/link`/`/unlink` and data merge via `link_audit`.
- (Stage 6) `run_telegram` / `run_vk` / `run_all` entrypoints; reminder
  fan-out to linked accounts with `notify_platforms=all|primary`
  (accounts.last_seen); Alembic migration for `accounts.last_seen`.
- (Stage 8) UX polish: inline command menu under `/start` and `/help`;
  button-driven date/time entry in the task wizard (today/tomorrow/… then
  time presets, typed input still accepted); past-deadline guard;
  URL defanging in task titles/place names + `link_preview=False`
  (anti-spam-ban hygiene); statistics chart now also renders a
  GitHub-style activity heatmap with per-day missed/planned sub-markers;
  command menu re-attached to completion replies (task saved, done,
  snooze, place set, delete, link/unlink); heatmap window always covers
  at least the last month even when records are sparse; scheduler now
  honors each task's own reminder time (`reminder_at`, incl. snooze) in
  addition to the global digest moment; statistics reports/heatmap keep
  planned (future) occurrences instead of filtering them out; command menu
  now attached to every plain command reply by default (and below task
  lists); Mermaid diagrams rendered to committed PNGs
  (`scripts/render_diagrams.sh`) and embedded in READMEs and the bilingual
  architecture/linking docs; the menu now arrives as a short
  "Would you like to do anything else?" follow-up message after a *completed*
  command (not mid-wizard, not under replies that already carry input
  buttons); statistics are interactive — period picked via buttons first,
  then text with format buttons (activity heatmap only, daily-bar chart
  retired; the menu follow-up arrives after the chart/document too); snooze asks for the duration (15 min / 1 h / 4 h / +1 day)
  instead of silently adding a fixed hour; the location-question frequency
  setting has an explicit label; docs/READMEs embed diagrams as GitHub-native
  ```mermaid blocks (vector) synced from the `.mmd` sources by
  `scripts/sync_diagram_blocks.py` (parity test in `tests/test_docs_diagrams.py`),
  3x PNGs kept as links for offline readers; submenu screens (reminder
  settings, places, language, stats pickers) carry an ⬅️ Назад button that
  returns to the main menu instead of the "anything else?" follow-up; the
  wizard's reminder time is picked via buttons (the "press Enter for the
  default" was impossible in Telegram); time input accepts `11 55`, `11.55`
  and `11-55` as well as `11:55`; every wizard step carries an ⬅️ Назад
  button to redo the previous answer; dates accept `15.11.2026`, `15/11/26`,
  `15 11 2026`, `15.11` (rolls forward), `завтра`/`tomorrow`, and a
  date-only answer proceeds to time buttons; new **⏭ Пропустить** button in
  task lists/digests closes today's occurrence without a missed penalty —
  skipped shows up in the text report, CSV/XLSX columns and an orange
  heatmap inset, and never counts against the completion percent; every
  prompt that waits for input (Где я?, place adding, reminder setting
  values, the scheduler's stale-location question) carries a ⬅️ Назад
  button that also cancels the awaited input; the buttons "Времена/
  Интервал/Тихие часы/Частота опроса/Часовой пояс" now answer with a
  concrete format hint (they used to swallow the tap), and setting changes
  re-render the settings message in place instead of posting a copy;
  reminder timing is now one process — a single «⏰ Когда напоминать»
  button offers «в определённые времена дня / каждые N минут», the mode
  and its value are shown in Russian on the settings screen («Режим:
  по временам (09:00 13:00)»), and the prompts explain what the values
  mean; heatmap weekday/month labels are localized (Пн/Ср/Пт, янв…дек).
- **✏️ Task editor** (`/изменить`): every task opens as a card whose buttons edit
  each item — title, place, repeat rule, work window (start + ready-by), reminder
  time — plus «🗑 Удалить задачу» behind a confirmation. Tabs switch between
  🔜 upcoming, 🔁 recurring, 📚 past and 📋 all tasks, so history is one tap away.
  Changing a schedule drops the repeats that were never worked and re-materializes
  them from the new rule; finished/missed history stays.
- **`PRAGMA foreign_keys=ON`** for SQLite: `ON DELETE CASCADE` now really fires, so
  deleting a task no longer leaves its occurrences and completions behind.
- **Task saved = task shown**: after the wizard the bot answers with a card of
  what was created — title, place, the work window («с 26.10 10:00 до 26.10 23:59»)
  or the repeat rule, the reminder moment and the first date it will fire on.
- **Hour grid marks work windows**: a task stretched over time keeps every hour
  between its start and its ready-by moment busy in the hourly map (a window
  longer than a day marks its boundaries instead of painting a whole row), and
  the daily map now also marks the day the work starts, not only its deadline.
- **Charts are drawn sharp**: resolution comes out at up to 300 dpi, sized to
  Telegram's photo pixel budget (a 5-week map used to be a 543 px stamp).
- **Activity charts, further ahead and finer grained**: the daily heatmap draws
  the whole window up to a per-user horizon — the days ahead stay on the picture
  even while nothing is planned in them, and today's cell is outlined as the
  past/future boundary (`user_settings.stats_horizon_days`, default one week,
  editable via ⚙️ Настройки → «📊 График вперёд»). The week period also offers an
  **hour-level** map (day rows × 24 hour columns) that uses the same statuses as
  the daily one — done, missed, skipped and still-planned occurrences land in the
  hour they are worked in (`start_at`, else the reminder time), so short periods
  show what was missed and what is coming, not only what was finished.
- **🔔 Напоминания → ⚙️ Настройки**: one screen now holds everything a user
  configures — reminders (mode and value), quiet hours, location validity,
  time zone, chart horizon, language and the **platform linking status**
  (`Telegram ✓, VK ✗`, one-time code generation, «🗑 Отвязать VK» rows);
  `/настройки` (`/settings`) replaces `/напоминания` (still an alias), and
  switching language re-renders that same screen.
- **Tasks stretched over time**: a one-off task is no longer a single instant —
  the wizard asks «Когда приступить?» and «До какого времени закончить?»
  (`tasks.start_at` + `tasks.deadline`, Alembic `f2a6b83c14d9`); lists and
  digests show the whole window («с … до …») and offer **▶ В работе**, which
  marks the occurrence `in_progress`: the deadline stays, but started work is
  never flipped to `missed` and keeps showing until done or skipped.
- **Stale keyboards are disarmed**: as soon as a newer answer arrives, every
  older inline keyboard in that chat is edited down to plain text, so only
  the current screen keeps buttons. Port-level convention:
  `IMessenger.edit_buttons(account, id, text, [])` strips the keyboard —
  Telegram sends `ReplyKeyboardHide` (an empty `ReplyInlineMarkup` is
  rejected by the API; verified live), VK cannot edit and skips it.

- **One shared date/time reader** (`bot/timeparse.py::parse_when`): every step
  that expects a moment — start, ready-by, first repeat, reminder time, plus the
  settings inputs — now reads from the same text: `13.00`, `13`, `11-55`,
  `15.11`, `15 11 2026`, `завтра 18`, `через 3 дня`. It reports *which kind* of
  value it read (only a time / only a day / both) and whether it had to guess: a
  missing year, or `9.5` that may be 9 May or 09:05 — ambiguous input carries
  both readings so the step that asked can pick the one it needs.
- **The bot asks before believing itself**: whenever the parser filled a hole,
  the answer comes back as «Я понял так: 28.10.2026 13:00. Верно?» with
  ✅ Да / ✖ Нет, другое — ✖ puts the same question back. Values that cannot work
  (ready-by earlier than the start, a date in the past) are refused *before*
  the card, not after.
- **«▶ Сейчас» on the "when do you start?" step**: work that has already begun
  no longer needs a typed minute.

- **«✖ Без срока — просто напомнить» on the ready-by step**: a one-off task can
  stay a moment instead of an event stretched over the rest of the day. The card
  then reads «🕐 27.10 13:00», and the hour map lights that single hour only —
  no band from the start up to midnight (`stats.occurrence_hours` now takes the
  task into account, not just the occurrence).

- **«Где вы сейчас?» no longer hijacks the conversation**: when the location is
  stale the scheduler used to drop its question into the middle of an open
  dialog, and the answer went to whichever context grabbed the text first — a
  place name typed for the task wizard became a location change, or vice versa.
  The question is modal now: the open dialog (wizard step, or the intent to
  show a task list) is parked, the answer — button *or* typed place name — sets
  the location, and the dialog is replayed exactly where it stopped. A new
  **⏭ Не сейчас** button returns to the dialog without choosing a place, and
  ⬅️ Назад cancels the question but keeps the dialog.

### Fixed
- **The main menu comes back after every action**: the task-editor card and the
  places screen used to leave a dialog open in memory, so the *next* answer
  (Готово / Пропущено / ⏰ Отложено, digests) arrived with its old buttons
  stripped and no new keyboard — nothing was clickable. Only an actual wizard
  step now suppresses the «Хотите сделать что-нибудь ещё?» follow-up.
- **Deleting a place asks first**: it silently removed every task of that place
  together with its whole history (now that SQLite cascades really run). The
  confirmation names how many tasks go with it.
- **Charts are sized by their cells, not by a fixed frame**: figure size is
  derived from the axis spans, so one cell gets the same pixels on both axes
  (squares are square) and the picture grows with the number of cells — a
  5-week map is ~509 px wide instead of 1226, a year map 2636. Narrow maps skip
  the legend, the caption already names the colors.
- **A typed place name is a real answer**: the task wizard and the editor's
  «Место» field accepted only the buttons below the question. With no places
  saved the bot said «добавьте текстом, например: Дом» and then quietly swallowed
  what was typed — the wizard just sat there. Typing now finds the place (case
  does not matter) or creates a new one, and the question stays visible even
  when the list is empty.
- **A place deleted under an open wizard** used to leave the wizard about to
  save a task pointing at a place that no longer exists (an FK error now that
  cascades work). The step is simply asked again, then the wizard finishes where
  it stopped.
- **A typed ready-by time was refused**: «До какого времени нужно закончить?»
  read a lone `13.00` as a date (month 00) and answered «Не понял», and the same
  happened on the start step. One reader now handles both, in every step.
- **Repeating tasks were missing days on longer charts**: a month chart looked
  only `stats_horizon_days` (a week by default) ahead, so occurrences further
  out had never been materialized and the map lit just the first days. A chart
  now looks as far ahead as the period it draws (week → 7 days, month → 30,
  year → 365), never less than the user's horizon, and materializes that whole
  window first — an every-3-days task is drawn on every planned day of the
  month, not on three of them.
