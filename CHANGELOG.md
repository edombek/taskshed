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
