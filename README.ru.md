# taskshed — бот напоминаний о задачах с привязкой к местам

**Язык:** [English](README.md) · [Русский](README.ru.md)

Чат-бот, который напоминает о задачах **в зависимости от того, где вы
находитесь**: домашняя рутина — дома, рабочие задачи — на работе. Работает в
**Telegram** (Telethon, поддержка MTProto-прокси) и **VK** (Bot Long Poll)
через единое платформо-независимое ядро.

> Бот не следит за вашей геолокацией — вы сами сообщаете ему, где находитесь,
> одним нажатием («Дома» / «На работе» / …). Если напоминать не о чем — бот
> молчит.

## Возможности

- Задачи разделены по **местам**, которые создаёте вы сами (`/места`) — или просто
  набираете название прямо в анкете задачи, и место создаётся. Удаление места
  спрашивает подтверждение: вместе с ним уходят его задачи и вся история.
- Расписания: разовая задача **с началом и сроком готовности** — или только
  момент, если выбрать **«без срока»** (такую задачу можно перевести в ▶
  «В работе» — просрочкой она не считается), каждые
  N дней, конкретные дни недели, день месяца; каждый повтор —
  материализованное **срабатывание**, которое можно отметить ✅ «Сделано»,
  ⏭ отложить (15 мин/час/4 часа/сутки) или ⏭ пропустить прямо из напоминания.
- **⚙️ Один экран настроек** для пользователя: напоминания (фиксированные
  времена либо интервал), тихие часы, часовой пояс, срок актуальности места,
  на сколько дней вперёд строятся графики, язык и **статус привязки платформ** (Telegram/VK).
- **Один способ назвать время**: `13.00`, `13`, `завтра 9`, `15.11 14.30`
  понимаются одинаково в любом вопросе, бот переспрашивает — «Понял так:
  27.10.2026 13:00. Верно?» — и только потом сохраняет; на шаге начала есть
  кнопка **«▶ Сейчас»**.
- **Редактор задач** (`/изменить`): поменять название, место, правило повтора,
  окно работы или время напоминания — и удалить задачу; вкладки «будущие /
  периодические / прошлые». Смена расписания перестраивает открытые повторы,
  история выполненного остаётся.
- В чате остаётся кликабельным только последний экран: кнопки у старых
  сообщений снимаются.
- **Без шума**: нет задач на сегодня — бот молчит; нет задач по текущему месту
  — тоже молчит. Место «протухло» — бот лишь спрашивает «Где вы сейчас?», и этот
  вопрос модальный: если вы были в середине диалога (анкета задачи, отчёт), он
  ждёт за ним и возвращается после ответа — либо жмёте ⏭ «Не сейчас».
- **Статистика**: текстовая сводка, выгрузка CSV/XLSX, PNG-графика — тепловая
  карта активности в стиле GitHub (метки просроченных/запланированных/
  пропущенных, окно графика всегда вытянуто на выбранный горизонт вперёд — по
  умолчанию неделя, и не короче самого периода) и **почасовая карта для
  коротких периодов**, где тоже видно
  просроченное и ещё не сделанное, топ задач/мест, тренд выполнения
  — за любой период или за весь срок.
- **Мультиаккаунт**: привяжите Telegram и VK одноразовым кодом (`/link`) —
  данные и напоминания общие.
- Интерфейс на **русском и английском** (`/lang`).
- Постоянное хранилище: по умолчанию SQLite, готово к PostgreSQL
  (SQLAlchemy 2, миграции Alembic).

## Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp config.example.yaml config.yaml   # впишите токены и (опционально) прокси
alembic upgrade head                 # создать схему БД

python -m taskshed.entrypoints.run_telegram   # только Telegram
python -m taskshed.entrypoints.run_vk         # только VK
python -m taskshed.entrypoints.run_all        # обе платформы сразу
```

Вместо `config.yaml` можно использовать переменные окружения — см.
[`.env.example`](.env.example).

### MTProto-прокси для Telegram

```yaml
platforms:
  telegram:
    proxy: {type: mtproxy, ip: "1.2.3.4", port: 443, secret: "..."}
```

VK API работает по обычному HTTPS, поэтому для VK указывается стандартный
HTTP/SOCKS5-прокси.

## Команды чата

| Команда | Значение |
|---|---|
| `/start` | регистрация |
| `/гдея` | выбрать текущее место |
| `/задачи` | задачи текущего места |
| `/задача` | создать задачу (диалог-анкета) |
| `/изменить` | изменить любой пункт задачи или удалить её |
| `/места` | управление местами |
| `/настройки` (ранее `/напоминания`) | все настройки пользователя |
| `/статистика` `[неделя\|месяц\|год\|всё] [csv\|xlsx\|график]` | отчёты |
| `/привязать` / `/отвязать` | связать Telegram ↔ VK |
| `/lang` | сменить язык (ru/en) |

## Разработка

```bash
ruff check . && pytest      # линт + тесты (чат-тесты идут через FakeMessenger, без сети)
```

- План и дорожная карта: [`docs/PLAN.md`](docs/PLAN.md)
- Архитектура (EN/RU): [`docs/en/architecture.md`](docs/en/architecture.md) ·
  [`docs/ru/architecture.md`](docs/ru/architecture.md)
- Конфигурация: [`docs/en/configuration.md`](docs/en/configuration.md) ·
  [`docs/ru/configuration.md`](docs/ru/configuration.md)
- Привязка аккаунтов: [`docs/en/linking.md`](docs/en/linking.md) ·
  [`docs/ru/linking.md`](docs/ru/linking.md)
- Диаграммы: [`docs/diagrams/`](docs/diagrams/) (исходники Mermaid + готовые PNG;
  блоки ниже GitHub отображает в векторе)

- История изменений: [`CHANGELOG.md`](CHANGELOG.md)

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

PNG-версия: [architecture.png](docs/diagrams/architecture.png)

## Лицензия

MIT
