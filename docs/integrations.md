# External Integrations

> Two integrations: the **Telegram Bot API** and **SQLite**. Both are
> dependency-light; failure modes are described below. See
> [`architecture.md`](architecture.md) for where they sit in the dependency
> graph.

## 1. Telegram Bot API

- **SDK**: `python-telegram-bot[job-queue]==22.7` (pinned in `Dockerfile` via
  `ENV PTB_VERSION=22.7` and in `pyproject.toml`).
- **Auth**: bot token in `TELEGRAM_BOT_TOKEN` env var. Read once in
  `build_application`; missing → `RuntimeError`, container exits.
- **Transport**: **long polling** (`app.run_polling(allowed_updates=Update.ALL_TYPES,
  drop_pending_updates=False)`). No webhook, no public ingress required — the
  bot works behind NAT (ideal for the CasaOS deploy target).
- **Surface used**:
  - `ApplicationBuilder().token(...).build()`
  - `CommandHandler`, `MessageHandler(filters.PHOTO | filters.Document.IMAGE)`,
    `add_error_handler`
  - `app.job_queue.run_daily(...)` (APScheduler via the `[job-queue]` extra)
  - Per-update: `update.effective_message`, `update.effective_chat`,
    `message.from_user`, `message.photo`, `message.document.mime_type`,
    `message.caption`, `message.text`, `message.message_id`, `chat.id`
  - Outbound: `message.reply_text(text, parse_mode=...)`,
    `context.bot.send_message(chat_id, text)`
- **Failure handling**:
  - Network / Telegram outages: PTB retries polling internally; no custom retry.
  - `send_message` failures during scheduled announcements are caught and
    logged in `_announce_period_winner` — the winner is still persisted, so we
    don't retry-spam.
  - Any unhandled handler exception → routed to `on_error` (logs only).

### Required BotFather configuration

- **Privacy mode disabled** (`/setprivacy` → Disable). Without this, the bot
  does not receive non-command messages in groups, so `+1` photos are never
  seen. The bot logs a warning reminding you of this on every startup.

## 2. SQLite

- **Driver**: stdlib `sqlite3`.
- **Location**: `$DB_PATH` (default `/data/hookah.db`; `/data` is a Docker
  volume — `nargila-counter-data` in production).
- **Mode**: WAL journal, `synchronous=NORMAL`, `busy_timeout=30000ms`,
  autocommit (`isolation_level=None`).
- **Failure handling**:
  - "database is locked" → mitigated by WAL + 30s busy timeout; writers briefly
    wait instead of failing. Only one writer at a time (single-process bot), so
    contention is essentially impossible in production.
  - File corruption / missing volume → bot crashes on first query; restart
    policy `unless-stopped` keeps retrying. Recovery is manual: restore from a
    `hookah.db.bak.*` on the volume (see README).
- **No foreign keys** are declared; relations are maintained by application
  convention (see [`database.md`](database.md)).

## 3. File logging (side effect, not an integration per se)

- Rotating file handler (`TimedRotatingFileHandler`, `when="W0"`, weekly
  rotation, `backupCount=LOG_BACKUP_WEEKS`, default 4).
- Writes to `$LOG_PATH` (default `/data/logs/bot.log`, i.e. inside the same
  Docker volume as the DB). Failure to create the log dir is *not* caught —
  startup will raise; ensure the volume is writable.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(none, required)* | Bot auth |
| `DB_PATH` | `hookah.db` (dev) / `/data/hookah.db` (container) | SQLite file |
| `LOG_LEVEL` | `INFO` | Root logger level |
| `LOG_PATH` | `logs/bot.log` (dev) / `/data/logs/bot.log` (container) | Log file |
| `LOG_BACKUP_WEEKS` | `4` | Rotated log files to keep |
| `TIMEZONE` | `Europe/Belgrade` | For the daily 00:00 scheduler job |

Documented in `.env.example` (only `TELEGRAM_BOT_TOKEN` — the rest have safe
container defaults baked into the `Dockerfile`).
