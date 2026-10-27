# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- **Every task kind has a deadline and a warning lead.** Four kinds — one-off,
  every N days, by weekdays, every M of the month — all end with the same two
  answers: the deadline (a moment for a one-off, a `deadline_time` clock on each
  repeat day for the rest) and `notify_before_min`, "warn me this long before"
  (buttons from 5 minutes to a week, or typed: `30`, `2 часа`, `за 3 дня`).
  The per-task reminder clock (`reminder_time`) and the «без срока»/«сейчас»
  steps are gone.
- **Deadlines are the user's wall-clock, not the host's.** `tasks.deadline`,
  `tasks.start_at` and `tasks.deadline_time` are stored as naive wall-clock on
  the owner's calendar; the store resolves them through that user's current
  timezone on every read, and occurrence rows
  (`task_occurrences.due_at`/`notify_at = due_at − lead`) are real UTC instants
  derived from them. Changing a user's `tz` re-derives their whole open future
  (`Store.replan_open_occurrences`) while finished history is left alone.
  `alembic upgrade head` converts existing rows (old `reminder_at` →
  `notify_at`, plan columns shifted into the owner's wall-clock).
- **Two reminder triggers replace the times list**: a **morning digest** at
  `settings.morning_time` (once per calendar day) covering every open repeat
  whose deadline is today or ahead and whose notice moment is today or already
  passed, and an **every-`nag_interval_min` repeat** for open rows sitting
  inside `[notify_at, due_at)`; 💤 «Уведомить позже» pushes just that row's
  `notify_at` out. `remind_mode`/`remind_times`/`remind_interval_min`/
  `last_reminded_at` are replaced by `morning_time`, `nag_interval_min`,
  `last_morning_at` and `last_notified_at`.
- **A notice is built for the current place**: its tasks arrive as separate
  messages with buttons, the other places come as one brief line-up, and a
  single ✅ «Готово» (`fin:`) ends the status round and hands the main menu back.
- Settings screen: «🌅 Утреннее уведомление» and «🔁 Повторять каждые (мин)»
  replace the mode picker; the card of a task now shows «🕓 срок — HH:MM» and
  «🔔 предупреждать за …», and the editor edits the deadline clock and the lead.

- **One task, one message**: `/задачи` and reminder digests send a plain header
  plus a message per task owning its occurrence. Tapping ✅/⏭ re-renders that
  very message (mark on, keyboard off); ▶ leaves just [✅][⏭]; 💤 shows the
  duration picker inside the same message. No confirmation messages and no
  main-menu follow-up after every tap; unknown/forwarded copies fall back to
  the old reply+menu behavior.
- **The places screen is one live message**: typed names add places onto it,
  🗑 asks for confirmation in place ([✅ Да] [✖️ Не удалять]), deletions and
  re-adds redraw the same message, and «✔️ Готово» leaves (keyboard off).

### Removed
- **The `.env` convention**: nothing ever loaded it (`load_dotenv` was never
  called), so `.env.example` and the `python-dotenv` dependency are gone.
  Two config sources remain: `config.yaml` and `TASKSHED_*` environment
  variables of the process (env wins).

### Fixed
- **Stale copies of the fanned-out «Где вы сейчас?»**: the question is sent
  to every linked chat, but answering it in one chat made the others' ⏭
  «Не сейчас» answer with silence; now any copy hands the user back the main
  menu instead of a dead end.
- **Raw i18n keys could reach the chat**: an unparseable date/time answer
  replied with the literal `bad` (a non-catalog key, the hint was swallowed
  too). Now it answers «Не понял» + repeats the question as the hint. `t()`
  logs missing keys, and CI now scans every static `t("…")` call in the
  source against both catalogs, plus ru/en `{placeholder}` parity.
- **Buttonless dead ends**: typing a new place name at the location question
  suppressed the menu follow-up; «⏭ Не сейчас» after a bare /гдея, a tap on
  a deleted place's button, and `/отвязать` with a single account answered
  with silence; a rejected settings value dropped the awaited input instead
  of re-asking the same prompt in place.
- **The «Где вы сейчас?» question honors its own invitation**: with no places
  saved it said «add one by text», but typing a name only re-asked forever;
  now the typed name creates the place and answers the question. Unknown names
  once places exist still just re-ask — without a menu follow-up piling on top
  of the modal question.
- **`/старт`** now works as a Russian alias of `/start`.

### Changed
- **`BotApp` split by feature** (pure refactor, no behavior change): the
  1.8k-line `bot/app.py` now keeps only dispatch; each dialog lives in its
  own module — `wizard.py`, `editor.py`, `cards.py`, `digest.py`,
  `reports.py`, `settings.py`, `location.py`, `places.py`, with shared state
  in `base.py` and constants in `consts.py`. The 1.5k-line `test_handlers.py`
  split into feature test files over a shared `tests/chatenv.py` fixture.
- **Test stability**: the bare-time wizard test no longer depends on the
  wall-clock minute (the typed `HH.MM` could also parse as a date).

### Added
- **The hour map pages between weeks**: next to «⏱ График по часам» the stats
  screen now offers «След. неделя ➡️», and the picture itself comes with
  «⬅️ Пред. неделя» / «След. неделя ➡️». The week being drawn is named in the
  caption (04.11 — 10.11), and occurrences for a further week are materialized
  on demand — the plan shows up even when it lies past `stats_horizon_days`.
  An empty period no longer hides the format buttons, so the hour screen is
  still reachable when nothing happened in the window itself.
- **The hour map marks the present moment**: the hour the user is standing in
  is outlined on today's row and ticked at the exact minute, so a short-period
  chart shows where the finished part stops and the plan begins.
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

- **Planned squares were cut off at the right edge of the map**: the horizon was
  a bare number of days, so the grid drew the whole current week while only
  seven days ahead were planned — the second week showed empty cells even
  though the task repeated there. A week the horizon touches is now planned to
  its Sunday, and occurrences are materialized across it.

- **Every statistic is split by place**: the text summary grows a block per
  place («📍 Дом:» with its tasks under it), and charts, CSV and XLSX are sent
  one file per place, with the place named in the caption and in the file name.
  Nothing of «Дом» is averaged into «Работа» any more.
- **Hour map rows are weekdays of a sliding week**: three days back, today,
  three ahead, labelled Пн…Вс instead of dates (today stays green). The
  calendar Mon–Sun grid from the previous change is gone.

### Changed
- **The weekly hour map is one week long, always**: seven day rows from Monday
  to Sunday of the current week, instead of whatever the horizon and the oldest
  data happened to add up to (it was 13 rows last week). Longer periods still
  trim to their data and stay under 24 rows.
- **Charts label their axes, the hour map loses its legend**: the daily map is
  captioned «недели» / «дни недели», the hour map «часы» / «дни». The color
  legend is dropped from the hour map — the caption under the picture already
  explains every color, so the space is better spent naming the axes.

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
- **Repeating tasks were missing days on charts**: occurrences are only ever
  materialized from today forward, so anything outside that short horizon had
  no rows to draw and an every-3-days task lit three cells and stopped. The
  chart window now follows a single rule — forward to `stats_horizon_days`
  (the user's setting) and back no further than the days that really exist —
  and occurrences are materialized across that whole window first. Empty weeks
  are no longer padded in front of the map either.
- **Charts invented no false history**: a rule is never planned before the
  date written inside it. An every-N-days repeat is drawn back to its anchor
  (its earlier planned days appear once the window covers them), while weekday
  and monthly rules, which carry no date of their own, only ever show the days
  they were actually running.
