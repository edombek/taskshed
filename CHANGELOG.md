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
  retired); snooze asks for the duration (15 min / 1 h / 4 h / +1 day)
  instead of silently adding a fixed hour; the location-question frequency
  setting has an explicit label; docs/READMEs embed diagrams as GitHub-native
  ```mermaid blocks (vector) synced from the `.mmd` sources by
  `scripts/sync_diagram_blocks.py` (parity test in `tests/test_docs_diagrams.py`),
  3x PNGs kept as links for offline readers.
