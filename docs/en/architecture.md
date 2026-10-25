# Architecture (English)

Component diagram: [architecture.mmd](../diagrams/architecture.mmd) ·
[ER](../diagrams/er-diagram.mmd) · [reminder flow](../diagrams/reminder-flow.mmd) ·
[linking flow](../diagrams/linking-flow.mmd)

## Idea

Hexagonal (ports & adapters). `taskshed/core` contains pure domain logic and
knows nothing about Telegram or VK. Each platform is an adapter that
(1) translates raw platform events into a platform-independent `Command`
(messages and button presses), and (2) implements the `IMessenger` port
(`send_text`, `send_image`, `send_document`, `ask_choice`). Chat logic
(`bot/handlers.py`) and the reminder scheduler (`scheduler/jobs.py`) work only
with `Command` / `IMessenger`.

Consequences that matter for tests: the whole bot can run in unit tests
against `messaging/fake.py::FakeMessenger` and an in-memory SQLite database —
no network, no real platforms.

## Module map

| Module | Responsibility |
|---|---|
| `taskshed/core/models.py` | domain dataclasses (`Task`, `Place`, `TaskOccurrence`, …) |
| `taskshed/core/scheduling.py` | schedule rules → occurrence datetimes; materialization; overdue pass |
| `taskshed/core/stats.py` | period aggregations (day/task/place) |
| `taskshed/storage/` | SQLAlchemy async ORM tables + repositories, Alembic migrations |
| `taskshed/messaging/` | port (`base.py`) + `telegram.py`, `vk.py`, `fake.py` |
| `taskshed/bot/handlers.py` | chat commands, dialogs/wizards, buttons |
| `taskshed/scheduler/jobs.py` | per-minute reminder tick, silence rules |
| `taskshed/stats/` | matplotlib charts, CSV/XLSX export |
| `taskshed/i18n/` | ru/en JSON catalogs + `t()` |
| `taskshed/config.py` | YAML + env config (per-platform proxy blocks) |
| `taskshed/entrypoints/` | `run_telegram`, `run_vk`, `run_all` |

## Domain model

See the [ER diagram](../diagrams/er-diagram.mmd). Key point: **`users` are
platform-agnostic**; `accounts` map `(platform, platform_user_id)` onto a
`user`. Everything else (tasks, places, settings) hangs off `users.id`, which
is what makes Telegram↔VK linking trivial.

### Schedules and occurrences

`schedule_type`: `once` · `every_n_days` · `weekdays` · `monthly_day`,
parameters in `schedule_data` (JSON). Occurrences are materialized a few days
ahead (`bot.occurrence_horizon_days`); each has `due_at`, `reminder_at`,
`status ∈ {pending, done, missed}`. A background pass flips `pending` rows
whose deadline passed to `missed`.

### Reminder tick

See the [sequence diagram](../diagrams/reminder-flow.mmd). Hard rules:
1. quiet hours / `reminders_on=false` / trigger moment not reached → nothing;
2. no occurrences due today → nothing;
3. location older than `location_ttl_hours` → ask with place buttons first;
4. no occurrences for the current place → nothing.

### Account linking

See the [sequence diagram](../diagrams/linking-flow.mmd). `/link` mints a
one-time 8-char code (10 min TTL); the other platform redeems it. Merging two
non-empty users shows a preview and requires confirmation; the operation is
audited in `link_audit`.

## Configuration

`config.yaml` (see `config.example.yaml`), overridable by `TASKSHED_*` env
vars. Telegram supports MTProto proxies (`mtproxy`/`securedmtproxy` —
Telethon's randomized-intermediate connection); VK takes plain http/socks5
since its API is ordinary HTTPS.
