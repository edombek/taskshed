# Configuration (English)

Two sources, merged in order (env wins): `config.yaml` (see
`config.example.yaml`), then `TASKSHED_*` environment variables of the
process. `config.yaml` is gitignored — never commit secrets.

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
    group_token: "vk1.a..."   # community token: "offline" + "longpoll" rights
    api_url: "https://api.vk.ru/method/"   # default; api.vk.com also answers
    proxy: {type: socks5, url: "socks5://user:pass@host:1080"}  # optional
```

The community itself is discovered at startup (`groups.getById` answers with
the community that issued the token), so there is no `group_id` to keep in
sync. Keyboards require the community to have the **chat bot** status enabled
(Community management → General → Status); without it VK refuses keyboards
(error 912) and the adapter degrades to text.

VK moved its API to the `vk.ru` domain zone: the default base is
`https://api.vk.ru/method/`, overridable via `api_url` or
`TASKSHED_VK_API_URL`. VK API is plain HTTPS, so ordinary HTTP/SOCKS5 proxies
are used; MTProxy does not apply. Env: `TASKSHED_VK_GROUP_TOKEN`,
`..._API_URL`, `..._PROXY_URL`.

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
