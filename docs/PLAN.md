# Project plan / План работ

Checklist of the implementation roadmap. EN summary in
[`docs/en/architecture.md`](en/architecture.md), RU in
[`docs/ru/architecture.md`](ru/architecture.md).

## Stage 0 — Repository bootstrap ✅
- [x] git repo, `.gitignore`, `pyproject.toml`
- [x] bilingual README, `docs/` skeleton, Mermaid diagrams
- [x] GitHub Actions CI: ruff + pytest
- [x] private repo `github.com/edombek/taskshed` (renamed 2026-10-26)

## Stage 1 — Core domain & storage ✅
- [x] `core/`: domain dataclasses, schedule rules (once / every_n_days /
      weekdays / monthly_day), occurrence materialization, overdue pass
- [x] `storage/`: SQLAlchemy 2 async models — `users`, `accounts`, `places`,
      `current_location`, `tasks`, `task_occurrences`, `completions`,
      `user_settings`, `link_codes` + repositories
- [x] Alembic migrations (verified: upgrade+downgrade against clean DB)
- [x] Unit tests for scheduling & repositories (in-memory SQLite)

## Stage 2 — Telegram adapter + i18n ✅
- [x] Telethon bot login (`start(bot_token=…)`), MTProto proxy support
- [x] Messenger port implemented; commands/buttons mapped to platform API
- [x] i18n: ru/en JSON catalogs, `t()`, per-user language, `/lang`
- [x] CI test: ru/en catalog key parity

## Stage 3 — Reminder scheduler ✅
- [x] Minute tick in user timezone (asyncio loop; APScheduler dropped entirely)
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

## Stage 7 — Real-world verification 🚧
- [x] `config.yaml` filled: Telegram token + local MTProxy (`127.0.0.1:8888`)
- [x] Alembic `upgrade head` on the real file DB (`taskshed.db`, head `b5c04a0c009c`)
- [x] Telegram smoke: bot connects through the proxy (`start(bot_token=…)`, Telethon 1.45)
- [ ] Telegram chat flow: places → tasks → digest → ✅ → stats (interactive)
- [ ] VK: real community token + `group_id` (placeholders removed), enable
- [ ] VK smoke: long poll connects, `/старт` replies
- [ ] `/link` code round-trip Telegram ↔ VK, fan-out of reminders

## Stage 8 — UX polish (from live-testing feedback) ✅
- [x] Inline command menu buttons under `/start` and `/help`
- [x] Wizard date/time entry via keyboards (Сегодня/Завтра/… → time presets),
      typed input still accepted; past-deadline guard
- [x] URL defanging in task titles and place names; `link_preview=False`
      on Telegram sends (anti-ban hygiene)
- [x] GitHub-style activity heatmap (week columns, Mon–Sun rows) with red
      (missed) and blue (planned) sub-squares inside each day cell; window
      always covers at least the last month (empty days drawn gray)
- [x] Command menu attached to completion replies (task saved, ✅ done,
      snooze, place set, delete, link/unlink)
- [x] Scheduler honors per-task `reminder_at` (and snooze), not just the
      global digest moment; stats keep planned occurrences in reports and
      on the heatmap; live user tz corrected to Europe/Moscow
- [x] Menu as a "Хотите сделать что-нибудь ещё?" follow-up message after
      completed commands (suppressed mid-wizard and under picker replies)
- [x] Interactive statistics: period buttons → text + format buttons;
      activity heatmap is the only chart (daily bars retired)
- [x] Snooze duration buttons: 15 min / 1 h / 4 h / +1 day
- [x] Explicit settings label for location-question frequency
      (`location_ttl_hours`)
