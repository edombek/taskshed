# Конфигурация (русский)

Два источника, сливаются по порядку (env сильнее): `config.yaml` (образец —
`config.example.yaml`), затем переменные окружения `TASKSHED_*` процесса.
`config.yaml` в `.gitignore` — секреты не коммитить.

## База данных

```yaml
database:
  url: sqlite+aiosqlite:///taskshed.db
  # PostgreSQL: url: postgresql+asyncpg://user:pass@host/taskshed
```

Env: `TASKSHED_DATABASE_URL`. Схема создаётся командой `alembic upgrade head`.

## Telegram

```yaml
platforms:
  telegram:
    enabled: true
    api_id: 123456          # https://my.telegram.org
    api_hash: "..."
    bot_token: "..."        # @BotFather
    proxy:                  # опциональный MTProto-прокси
      type: mtproxy         # mtproxy | securedmtproxy
      ip: 1.2.3.4
      port: 443
      secret: "..."
```

Env: `TASKSHED_TELEGRAM_API_ID`, `..._API_HASH`, `..._BOT_TOKEN`,
`..._PROXY_IP/_PROXY_PORT/_PROXY_SECRET` (все три вместе включают mtproxy).

## VK

```yaml
platforms:
  vk:
    enabled: true
    group_token: "vk1.a..."   # токен сообщества с включённым «offline»
    group_id: 123456789
    api_url: "https://api.vk.ru/method/"   # по умолчанию; api.vk.com тоже отвечает
    proxy: {type: socks5, url: "socks5://user:pass@host:1080"}  # опционально
```

VK перевёл API в доменную зону `vk.ru`: база по умолчанию
`https://api.vk.ru/method/`, переопределяется `api_url` или
`TASKSHED_VK_API_URL`. API VK — обычный HTTPS, поэтому подходят стандартные
HTTP/SOCKS5-прокси; MTProxy к VK неприменим. Env: `TASKSHED_VK_GROUP_TOKEN`,
`..._GROUP_ID`, `..._API_URL`, `..._PROXY_URL`.

## Бот

```yaml
bot:
  default_language: ru        # язык при регистрации, если платформа
                              # не сообщает язык пользователя
  occurrence_horizon_days: 8  # на сколько дней вперёд материализуются повторы
```

## Запуск

```bash
python -m taskshed.entrypoints.run_telegram [config.yaml]
python -m taskshed.entrypoints.run_vk       [config.yaml]
python -m taskshed.entrypoints.run_all      [config.yaml]  # всё включённое
```
