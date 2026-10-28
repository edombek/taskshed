# Architecture (English)

## Diagrams

The blocks below are rendered by GitHub itself (vector). Their text is synced
from the `.mmd` sources in [`docs/diagrams/`](../diagrams/) — those remain the
single source of truth; run `scripts/sync_diagram_blocks.py` after editing one
(`tests/test_docs_diagrams.py` keeps the copies honest).

<!-- mermaid:begin architecture -->
```mermaid
graph LR
    subgraph Platforms
        TG[Telegram]
        VK[VK]
    end

    subgraph Adapters
        TGA[messaging/telegram.py<br/>Telethon start_bot + MTProxy]
        VKA[messaging/vk.py<br/>Bot Long Poll, aiohttp]
    end

    EP[entrypoints/common.py<br/>wiring: run_telegram / run_vk / run_all]

    subgraph Port["Messenger port (messaging/base.py)"]
        P[IMessenger / RawEvent]
        FAKE[FakeMessenger — tests]
    end

    subgraph Core
        H[bot/ BotApp<br/>app.py dispatch + per-feature modules<br/>commands · wizards · task editor · buttons]
        C[core/<br/>models · scheduling · stats]
        S[scheduler/jobs.py<br/>minute tick, silence rules]
        ST[stats/charts.py · stats/export.py<br/>PNG · CSV · XLSX]
        I[i18n/ ru.json en.json]
    end

    subgraph Storage["storage/ (SQLAlchemy async + Alembic)"]
        R[repositories.Store]
        DB[(SQLite / PostgreSQL)]
    end

    TG --> TGA --> P
    VK --> VKA --> P
    FAKE -.tests only.-> P
    EP -->|builds| TGA
    EP -->|builds| VKA
    EP -->|starts| S
    P --> H --> C
    S --> C
    S -->|digest / ask-where| P
    C --> R --> DB
    H --> ST
    H --> I
    S --> I
```
<!-- mermaid:end architecture -->

<!-- mermaid:begin er-diagram -->
```mermaid
erDiagram
    users ||--o{ accounts : "identity per platform"
    users ||--|| user_settings : "reminder prefs"
    users ||--o{ places : "defines"
    users |o--o| current_location : "where am I now"
    users ||--o{ tasks : "owns"
    places ||--o{ tasks : "groups, delete cascades"
    tasks ||--o{ task_occurrences : "materialized repeats"
    task_occurrences |o--o| completions : "marked done"
    users ||--o{ link_codes : "one-time link codes"
    users ||--o{ link_audit : "link/unlink/merge trail"

    users {
        int id PK
        datetime created_at
        string language "ru|en"
        json state_json "reserved (wizard state is in-memory)"
    }
    accounts {
        int id PK
        int user_id FK
        string platform "telegram|vk"
        string platform_user_id "UNIQUE(platform, platform_user_id)"
        datetime linked_at
        datetime last_seen "nullable; picks primary account"
    }
    places {
        int id PK
        int user_id FK
        string name "UNIQUE(user_id, name)"
    }
    current_location {
        int user_id PK "also FK to users"
        int place_id FK
        datetime set_at "UTC; freshness = set_at + ttl"
    }
    user_settings {
        int user_id PK "also FK to users"
        bool reminders_on
        string morning_time "HH:MM, user wall-clock: the daily digest"
        int nag_interval_min "repeat for tasks inside the notice window"
        string quiet_start "HH:MM"
        string quiet_end "HH:MM"
        int location_ttl_hours
        string tz
        string notify_platforms "all|primary|telegram|vk"
        string stats_format "both|text|image"
        int stats_horizon_days "chart lookahead, default 7"
        datetime last_morning_at "nullable, UTC"
        datetime last_notified_at "nullable, UTC"
        bool pending_reminder "digest awaiting location answer"
    }
    tasks {
        int id PK
        int user_id FK
        int place_id FK
        string title
        text note
        datetime deadline "wall-clock plan in the user tz, nullable"
        datetime start_at "work begins (once tasks), wall-clock, nullable"
        string schedule_type "once|every_n_days|weekdays|monthly_day"
        json schedule_data
        string deadline_time "HH:MM clock of the deadline on repeat days"
        int notify_before_min "warn this many minutes before the deadline"
        bool is_active
        datetime created_at
    }
    task_occurrences {
        int id PK
        int task_id FK "UNIQUE(task_id, due_at)"
        datetime due_at "deadline of this repeat, UTC instant"
        datetime notify_at "due_at minus the task lead, UTC instant"
        datetime start_at "work window opens (once tasks), nullable"
        string status "pending|in_progress|done|missed|skipped"
    }
    completions {
        int occurrence_id PK "also FK to task_occurrences"
        datetime done_at
        string completed_via "telegram|vk"
    }
    link_codes {
        string code PK "8 chars, one active per user"
        int user_id FK
        string created_by_platform
        datetime created_at
        datetime expires_at "TTL 10 min"
        bool used
    }
    link_audit {
        int id PK
        int user_id FK
        string action "link|unlink|merge"
        text detail
        datetime at
    }
```
<!-- mermaid:end er-diagram -->

