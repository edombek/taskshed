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
