# AGENTS.md

## State of the repo

Plan stages 0–6 and stage 8 (UX polish) are done; stage 7 (real-world
verification against live Telegram/VK) is in progress. `docs/PLAN.md` is the
living checklist; backlog items are listed at its bottom. The full pytest
suite (~200 tests) is green in seconds; ruff is clean.

## Commands

```bash
source .venv/bin/activate          # venv already provisioned (Python 3.14); or:
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
ruff check . && pytest             # the CI gate; pytest needs no extra flags
```

- pytest runs async tests without markers: `asyncio_mode = "auto"` is set in
  `pyproject.toml`.
- Single test: `pytest tests/test_scheduling.py::test_name`.
- Local Python is 3.14 but CI pins 3.12 — avoid version-specific syntax beyond
  what `requires-python = ">=3.12"` allows.
- Running the bot for real: `alembic upgrade head`, then
  `python -m taskshed.entrypoints.run_telegram|run_vk|run_all`
  (config from `config.yaml` / `.env`).

## Architecture map (don't fight it)

- **All chat/dialog logic lives in `taskshed/bot/`**: `app.py` keeps `BotApp`
  (event dispatch, text/command routing) assembled from one mixin module per
  dialog — `wizard.py`, `editor.py`, `cards.py`, `digest.py`, `reports.py`,
  `settings.py`, `location.py`, `places.py`; the shared transient state
  (`wizards`, `_pending_inputs`, `_await_loc`, …) lives in `base.py::BotBase`,
  constants in `consts.py`. Put a new dialog in its own module, not in app.py.
- Adapters in `messaging/` are thin: they only translate their wire format
  into `RawEvent` and implement `IMessenger` (`messaging/base.py`).
  Never put chat logic in an adapter.
- `bot/timeparse.py` is the single date/time parser for every step (wizard,
  settings, scheduler prompts); it re-confirms guesses (✅/✖). Reuse it —
  don't hand-roll date/time parsing.
- `core/` is pure domain (dataclasses, schedule rules, stats aggregations) —
  no storage/platform imports. `storage/` = SQLAlchemy 2 async models +
  repositories; `scheduler/jobs.py` runs the per-minute tick loop;
  `entrypoints/common.py` wires config → store → adapters → BotApp + scheduler.

## Project intent

Multi-platform task-reminder chat bot (Telegram + VK) that segments tasks by
the user's current location ("home", "work"). Python 3.12+ (`python3`), asyncio.

## Fixed design decisions (agreed with the owner — don't relitigate)

- Telegram via **Telethon** (`start(bot_token=…)`, MTProto/MTProxy support),
  VK via Bot Long Poll (aiohttp, no heavy framework).
- Location is set **manually** by the user (buttons or typed names), never via GPS.
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
  is stale, first ask "where are you?" with place buttons (modal — an open
  wizard intent is parked and replayed after the answer), then digest.
- Occurrences are materialized (`task_occurrences`: `pending` / `in_progress`
  / `done` / `missed` / `skipped`); overdue rows are flipped to `missed` in a
  background pass; `skipped` (⏭) carries no missed penalty and is excluded
  from the completion percent.
- Stats: text summary, CSV/XLSX documents, and PNG charts via matplotlib
  (Agg backend only, no display); per-user `stats_format`.
- i18n ru/en: JSON catalogs in `taskshed/i18n/`, all UI strings through `t()`;
  CI must check that ru/en key sets match. Language is per-user, switchable
  via `/lang`.
- Chat-only interface; no web UI.

## Conventions

- Commit messages: plain conventional-commit style, **no AI co-author trailers
  or AI markers** (owner's explicit choice — do not add them back).
- Docs: bilingual. `README.md` (English) + `README.ru.md` (Russian), cross-linked;
  mirrored `docs/en/` and `docs/ru/`; diagrams as Mermaid in `docs/diagrams/`
  (`.mmd` are the source of truth; docs/READMEs embed them as fenced ```mermaid
  blocks — GitHub renders those as vector — kept in sync by
  `scripts/sync_diagram_blocks.py` and guarded by `tests/test_docs_diagrams.py`;
  rendered 3x `.png` are committed and linked for offline readers, regenerate
  with `scripts/render_diagrams.sh`). **When behavior/flows/schema change,
  update the `.mmd` diagrams in the same commit** (statuses, buttons, new
  interactions), then re-render PNGs and run `scripts/sync_diagram_blocks.py`
  — the parity test only catches drifted copies, content staleness is on us);
  living checklist in `docs/PLAN.md`; `CHANGELOG.md` (Keep a Changelog).
  When code changes, update **both** language copies.
- Tests: pytest (+pytest-asyncio). Chat-logic tests drive `BotApp` through the
  `FakeMessenger` fake — it ships in `taskshed/messaging/base.py`, don't
  reinvent it — and hit the DB via in-memory SQLite; no network in tests.
  Chat-flow tests are split by feature (`test_wizard.py`, `test_editor_flow.py`,
  …); the shared `env` fixture and `send`/`place_id_of` helpers live in
  `tests/chatenv.py` (re-exported via `conftest.py`).
  CI (`.github/workflows/ci.yml`): ruff + pytest; never let adapters be
  required for core unit tests.
- Secrets: `.env`/`config.yaml` are gitignored; commit only `.env.example` /
  `config.example.yaml` (per-platform proxy blocks: mtproxy for Telegram,
  http/socks5 for VK).

## Gotchas

- All datetimes in `core` and repositories are **timezone-aware UTC**; convert
  to the user's tz only at display/scheduling boundaries (`core/models.py`
  docstring). SQLite itself stores naive.
- In-memory SQLite tests must build the engine via `storage/db.py::make_engine`
  — it applies `StaticPool`, without it every connection gets its own empty DB.
- Telegram uses **Telethon in bot mode** (`start(bot_token=…)`; Telethon 1.45
  removed `client.start_bot()`), not python-telegram-bot;
  MTProxy needs `ConnectionTcpMTProxyRandomizedIntermediate`.
- Alembic: autogenerate/upgrade take the URL from `TASKSHED_DATABASE_URL`
  (env), not only `alembic.ini`. `alembic/versions/*` is exempted from ruff
  import-sorting — migrations stay as autogenerated.
- Chat tests must not lean on wall-clock time: text like `now+2h` typed as
  `%H.%M` is also a valid date for `timeparse` (`11.08` = 11 August), so such
  tests only pass at certain minutes — pin the minute (e.g. `.replace(minute=30)`).