- [x] Diagrams embedded in docs/READMEs as GitHub-native ```mermaid blocks
      (vector), synced from `.mmd` via `scripts/sync_diagram_blocks.py` +
      parity test; PNGs re-rendered at 3x and linked for offline readers
- [x] Submenu screens (reminder settings, places, language, stats pickers)
      get an ⬅️ Назад button back to the main menu, no menu follow-up there
- [x] Wizard reminder time as buttons (⏎ 09:00 default); flexible time
      parsing: `11 55` / `11.55` / `11-55` / `11:55`
- [x] ⬅️ Назад on every wizard step (redoes the previous answer)
- [x] Flexible date parsing: `15.11.2026`, `15/11/26`, `15 11 2026`,
      `15.11` (next occurrence), `завтра`/`tomorrow`; date-only → time buttons
- [x] ⏭ Пропустить: closes an occurrence as `skipped` (no missed penalty,
      hidden from today's list, tracked separately in reports/heatmap,
      excluded from completion percent)
- [x] ⬅️ Назад on every input-waiting prompt (Где я?, places, reminder
      settings values, scheduler location question); Назад cancels the
      awaited input; reminder settings edited in place, prompts explain
      the expected value; localized heatmap labels
- [x] Reminder timing merged into one flow: «⏰ Когда напоминать» →
      times/every-N-minutes picker; localized mode line on the settings
      screen; ⬅️ Назад in the picker returns to settings (in place)
- [x] Task lists/digests: «▶ В работе» status for tasks stretched over time
      (start + ready-by deadline), instead of one-off tasks being a moment
- [x] ⚙️ «Настройки» instead of 🔔 «Напоминания»: one per-user settings screen
      incl. linked platforms status, language and chart horizon
- [x] Activity charts look ahead: the daily map draws the window up to
      `stats_horizon_days` (default 1 week) — future days are shown even while
      empty, today is outlined; the hour-level map (week period: day rows ×
      24 hour columns) counts the same statuses as the daily one, bucketed by
      the hour the work belongs to (start_at, else reminder time)
- [x] Saving a task answers with a card: title, place, the window or repeat
      rule, the reminder time and the first date it fires
- [x] Hour map marks every hour inside a stretched task's window (only the
      boundaries for windows over a day); daily map marks the start day too
- [x] Chart PNGs rendered at up to 300 dpi inside Telegram's photo budget
- [x] Stale keyboards disarmed: only the newest screen in a chat keeps its
      inline buttons (`edit_buttons(..., [])` port convention)
- [x] ✏️ Task editor (`/изменить`): card with a button per item (title, place,
      repeat rule, work window, reminder), delete behind confirmation; tabs for
      upcoming / recurring / past / all tasks; a schedule change rebuilds open
      repeats and keeps finished history
- [x] SQLite: `PRAGMA foreign_keys=ON` per connection, so ON DELETE CASCADE
      actually removes a deleted task's occurrences and completions
- [x] Menu follow-up after every action (a card/screen is not a dialog: only a
      wizard step suppresses it); place deletion confirms with the task count
- [x] Chart figures sized from the grid (equal unit on both axes, size grows
      with the number of cells; legend only on wide maps)
- [x] Place names typed into the task wizard create/reuse the place (case
      blind); a place deleted mid-wizard is asked for again, no dangling FK
- [x] One date/time parser (`bot/timeparse.py`) for every step + settings; it
      returns time-only / day-only / both and asks "did I read that right?"
      (✅/✖) whenever it guessed; «▶ Сейчас» button on the start step
- [x] «Без срока» on the ready-by step: a one-off task can be a bare moment
      (reminder only), drawn as one hour on the hour map, not a band
- [x] Chart window: forward = `stats_horizon_days`, back = only real days (no
      padded weeks); materialize the window, never plan before a rule's own date
- [x] Horizon rounded up to its Sunday (whole weeks planned, no empty right
      column) and the hour map outlines the current hour with a minute tick
- [x] Hour map: week period = exactly Mon–Sun, no legend; both maps name their
      axes
- [x] Every statistic per place (text block + chart + file each); hour map rows
      = weekday names of the sliding week (−3 … +3)
- [x] Hour map week paging: «⬅️ Пред. неделя» / «След. неделя ➡️», materialized
      on demand beyond the horizon setting
- [x] Modal «Где вы сейчас?»: an open wizard/list intent is parked, the place
      answer (button or typed name) resumes it; «⏭ Не сейчас» skips the question

## Backlog / known limitations
- VK photo/document upload for charts is best-effort (falls back to text).
- Task-creation wizard state is in-memory (lost on restart).
- Merge on `/link` runs without a confirmation preview of the two sides.
- PostgreSQL driver (`asyncpg`) not exercised in CI yet.
