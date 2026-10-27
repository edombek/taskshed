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
        H[bot/app.py BotApp<br/>commands · wizards · task editor · buttons]
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
        string remind_mode "times|interval"
        json remind_times "HH:MM list, user tz"
        int remind_interval_min
        string quiet_start "HH:MM"
        string quiet_end "HH:MM"
        int location_ttl_hours
        string tz
        string notify_platforms "all|primary"
        string stats_format "both|text|image"
        int stats_horizon_days "chart lookahead, default 7"
        datetime last_reminded_at "nullable"
        bool pending_reminder "digest awaiting location answer"
    }
    tasks {
        int id PK
        int user_id FK
        int place_id FK
        string title
        text note
        datetime deadline "ready-by moment, nullable"
        datetime start_at "work begins (once tasks), nullable"
        string schedule_type "once|every_n_days|weekdays|monthly_day"
        json schedule_data
        string reminder_time "HH:MM user tz"
        bool is_active
        datetime created_at
    }
    task_occurrences {
        int id PK
        int task_id FK "UNIQUE(task_id, due_at)"
        datetime due_at "deadline of this repeat, UTC"
        datetime reminder_at "when the reminder may fire"
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
(`bot/app.py::BotApp`) and the reminder scheduler (`scheduler/jobs.py`) work
only with `RawEvent` / `IMessenger`.

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
| `taskshed/bot/app.py` | `BotApp`: chat commands, dialogs/wizards, buttons |
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
parameters in `schedule_data` (JSON). Occurrences are materialized a few days
ahead (`bot.occurrence_horizon_days`); each has `due_at`, `reminder_at`,
`start_at`, `status ∈ {pending, in_progress, done, missed, skipped}`. A
background pass flips `pending` rows whose deadline passed to `missed`; a
user-skipped occurrence becomes `skipped` — it is hidden from today's list,
never turns into `missed` and does not count against the completion percent.
A one-off task is not a single instant: `tasks.start_at` opens a work window
and `tasks.deadline` is the ready-by moment, so the row shows «from … to …»
and appears in the list from its start day on. Tapping **▶ В работе** sets
`in_progress`: the deadline stays, the row is never punished as missed, and
it remains visible until done or skipped.

### Reminder tick

<!-- mermaid:begin reminder-flow -->
```mermaid
sequenceDiagram
    autonumber
    participant Sch as run_tick (every 60s, UTC now)
    participant Core as BotApp + Store
    participant Msg as Messenger port<br/>(notify_user → all/primary accounts)
    participant U as User (TG/VK)

    Sch->>Core: mark_overdue(now): pending past due → missed<br/>(in_progress work stays open)
    loop per user (in user tz)
        Core->>Core: reminders_on? materialize horizon,<br/>trigger reached (times/interval<br/>or any occurrence reminder_at)? quiet hours?
        alt no trigger / quiet / reminders off
            Core-->>Sch: silent
        end
        Core->>Core: occurrences due today (any place)
        alt none
            Core-->>Sch: silent (no message at all)
        end
        Core->>Core: current_location fresh? (set_at + ttl > now)
        alt stale or unset
            Core->>Msg: "Where are you now?" + place buttons<br/>+ «Not now» (once: pending_reminder=true, last_reminded_at=now)
            Msg->>U: 📍 Где вы сейчас? [Дом] [Работа] … [⏭ Не сейчас]
            Core->>Core: whatever dialog is in progress (wizard, list intent)<br/>is parked — the question is modal
            U->>Msg: tap "place:42" (or type a place name)
            Msg->>Core: set location, pending_reminder=false
            Core->>Msg: the parked dialog is replayed, then the digest follows
        else fresh
            Core->>Core: digest = today's occurrences at that place
            alt empty
                Core-->>Sch: silent
            end
        end
        Core->>Msg: digest text + per-task buttons<br/>(older keyboards of this chat get disarmed)
        Msg->>U: 📋 Задачи «Работа»:<br/>1. Отчёт — до 18:00 [✅][⏭][⏭skip]<br/>2. Полка — с 26.10 09:00 до 30.10 18:00 [✅][▶][⏭][⏭skip]<br/>3. Созвон — до 12:00 ⚠️просрочено [✅][⏭][⏭skip]
    end
    U->>Msg: "done:42" button (any linked chat)
    Msg->>Core: ownership check → status=done + completions row
    U->>Msg: "wip:42" → status=in_progress: «▶ в работе»,<br/>row keeps its deadline but is never marked missed
    U->>Msg: "snooze:42" → duration buttons [15м][1ч][4ч][+сутки]
    U->>Msg: "snz:42:60" → reminder_at = now + 60 min
    U->>Msg: "skip:42" → status=skipped: hidden today,<br/>never flipped to missed, no penalty in stats
    Msg->>U: after any completed command:<br/>"Хотите сделать что-нибудь ещё?" + command menu
```
<!-- mermaid:end reminder-flow -->

Hard rules:
1. quiet hours / `reminders_on=false` / trigger moment not reached → nothing;
2. no occurrences due today → nothing;
3. location older than `location_ttl_hours` → ask with place buttons first;
4. no occurrences for the current place → nothing.

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
