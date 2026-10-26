# Архитектура (русский)

## Диаграммы

Ниже блоки отображаются самим GitHub (вектор). Их текст синхронизируется из
исходников `.mmd` в [`docs/diagrams/`](../diagrams/) — они остаются единственным
источником истины; после правки `.mmd` запускайте `scripts/sync_diagram_blocks.py`
(`tests/test_docs_diagrams.py` следит, чтобы копии не разъезжались).

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
    places ||--o{ tasks : "groups"
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

Для чтения офлайн и не на GitHub есть PNG (перегенерация —
`scripts/render_diagrams.sh`): [architecture.png](../diagrams/architecture.png) ·
[er-diagram.png](../diagrams/er-diagram.png) ·
[reminder-flow.png](../diagrams/reminder-flow.png) ·
[linking-flow.png](../diagrams/linking-flow.png)

## Идея

Гексагональная архитектура (порты и адаптеры). `taskshed/core` — чистая
доменная логика, которая ничего не знает про Telegram и VK. Каждая платформа —
адаптер, который (1) переводит сырые события платформы в платформо-независимый
`RawEvent` (текст сообщений и нажатия кнопок) и (2) реализует порт `IMessenger`
(`send_text`, `send_buttons`, `send_image`, `send_document`). Логика чата
(`bot/app.py::BotApp`) и планировщик (`scheduler/jobs.py`) работают только с
`RawEvent` / `IMessenger`.

Практическое следствие для тестов: весь бот прогоняется в юнит-тестах против
`messaging/base.py::FakeMessenger` и in-memory SQLite — без сети и реальных
платформ.

## Модули

| Модуль | Ответственность |
|---|---|
| `taskshed/core/models.py` | доменные датаклассы (`Task`, `Place`, `TaskOccurrence`, …) |
| `taskshed/core/scheduling.py` | правила расписания → даты срабатываний, материализация, просрочки |
| `taskshed/core/stats.py` | агрегации за период (дни/задачи/места) |
| `taskshed/storage/` | таблицы SQLAlchemy async + репозитории, миграции Alembic |
| `taskshed/messaging/` | порт + `FakeMessenger` (`base.py`) + `telegram.py`, `vk.py` |
| `taskshed/bot/app.py` | `BotApp`: команды чата, диалоги-анкеты, кнопки |
| `taskshed/scheduler/jobs.py` | ежеминутный тик напоминаний, правила молчания |
| `taskshed/stats/` | графики matplotlib (дневные и почасовые карты активности), экспорт CSV/XLSX |
| `taskshed/i18n/` | каталоги ru/en JSON + `t()` |
| `taskshed/config.py` | конфиг YAML + env, прокси по платформам |
| `taskshed/entrypoints/` | `run_telegram`, `run_vk`, `run_all` |

## Модель данных

См. [ER-диаграмму](../diagrams/er-diagram.mmd). Ключевое: **`users` не зависит
от платформы**; `accounts` отображает `(platform, platform_user_id)` на
`user`. Всё остальное (задачи, места, настройки) висит на `users.id` — поэтому
привязка Telegram↔VK тривиальна.

### Расписания и срабатывания

`schedule_type`: `once` · `every_n_days` · `weekdays` · `monthly_day`,
параметры в `schedule_data` (JSON). Срабатывания материализуются на несколько
дней вперёд (`bot.occurrence_horizon_days`); у каждого `due_at`, `reminder_at`,
`start_at`, `status ∈ {pending, in_progress, done, missed, skipped}`. Фоновый
проход переводит `pending` с истёкшим сроком в `missed`; пропущенная
пользователем задача становится `skipped` — она скрывается из списка на
сегодня, никогда не становится `missed` и не портит процент выполнения.
Разовая задача — не миг: `tasks.start_at` открывает окно работы, а
`tasks.deadline` — срок готовности, поэтому строка показывается «с … до …» и
появляется в списке со дня начала. Кнопка **▶ В работе** ставит
`in_progress`: срок остаётся, просрочкой задача не считается и видна в списке,
пока её не сделают или не пропустят.

### Тик напоминаний

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
            Core->>Msg: "Where are you now?" + place buttons<br/>(once: pending_reminder=true, last_reminded_at=now)
            Msg->>U: 📍 Где вы сейчас? [Дом] [Работа] …
            U->>Msg: tap "place:42" button
            Msg->>Core: set location, pending_reminder=false
            Core->>Msg: digest follows immediately
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

Жёсткие правила:
1. тихие часы / `reminders_on=false` / момент срабатывания не наступил → молчим;
2. нет срабатываний на сегодня → молчим;
3. место старше `location_ttl_hours` → сначала вопрос с кнопками мест;
4. нет срабатываний по текущему месту → молчим.

### Привязка аккаунтов

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

`/привязать` создаёт
одноразовый 8-символьный код (TTL 10 минут, активен только один за раз);
вторая платформа его активирует. Аккаунт переносится к владельцу кода, его
прежний пользователь сливается автоматически (коллизии имён мест получают
`#2`); все шаги пишутся в `link_audit`. Предпросмотр слияния — в бэклоге.

## Конфигурация

`config.yaml` (образец — `config.example.yaml`), переменные `TASKSHED_*`
переопределяют. Telegram поддерживает MTProto-прокси (`mtproxy` /
`securedmtproxy`, соединение Telethon randomized-intermediate); для VK —
обычные http/socks5, так как API VK это обычный HTTPS.
