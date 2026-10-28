# Привязка аккаунтов (русский)

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

Источник: [linking-flow.mmd](../diagrams/linking-flow.mmd) ·
PNG: [linking-flow.png](../diagrams/linking-flow.png)

Один человек (строка `users`) может владеть аккаунтами в Telegram и VK;
задачи, места и настройки привязаны к пользователю и общие по умолчанию.

- `/привязать` создаёт одноразовый 8-символьный код, живущий 10 минут;
  у пользователя одновременно активен только один код (новый заменяет старый).
- `привязать КОД` отправляется боту **на другой платформе**.
- Аккаунт второй платформы переносится к владельцу кода; если у него уже
  были свои задачи/места, они сливаются (одноимённые места получают суффикс
  `#2`). Все действия пишутся в `link_audit`.
- `/отвязать` отвязывает текущий аккаунт (со следующего сообщения он —
  новый пустой пользователь). Запрещено, если аккаунт единственный.

Рассылка напоминаний определяется `user_settings.notify_platforms`
(⚙️ Настройки → 📡 Куда слать): `primary` (по умолчанию) — только в чат,
где вы последний раз общались с ботом (`accounts.last_seen`); `all` — во все
привязанные чаты; `telegram` / `vk` — прикрепить рассылку к одной платформе
(если она не привязана, берётся самый активный аккаунт).
