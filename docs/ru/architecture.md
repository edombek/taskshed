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
        string notify_platforms "all|primary"
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
(`BotApp` — диспетчер в `bot/app.py`, по модулю на диалог в `bot/`) и
планировщик (`scheduler/jobs.py`) работают только с `RawEvent` / `IMessenger`.

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
| `taskshed/bot/` | `BotApp` (`app.py`) + по модулю на диалог: анкета, редактор, карточки, дайджест, отчёты, настройки, локация, места; общее состояние — в `base.py` |
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
параметры в `schedule_data` (JSON). У любого вида одни и те же два вопроса:
**к какому моменту быть готовым** и **за сколько до этого предупредить** —
`deadline_time` (часы, на которые приходится срок в каждый день повтора) и
`notify_before_min`; у разовой задачи вместо этого стоит собственный срок
готовности в `tasks.deadline`.

**Планы хранятся во времени пользователя.** `tasks.deadline`,
`tasks.start_at`, `tasks.deadline_time` и часы настроек — это wall-clock
пользователя, а не время сервера. Строки `task_occurrences` — реальные моменты
в UTC, выведенные из плана: `due_at` — срок,
`notify_at = due_at − notify_before_min`. Смена `tz` пересчитывает всё
открытое будущее (`Store.replan_open_occurrences`) — «к 18:00» остаётся 18:00
на часах пользователя, — а готовая история не меняется.

Срабатывания материализуются на несколько дней вперёд
(`bot.occurrence_horizon_days`); у каждого есть `start_at` и
`status ∈ {pending, in_progress, done, missed, skipped}`. Фоновый проход
переводит `pending` с истёкшим сроком в `missed`; пропущенная пользователем
задача становится `skipped` — она скрывается из списка на сегодня, никогда не
становится `missed` и не портит процент выполнения. Разовая задача — не миг:
`tasks.start_at` открывает окно работы, а `tasks.deadline` — срок готовности,
поэтому строка показывается «с … до …» и появляется в списке со дня начала.
Кнопка **▶ В работе** ставит `in_progress`: срок остаётся, просрочкой задача
не считается и видна в списке, пока её не сделают или не пропустят.

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
        Core->>Msg: notice header + one message per task of this place<br/>+ one brief line-up for the others + «Готово»<br/>(older keyboards of this chat get disarmed)
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
```
<!-- mermaid:end reminder-flow -->

Жёсткие правила:
1. тихие часы / `reminders_on=false` → молчим;
2. сообщать не о чем — нет открытой строки с `due_at` не раньше начала дня и
   `notify_at` не позже конца дня → молчим;
3. момент не наступил → молчим. Моментов два: **утренняя сводка** в
   `morning_time` (раз в календарный день, по часам пользователя) и
   **повтор каждые `nag_interval_min`** для строк внутри
   `[notify_at, due_at)`;
4. место старше `location_ttl_hours` → сначала вопрос «где вы сейчас?» с
   кнопками мест (вопрос модальный: открытый диалог откладывается и
   возвращается после ответа);
5. уведомление отправляет задачи **текущего места** отдельными сообщениями с
   кнопками, задачи **остальных мест** — одним кратким списком, и заканчивается
   одной кнопкой «Готово» (`fin:`), которая закрывает раунд статусов и
   возвращает главное меню.

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