For offline or non-GitHub readers there are PNGs, regenerated with
`scripts/render_diagrams.sh`: [architecture.png](../diagrams/architecture.png) ·
[er-diagram.png](../diagrams/er-diagram.png) ·
[reminder-flow.png](../diagrams/reminder-flow.png) ·
[linking-flow.png](../diagrams/linking-flow.png)

## Idea

Hexagonal (ports & adapters). `taskshed/core` contains pure domain logic and
knows nothing about Telegram or VK. Each platform is an adapter that
(1) translates raw platform events into a platform-independent `RawEvent`
(messages and button presses), and (2) implements the `IMessenger` port
(`send_text`, `send_buttons`, `send_image`, `send_document`). Chat logic
(`BotApp` — dispatch in `bot/app.py`, one dialog per module under `bot/`)
and the reminder scheduler (`scheduler/jobs.py`) work only with
`RawEvent` / `IMessenger`.

Consequences that matter for tests: the whole bot can run in unit tests
against `messaging/base.py::FakeMessenger` and an in-memory SQLite database —
no network, no real platforms.

## Module map

| Module | Responsibility |
|---|---|
| `taskshed/core/models.py` | domain dataclasses (`Task`, `Place`, `TaskOccurrence`, …) |
| `taskshed/core/scheduling.py` | schedule rules → occurrence datetimes; materialization; overdue pass |
| `taskshed/core/stats.py` | period aggregations (day/task/place) |
| `taskshed/storage/` | SQLAlchemy async ORM tables + repositories, Alembic migrations |
| `taskshed/messaging/` | port + `FakeMessenger` (`base.py`) + `telegram.py`, `vk.py` |
| `taskshed/bot/` | `BotApp` (`app.py`) + one module per dialog: wizard, editor, cards, digest, reports, settings, location, places; shared state in `base.py` |
| `taskshed/scheduler/jobs.py` | per-minute reminder tick, silence rules |
| `taskshed/stats/` | matplotlib charts (daily + hourly activity maps), CSV/XLSX export |
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
parameters in `schedule_data` (JSON). Every kind answers the same two
questions: **when it has to be ready** and **how long before that to warn** —
`deadline_time` (the clock the deadline falls at on each repeat day) plus
`notify_before_min`; a one-off instead stores its own ready-by moment in
`tasks.deadline`.

**Plans are kept in the user's own time.** `tasks.deadline`, `tasks.start_at`,
`tasks.deadline_time` and the settings clocks are wall-clock values on the
owner's calendar, never host time. `task_occurrences` are real UTC instants
derived from those plans: `due_at` is the deadline,
`notify_at = due_at − notify_before_min`. Switching a user's `tz` re-derives
their whole open future (`Store.replan_open_occurrences`) — «к 18:00» still
means 18:00 on *their* watch — while finished history is left untouched.

