# API — Telegram Bot Surface

> This bot has **no HTTP API**. Its "API" is the set of Telegram updates it
> reacts to. See [`architecture.md`](architecture.md) for wiring and
> [`request-flows.md`](request-flows.md) for end-to-end sequences.

## Authentication & permissions

- **Auth**: single shared secret `TELEGRAM_BOT_TOKEN` (env var, supplied to the
  container via `~/nargila-counter/.env` on the server — never committed).
- **Per-user auth**: **none**. Anyone in a chat the bot is a member of can
  trigger `+1` for themselves and call `/stats`.
- **Group requirement**: BotFather **privacy mode must be disabled**
  (`/setprivacy` → Disable) for the bot to receive photo messages in groups.
  The bot logs a warning about this on every startup.

## Command & message reference

| Trigger | Method | Handler | Purpose |
|---|---|---|---|
| `/start` | command | `start` | Reply with the help text (`HELP_TEXT`) |
| `/help` | command | `help_command` | Same help text |
| `/stats` | command | `stats` | All-time leaderboard + per-user win counts |
| `/stats week\|w\|неделя\|неделю\|нед` | command | `stats` | Current-week leaderboard |
| `/stats month\|m\|месяц\|мес` | command | `stats` | Current-month leaderboard |
| Photo **or** image document whose caption/text matches `\+\s*1\b` | message | `on_hookah_message` | Record +1 for the sender, reply with current-week leaderboard |

### `/stats` (no args)

**Response schema** (plain text, anti-rating — fewer hookahs ranks first):

```
📉 Общий счёт кальянов (меньше — лучше):
🥇 <name> — <count>
🥈 <name> — <count>
🥉 <name> — <count>
4. <name> — <count>

🏆 Победы (анти-рейтинг — кто выкурил меньше всех):
  • <name>: недель — <n>, месяцев — <n>
```

- If no history exists → `Пока нет записей.`
- The `🏆 Победы` block is omitted entirely when no one has won a period yet
  (`get_win_counts` returns empty).
- Each win-count line lists **both** week and month wins (zeros included) and is
  only printed for users who have at least one win of either type.

**DB effects**: none (read-only).
**Business purpose**: see the all-time ranking at a glance.

### `/stats week` / `/stats month`

**Response**: a leaderboard scoped to the current period, titled
`📉 Счёт за текущую неделю (меньше — лучше):` or `... текущий месяц ...`.
Rows are ordered ascending by count (anti-rating), tie-broken by `user_name`.
Empty period → `За этот период записей пока нет.`

**Alias sets** (case-insensitive via `.lower()`):
- week: `w`, `week`, `неделя`, `неделю`, `нед`
- month: `m`, `month`, `месяц`, `мес`

**DB effects**: none.

### Photo message with `+1` (`on_hookah_message`)

**Trigger conditions** (all required):
1. `update.effective_message` and `update.effective_chat` are present.
2. `message.from_user` is present (else warn + return — covers channels).
3. `has_image(message)` is true (photo **or** a document whose MIME starts with
   `image/`).
4. The combined `caption + " " + text` matches `PLUS_ONE_RE` = `\+\s*1\b`.

**Flow**: see [`request-flows.md`](request-flows.md) §"Photo +1 ingestion".

**Response when newly counted** (current-week leaderboard):
```
📉 Счёт за эту неделю (меньше — лучше):
🥇 <name> — <count>
...
```

**Response when duplicate** (same `message_id` already processed): **no reply**
— the +1 is silently ignored (idempotent dedup via `processed_messages`).

**Response when any trigger condition fails**: **no reply** (early return).

**DB effects** (single transaction inside `add_hookah_and_get_score`):
- `INSERT OR IGNORE INTO processed_messages (chat_id, message_id)` — dedup gate.
- If new: `INSERT ... ON CONFLICT DO UPDATE count = count + 1` into
  `hookah_stats`, and `INSERT INTO hookah_log`.
- Returns `(rows, was_counted)`; the handler replies only if `was_counted`.

### Possible errors

- `RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")` — raised at
  startup in `build_application` if the env var is missing. Container will exit
  (restart policy `unless-stopped` will retry once the env is fixed).
- Any unhandled exception inside a handler is routed to `on_error`, which logs
  it; the user sees nothing (the bot does not crash).

## Integrations called

- **Telegram Bot API** (via `python-telegram-bot` 22.7): long polling
  (`run_polling`), `reply_text`, `send_message`. See
  [`integrations.md`](integrations.md).
- **SQLite** (`sqlite3` stdlib): all reads/writes. See [`database.md`](database.md).
