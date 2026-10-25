# Project plan / План работ

Checklist of the implementation roadmap. EN summary in
[`docs/en/architecture.md`](en/architecture.md), RU in
[`docs/ru/architecture.md`](ru/architecture.md).

## Stage 0 — Repository bootstrap ✅
- [x] git repo, `.gitignore`, `pyproject.toml`
- [x] bilingual README, `docs/` skeleton, Mermaid diagrams
- [x] GitHub Actions CI: ruff + pytest
- [x] private repo `github.com/edombek/tg-bot-task-sheduler`

## Stage 1 — Core domain & storage ✅
- [x] `core/`: domain dataclasses, schedule rules (once / every_n_days /
      weekdays / monthly_day), occurrence materialization, overdue pass
- [x] `storage/`: SQLAlchemy 2 async models — `users`, `accounts`, `places`,
      `current_location`, `tasks`, `task_occurrences`, `completions`,
      `user_settings`, `link_codes` + repositories
- [x] Alembic migrations (verified: upgrade+downgrade against clean DB)
- [x] Unit tests for scheduling & repositories (in-memory SQLite)

## Stage 2 — Telegram adapter + i18n ✅
- [x] Telethon `start_bot`, MTProto proxy support
- [x] Messenger port implemented; commands/buttons mapped to platform API
- [x] i18n: ru/en JSON catalogs, `t()`, per-user language, `/lang`
- [x] CI test: ru/en catalog key parity

## Stage 3 — Reminder scheduler ✅
- [x] Minute tick in user timezone (asyncio loop; APScheduler left out as dep-only)
- [x] Silence rules: no tasks today / no tasks for current place → no message
- [x] Stale location → "Where are you?" place buttons, then digest
- [x] Digest with ✅ / ⏭ buttons; user reminder settings (times/interval,
      quiet hours, location TTL)

## Stage 4 — Statistics ✅
- [x] Aggregations in `core/stats.py` (by day / task / place / period)
- [x] Text report; CSV & XLSX export
- [x] matplotlib PNG charts (Agg): daily bars, calendar heatmap,
      top tasks/places, completion trend

## Stage 5 — VK adapter + account linking ✅
- [x] VK Bot Long Poll over aiohttp (no heavy framework dependency)
- [x] VK keyboards, text stats, documents/photos (best-effort upload); proxy
      (http/socks5)
- [x] `/link` one-time code flow (RU: `/привязать`), `/unlink`, merge
      (places/tasks moved, name clashes renamed, `link_audit` trail)

## Stage 6 — Hardening & docs ✅
- [x] Parallel run of both platforms (`run_all`)
- [x] Reminder fan-out to linked accounts, `notify_platforms` setting
      (accounts.last_seen picks the primary)
- [x] Final bilingual docs with diagrams and examples
- [x] `CHANGELOG.md` up to date

## Backlog / known limitations
- VK photo/document upload for charts is best-effort (falls back to text).
- Task-creation wizard state is in-memory (lost on restart).
- Merge on `/link` runs without a confirmation preview of the two sides.
- PostgreSQL driver (`asyncpg`) not exercised in CI yet.