Occurrences are materialized a few days ahead
(`bot.occurrence_horizon_days`); each has `start_at` and
`status ∈ {pending, in_progress, done, missed, skipped}`. A background pass
flips `pending` rows whose deadline passed to `missed`; a user-skipped
occurrence becomes `skipped` — it is hidden from today's list, never turns
into `missed` and does not count against the completion percent. A one-off task
is not a single instant either: `tasks.start_at` opens a work window and
`tasks.deadline` is the ready-by moment, so the row shows «from … to …» and
appears in the list from its start day on. Tapping **▶ В работе** sets
`in_progress`: the deadline stays, the row is never punished as missed, and it
remains visible until done or skipped.

### Reminder tick

<!-- mermaid:begin reminder-flow -->
```mermaid
sequenceDiagram
    autonumber
    participant Sch as run_tick (every 60s, UTC now)
    participant Core as BotApp + Store
    participant Msg as Messenger port<br/>(notify_user → all/primary/pinned accounts)
    participant U as User (TG/VK)

    Sch->>Core: mark_overdue(now): pending past due → missed<br/>(in_progress work stays open)
    loop per user (in user tz)
        Core->>Core: reminders_on? materialize the horizon,<br/>quiet hours?
        Note over Core: plans are the user's wall-clock (deadline,<br/>deadline_time) — every instant is re-derived<br/>through their current tz
        Core->>Core: rows_to_announce = open repeats with<br/>due_at ≥ start of today and notify_at ≤ end of today<br/>(notify_at = due_at − notify_before_min)
        alt nothing to announce (any place)
            Core-->>Sch: silent (no message at all)
        end
        Core->>Core: morning digest due? (morning_time reached,<br/>not yet sent this calendar day)<br/>repeat due? ≥ nag_interval_min since last_notified_at<br/>for rows inside [notify_at, due_at)
        alt neither trigger
            Core-->>Sch: silent
        end
        Core->>Core: current_location fresh? (set_at + ttl > now)
        alt stale or unset
            Core->>Msg: "Where are you now?" + place buttons<br/>+ «Not now» (once: pending_reminder=true)
            Msg->>U: 📍 Где вы сейчас? [Дом] [Работа] … [⏭ Не сейчас]
            Core->>Core: whatever dialog is in progress (wizard, list intent)<br/>is parked — the question is modal
            U->>Msg: tap "place:42" (or type a place name)
            Msg->>Core: set location, pending_reminder=false
            Core->>Msg: the parked dialog is replayed, then the notice follows
        else fresh
            Core->>Core: notice = tasks of the current place in full,<br/>tasks of other places in brief
        end
        Core->>Msg: notice header + one message per task of this place<br/>+ one brief line-up for the others + footer<br/>[✅ Готово][🌍 Все задачи] (older keyboards get disarmed)
        Msg->>U: 🌅 Доброе утро. Задачи «Работа» на сегодня:<br/>📌 Отчёт — до 18:00 [✅ Готово][⏭ Пропустить][💤 Позже][▶ В работе]<br/>📌 Созвон — до 12:00 ⚠️просрочено [✅][⏭][💤][▶]<br/>📍 Другие места: • Дом: Полить цветы — до 20:00<br/>✅ Готово
    end
    U->>Msg: "done:42" button on a task's own message
    Msg->>Core: ownership check → status=done + completions row
    Core->>Msg: the very task message is re-rendered: ✅ mark,<br/>keyboard gone — no confirmation, no menu, no chat spam
    U->>Msg: "wip:42" → the message now reads «▶ в работе»,<br/>keyboard becomes [✅][⏭] — and the task is never marked missed
    U->>Msg: "snooze:42" → the same message shows duration buttons<br/>[15м][1ч][4ч][+сутки][⬅️]
    U->>Msg: "snz:42:60" → the notice moment of this row moves out by an hour,<br/>the every-T repeat picks the task up again after that
    U->>Msg: "skip:42" → status=skipped: «⏭пропущено»,<br/>keyboard off, never flipped to missed, no penalty in stats
    U->>Msg: "fin:" under the notice → the status round ends,<br/>the main menu returns and every keyboard of the notice is disarmed
    U->>Msg: "tas:all" → open rows of every place, grouped by place<br/>on one screen — the same footer closes it
    Note over U,Core: the last level of every menu walk carries ✅ «Готово»<br/>(menu:back) — ⬅️ Назад only moves one step up
```
<!-- mermaid:end reminder-flow -->

