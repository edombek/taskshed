# Account linking (English)

<!-- mermaid:begin linking-flow -->
```mermaid
sequenceDiagram
    autonumber
    participant U as Owner (already registered)
    participant TG as Telegram bot
    participant Core as BotApp + Store
    participant VK as VK bot

    U->>TG: /link  (RU: /привязать)
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
    else expired / used / unknown
        Core-->>VK: None
        VK-->>U: "Code invalid. Request a new one."
    end
    note over Core: Merge is automatic (no confirmation preview yet —<br/>see backlog in docs/PLAN.md). /unlink reverts only<br/>the account, when the user has other accounts.
```
<!-- mermaid:end linking-flow -->

Source: [linking-flow.mmd](../diagrams/linking-flow.mmd) ·
PNG: [linking-flow.png](../diagrams/linking-flow.png)

One human (`users` row) can own a Telegram and a VK account; tasks, places
and settings live on the user, so they are shared automatically.

- `/link` (RU `/привязать`) mints a one-time 8-character code, valid 10
  minutes, one per user at a time (new code replaces the old one).
- Send `link CODE` to the bot **on the other platform**.
- The other platform's account is moved to the code owner; if that account
  already had its own tasks/places, they are merged into the owner
  (place-name clashes get a `#2` suffix). Every operation lands in
  `link_audit`.
- `/unlink` (RU `/отвязать`) detaches the current account (it becomes a
  fresh empty user on the next message). Refused when it is the only
  account.

Reminder fan-out follows `user_settings.notify_platforms`:
`all` (default) sends to every linked chat; `primary` sends only to the
account with the most recent activity (`accounts.last_seen`).
