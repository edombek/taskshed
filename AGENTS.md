# AGENTS.md

## State of the repo

Greenfield as of 2026-10-25: no code committed yet. Do not assume any files,
commands, or layout exist — verify before referencing them. The agreed design
contract is below; keep changes consistent with it until `docs/PLAN.md` (once
created) supersedes it.

## Project intent

Multi-platform task-reminder chat bot (Telegram + VK) that segments tasks by
the user's current location ("home", "work"). Python 3.14 (`python3`), asyncio.

## Fixed design decisions (agreed with the owner — don't relitigate)

- Hexagonal architecture: platform-independent `core` (domain, scheduling,
  stats) behind a messenger port; adapters: Telegram via **Telethon**
  (`start_bot`, MTProto/MTProxy support), VK via Bot Long Poll.
- Location is set **manually** by the user (buttons), never via GPS.
- Multi-user from day one. `users` (human, platform-agnostic) and `accounts`
  (platform + chat id, `UNIQUE(platform, platform_user_id)`) are separate
  tables; all task/place/setting rows reference `users.id`.
- Account linking across platforms via one-time codes (~10 min TTL, table
  `link_codes`); reminders fan out to all linked accounts unless
  `notify_platforms=primary`.
- DB: SQLAlchemy 2.x async + aiosqlite, **SQLite by default**, PostgreSQL-ready.
- Reminders: per-user settings (`reminders_on`, `times`/`interval` mode, quiet
  hours, `location_ttl_hours`, tz). Hard rule — **stay silent** when there are
  no tasks due today or none match the current place; when the saved location
  is stale, first ask "where are you?" with place buttons, then digest.
- Occurrences are materialized (`task_occurrences`: pending/done/missed);
  overdue rows are flipped to `missed` in a background pass.
- Stats: text summary, CSV/XLSX documents, and PNG charts via matplotlib
  (Agg backend only, no display); per-user `stats_format`.
- i18n ru/en: JSON catalogs in `taskshed/i18n/`, all UI strings through `t()`;
  CI must check that ru/en key sets match. Language is per-user, switchable
  via `/lang`.
- Chat-only interface; no web UI.

## Conventions

- Commit messages: plain conventional-commit style, **no AI co-author trailers
  or AI markers** (owner's explicit choice — do not add them back).
- GitHub: private repo `github.com/edombek/tg-bot-task-sheduler`, pushed with
  `gh` (authenticated as `edombek`, ssh protocol). Global git identity is
  `edombek <edombek@yandex.ru>` — do not override globally.
- Docs: bilingual. `README.md` (English) + `README.ru.md` (Russian), cross-linked;
  mirrored `docs/en/` and `docs/ru/`; diagrams as Mermaid in `docs/diagrams/`;
  living checklist in `docs/PLAN.md`; `CHANGELOG.md` (Keep a Changelog).
  When code changes, update **both** language copies.
- Tests: pytest (+pytest-asyncio). Chat-logic tests must go through the
  `FakeMessenger` fake implementing the messenger port — no network in tests.
  CI (`.github/workflows/ci.yml`): ruff + pytest; never let adapters be
  required for core unit tests.
- Secrets: `.env`/`config.yaml` are gitignored; commit only `.env.example` /
  `config.example.yaml` (per-platform proxy blocks: mtproxy for Telegram,
  http/socks5 for VK).