Hard rules:
1. quiet hours / `reminders_on=false` → nothing;
2. nothing to announce today — no open repeat with `due_at` ≥ start of the
   day and `notify_at` ≤ end of the day → nothing;
3. no trigger yet → nothing. There are two: the **morning digest** at
   `morning_time` (once per calendar day, wall-clock in the user's zone) and
   the **every-`nag_interval_min` repeat** over rows sitting inside
   `[notify_at, due_at)`;
4. location older than `location_ttl_hours` → ask «where are you?» with place
   buttons first (the question is modal: an open dialog is parked and
   replayed afterwards);
5. a notice then sends the tasks of the **current place** as one message each
   with buttons, the tasks of **other places** as one brief line-up, and closes
   with a footer that is always sent: ✅ «Готово» (`fin:`) ends the status
   round and returns the main menu, 🌍 «Все задачи» (`tas:all`) shows the open
   rows of **every** place grouped by place on one screen.
6. the last level of any menu walk carries ✅ «Готово» (`menu:back` → main
   menu); ⬅️ Назад only moves one step up inside a dialog. The hour map pages
   by whole weeks in both directions without a limit — each paging button
   carries the shift of the week it draws and names its dates.

### Account linking

<!-- mermaid:begin linking-flow -->
```mermaid
sequenceDiagram
    autonumber
    participant U as Owner (already registered)
    participant TG as Telegram bot
    participant Core as BotApp + Store
    participant VK as VK bot

    U->>TG: /link  (RU: /привязать)<br/>or ⚙️ Настройки → 🔗 Привязать платформу
    TG->>Core: create_link_code(user, telegram)<br/>8 chars, TTL 10 min, replaces any previous code
    Core-->>TG: K3F9A2B7
    TG-->>U: "Send to the VK bot: /link K3F9A2B7"
    U->>VK: /link K3F9A2B7
    VK->>Core: redeem_link_code
    alt code valid & fresh & unused
        Core->>Core: mark used, attach_account(vk → owner's user)
        Core->>Core: merge_user(vk's old user → owner):<br/>places/tasks moved, name clashes renamed "#2",<br/>old settings/location deleted
        Core->>Core: link_audit rows: link, merge
        Core-->>VK: OK
        VK-->>U: "Accounts linked. Reminders go to both chats."
        Core-->>U: ⚙️ Настройки shows "Платформы: Telegram ✓, VK ✓"<br/>plus a «🗑 Отвязать VK» row (rem:unlink:ID)
    else expired / used / unknown
        Core-->>VK: None
        VK-->>U: "Code invalid. Request a new one."
    end
    note over Core: Merge is automatic (no confirmation preview yet —<br/>see backlog in docs/PLAN.md). /unlink reverts only<br/>the account, when the user has other accounts.
```
<!-- mermaid:end linking-flow -->

`/link` mints a
one-time 8-char code (10 min TTL, one active per user); the other platform
redeems it. The redeeming account moves to the code owner and its previous
user is merged in automatically (name clashes renamed `#2`); every step is
audited in `link_audit`. A confirmation preview before merge is on the backlog.

## Configuration

`config.yaml` (see `config.example.yaml`), overridable by `TASKSHED_*` env
vars. Telegram supports MTProto proxies (`mtproxy`/`securedmtproxy` —
Telethon's randomized-intermediate connection); VK takes plain http/socks5
since its API is ordinary HTTPS.
