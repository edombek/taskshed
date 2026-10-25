# Configuration (English)

Two sources, merged in order (env wins): `config.yaml` (see
`config.example.yaml`), then `TASKSHED_*` environment variables / `.env`.
`config.yaml` and `.env` are gitignored — never commit secrets.

## Database

```yaml
database:
  url: sqlite+aiosqlite:///taskshed.db
  # PostgreSQL: url: postgresql+asyncpg://user:pass@host/taskshed
```

Env: `TASKSHED_DATABASE_URL`. Create/upgrade the schema with
`alembic upgrade head`.

## Telegram

```yaml
platforms:
  telegram:
    enabled: true
    api_id: 123456          # https://my.telegram.org
    api_hash: "..."
    bot_token: "..."        # @BotFather
    proxy:                  # optional MTProto proxy
      type: mtproxy         # mtproxy | securedmtproxy
      ip: 1.2.3.4
      port: 443
      secret: "..."
```

Env: `TASKSHED_TELEGRAM_API_ID`, `..._API_HASH`, `..._BOT_TOKEN`,
`..._PROXY_IP/_PROXY_PORT/_PROXY_SECRET` (all three together enable mtproxy).

## VK

```yaml
platforms:
  vk:
    enabled: true
    group_token: "vk1.a..."   # community token with "offline" enabled
    group_id: 123456789
    proxy: {type: socks5, url: "socks5://user:pass@host:1080"}  # optional
```

VK API is plain HTTPS, so ordinary HTTP/SOCKS5 proxies are used; MTProxy
does not apply. Env: `TASKSHED_VK_GROUP_TOKEN`, `..._GROUP_ID`, `..._PROXY_URL`.

## Bot

```yaml
bot:
  default_language: ru           # used at registration when platform
                                 # language is unknown
  occurrence_horizon_days: 8     # how far ahead repeats are materialized
```

## Running

```bash
python -m taskshed.entrypoints.run_telegram [config.yaml]
python -m taskshed.entrypoints.run_vk       [config.yaml]
python -m taskshed.entrypoints.run_all      [config.yaml]  # everything enabled
```
