# Архитектура (русский)

Диаграмма компонентов: [architecture.mmd](../diagrams/architecture.mmd) ·
[ER-диаграмма](../diagrams/er-diagram.mmd) ·
[поток напоминаний](../diagrams/reminder-flow.mmd) ·
[поток привязки аккаунтов](../diagrams/linking-flow.mmd)

## Идея

Гексагональная архитектура (порты и адаптеры). `taskshed/core` — чистая
доменная логика, которая ничего не знает про Telegram и VK. Каждая платформа —
адаптер, который (1) переводит сырые события платформы в платформо-независимый
`Command` (текст сообщений и нажатия кнопок) и (2) реализует порт `IMessenger`
(`send_text`, `send_image`, `send_document`, `ask_choice`). Логика чата
(`bot/handlers.py`) и планировщик (`scheduler/jobs.py`) работают только с
`Command` / `IMessenger`.

Практическое следствие для тестов: весь бот прогоняется в юнит-тестах против
`messaging/fake.py::FakeMessenger` и in-memory SQLite — без сети и реальных
платформ.

## Модули

| Модуль | Ответственность |
|---|---|
| `taskshed/core/models.py` | доменные датаклассы (`Task`, `Place`, `TaskOccurrence`, …) |
| `taskshed/core/scheduling.py` | правила расписания → даты срабатываний, материализация, просрочки |
| `taskshed/core/stats.py` | агрегации за период (дни/задачи/места) |
| `taskshed/storage/` | таблицы SQLAlchemy async + репозитории, миграции Alembic |
| `taskshed/messaging/` | порт (`base.py`) + `telegram.py`, `vk.py`, `fake.py` |
| `taskshed/bot/handlers.py` | команды чата, диалоги-анкеты, кнопки |
| `taskshed/scheduler/jobs.py` | ежеминутный тик напоминаний, правила молчания |
| `taskshed/stats/` | графики matplotlib, экспорт CSV/XLSX |
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
`status ∈ {pending, done, missed}`. Фоновый проход переводит `pending` с
истёкшим сроком в `missed`.

### Тик напоминаний

См. [диаграмму потока](../diagrams/reminder-flow.mmd). Жёсткие правила:
1. тихие часы / `reminders_on=false` / момент срабатывания не наступил → молчим;
2. нет срабатываний на сегодня → молчим;
3. место старше `location_ttl_hours` → сначала вопрос с кнопками мест;
4. нет срабатываний по текущему месту → молчим.

### Привязка аккаунтов

См. [диаграмму потока](../diagrams/linking-flow.mmd). `/link` создаёт
одноразовый 8-символьный код (TTL 10 минут); вторая платформа его активирует.
Слияние двух непустых пользователей показывает предпросмотр и требует
подтверждения; операция пишется в `link_audit`.

## Конфигурация

`config.yaml` (образец — `config.example.yaml`), переменные `TASKSHED_*`
переопределяют. Telegram поддерживает MTProto-прокси (`mtproxy` /
`securedmtproxy`, соединение Telethon randomized-intermediate); для VK —
обычные http/socks5, так как API VK это обычный HTTPS.
